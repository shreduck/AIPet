"""Reading config.json and saving one setting at a time, at the paths in aipetlib.runtime.paths."""
from ..config import DEFAULT_CONFIG, ConfigStore
from . import paths

SAVE_PAUSED = {"v": False}  # while True, setters apply a setting without writing it (the app applying an edited file)


def load_config():
    return ConfigStore(paths.CONFIG_PATH, paths.HOME_DIR, DEFAULT_CONFIG).load()


def save_setting(key, value):
    return ConfigStore(paths.CONFIG_PATH, paths.HOME_DIR, DEFAULT_CONFIG).save_setting(key, value,
                                                                                      paused=SAVE_PAUSED["v"])
