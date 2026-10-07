# AIPet

A floating companion with one creature per Claude Code session. It lives in the system tray and installs its own hooks.

> **Renamed from Claude Pet.** On its first start AIPet explains the changes and moves you over: it closes the old
> app (or asks you to quit it), copies settings, backups and sessions from `~/.claude-pet` to `~/.aipet` (the old
> folder is kept and still read until every hook is updated), moves *Start with Windows / at login*, and then offers
> to update the hooks in `settings.json` (old `claude-pet-hook` entries are recognised and replaced, with backups).
> Re-upload the Cowork plugin (`aipet-cowork-plugin.zip`) after uninstalling the old one.
>
> Hooks and plugins still on the old name keep working, but once a day AIPet lists what is left and how to update it
> (`settings.json` hooks, the old Cowork / CLI plugin, sessions still arriving through `~/.claude-pet`); *Claude Code
> hooks > Check for old Claude Pet hooks...* runs the check on demand. All of this lives in `legacy.py` plus lines
> marked `LEGACY`, so it can be removed in a later version.

| Pet | Meaning |
|---|---|
| Blue, bobbing | working |
| Orange, jumping, "!" + beep + Windows notification | needs your input (re-reminds every 90 s until you click it) |
| Green, sleeping | done |
| Red, shaking | error (Workbench) |

Pets keep their position: a session stays where it first appeared (new ones join on the left) even when its state changes; only if there are more than `max_pets` are the least urgent dropped.

Each pet shows small badges for where its session runs: **Claude**, **Cowork** (Claude app), **Codex**, **WSL**, **VS Code** or **Workbench** (shortened to `CC`, `CW`, `CX`, `VS`, `WB` when several don't fit). Only the robot and its "needs you" bubble catch clicks; the rest of the pet window lets clicks through to what is behind it (turn off with `"click_through": false` in `config.json`). The tray icon takes the colour of the most urgent session, and hovering it shows a summary.

## Build the exe
On Windows with Python 3.10+, double-click **`build.bat`**. It produces `dist\AIPet.exe` (single file).
**Prebuilt:** the **Build AIPet** GitHub Actions workflow runs on every push to `master` (or by hand) and publishes `AIPet.exe` and an Apple Silicon `AIPet-mac-arm64.zip` to the rolling **latest** release on the repo's [Releases](../../releases) page; pushing a `v*` tag makes a versioned release. No binaries are committed. The Mac app is unsigned: unzip it, then right-click it and choose *Open* the first time.

The exe is unsigned, so the first time you run it SmartScreen may show "Windows protected your PC". Click *More info → Run anyway*.

## Use it
Run `AIPet.exe`. The pet appears bottom-right and a tray icon appears near the clock.

**Tray menu**
- **Show / Hide pet**. Left-clicking the tray icon does the same. The pet's right-click menu also has *Hide to tray*.
- **Claude Code hooks**
  - **This PC (Windows)**: installs into `%USERPROFILE%\.claude\settings.json`. Covers terminal and VS Code sessions on Windows.
  - **WSL: \<distro\>**: one entry per auto-detected distro. Installs into that distro's `~/.claude/settings.json` and covers terminal and VS Code Remote-WSL sessions there. Stopped distros aren't booted just to show their status. They only start when you install or remove hooks.
  - Each target shows its status (✓ installed, not installed, needs python3, …) and offers *Install / update* and *Remove*.
  - **Cowork (Claude desktop app)...**: builds the Cowork plugin and shows how to install it (see below).
  - The pet's right-click menu has the same *Claude Code hooks* submenu.
- **Mute sounds**, **Start with Windows**, **Workbench status**, **Open config folder**, **Quit**.

What hook install does:
1. Copies the hook to `~\.aipet\bin` (deliberately outside AppData: apps started from a packaged app such as Claude Desktop get AppData writes redirected to a private copy that the CLI and WSL cannot see). Nothing is copied until you choose Install; an existing install is refreshed when the app starts, so updates reach every target.
2. Asks for confirmation, then backs up the current `settings.json` (see below).
3. Adds the hook entries and keeps every other hook and setting. It never overwrites a `settings.json` that isn't valid JSON. Running install again changes nothing if the hooks are already up to date.

Only Claude Code sessions started *after* installing will appear.

### Backup & restore
Each target's submenu also has **Restore backup**, **Back up now** and **Open backups folder**.

- **Automatic backups:** taken before every install, remove or restore, and only when the file actually changes.
- **Storage:** backups are byte-exact copies kept in `~\.aipet\backups\<target>\`. WSL backups are stored there too, so browsing them never starts a distro.
- **"No file" state:** if `settings.json` didn't exist yet, that is recorded as well. Restoring it deletes the file again.
- **Retention:** the **oldest backup is always kept** (labelled *[original]*, your settings from before AIPet first touched them), plus the 20 most recent.
- **What the menu shows:** each backup's date, reason, and whether it contains AIPet hooks. It also warns if that version isn't valid JSON.
- **Undo:** a restore first saves the current file as a *before restore* backup, so it can be undone from the same menu.
- After restoring, restart running Claude Code sessions to pick up the change.

Backups are plain copies of your `settings.json`. If you keep secrets in it (e.g. under `env`), the copies contain them too. They stay in your own user profile.

**Pet:** hover for details, click to acknowledge a pet that needs input (or dismiss a done one), drag to move. Right-click opens the menu, including *Open in VS Code*.

## Taskbar / Dock / menu bar icon
The tray icon (Windows) and the Dock icon (macOS) show the robot: white normally, **red** with a "?" face while a session
needs you (worried face on an error). On macOS the Dock icon also bounces once when that starts.

On macOS AIPet also has a **menu-bar icon** (top right): a line-art robot in the menu bar's own colour, red while a
session needs you. Its menu is the same as the Windows tray menu (hooks for Claude Code and Codex, compact mode, sounds,
theme, sizes, timeouts, start at login, quit...). It is built with the Objective-C runtime directly (`mac_statusbar.py`),
so no extra packages are needed.

## Codex
AIPet also shows **Codex** (OpenAI's coding agent) sessions, with a green `CX` badge. Codex has the same kind of hooks as
Claude Code, kept in `~/.codex/hooks.json` (or `$CODEX_HOME/hooks.json`):

- The setup window and *Claude Code hooks > Codex* list every place Codex is found: this PC and each running WSL distro
  with `~/.codex` or `codex` on the PATH. Install / update / remove / backups work like the Claude Code targets.
- **Trust step:** Codex only runs hooks you have trusted. After installing, start Codex, type `/hooks` and trust the AIPet
  hooks (Codex asks again only if they change).
- Codex permission prompts can be answered from the pet's prompt window (Allow once / Deny), like Claude's. One
  difference: Codex asks its permission hook *before* showing its own prompt, so Codex's prompt only appears if you
  don't answer in the pet's window within the answer timeout. To have Codex show its prompt straight away instead,
  switch off **Answer Codex prompts from the pet** (menu or tray). Updating from an older AIPet needs the Codex hooks
  updated once (they get a longer permission timeout) and trusted again in `/hooks`.
- Events: start, prompt, tool use, permission, stop, **Interrupt** (shows as done), session end and subagents. Codex has no
  `Notification` event.

## Cowork (Claude desktop app)
Cowork runs its own Claude Code with a private config folder, so it never reads `settings.json` and the hooks above don't reach it. It does run plugin hooks, on Windows itself (not in its Linux sandbox), so AIPet ships its hooks as a plugin. Only the Claude app can install plugins, so this step is manual:

1. The setup window and *Claude Code hooks > Cowork (Claude desktop app)...* write `aipet-cowork-plugin.zip` next to `AIPet.exe` (or to `~\.aipet` if that folder is read-only). The zip is refreshed at every start once hooks are installed.
2. In the Claude app: **Customize > Plugins > upload** the zip, and keep the plugin's hooks enabled.
3. Restart the Claude app and start a new Cowork session.

Cowork ends its session after every reply, so a Cowork pet isn't removed then: it turns *done* and is cleared after the **Clear finished after** time, like any finished session. Your next message brings it back.

Cowork sessions get an orange **CW** badge and are named after the folder you connected to the session (`Name +2` when there are several), or after your first prompt when no folder is connected.

The plugin (`pet-hooks`) calls the same hook as `settings.json` (`~\.aipet\bin`), so updating AIPet updates it too. Remove it from Customize > Plugins.

**Claude Code CLI:** the same window shows how to install the plugin from a local marketplace in `~\.aipet\plugin-marketplace`:
```
claude plugin marketplace add "%USERPROFILE%\.aipet\plugin-marketplace"
claude plugin install pet-hooks@desktop-pet-local
```
Use it *instead of* the settings.json hooks, never both, or every event reaches the pet twice.

## Compact mode
*Compact mode (one pet)* in the right-click or tray menu shows a single pet for all sessions, with a robot per session
(up to four; a `+N` badge counts the rest), each robot in its own session's state. The pet's name tag, badges, bubble and
click belong to the session in front:

- Sessions that need you **queue up** in the order they asked. The first one is in front with a `N waiting` badge; answer
  it and the next one steps forward.
- With nobody waiting, the most urgent / most recently active session is in front.
- The name tag scrolls through every session's title like a banner, each after its state symbol in a faded state colour: `❃` working (blue), `❉` waiting for you (red), `✺` done (green). Each session's conversation title runs right below its name in small text (cut with `…` when long), and both lines scroll together.
- Hover the pet for a list of every session.
- The badges collapse into one: different agents are listed by name (`Claude + Codex`), a single agent gets all its tags (`Claude Cowork+WSL+VS`); it turns faint red if any session needs you. Click it (`▴`) to show one badge per session in a column snapped to the pet's left side (a second column further left if it fills up), each with its conversation's title on a second, smaller line, and `▾` to collapse again.

## WSL notes
- The WSL hook is a Python script, so the distro needs `python3`. Ubuntu has it by default. Otherwise the menu shows *needs python3*; install it in the distro (e.g. `sudo apt install python3`).
- The hook writes into your Windows `.aipet` folder through `/mnt/c`. That path is set in the hook command, so nothing else needs configuring.

## Clicking pets, bubbles
- **Conversation titles on the name tag:** the name moves to the top of the tag and the conversation's title sits below it
  in small text; a line too long for the tag scrolls round instead of being cut. Sessions that share a folder name also get it in
  their tooltip and prompt window (`esign-online · Fix login bug`). The title is the name the harness shows: for Claude Code your `/rename` name (`custom-title` in
  the transcript), else the name Claude Code gave the session (`agent-name`, kept for good once seen); for Codex the
  thread name from `~/.codex/session_index.jsonl` (renames follow). Right-click (or tray / menu bar) > *Session titles* switches between *Session name* (the default)
  and *Last prompt* (saved as `"session_titles"`: `"name"` / `"prompt"`). Without a name, the latest prompt you typed (a
  short session id before that).
- **Click a pet** to acknowledge it and bring its window to the front: the terminal or Claude desktop app that hosts the
  session (the hook records the window), or the right VS Code window for VS Code sessions. WSL terminals are best effort
  (matches a Windows Terminal window by distro/project, else the only/frontmost one). It cannot pick the tab inside a
  Windows Terminal window or the conversation inside the desktop app.
- **VS Code extension sessions** go one step further: after raising the window that holds the folder (also when VS Code
  has a parent folder or a `.code-workspace` open), the pet opens that conversation's tab through the extension's
  `vscode://anthropic.claude-code/open?session=<id>` link (Claude Code 2.1.72+). Claude Code run in VS Code's terminal
  only gets its window. Turn it off with `"vscode_open_conversation": false` in `config.json`.
  The open folders are read from VS Code's live window list (`backupWorkspaces` in `globalStorage/storage.json`), then
  the older `Backups/workspaces.json`, then the saved window state; on macOS paths are compared case-insensitively and
  with symlinks resolved. *Save diagnostics...* shows how many folders each source reported.
- **Click goes to the session's window** (right-click, tray or menu bar; on by default, saved as `click_to_focus`):
  switch it off and a click only acknowledges the pet. The prompt window's *Go to window* still works.
- **macOS: Show on all desktops** (on by default, saved as `all_spaces`): the pet stays visible when you switch desktops
  (Spaces, e.g. with a three-finger swipe) and over full-screen apps.
- **Click a badge** to open that session's prompt window if it needs you (in compact mode, each badge is one session), or to go to its window otherwise.
- **Dismiss** a pet from the right-click menu (*Dismiss this pet*) or with *Clear finished*; clicking no longer dismisses.
- When a session **needs you**, the robot's **check / cross / "?" bubble** is clickable (hand cursor): it opens an
  **independent popup** with what Claude wants to run (the tool, its description and the command), where it runs and how
  long it has waited. Clicking the robot itself goes to the session's window. The popup closes by itself when the session
  stops needing you. Sessions that send only a notification (the VS Code extension) still show the question: the hook
  recovers it from the end of the session transcript, but those prompts can't be answered from the pet.

## The pet, subagents, resizing
- The default pet is a little **robot** (a PC) from the pixel art in `assets/reference/duck-robot.webp`; the sprites are in
  `assets/sprites` and regenerate with `python tools/make_sprites.py`. **Working:** happy face, blinking, cycling lights and a
  bubble of scrolling hacker text. **Needs you:** a "?" on its screen, flashing amber lights, a small hop and a
  check / cross / "?" bubble. **Done:** check bubble. **Error:** worried face and a cross. **Idle:** asleep (eyes closed,
  lights off, floating z's). The state colour shows on the name tag.
- **Pet style** in the tray menu (or the pet's right-click menu on macOS) switches between *Robot*, the older *Mole* and the
  original *Cat*. Without Pillow or the sprite files the app falls back to the mole.
- A session that runs **subagents** shows extra robots next to the main one (up to four), and the state line shows `+N`.
  This relies on the hook payloads carrying an `agent_id` (finished agents drop off after `SubagentStop` or 90 s without
  activity). A subagent's tool call no longer hides a permission prompt that belongs to a different agent.
- **Resize** with the slider: right-click the pet > *Size...* (or tray > *Pet size...*) opens a small window with a 30% - 300%
  slider; the pet follows it live and the value is saved as `size` (1.0 = 100%). 100% is the default size (twice the drawing
  size the first versions used; an older `scale` value in `config.json` is converted once). *Reset size* restores 100%.
  The pet keeps its bottom-right corner while it grows or shrinks, but stays on the screen it is on (on macOS too, kept
  above the Dock; the screens are read from NSScreen). If it is ever lost off screen, *Reset position (main screen)* in the pet's
  right-click menu, the tray menu or the macOS menu-bar menu puts it back in the main screen's bottom-right corner.

## Answering permission prompts from the pet
When Claude Code asks for permission, the popup (click the bubble's title) shows **Deny** and **Allow once**. Your click is
handed to the `PermissionRequest` hook, which prints the decision to Claude Code.
- The hook waits up to the **answer timeout** (default 3 minutes, set 0 - 30 minutes with right-click > *Answer timeout...*
  or tray > *Answer timeout...*; 0 = no limit, it waits until you answer) and **only while the pet is running** (it checks a
  heartbeat file). If you don't click, or the pet is closed, it prints nothing and the normal prompt appears as usual.
  It also stops waiting as soon as the prompt is answered in Claude Code itself.
  The setting is saved as `answer_wait_seconds` and mirrored into `~/.aipet/answer-wait` for the hooks.
- Claude Code (CLI/desktop) shows its own prompt at the same time and the first answer wins, so you can still answer there.
- For background subagents Claude Code may wait for the hook before showing its own prompt, so a long timeout also
  lengthens that delay (with no limit, until you answer from the pet); lower the slider if you notice it.

## Auto approve
**Auto approve** (pet right-click, the tray icon's menu, or the macOS menu-bar icon) lists every hook config: *This PC /
This Mac*, each *WSL* distro, *Cowork* (the Claude app plugin) and each place *Codex* was found. **Everything is off by
default.** Each entry opens a window for that config:
- **Auto approve for this config** switches it on or off.
- **Whitelist** (approve automatically) and **Blacklist** (always ask me): one regex per line; empty lines and lines
  starting with `#` are ignored. Defaults: the whitelist is empty and the blacklist is `.*`, so nothing is approved
  until you replace it.
  - Patterns are matched against the command (or file path, URL, search pattern...), the tool name and `Tool(command)`,
    for example `git (status|diff)`, `npm test`, `Read` or `Bash\(ls\)`.
  - Whitelist patterns must match the **whole** text (`re.fullmatch`), so `git status` doesn't also approve
    `git status; rm -rf ~`. Blacklist patterns match **anywhere** (`re.search`). Tip: `[;&|`$<>]` makes chained and
    redirected commands ask.
  - The **blacklist always wins**; anything on neither list asks you as usual. An invalid blacklist pattern counts as a
    match, so a typo can only make the pet ask more. Save refuses patterns that don't compile.
- **Allow all** approves every request and greys out both lists.
- Switching a config on, or turning on Allow all, first shows a warning (Enter and Escape both cancel). *Turn all off*
  switches every config off and keeps the lists.

Details:
- Saved in `~/.aipet/auto-approve.json`; hooks read it on every prompt, so changes apply to running sessions at once.
- It only works **while AIPet is running** (the hooks check the pet's heartbeat) and with AIPet's hooks installed for
  that config; the menu marks configs without them. The VS Code extension sends no permission events, so it always asks.
- When the **whitelist** approves something, the robot shows steady green lights, a gentle bob and a small pale-green `✓ auto` bubble
  for 10 seconds, or until any session needs you. Its tooltip shows what was approved. *Allow all* approvals don't
  animate (they would never stop). The tray tooltip starts with `AUTO APPROVE ON` while any config is on.

## Stuck sessions
Every 15 seconds (right-click or tray > *Health check every...*: 0 = off, up to 5 minutes; saved as
`health_check_seconds`) the pet checks that each working or waiting session's Claude Code / Codex process still runs. If
it has ended without saying so (the app was closed, or Cowork moved the conversation to a new session), the session is
shown as finished and the done timeout clears it. WSL sessions can't be checked from Windows: right-click > *Mark as
finished* ends any stuck session by hand (in compact mode, the one in front).

## Clearing finished sessions
A finished session's pet is cleared after the **done timeout**: right-click > *Clear finished after...* (or the tray menu),
0 - 30 minutes, default 3 minutes; 0 keeps finished pets until you dismiss them. Saved as `done_timeout_minutes`.
- There is no "always allow" button: that needs a permission-rule format I haven't verified. The VS Code extension does
  not send `PermissionRequest` events, so its prompts keep the read-only bubble.

## New versions
Once a day (and from *Check for updates...* in the right-click, tray or menu-bar menu) AIPet asks GitHub for the newest
published release, i.e. the newest `vX.Y.Z` tag (the rolling *latest* pre-release is ignored). If it is newer than the
running copy, a popup offers *Open release page*, *Later* or *Skip this version*, and the menus show *Update available:
vX.Y.Z...* until you install it. Nothing is downloaded or installed automatically: download the new `AIPet.exe` /
`AIPet-mac-arm64.zip`, quit AIPet and replace your copy (settings, hooks and backups in `~/.aipet` are kept).
- Turn the daily check off with *Check for updates automatically* (saved as `update_check`).
- The menus show the running version (*AIPet v0.2.4*). Builds take it from `git describe` (`tools/write_version.py`,
  run by `build.bat`, `build_mac.sh` and CI), so a local build two commits after v0.2.4 reads `v0.2.4-2-g<commit>` and
  counts as v0.2.4.

## Theme, diagnostics
- **Testing on macOS without a Mac:** run the **macOS self-test** workflow by hand (Actions tab). It starts the app on an Apple Silicon runner with fake sessions (`tools/seed_sessions.py`) and uploads screenshots plus the diagnostics report, for the packaged app and for the source.
- **Save diagnostics...** (pet right-click) writes `~/.aipet/diagnostics.txt` and opens it: versions, sprite loading, image tests, window state and recent errors. No prompts or session contents. Send it along with bug reports from machines that can't be tested here.
- **Light theme is the default.** Tray menu (or the pet's right-click menu on macOS) > *Dark theme* switches live and is
  saved as `theme` in `config.json`. It covers the name tags, bubbles, tooltips and the permission popup, and also the
  setup, Cowork, slider and reminder windows, the confirmation / message dialogs (now cards in the pet's style), the
  pet's right-click menu and, on Windows 10 1903+ / 11, the tray menu and window title bars. macOS keeps its native
  menus. The one-time *Claude Pet* migration window stays native.
- **Log hook events (debug)** appends one metadata line per hook event to `~/.aipet/events.log` (event, tool name, field
  names, state change, VS Code detection hints - never prompts, commands or other values). Use it to diagnose odd pet states,
  e.g. a permission prompt that the pet shows as "working" because another subagent's tool call arrived in between.

## First-run setup, notifications
- On first run a **setup window** lists the Claude Code installs it found (this PC, running WSL distros with Claude Code)
  and shows for each whether the hooks are missing, installed and up to date, or out of date. Nothing is written until you
  press *Install selected*. Reopen it any time: tray > Claude Code hooks > Run setup again.
- **Windows notifications are off by default** (the Claude app already notifies). Tray menu > Windows notifications turns
  them on; the choice is saved in `config.json` as `notifications`. Sounds are a separate toggle.

## macOS (experimental, not yet tested on a Mac)
The code has macOS support but has only been verified by unit tests on Windows. Build with `bash build_mac.sh` on a Mac, or
use the *macos* job of the GitHub Actions workflow (artifact `AIPet-mac`). The app is unsigned: first launch needs
right-click > Open.
- **Hook runtime.** The setup window offers *your Python 3* (faster) or the *built-in hook* (nothing to install, slower per
  tool call). If Python 3 isn't found it selects the built-in hook, warns that Python is faster, and offers help installing
  Python (python.org page, or Apple's Command Line Tools installer) - nothing is installed without your confirmation.
  `/usr/bin/python3` is never executed unless the Command Line Tools are present, because without them it is a stub that
  pops up Apple's installer dialog.
- **No tray icon yet.** pystray needs the main thread, which Tk already owns, so on macOS the essentials (hooks/setup,
  notifications, start at login, config folder, quit) are in the pet's right-click menu (ctrl-click works too).
- Notifications use `osascript`, sounds use `afplay`, start at login is a LaunchAgent, backups and hooks live in `~/.aipet`.

## mcp-workbench Agents chats (optional)
Open the config folder from the tray and set `"workbench": { "enabled": true, "mcp_url": "<your endpoint>" }` in `config.json`. Put the key in an environment variable, not in the file: `setx WORKBENCH_MCP_KEY "<key>"`. The key needs the Agents grant.

To check what Workbench returns and how the pet reads each chat's state, run `AIPet.exe --probe-workbench`. It writes the report to `workbench-probe.txt` and opens it. If a state is read wrong, adjust `state_map` in `config.json`. Restart the app after changing the config.

## Run from source
```
pip install pystray pillow
pythonw aipet_app.py
```
From source, the Windows hook runs via `pythonw` and the script, so no exe is needed.

## Files
| File | Purpose |
|---|---|
| `aipet_app.py` | Entry point: tray, hook menu, autostart |
| `aipet.py` | The pet window, session reading, Workbench poller |
| `hooks_installer.py` | settings.json merge/remove, backups and restore for Windows and WSL |
| `aipet_hook.py` | The hook Claude Code runs on each event |
| `build.bat`, `.github/workflows/build.yml` | Build `AIPet.exe` |

## License
Free for everyone to use, modify and share, including at work. Forks and modified versions must keep the license and credit "AIPet by Duck Code". Selling AIPet, or a product that mainly provides it, needs a written agreement first. See [LICENSE](LICENSE).
