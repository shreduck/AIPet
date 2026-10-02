# Claude Pet

A floating companion with one creature per Claude Code session. It lives in the system tray and installs its own hooks.

| Pet | Meaning |
|---|---|
| Blue, bobbing | working |
| Orange, jumping, "!" + beep + Windows notification | needs your input (re-reminds every 90 s until you click it) |
| Green, sleeping | done |
| Red, shaking | error (Workbench) |

Pets keep their position: a session stays where it first appeared (new ones join on the left) even when its state changes; only if there are more than `max_pets` are the least urgent dropped.

Each pet shows a badge for where its session runs: `CC` (Windows terminal), `WSL`, `VS` (VS Code), or `WB` (Workbench). The tray icon takes the colour of the most urgent session, and hovering it shows a summary.

## Build the exe
On Windows with Python 3.10+, double-click **`build.bat`**. It produces `dist\ClaudePet.exe` (single file).
If the repo is on GitHub, you can instead run the **Build ClaudePet.exe** workflow and download the artifact.

The exe is unsigned, so the first time you run it SmartScreen may show "Windows protected your PC". Click *More info → Run anyway*.

## Use it
Run `ClaudePet.exe`. The pet appears bottom-right and a tray icon appears near the clock.

**Tray menu**
- **Show / Hide pet**. Left-clicking the tray icon does the same. The pet's right-click menu also has *Hide to tray*.
- **Claude Code hooks**
  - **This PC (Windows)**: installs into `%USERPROFILE%\.claude\settings.json`. Covers terminal and VS Code sessions on Windows.
  - **WSL: \<distro\>**: one entry per auto-detected distro. Installs into that distro's `~/.claude/settings.json` and covers terminal and VS Code Remote-WSL sessions there. Stopped distros aren't booted just to show their status. They only start when you install or remove hooks.
  - Each target shows its status (✓ installed, not installed, needs python3, …) and offers *Install / update* and *Remove*.
- **Mute sounds**, **Start with Windows**, **Workbench status**, **Open config folder**, **Quit**.

What hook install does:
1. Copies the hook to `~\.claude-pet\bin` (deliberately outside AppData: apps started from a packaged app such as Claude Desktop get AppData writes redirected to a private copy that the CLI and WSL cannot see). Nothing is copied until you choose Install; an existing install is refreshed when the app starts, so updates reach every target.
2. Asks for confirmation, then backs up the current `settings.json` (see below).
3. Adds the hook entries and keeps every other hook and setting. It never overwrites a `settings.json` that isn't valid JSON. Running install again changes nothing if the hooks are already up to date.

Only Claude Code sessions started *after* installing will appear.

### Backup & restore
Each target's submenu also has **Restore backup**, **Back up now** and **Open backups folder**.

- **Automatic backups:** taken before every install, remove or restore, and only when the file actually changes.
- **Storage:** backups are byte-exact copies kept in `~\.claude-pet\backups\<target>\`. WSL backups are stored there too, so browsing them never starts a distro.
- **"No file" state:** if `settings.json` didn't exist yet, that is recorded as well. Restoring it deletes the file again.
- **Retention:** the **oldest backup is always kept** (labelled *[original]*, your settings from before Claude Pet first touched them), plus the 20 most recent.
- **What the menu shows:** each backup's date, reason, and whether it contains Claude Pet hooks. It also warns if that version isn't valid JSON.
- **Undo:** a restore first saves the current file as a *before restore* backup, so it can be undone from the same menu.
- After restoring, restart running Claude Code sessions to pick up the change.

Backups are plain copies of your `settings.json`. If you keep secrets in it (e.g. under `env`), the copies contain them too. They stay in your own user profile.

**Pet:** hover for details, click to acknowledge a pet that needs input (or dismiss a done one), drag to move. Right-click opens the menu, including *Open in VS Code*.

## WSL notes
- The WSL hook is a Python script, so the distro needs `python3`. Ubuntu has it by default. Otherwise the menu shows *needs python3*; install it in the distro (e.g. `sudo apt install python3`).
- The hook writes into your Windows `.claude-pet` folder through `/mnt/c`. That path is set in the hook command, so nothing else needs configuring.

## Clicking pets, bubbles
- **Click a pet** to acknowledge it and bring its window to the front: the terminal or Claude desktop app that hosts the
  session (the hook records the window), or the right VS Code window for VS Code sessions. WSL terminals are best effort
  (matches a Windows Terminal window by distro/project, else the only/frontmost one). It cannot pick the tab inside a
  Windows Terminal window or the conversation inside the desktop app.
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

## Answering permission prompts from the pet
When Claude Code asks for permission, the popup (click the bubble's title) shows **Deny** and **Allow once**. Your click is
handed to the `PermissionRequest` hook, which prints the decision to Claude Code.
- The hook waits up to the **answer timeout** (default 3 minutes, set 0 - 5 minutes with right-click > *Answer timeout...*
  or tray > *Answer timeout...*; 0 turns answering from the pet off) and **only while the pet is running** (it checks a
  heartbeat file). If you don't click, or the pet is closed, it prints nothing and the normal prompt appears as usual.
  The setting is saved as `answer_wait_seconds` and mirrored into `~/.claude-pet/answer-wait` for the hooks.
- Claude Code (CLI/desktop) shows its own prompt at the same time and the first answer wins, so you can still answer there.
- For background subagents Claude Code may wait for the hook before showing its own prompt, so a long timeout also
  lengthens that delay; lower the slider if you notice it.
- There is no "always allow" button: that needs a permission-rule format I haven't verified. The VS Code extension does
  not send `PermissionRequest` events, so its prompts keep the read-only bubble.

## Theme, diagnostics
- **Light theme is the default.** Tray menu (or the pet's right-click menu on macOS) > *Dark theme* switches live and is
  saved as `theme` in `config.json`. It covers the name tags, bubbles and tooltips; setup and confirmation dialogs stay native.
- **Log hook events (debug)** appends one metadata line per hook event to `~/.claude-pet/events.log` (event, tool name, field
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
use the *macos* job of the GitHub Actions workflow (artifact `ClaudePet-mac`). The app is unsigned: first launch needs
right-click > Open.
- **Hook runtime.** The setup window offers *your Python 3* (faster) or the *built-in hook* (nothing to install, slower per
  tool call). If Python 3 isn't found it selects the built-in hook, warns that Python is faster, and offers help installing
  Python (python.org page, or Apple's Command Line Tools installer) - nothing is installed without your confirmation.
  `/usr/bin/python3` is never executed unless the Command Line Tools are present, because without them it is a stub that
  pops up Apple's installer dialog.
- **No tray icon yet.** pystray needs the main thread, which Tk already owns, so on macOS the essentials (hooks/setup,
  notifications, start at login, config folder, quit) are in the pet's right-click menu (ctrl-click works too).
- Notifications use `osascript`, sounds use `afplay`, start at login is a LaunchAgent, backups and hooks live in `~/.claude-pet`.

## mcp-workbench Agents chats (optional)
Open the config folder from the tray and set `"workbench": { "enabled": true, "mcp_url": "<your endpoint>" }` in `config.json`. Put the key in an environment variable, not in the file: `setx WORKBENCH_MCP_KEY "<key>"`. The key needs the Agents grant.

To check what Workbench returns and how the pet reads each chat's state, run `ClaudePet.exe --probe-workbench`. It writes the report to `workbench-probe.txt` and opens it. If a state is read wrong, adjust `state_map` in `config.json`. Restart the app after changing the config.

## Run from source
```
pip install pystray pillow
pythonw claude_pet_app.py
```
From source, the Windows hook runs via `pythonw` and the script, so no exe is needed.

## Files
| File | Purpose |
|---|---|
| `claude_pet_app.py` | Entry point: tray, hook menu, autostart |
| `claude_pet.py` | The pet window, session reading, Workbench poller |
| `hooks_installer.py` | settings.json merge/remove, backups and restore for Windows and WSL |
| `claude_pet_hook.py` | The hook Claude Code runs on each event |
| `build.bat`, `.github/workflows/build.yml` | Build `ClaudePet.exe` |
