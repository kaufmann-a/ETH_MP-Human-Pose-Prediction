import torch
import torch.nn.functional as F
import torchvision

from source.models.basemodel import BaseModel
from source.logcreator.logcreator import Logcreator




class BackboneResNet(torch.nn.Sequential):
    def __init__(self, resnet_model):
        resnet = self.resolve_resnet_model(resnet_model, pretrained=True)
        super().__init__(
            resnet.conv1,
            resnet.bn1,
            resnet.relu,
            resnet.maxpool,
            resnet.layer1,
            resnet.layer2,
            resnet.layer3,
            resnet.layer4,
        )

    @staticmethod
    def resolve_resnet_model(name, *args, **kwargs):
        return {
            'resnet18': torchvision.models.resnet18,
            'resnet34': torchvision.models.resnet34,
            'resnet50': torchvision.models.resnet50,
            'resnet101': torchvision.models.resnet101,
            'resnet152': torchvision.models.resnet152,
        }[name](*args, **kwargs)


class JointHeatmapDeconv2x(torch.nn.Sequential):
    def __init__(self, in_channels, out_channels, kernel_size):
        assert kernel_size in (2, 3, 4), 'kernel_size must be 2, 3 or 4'

        if kernel_size == 2:
            padding, output_padding = 0, 0
        elif kernel_size == 3:
            padding, output_padding = 1, 1
        elif kernel_size == 4:
            padding, output_padding = 1, 0

        super().__init__(
            torch.nn.ConvTranspose2d(in_channels, out_channels, kernel_size, stride=2, padding=padding,
                                     output_padding=output_padding, bias=False),
            torch.nn.BatchNorm2d(out_channels),
            torch.nn.ReLU(inplace=True)
        )


# Alternative to Deconv2x layer but only supports 3x3 convolution
class JointHeatmapUpsample2x(torch.nn.Sequential):
    def __init__(self, in_channels, out_channels):
        super().__init__(
            torch.nn.Upsample(scale_factor=2, mode='bilinear'),
            torch.nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1, bias=False),
            torch.nn.BatchNorm2d(out_channels),
            torch.nn.ReLU(inplace=True)
        )


class JointHeatmapDecoder(torch.nn.Module):
    def __init__(self, in_channels, num_layers, num_filters, kernel_size, num_joints, depth_dim):
        super().__init__()
        self.num_joints = num_joints
        self.depth_dim = depth_dim

        # TODO: Add configurable Upsample2x as an alternative to Deconv2x?
        upsample_module_list = []
        for i in range(num_layers):
            upsample_module_list.append(JointHeatmapDeconv2x(in_channels=in_channels if i == 0 else num_filters,
                                  out_channels=num_filters, kernel_size=kernel_size))
        self.upsample_features = torch.nn.Sequential(*upsample_module_list)
        # TODO: Add configurable "non-bias" end? (see `with_bias_end' in deconv_head.py)
        self.features_to_heatmaps = torch.nn.Conv2d(num_filters, num_joints * depth_dim, kernel_size=1)

        JointHeatmapDecoder._init_weights(self)

    @staticmethod
    def _init_weights(module):
        if isinstance(module, torch.nn.Conv2d):
            torch.nn.init.normal_(module.weight, mean=0, std=0.001)
            if hasattr(module, 'bias'):
                torch.nn.init.constant_(module.bias, 0)

        elif isinstance(module, torch.nn.BatchNorm2d):
            torch.nn.init.constant_(module.weight, 1)
            torch.nn.init.constant_(module.bias, 0)

        elif isinstance(module, torch.nn.ConvTranspose2d):
            torch.nn.init.normal_(module.weight, mean=0, std=0.001)

        elif isinstance(module, torch.nn.Module):
            for m in module.children():
                JointHeatmapDecoder._init_weights(m)

    def forward(self, features):
        features = self.upsample_features(features)
        heatmaps = self.features_to_heatmaps(features)

        N, _, H, W = heatmaps.shape
        heatmaps = heatmaps.reshape(N, self.num_joints, self.depth_dim, H, W)
        return heatmaps  # Return format N, J, D, H, W


class JointIntegralRegressor(torch.nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, heatmaps):
        N, J, D, H, W = heatmaps.shape

        # Apply global softmax to the heatmaps
        heatmaps = heatmaps.reshape(N, J, D * H * W)
        heatmaps = F.softmax(heatmaps)
        heatmaps = heatmaps.reshape(N, J, D, H, W)

        # Integrate over other axes
        x_maps = heatmaps.sum(dim=2).sum(dim=2)
        y_maps = heatmaps.sum(dim=2).sum(dim=3)
        z_maps = heatmaps.sum(dim=3).sum(dim=3)

        # Take expected coordinate for each axis (and recenter)
        x_preds = (x_maps * torch.arange(W)).sum(dim=2) / W - 0.5
        y_preds = (y_maps * torch.arange(H)).sum(dim=2) / H - 0.5
        z_preds = (z_maps * torch.arange(D)).sum(dim=2) / D - 0.5

        return torch.stack((x_preds, y_preds, z_preds), dim=2)  # Return format N, J, 3


# TODO: Investigate DSAC-like argmax as an alternative to the integral (soft-argmax)
# "Probabilistic selection for which we can derive the expected loss w.r.t. to all learnable parameters."
# DSAC - Differentiable RANSAC for Camera Localization (https://arxiv.org/abs/1611.05705)


class ModelIntegralPoseRegression(BaseModel):
    name = 'IntegralPoseRegressionModel'

    def __init__(self, model_params, dataset_params):
        super().__init__()
        resnet_params = getattr(model_params, str(model_params.resnet_model) + "_params")
        dataset_specific_params = []
        for dataset in range(len(dataset_params.dataset)):
            dataset_specific_params.append(getattr(dataset_params, str(dataset_params.dataset[dataset]) + "_params"))

        NUM_JOINTS = 18  # ToDo: What do we input here? Especially if we train with different datasets??

        self.backbone = BackboneResNet(model_params.resnet_model)

        self.joint_decoder = JointHeatmapDecoder(in_channels=resnet_params.channels[-1],
                                                 num_layers=model_params.num_deconv_layers,
                                                 num_filters=model_params.num_deconv_filters,
                                                 kernel_size=model_params.kernel_size,
                                                 num_joints=NUM_JOINTS,
                                                 depth_dim=model_params.depth_dim
                                                 )
        self.joint_regressor = JointIntegralRegressor()
        Logcreator.info("Successfully initialized model with name IntegralPoseRegressionModel successfully initialized")

    def forward(self, input):
        features = self.backbone(input)
        heatmaps = self.joint_decoder(features)
        joints = self.joint_regressor(heatmaps)

        return heatmaps, joints
