"""Hook runtime: diagnostics."""
import json
import os
import sys
import time
from . import config as hook_config
from . import hosts as hook_hosts
from . import waiting as hook_waiting

def log_wait_end(base_dir, record, req, decision):
    """With diagnostics on (<pet dir>/debug-events), one events.log line per finished wait: how it ended and after how
    long. Names and numbers only, like debug_log."""
    try:
        if not os.path.exists(os.path.join(base_dir, "debug-events")):
            return
        row = {"t": time.strftime("%H:%M:%S"), "ev": "WaitEnded", "sid": str(record.get("id", ""))[:8],
               "tool": req.get("tool", ""), "reason": hook_waiting.WAIT_END.get("reason", ""), "decision": decision or "",
               "after": hook_waiting.WAIT_END.get("after"), "signal": hook_waiting.WAIT_END.get("signal", ""), "agent": hook_config.AGENT}
        with open(os.path.join(base_dir, "events.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def debug_log(base_dir, event, data, prev_state, new_state):
    """Opt-in diagnostics: if <pet dir>/debug-events exists, append one metadata line per hook event to
    events.log. Field NAMES only - never prompts, tool inputs or other values - so it is safe to share."""
    try:
        if not os.path.exists(os.path.join(base_dir, "debug-events")):
            return
        e = os.environ
        row = {
            "t": time.strftime("%H:%M:%S"), "ev": event, "sid": str(data.get("session_id", ""))[:8],
            "tool": data.get("tool_name", ""), "ntype": data.get("notification_type", ""),
            "msg": str(data.get("message", ""))[:80], "keys": sorted(data.keys()),
            "agent": {k: str(data[k])[:12] for k in ("agent_id", "agent_type", "parent_session_id") if k in data},
            "prev": prev_state, "new": new_state,
            # shapes only (type, length, key names): never the values
            "shape": {k: [type(data[k]).__name__, len(data[k]) if hasattr(data[k], "__len__") else data[k],
                          sorted(data[k][0].keys()) if isinstance(data[k], list) and data[k] and isinstance(data[k][0], dict) else None]
                      for k in ("background_tasks", "session_crons", "agents", "subagents") if k in data},
            "env": {"entry": e.get("CLAUDE_CODE_ENTRYPOINT", ""), "term": e.get("TERM_PROGRAM", ""),
                    "cowork": hook_hosts.is_cowork(), "cowork_folders": len(hook_hosts.cowork_folders()),  # a count, not the paths
                    "vscode_vars": [k for k in ("VSCODE_PID", "VSCODE_IPC_HOOK_CLI", "VSCODE_GIT_IPC_HANDLE") if e.get(k)]},
        }
        path = os.path.join(base_dir, "events.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if os.path.getsize(path) > 400_000:  # keep the newest half
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[len(lines) // 2:])
    except Exception:
        pass


def codex_raw_log(raw, base):
    if not (hook_config.CODEX_RAW_LOG and hook_config.AGENT == "codex"):
        return
    try:
        import platform
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "codex-raw.log")
        if os.path.exists(path) and os.path.getsize(path) > 5_000_000:  # keep the newest half
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            with open(path, "w", encoding="utf-8") as f:
                f.write(text[len(text) // 2:])
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "argv": sys.argv, "os": platform.platform(),
               "wsl": hook_hosts.is_wsl(), "cwd": os.getcwd(), "pid": os.getpid(), "ppid": os.getppid(),
               "env_names": sorted(k for k in os.environ if k.upper().startswith(("CODEX", "OPENAI"))),
               "ancestors": hook_hosts._ancestor_names(),
               "codex_procs": hook_hosts._codex_procs(),
               "payload_raw": raw}
        try:
            row["payload"] = json.loads(raw) if raw else None
            del row["payload_raw"]
        except ValueError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
