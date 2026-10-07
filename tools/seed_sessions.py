#!/usr/bin/env python3
"""Write a few fake sessions into ~/.aipet/sessions so the pet has something to draw without Claude Code
(used by the mac-selftest workflow; also handy for checking the look locally). Remove them with --clear."""
import json
import os
import sys
import time

SESSIONS = os.path.join(os.path.expanduser("~"), ".aipet", "sessions")
FAKE = [
    {"id": "selftest-working", "title": "working", "state": "working", "app": "", "env": "mac"},
    {"id": "selftest-input", "title": "needs input", "state": "needs_input", "app": "", "env": "mac",
     "message": "Claude needs your permission to use Bash",
     "request": {"tool": "Bash", "detail": "ls -la", "id": "selftest", "t": 0}},
    {"id": "selftest-cowork", "title": "Cowork", "state": "done", "app": "cowork", "env": "mac"},
    {"id": "selftest-codex", "title": "Codex", "state": "working", "agent": "codex", "env": "mac"},
]


def main():
    os.makedirs(SESSIONS, exist_ok=True)
    usage_dir = os.path.join(os.path.dirname(SESSIONS), "usage")
    os.makedirs(usage_dir, exist_ok=True)
    for f in FAKE:
        path = os.path.join(SESSIONS, f["id"] + ".json")
        if "--clear" in sys.argv:
            if os.path.exists(path):
                os.remove(path)
            usage_path = os.path.join(usage_dir, f["id"] + ".json")
            if os.path.exists(usage_path):
                os.remove(usage_path)
            continue
        now = time.time()
        rec = {"source": "claude-code", "distro": "", "ide": "", "topic": "", "hwnd": None, "host": "", "pid": None,
               "agents": {}, "main_stopped": False, "wait_agent": "", "request": {}, "cwd": os.getcwd(),
               "message": "", "updated": now, "changed": now, **f}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        with open(os.path.join(usage_dir, f["id"] + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"updated": now, "rows": [
                {"window": "5h", "percent": 73 if f.get("agent") == "codex" else 50, "resets_at": now + 18000},
                {"window": "7d", "percent": 81 if f.get("agent") == "codex" else 65, "resets_at": now + 604800}]}, fh)
    print(("cleared" if "--clear" in sys.argv else "seeded") + f" {len(FAKE)} sessions in {SESSIONS}")


if __name__ == "__main__":
    main()
