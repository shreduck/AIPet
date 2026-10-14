"""Housekeeping: files AIPet makes that nothing will ever need again (swept a minute after start, then hourly).

LEFTOVERS: name pattern (in a folder of the AIPet folder) -> how old (seconds since last written) before it is deleted.
"""
import json
import os
import time

from . import permissions
from .runtime import paths

LEFTOVERS = (
    ("sessions", ".tmp", 3600),    # a hook's half-written session file (the hook was killed mid-write)
    ("sessions", ".lock", 3600),   # a hook's lock on a session file (held for milliseconds; a crashed hook's stays)
    ("answers", ".waiting", 3600),  # "a hook waits here": touched every second while it waits, so 1 h = long gone
    ("answers", ".json", 3600),    # an answer no hook picked up (each hook deletes any older one when it starts)
    ("answers", ".tmp", 3600),
    ("", ".tmp", 3600),            # half-written config / auto-approve / update files
    ("", "codex-raw.log", 7 * 86400),   # Codex test log (full payloads): switched off in releases
    ("", "events.log", 7 * 86400),      # debug event log: appended to while "Log hook events" is on
    ("", "perf.log", 7 * 86400),        # UI timings, also while "Log hook events" is on
    ("", "perf.log.old", 7 * 86400),
    ("", "diagnostics.txt", 30 * 86400),  # saved by Save diagnostics..., for a bug report
    ("", "workbench-probe.txt", 30 * 86400),
)


def sweep_leftovers(home=None, now=None):
    """Delete leftovers (see LEFTOVERS) and expired timed auto approves. Returns the paths removed."""
    home = home or paths.HOME_DIR
    now = time.time() if now is None else now
    removed = []
    for folder, suffix, max_age in LEFTOVERS:
        base = os.path.join(home, folder) if folder else home
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            path = os.path.join(base, name)
            if not name.endswith(suffix):
                continue
            try:
                if not os.path.isfile(path) or now - os.path.getmtime(path) < max_age:
                    continue
                os.remove(path)
                removed.append(path)
            except OSError:
                pass
    try:  # timed auto approves that ran out stay in auto-approve.json until something rewrites it
        with open(os.path.join(home, "auto-approve.json"), encoding="utf-8") as f:
            timed = (json.load(f) or {}).get("timed") or {}
        live = {sid: g for sid, g in timed.items()
                if isinstance(g, dict) and isinstance(g.get("until"), (int, float)) and g["until"] > now}
        if len(live) != len(timed) and home == paths.HOME_DIR:
            permissions._write_auto_approve(timed=live)
            removed.append("auto-approve.json: expired timed auto approves")
    except (OSError, ValueError, AttributeError):
        pass
    return removed
