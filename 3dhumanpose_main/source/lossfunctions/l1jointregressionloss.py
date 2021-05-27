import torch
import torch.nn as nn


def weighted_l1_loss(input, target, weights, size_average, norm=False):
    if norm:
        input = input / torch.norm(input, 1)
        target = target / torch.norm(target, 1)

    out = torch.abs(input - target)
    out = out * weights
    if size_average:
        num_valid = weights.byte().any(dim=1).float().sum()
        return out.sum() / num_valid
    else:
        return out.sum()


class L1JointRegressionLoss_eth_code(nn.Module):
    def __init__(self, num_joints, size_average=True, reduce=True, norm=False):
        super(L1JointRegressionLoss_eth_code, self).__init__()
        self.size_average = size_average
        self.reduce = reduce
        self.num_joints = num_joints
        self.norm = norm

    def _assert_no_grad(self, tensor):
        assert not tensor.requires_grad, \
            "nn criterions don't compute the gradient w.r.t. targets - please " \
            "mark these tensors as not requiring gradients"

    def forward(self, preds, gt_joints, gt_joints_vis):
        pred_jts = preds.reshape((preds.shape[0], self.num_joints * 3))

        self._assert_no_grad(gt_joints)
        self._assert_no_grad(gt_joints_vis)
        return weighted_l1_loss(pred_jts, gt_joints, gt_joints_vis, self.size_average, self.norm)


class L1JointRegressionLoss(torch.nn.Module):
    def __init__(self, size_average=True):
        super(L1JointRegressionLoss, self).__init__()
        self.size_average = size_average

    def regression_loss_2d(self, pred_joints, gt_joints, gt_joints_vis):
        return weighted_l1_loss(pred_joints[:, :2], gt_joints, gt_joints_vis.unsqueeze(1), False)

    def regression_loss_3d(self, pred_joints, gt_joints, gt_joints_vis):
        # error = abs(pred_joints - gt_joints) * gt_joints_vis.unsqueeze(1)
        # err2 = weighted_l1_loss(pred_joints, gt_joints, gt_joints_vis.unsqueeze(1), False)
        # assert(err2 == torch.sum(error))
        return weighted_l1_loss(pred_joints, gt_joints, gt_joints_vis.unsqueeze(1), False)

    def forward(self, preds, batch_joints, batch_joints_vis):
        N, J, _ = preds.shape

        assert len(batch_joints) == len(batch_joints_vis) == N, \
            f'Batch size mismatch: {N=}, {len(batch_joints)=}, {len(batch_joints_vis)=}'

        batch_losses = []
        for batch_idx in range(N):
            gt_joints = batch_joints[batch_idx]
            gt_joints_vis = batch_joints_vis[batch_idx]

            assert gt_joints_vis.shape == (J,), \
                f'Invalid shape for batch_joints_vis[{batch_idx}]: expected (J,) ' \
                f'where {J=} but actual {gt_joints_vis.shape}'

            if gt_joints.shape == (J, 2):
                batch_losses.append(
                    self.regression_loss_2d(preds[batch_idx], gt_joints, gt_joints_vis)
                )
            elif gt_joints.shape == (J, 3):
                batch_losses.append(
                    self.regression_loss_3d(preds[batch_idx], gt_joints, gt_joints_vis)
                )
            else:
                raise AssertionError(
                    f'Invalid shape for batch_joints[{batch_idx}]: expected (J, 2) or (J, 3) '
                    f'where {J=} but actual {gt_joints.shape}'
                )
        if self.size_average:
            return torch.stack(batch_losses).mean()
        return torch.stack(batch_losses).sum()
