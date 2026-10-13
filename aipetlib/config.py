"""Configuration defaults and persistence, independent of the desktop toolkit."""
import copy
import json
import os
import sys

DEFAULT_CONFIG = {
    "poll_ms": 700,
    "max_pets": 10,
    "sounds": True,
    "sound_on_done": True,
    "sound_style": "chimes",  # AIPet's own sounds by default; "system" uses the host OS sounds
    "notifications": False,  # Windows toast notifications; off by default (toggle in the tray menu)
    "theme": "light",  # "light" (default) or "dark"; toggle in the tray / right-click menu
    "answer_wait_seconds": 180,
    "timed_approve_minutes": 10,  # length of "Auto approve for N min" (Live Management / prompt card): 1 - 120  # how long a permission prompt can be answered from the pet: 0 (no limit) - 1800
    "pet_style": "robot",  # "robot" (default), "mole" or "cat"
    # "size": 1.0 is written when you use the Size slider (1.0 = 100%, range 0.3 - 3.0); deliberately not a default
    # here, so an old absolute "scale" value in config.json can still be migrated once.
    "remind_seconds": 90,
    "compact": False,
    "update_check": True,  # look for a new AIPet release on GitHub once a day (tells you; never installs anything)
    "click_to_focus": True,  # clicking a pet (or a badge) brings its session's window to the front
    "session_tooltips": True,
    "usage_tooltips": True,
    "context_banner": True,  # beside the pet: each AI's context in use (CL ctx 84k/~200k), before its usage limits
    "context_windows": {},  # Claude context window per model name part, e.g. {"opus": 1000000}; else estimated
    "tooltip_text_scale": 1.0,
    "claude_oauth_usage": False,  # experimental credential-based quota requests require explicit consent
    "all_spaces": True,  # show the pet on every virtual desktop / macOS Space
    "session_titles": "name",  # extra title for same-folder sessions: "name" (the harness's name) or "prompt" (latest prompt)
    "codex_answers": True,  # answer Codex permission prompts in the pet's prompt window
    "claude_answers": True,  # preserve existing Claude Code answering behavior
    "click_through": True,  # only the robot and its "needs you" bubble take clicks; the rest of the window lets them through
    # clicking a session of the VS Code extension also opens its conversation tab (vscode://anthropic.claude-code/open)
    "vscode_open_conversation": True,
    "done_timeout_minutes": 3,
    "health_check_seconds": 15,  # how often a working session's process is checked (0 = off); "Health check every..."  # finished sessions disappear after this: 0 (never) - 30; "Clear finished after..." slider
    "stale_hours": 12,
    "claude_code": {
        "enabled": True,
        # Extra folders to watch, e.g. a WSL distro if the hook can't reach Windows:
        # (in config.json: "\\\\wsl.localhost\\Ubuntu\\home\\<you>\\.aipet\\sessions")
        "extra_session_dirs": [],
    },
    "workbench": {
        "enabled": False,
        "mcp_url": "http://localhost:8080/mcp",
        "tool_name": "listAgentChats",
        "tool_arguments": {"limit": 50},
        "api_key_env": "WORKBENCH_MCP_KEY",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
        "poll_seconds": 10,
        "timeout_seconds": 20,
        "max_chats": 8,
        "state_fields": ["state", "status", "runState", "runStatus", "agentState", "lastRunState", "phase"],
        "state_map": {
            "needs_input": ["waiting", "awaiting", "awaiting_input", "needs_input", "input_required",
                            "pending_approval", "approval", "paused", "blocked", "waiting_for_user"],
            "error": ["error", "failed", "failure", "cancelled", "canceled", "timeout", "timed_out"],
            "done": ["completed", "complete", "done", "finished", "succeeded", "success", "idle",
                     "stopped", "ended", "inactive", "closed"],
            "working": ["running", "processing", "in_progress", "streaming", "active", "busy",
                        "executing", "thinking", "started", "queued"],
        },
    },
}

def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigStore:
    """Read merged defaults and atomically update individual user settings."""
    def __init__(self, path, home, defaults=DEFAULT_CONFIG):
        self.path, self.home, self.defaults = path, home, defaults

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return deep_merge(self.defaults, json.load(f))
        except FileNotFoundError:
            return copy.deepcopy(self.defaults)
        except Exception as e:
            print(f"[aipet] config.json unreadable, using defaults: {e}", file=sys.stderr)
            return copy.deepcopy(self.defaults)

    def save_setting(self, key, value, *, paused=False):
        if paused:
            return True
        try:
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
            except FileNotFoundError:
                data = {}
            data[key] = value
            os.makedirs(self.home, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
            return True
        except Exception as e:
            print(f"[aipet] couldn't save {key}: {e}", file=sys.stderr)
            return False
