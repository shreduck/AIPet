"""Where AIPet keeps its per-user files, and where its bundled resources are."""
import os
import sys

HOME_DIR = os.path.join(os.path.expanduser("~"), ".aipet")
# Bundled resources: the PyInstaller bundle when frozen, else the repository root (three levels above this file)
RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SPRITE_DIR = os.path.join(RESOURCE_DIR, "assets", "sprites")
