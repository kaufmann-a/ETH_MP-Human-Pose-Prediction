import glob
# Load all models
from os.path import dirname, basename, isfile

import torch.utils.data

from source.configuration import Configuration
from source.exceptions.configurationerror import ConfigurationError
from source.data.JointDataset import JointDataset
from source.logcreator.logcreator import Logcreator

modules = glob.glob(dirname(__file__) + "/*.py")
for module in [basename(f)[:-3] for f in modules if
               isfile(f) and not f.endswith('__init__.py') and not f == "datasetfactory"]:
    __import__("source.data." + module)

class DataSetFactory(object):

    @staticmethod
    def load(general_cfg, is_train):
        dataset_cfg = general_cfg.dataset

        all_datasets = JointDataset.__subclasses__()
        if dataset_cfg:
            dataset = [m(general_cfg, is_train) for m in all_datasets if m.name.lower() in [dataset.lower() for dataset in dataset_cfg]]
            if dataset and len(dataset) > 0:
                return torch.utils.data.ConcatDataset(dataset)


        raise ConfigurationError('data_collection.dataset')
