"""A Markdown guide to AIPet's settings files, written for an AI assistant.

AIPet menu > General > "Copy settings guide for AI" puts it on the clipboard, so a user can paste it into Claude, Codex or
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
    "context_banner": ("true / false (default true)", "Beside the pet: each AI's context in use and its window "
                       "(CL ctx 84k/~200k; ~ = window estimated from the model).", "live"),
    "context_windows": ("object (default {})", "Claude context window per model name part, e.g. {\"opus\": 1000000}. "
                        "Claude transcripts don't record the window, so it is estimated (200k, or 1M for [1m] models "
                        "and contexts past 200k). Codex reports its own.", "live"),
    "tooltip_text_scale": ("0.5 - 2.5 (default 1.0)", "Text size in session and usage tooltips, independent of pet size.", "live"),
    "sounds": ("true / false", "Play sounds (false = muted).", "live"),
    "sound_on_done": ("true / false", "Play the 'done' chime when a working session finishes.", "live"),
    "sound_style": ('"chimes" or "system"', "AIPet's own chimes, or the system's sounds (Windows / macOS).", "live"),
    "notifications": ("true / false", "System notifications when a session needs you, errors or finishes.", "live"),
    "addins": ('object: add-in ID -> true / false',
               'Enable built-in or user add-ins. All start off. User manifests live in ~/.aipet/addins/<id>/addin.json; '
               'use Settings > Add-ins > Reload add-ins after installing or editing one. Scripts receive raw hook '
               'events and run with your account permissions. Built-in hook-journal stores events locally; token-guard '
               'detects likely credentials and sends redacted warnings. The callable aipet_addin.AIPet API provides '
               'status, info, warning and error reports. Reports appear beside the pet and as agent-window mini nodes.', 'live'),
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
    "timed_approve_minutes": ("number 1 - 120 (default 10)",
                              "How long a timed auto approve lasts (Live Management / the prompt card). Starting one "
                              "always asks the user to confirm; don't start one yourself.", "live"),
    "settings_zoom": ("number 0.5 - 1.5", "Size of the AIPet menu window (1.0 = 100%).", "live"),
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


def build_guide(cfg, home_dir, version="", addins=None):
    sep = "\\" if os.name == "nt" else "/"
    config = home_dir + sep + "config.json"
    rows = "\n".join(f"| `{k}` | {_value(cfg, k)} | {allowed} | {what} | {'yes' if when == 'live' else 'after restart'} |"
                     for k, (allowed, what, when) in CONFIG_KEYS.items())
    os_name = "macOS" if IS_MAC else ("Windows" if os.name == "nt" else sys.platform)
    addin_guide = _addin_guide(addins)
    return f"""# AIPet configuration guide (for an AI assistant)

AIPet {version} is a desktop pet that shows the state of the user's Claude Code, Codex and Cowork sessions. You can change
its settings by editing files on the user's computer ({os_name}). Ask before changing anything the user didn't request.

## Where the files are

AIPet's default data folder is: `{home_dir}`
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
| `addins/<id>/addin.json` | Custom add-in manifest and scripts | Yes; reload add-ins afterwards |
| `addin-settings/<id>.json` | Saved add-in property values | Use Add-in settings or the SDK; directory changes can require file moves |
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

{addin_guide}

## auto-approve.json

The default file is in the data folder above. If `{home_dir}-approval-storage.json` exists beside that folder,
read its `path` first and edit the rules file at that location instead. The app's **Choose rules folder…**
sets this location; do not change it without the user's request.

Lets AIPet approve permission prompts automatically. Format:

```json
{{
  "targets": {{
    "windows": {{"enabled": true, "allow_all": false, "precedence": "blacklist", "whitelist": ["git (status|diff)"], "blacklist": ["[;&|`$<>]"]}}
  }}
}}
```

- Target keys: `windows` / `mac` (this computer), `wsl:<distro>`, `cowork`, and the same with a `codex:` prefix for
  Codex (`codex:windows`, `codex:wsl:<distro>`).
- `whitelist`: regexes that must match the WHOLE command, file path, URL or tool name (re.fullmatch). Also matched
  against the tool name alone and `Tool(command)`, e.g. `Bash\\(npm test\\)`.
- `blacklist`: regexes matched ANYWHERE. The default `[".*"]` matches everything.
- `precedence`: `"blacklist"` (default) or `"whitelist"`. When both lists match, blacklist precedence asks the user;
  whitelist precedence approves. Both the normal rules editor and timed mode dialog include this switch and full
  editable lists. Changes in the timed dialog save per-install lists only when a mode is chosen; Cancel discards them.
  The precedence control is a sliding switch (Space/Enter also toggle it). Both editors and other app dialogs are
  resizable; custom frameless dialogs use a themed bottom-right resize grip, with list areas expanding to fit.
  In timed pause/reject modes, blacklist precedence pauses/rejects overlapping matches; whitelist precedence approves
  them. Other nonblacklisted requests are approved for the timer duration. Lists/precedence apply even when normal
  approval is off or Allow all; the timed Approve all choice ignores both lists. AI questions always wait.
  The light-orange Pause on blacklist button dismisses the visible confirmation and permission card after valid
  input; blacklisted requests remain paused and can be reopened from the pet. Invalid regexes keep the editor open.
- `allow_all: true` approves every prompt of that target. Risky: only if the user explicitly wants it.
- Questions the AI asks the user are never auto-approved. Read fresh by the hooks on every prompt.
- `"timed": {{"<session id>": {{"until": <epoch seconds>, "title": "..."}}}}` holds running timed auto approves, written
  by the app after the user confirms. Leave it alone: never add or extend one on the user's behalf.

## Other things

- Hooks live in each tool's own settings (`~/.claude/settings.json`, Codex's `hooks.json`, also inside WSL). Don't
  edit AIPet's hook entries by hand; the app installs and updates them.
- Starting with the OS is a setting in the app's menu (Behavior > Start with Windows / Start at login), not a file.
- After editing, the user can check the result in AIPet's menus or its AIPet menu window, which show the current values.
"""


def _addin_guide(addins):
    current = json.dumps(addins or [], ensure_ascii=False, indent=2)
    return '''### Add-ins: configuration and expected interface

Enable IDs through `config.json`: `"addins": {"hook-journal": true}`. All start off.
In **Add-ins**, click **Settings** beside an add-in's enable/disable switch to expand its property inputs below
the row, including while disabled. Use **Save** to apply changes; collapse hides inputs without discarding drafts.
Hook journal's `directory` property moves both `events.jsonl` and `events.previous.jsonl` to the chosen absolute
directory, then continues writing there. Existing destination logs are never overwritten; a failed change keeps
the old settings. Use the editor for this move, rather than editing the saved settings JSON or moving live logs.
Token guard reports named rules, explanations, warning levels and fingerprints. Values and decoded contents are
masked by default. Full matching hook text (prompt/command/field) appears with sensitive spans emphasized inline,
each immediately followed by an eye control for explicit reveal/hide. Reports include conversation title, AI harness,
session ID, project/directory, hook event/time and available tool/agent context. A rotating amber/red beacon marks
severity. Raw values are isolated in structured local reports (temporary outbox plus bounded in-memory history),
never ordinary notification text. Hook text/spans remain complete; decoded/legacy previews are limited to 4 KiB
and marked if truncated. It does not block requests.
Additional rules recognize plain credential pairs, connection URLs, CLI password/authentication flags, session
cookies, additional provider token prefixes and random-looking secrets in sensitive variable contexts. Placeholder,
public-URL and unrelated cookie contexts are filtered. URL/Base64 decoding is bounded to three layers; high warnings
require decoded credential/token structures rather than arbitrary encoded text.
Standalone hexadecimal hash-like strings get a low warning (amber). Base64 that decodes to likely credentials,
credential JSON, known token formats, or hash-like tokens gets a high warning (red, SDK error severity).
Ordinary Base64 text and model token counters do not trigger alerts. Detection is heuristic, not proof of a leak.
Each Token guard warning opens its own timestamped, nonmodal report window centered on the pet's screen.
Later warnings never replace earlier reports; closing one acknowledges only that warning.
Other add-in messages remain available through their pet bubbles.
Alert windows default to approximately 60% of the destination screen's usable area (about 77.5% of width/height).
Their Markdown preview toggle formats headings, emphasis, lists, quotes, links and fenced code locally, retaining
inline masked values/eye controls. Toggling preview remasks revealed values. No HTML execution or remote content fetching.

For custom add-ins, create `addins/<id>/addin.json` and a script, reload, then enable. Built-in IDs are reserved.
Minimal manifest (add `settings` only when needed):

```json
{
  "api_version": 1,
  "name": "Example",
  "command": ["{python}", "{addin_dir}/main.py"],
  "events": ["*"],
  "timeout_seconds": 5,
  "settings": [
    {"key": "verbose", "label": "Verbose reports", "type": "boolean", "default": false}
  ]
}
```

`settings` is a list of up to 32 properties with unique lowercase keys (letters, numbers, underscores; start with
a letter). Each has `key`, optional `label`/`description`, `type`, and a matching `default`.
Types: `string`, `boolean`, `integer`, `number` (finite), `directory` (absolute path). Defaults are required.
Also `integer-slider` and `number-slider`: require finite `min < max`, with optional positive `step`
(default 1 for integer-slider, 0.1 for number-slider). Integer sliders require integer bounds, step and values.
Defaults and saved values must be in bounds; step controls UI increments, not the allowed SDK values.
Example: {"key": "opacity", "type": "number-slider", "min": 0, "max": 1, "step": 0.05, "default": 0.8}.
Directory properties on custom add-ins are values only; their scripts implement any file migration.
Commands are argument arrays, never shell strings. `{addin_dir}` expands to the script folder; `{python}` works in
source runs; packaged builds need an explicit installed interpreter. Timeout is 0.1–30 seconds.

Each event starts a fresh process. Read one UTF-8 JSON object from stdin with `event` and `context`:

```python
import json, sys
message = json.loads(sys.stdin.buffer.read().decode('utf-8'))
event, context = message['event'], message['context']
sys.path.insert(0, context['sdk_dir'])
from aipet_addin import AIPet
pet = AIPet(context)
properties = pet.get_settings()  # list of descriptors, each with current `value`
values = {p['key']: p['value'] for p in properties}
if values.get('verbose'):
    pet.info('Event received', event['event'])
# pet.set_settings({'verbose': False})  # partial update of this add-in's declared settings
```

`event` contains api_version, id, timestamp, agent, event, session_id, data (raw hook payload).
`context` contains addin_id, data_dir, sdk_dir, outbox, run_id, agent, session_id, settings (values),
and settings_properties (descriptors). Persist script state in data_dir.
`get_settings()` returns the snapshot captured at delivery start. `set_settings(values)` returns whether the
request was queued, not whether it was saved; AIPet validates and atomically saves it, and later deliveries receive
saved values. Unknown keys and invalid types are rejected with an error interjection. The snapshot does not mutate.
Host API: `AddinManager.get_settings(id)` returns properties plus values; `set_settings(id, partial_values)` validates
and saves on a background thread. UI callers use `request_set_settings(id, values, callback)`; callback(error) runs
on the dispatch thread, with None on success, so marshal UI updates back to Tk.
SDK also offers `status('working'|'stopped'|'needs_attention', detail)`, `info`, `warning`, and `error` (title, content,
optional key= for deduplication). Scripts are observers: return values never approve, deny, or modify requests.
`pet.get_pet_position()` returns a delivery-time snapshot of x, y, width, height, screen_id and screen;
`pet.get_pet_screen()` returns id, bounds, work_area and primary. Bounds use [left, top, right, bottom] in desktop
coordinates (negative coordinates are valid). Windows screen IDs are OS device names; fallback IDs are based on
screen bounds and may change with monitor layout. Both methods return None before the host publishes a position.
The host publishes the session's pet position (or the overlay fallback) on the UI thread, so workers never call Tk.
Use `pet.warning('Title', 'Details', window='pet')` or `pet.error(..., window='pet')` to request a themed window
centered on the current pet screen. `window='main'` centers on the main screen. Omit window for bubble-only delivery.
Window requests are asynchronous, open a separate timestamped window per message, and are acknowledged individually when closed. Missing pet
geometry falls back to the main screen. Position getters are snapshots; placement uses current host geometry.
Delivery is bounded and best effort; errors/timeouts appear in error.log and add-in messages. Enabled scripts run
with the user's permissions. Discovery and editing settings do not execute disabled scripts.

#### Add-in UI and AIPet theming

User scripts must use the SDK for messages and settings; they run in worker processes and must not call Tk or
create their own unthemed warning windows. AIPet's host UI renders settings inline below each activator row and
messages in a nonmodal, scrollable AIPet window. Keep credential values, decoded secrets and payload excerpts out
of ordinary titles, content and logs; use constant labels and fingerprints there. For explicit reveal controls,
pass a structured `report=` to `warning` or `error`: rule (stable ID), name, warning_level (low/standard/high),
explanation, fingerprint, value, optional decoded, and optional context. Value/decoded fields are limited to 4096
UTF-8 bytes each; reports render with values masked and individual eye toggles. Never interpolate them into plain text.
For surrounding hook text, add hook_text (complete UTF-8 string up to 256 KiB), hook_field (field path), and
hook_spans (sorted nonoverlapping [start, end] character ranges covering every detected secret in that text).
The host displays normal surrounding text and masks/highlights those spans with adjacent inline eye controls.
Structured messages carrying hook_text allow up to 1 MB of transport; ordinary reports retain the 16 KiB limit.
For formatted report sections, add `markdown_template` (up to 8192 UTF-8 bytes) to the structured report.
Use Markdown headings, lists and emphasis with placeholders such as `{{rule}}`, `{{explanation}}`,
`{{timestamp}}`, `{{context.conversation_title}}`, `{{hook_text}}` and `{{decoded}}`.
The host escapes metadata, expands source placeholders with their masked spans and opens these reports with
Markdown preview enabled. Templates are expanded once, without code execution. Keep secrets out of the template
itself. `aipet_addin.format_markdown_report(report, include_source=False)` returns `(content, spans)` for a censored
Markdown notification body; pass its content alongside `report=report`. Token guard uses this interface for its
conversation, finding and hook-text sections. The shared AIPet window heading remains host-owned.
Host `context['session']` supplies conversation_title, harness, project, cwd and environment when available;
retain triggering hook/event/session metadata in report context for diagnosis. Use warning for low/ordinary attention and
error for high attention; host colours are amber and red respectively.
For host-side UI extensions, use `aipetlib.ui.theme.UI_FONT` (Consolas on Windows), `theme.T` palette roles,
`theme.button_style(...)`, and `theme.themed_color(...)` for readable status accents in both light and dark modes.
Call `theme.theme_widget(panel)` for inline controls or `theme.theme_window(win)` for a Toplevel (including titlebar
styling and live theme changes). Recolour custom text tags/canvas accents on `<<AIPetThemeChanged>>`; do all Tk
work on the UI thread. Use AIPet geometry helpers to fit windows to the current monitor and keep close/Escape
actions available. Avoid native message boxes and hardcoded light backgrounds for add-in alerts.
Center windows on the main monitor by default; explicit `window='pet'` requests center on the pet's monitor,
within its usable work area (excluding the Windows taskbar). Scripts request placement through the SDK.
Use `aipetlib.ui.toggle_switch.ToggleSwitch` for host-side sliding switches. Keep application windows resizable;
frameless dialogs can use `aipetlib.ui.window_resize.corner_grip(win)`. Configure pack/grid expansion so text/list
areas grow with their windows rather than changing only the outer frame.
Independent dialogs must not use application-wide Tk grabs. Themed confirmations leave other AIPet windows
interactive; track pending actions and their callbacks locally rather than blocking unrelated alerts or editors.
Open interactive timed-approval confirmations through `Answers.start_timed(..., on_result=callback)`;
the card, agent window and menu use this callback path rather than entering a nested window-wait loop.

Current installed add-ins, enablement, property definitions and values:

```json
''' + current + '\n```'
