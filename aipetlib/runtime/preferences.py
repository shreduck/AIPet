"""Live preference holders shared by the UI and background workers.

Each holder is a one-key dict mutated in place (holder["v"] = ...), so every module that imported it sees the
change. Import the holder, never its value, and never rebind it.
"""

DONE_TIMEOUT = {"v": 3}  # minutes until a finished session's pet is cleared (0 = never)
SCALE_UNIT = 2.0  # the absolute drawing scale that counts as 100% (it was the old 200%, the size people settled on)
SCALE = {"v": SCALE_UNIT}  # absolute drawing scale: 92x122 px per pet at 1.0; the slider shows SCALE / SCALE_UNIT
