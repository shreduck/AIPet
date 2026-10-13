"""Where AIPet keeps its per-user files, and where its bundled resources are.

Code reads these as module attributes at call time (paths.CONFIG_PATH), so a test or a relocated profile can point
them elsewhere in one place.
"""
import os
import sys

HOME_DIR = os.path.join(os.path.expanduser("~"), ".aipet")
SESSIONS_DIR = os.path.join(HOME_DIR, "sessions")
# Claude Pet (the old name) kept its files in ~/.claude-pet. Hooks not yet updated to AIPet still write their sessions
# there, so the pet keeps reading that folder while it exists.
LEGACY_SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".claude-pet", "sessions")  # LEGACY
CONFIG_PATH = os.path.join(HOME_DIR, "config.json")
AUTO_APPROVE_PATH = os.path.join(HOME_DIR, "auto-approve.json")  # read by every hook (WSL ones through /mnt/c)
DEBUG_FLAG = os.path.join(HOME_DIR, "debug-events")  # while it exists, hooks append to events.log
# Bundled resources: the PyInstaller bundle when frozen, else the repository root (three levels above this file)
RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SPRITE_DIR = os.path.join(RESOURCE_DIR, "assets", "sprites")
