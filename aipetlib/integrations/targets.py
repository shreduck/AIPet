"""Hook target keys: "windows" / "mac" / "wsl:<distro>" for Claude Code, the same with a "codex:" prefix for Codex."""
import sys

IS_MAC = sys.platform == "darwin"
LOCAL = "mac" if IS_MAC else "windows"  # key of the machine this app runs on


def is_codex(key):
    return key.startswith("codex:")
