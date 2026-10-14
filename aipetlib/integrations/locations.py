"""Installer paths and source/frozen resource roots."""
import os
import sys

IS_MAC = sys.platform == "darwin"

HOME = os.path.expanduser("~")
PET_DIR = os.path.join(HOME, ".aipet")
INSTALL_DIR = os.path.join(PET_DIR, "bin")
WINDOWS_SETTINGS = os.path.join(HOME, ".claude", "settings.json")
BACKUP_ROOT = os.path.join(PET_DIR, "backups")


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(base, *parts)


PLUGIN_MARKET_DIR = os.path.join(PET_DIR, "plugin-marketplace")  # stable path for `claude plugin marketplace add`


def app_dir():
    """Folder holding AIPet.exe (or this script when running from source)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
