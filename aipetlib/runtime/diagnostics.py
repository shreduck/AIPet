"""Error reporting for a windowed app that has no console: ~/.aipet/error.log."""
import os
import sys
import time

from . import paths

_LOGGED = set()


def log_error(msg, home=None):
    """Append a message (once per run) to <home>/error.log; home defaults to ~/.aipet."""
    if msg in _LOGGED:
        return
    _LOGGED.add(msg)
    print(f"[aipet] {msg}", file=sys.stderr)
    home = home or paths.HOME_DIR
    try:
        os.makedirs(home, exist_ok=True)
        path = os.path.join(home, "error.log")
        if os.path.exists(path) and os.path.getsize(path) > 200_000:
            os.remove(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass
