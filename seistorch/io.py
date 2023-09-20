
import os
import pickle
from yaml import load
from yaml import CLoader as Loader

class SeisIO:
    """The class for file/data input and output.
    """

    def __init__(self, cfg_path=None, load_cfg=True):
        """Initialize the SeisIO class."""
        if load_cfg:
            self.cfg = self.read_cfg(cfg_path)

    def read_cfg(self, cfg_path):
        """Read the configure file.

        Args:
            cfg_path (str): The path to the configure file.

        Raises:
            FileNotFoundError: If the configure file is not found.

        Returns:
            cfg: The converted configure dictionary.
        """
        if self.path_exists(cfg_path):
            with open(cfg_path, 'r') as ymlfile:
                cfg = load(ymlfile, Loader=Loader)
            return cfg
        else:
            raise FileNotFoundError(f"Config file {cfg_path} not found.")
    
    def path_exists(self, path):
        """Check if the path exists.

        Args:
            path (str): The path to be checked.

        Returns:
            bool: True if the path exists, False otherwise.
        """
        path = '' if path is None else path

        return os.path.exists(path)
    
    def read_pkl(self, path: str):
        """Read a pickle file.

        Args:
            path (str): The path to the pickle file.

        Returns:
            data: The data loaded from the pickle file.
        """
        # Open the file in binary mode and load the list using pickle

        self.path_exists(path)
        with open(path, 'rb') as f:
            data = pickle.load(f)

        return data