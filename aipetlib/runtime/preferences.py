"""Live preference holders shared by the UI and background workers.

Each holder is a one-key dict mutated in place (holder["v"] = ...), so every module that imported it sees the
change. Import the holder, never its value, and never rebind it.
"""

DONE_TIMEOUT = {"v": 3}  # minutes until a finished session's pet is cleared (0 = never)
