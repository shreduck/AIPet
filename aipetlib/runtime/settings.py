"""Reading config.json and saving one setting at a time, at the paths in aipetlib.runtime.paths."""
import json
import os

from ..config import DEFAULT_CONFIG, ConfigStore
from . import paths

SAVE_PAUSED = {"v": False}  # while True, setters apply a setting without writing it (the app applying an edited file)


def load_config():
    return ConfigStore(paths.CONFIG_PATH, paths.HOME_DIR, DEFAULT_CONFIG).load()


def save_setting(key, value):
    return ConfigStore(paths.CONFIG_PATH, paths.HOME_DIR, DEFAULT_CONFIG).save_setting(key, value,
                                                                                      paused=SAVE_PAUSED["v"])


def ensure_home():
    """The AIPet folder and its sessions folder exist, and a default config.json (first run)."""
    os.makedirs(paths.SESSIONS_DIR, exist_ok=True)
    if not os.path.exists(paths.CONFIG_PATH):
        with open(paths.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
