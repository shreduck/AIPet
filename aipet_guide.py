"""A Markdown guide to AIPet's settings files, written for an AI assistant.

Settings > General > "Copy settings guide for AI" puts it on the clipboard, so a user can paste it into Claude, Codex or
any other assistant and then ask it to change AIPet's configuration for them. Built from the running app, so the folder
path and the current values are the user's own.
"""
import json
import os
import sys

IS_MAC = sys.platform == "darwin"

# key -> (allowed values, what it does, "live" | "restart")
CONFIG_KEYS = {
    "theme": ('"light" or "dark"', "Colours of name tags, bubbles, cards and windows.", "live"),
    "pet_style": ('"robot" (default), "mole", "cat"', "Which creature is drawn. mole and cat are old, unmaintained styles.",
                  "live"),
    "size": ("number 0.3 - 3.0 (1.0 = 100%)", "Size of the pets on screen.", "live"),
    "compact": ("true / false", "One pet stands in for every session (the one that needs you most).", "live"),
    "session_titles": ('"name" or "prompt"', "Extra title under a pet: the session's name or the latest prompt.", "live"),
    "session_tooltips": ("true / false", "Tooltip with title, state and last message when hovering a pet.", "live"),
    "usage_tooltips": ("true / false", "Tooltip with usage limits when hovering a usage badge.", "live"),
    "sounds": ("true / false", "Play sounds (false = muted).", "live"),
    "sound_on_done": ("true / false", "Play the 'done' chime when a working session finishes.", "live"),
    "sound_style": ('"chimes" or "system"', "AIPet's own chimes, or the system's sounds (Windows / macOS).", "live"),
    "notifications": ("true / false", "System notifications when a session needs you, errors or finishes.", "live"),
    "click_to_focus": ("true / false", "Clicking a pet brings its session's window to the front.", "live"),
    "all_spaces": ("true / false", "Show the pet on every virtual desktop (macOS: every Space).", "live"),
    "answer_wait_seconds": ("number 0 - 1800 (0 = no limit)",
                            "How long a permission prompt / question can be answered from the pet.", "live"),
    "done_timeout_minutes": ("number 0 - 30 (0 = never)", "How long a finished session's pet stays.", "live"),
    "health_check_seconds": ("number 0 - 300 (0 = off)",
                             "How often a working session's process is checked to still be running.", "live"),
    "remind_seconds": ("number of seconds (0 = off)", "Repeat the 'needs you' sound while a session keeps waiting.",
                       "live"),
    "claude_answers": ("true / false", "Answer Claude Code permission prompts and questions from the pet.", "live"),
    "codex_answers": ("true / false", "Answer Codex permission prompts from the pet.", "live"),
    "update_check": ("true / false", "Look for a new AIPet release once a day (never installs by itself).", "live"),
    "claude_oauth_usage": ("true / false",
                           "EXPERIMENTAL: reads Claude Code's sign-in token to fetch account usage from an unofficial "
                           "endpoint. Only turn on if the user explicitly asks; the app asks for consent itself.",
                           "live"),
    "settings_zoom": ("number 0.5 - 1.5", "Size of the settings window (1.0 = 100%).", "live"),
    "card_scale": ("number 0.5 - 1.5", "Size of the permission / question card.", "live"),
    "vscode_open_conversation": ("true / false",
                                 "Clicking a VS Code session also opens its conversation tab.", "live"),
    "stale_hours": ("number of hours", "Sessions not updated for this long are dropped.", "live"),
    "poll_ms": ("milliseconds (default 700)", "How often session files are read. Lower = snappier, more CPU.", "live"),
    "max_pets": ("number (default 10)", "Most pets shown at once.", "restart"),
    "click_through": ("true / false", "Clicks on empty parts of the pet window pass through to what's behind.",
                      "restart"),
    "claude_code": ("object", '{"enabled": true, "extra_session_dirs": []} - extra folders with session files.',
                    "restart"),
    "workbench": ("object", "Optional MCP Workbench source (mcp_url, tool_name, ...). Leave alone unless asked.",
                  "restart"),
}


def _value(cfg, key):
    if key not in cfg:
        return "(not set: default)"
    v = cfg[key]
    if isinstance(v, (dict, list)):
        return "(object)"
    return "`" + json.dumps(v) + "`"


def build_guide(cfg, home_dir, version=""):
    sep = "\\" if os.name == "nt" else "/"
    config = home_dir + sep + "config.json"
    rows = "\n".join(f"| `{k}` | {_value(cfg, k)} | {allowed} | {what} | {'yes' if when == 'live' else 'after restart'} |"
                     for k, (allowed, what, when) in CONFIG_KEYS.items())
    os_name = "macOS" if IS_MAC else ("Windows" if os.name == "nt" else sys.platform)
    return f"""# AIPet configuration guide (for an AI assistant)

AIPet {version} is a desktop pet that shows the state of the user's Claude Code, Codex and Cowork sessions. You can change
its settings by editing files on the user's computer ({os_name}). Ask before changing anything the user didn't request.

## Where the files are

All of AIPet's data lives in one folder: `{home_dir}`
(`~/.aipet` on macOS and Linux, `%USERPROFILE%\\.aipet` on Windows; WSL hooks reach the Windows one through `/mnt/c/...`).

| Path | What it is | Edit it? |
| :- | :- | :- |
| `config.json` | All app settings (table below) | **Yes** |
| `auto-approve.json` | Auto-approve rules for permission prompts, per install | **Yes, carefully** |
| `sessions/` | One JSON file per running session, written by the hooks | No (live data) |
| `answers/` | Answers handed from the pet to waiting hooks | No |
| `usage/` | Usage-limit readings | No |
| `bin/`, `plugin-marketplace/` | The installed hook program and Cowork plugin | No (use the app's Install / update hooks) |
| `backups/` | Copies of Claude Code / Codex settings from before AIPet changed them | No (restore from the app) |
| `error.log`, `events.log` | Logs; `events.log` exists while debug logging is on | Read only |
| `debug-events` | Empty file: present = log hook events to `events.log` | Create / delete |
| `no-claude-answers`, `codex-answers`, `answer-wait` | Switch files the hooks read; the app writes them from config.json | No (change config.json) |

## config.json

`{config}` is a JSON object. Change only the keys you need and keep the rest exactly as it is; it must stay valid JSON
(no comments, no trailing commas). The app watches the file and applies most changes within about two seconds; the
last column says which ones need AIPet to be restarted. A file that isn't valid JSON is ignored until it is fixed.

| Key | Current | Allowed | What it does | Applies live |
| :- | :- | :- | :- | :- |
{rows}

Example - dark theme, bigger pets, quieter:

```json
{{
  "theme": "dark",
  "size": 1.5,
  "sound_on_done": false
}}
```
(Merge these keys into the existing file; don't replace the whole file.)

## auto-approve.json

Lets AIPet approve permission prompts automatically. Format:

```json
{{
  "targets": {{
    "windows": {{"enabled": true, "allow_all": false, "whitelist": ["git (status|diff)"], "blacklist": ["[;&|`$<>]"]}}
  }}
}}
```

- Target keys: `windows` / `mac` (this computer), `wsl:<distro>`, `cowork`, and the same with a `codex:` prefix for
  Codex (`codex:windows`, `codex:wsl:<distro>`).
- `whitelist`: regexes that must match the WHOLE command, file path, URL or tool name (re.fullmatch). Also matched
  against the tool name alone and `Tool(command)`, e.g. `Bash\\(npm test\\)`.
- `blacklist`: regexes matched ANYWHERE; a match always asks the user and wins over the whitelist. The default `[".*"]`
  asks for everything (the whitelist then never applies).
- `allow_all: true` approves every prompt of that target. Risky: only if the user explicitly wants it.
- Questions the AI asks the user are never auto-approved. Read fresh by the hooks on every prompt.

## Other things

- Hooks live in each tool's own settings (`~/.claude/settings.json`, Codex's `hooks.json`, also inside WSL). Don't
  edit AIPet's hook entries by hand; the app installs and updates them.
- Starting with the OS is a setting in the app's menu (Behavior > Start with Windows / Start at login), not a file.
- After editing, the user can check the result in AIPet's menu or Settings window, which show the current values.
"""
