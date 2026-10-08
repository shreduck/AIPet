"""
Moving from Claude Pet (the old name) to AIPet.

The old version kept everything in ~/.claude-pet, registered "ClaudePet" as a startup command (Windows Run key /
macOS LaunchAgent com.claudepet.app) and named its hooks claude-pet-hook. On first start, AIPet:

* checks whether the old app is still running and offers to close it (or asks the user to quit it);
* copies settings, backups and current sessions into ~/.aipet (never overwriting, never deleting the old folder,
  so hooks that still point at it keep working until they are updated);
* moves "start with Windows / at login" over to AIPet if it was on;
* leaves a note in the old folder.

Hooks in settings.json are updated by the normal "hooks point at an older location" prompt afterwards (they are
recognised by name, see hooks_installer.MARKER). Anything still on the old name keeps working; AIPet reminds the
user once a day what to update (findings()).

Everything about the old name lives here, plus lines marked "LEGACY" elsewhere: dropping legacy support later means
deleting this file and those lines.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import time

import hooks_installer as hi

UI_FONT = "Consolas" if os.name == "nt" else ("Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono")

IS_MAC = sys.platform == "darwin"
HOME = os.path.expanduser("~")
WARNED = os.path.join(os.path.expanduser("~"), ".aipet", "legacy-warned")  # date of the last daily reminder
LEGACY_DIR = os.path.join(HOME, ".claude-pet")
NEW_DIR = os.path.join(HOME, ".aipet")
MARKER = os.path.join(NEW_DIR, "migrated-from-claude-pet")
LEGACY_MUTEX = "Local\\ClaudePetSingleton"
LEGACY_RUN_VALUE = "ClaudePet"
LEGACY_LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/com.claudepet.app.plist")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# what is worth carrying over (the hook program in bin/ and the Cowork/CLI plugin are rebuilt for AIPet)
COPY_FILES = ("config.json", "setup-done", "answer-wait", "debug-events", "no-answers")
COPY_DIRS = ("backups", "sessions")


def needs_migration():
    return os.path.isdir(LEGACY_DIR) and not os.path.exists(MARKER)


def old_running():
    """True if the old app is running: its single-instance mutex (Windows) or lock file (macOS/Linux) is held."""
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.OpenMutexW.restype = ctypes.c_void_p
            h = k32.OpenMutexW(0x00100000, False, LEGACY_MUTEX)  # SYNCHRONIZE
            if h:
                k32.CloseHandle(ctypes.c_void_p(h))
                return True
        except Exception:
            pass
        return False
    try:
        import fcntl
        with open(os.path.join(LEGACY_DIR, "app.lock"), "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True
    except (ImportError, OSError):
        return False


def close_old(wait=8.0):
    """Ask the OS to end the old app (ClaudePet.exe / ClaudePet.app, or claude_pet_app.py run from source).
    Returns True once it is no longer running."""
    try:
        if os.name == "nt":
            ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'ClaudePet.exe' -or "
                  "($_.CommandLine -like '*claude_pet_app.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
            subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                           creationflags=NO_WINDOW)
        else:
            for pattern in ("ClaudePet.app/Contents/MacOS/ClaudePet", "claude_pet_app.py"):
                subprocess.run(["pkill", "-f", pattern], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    end = time.time() + wait
    while time.time() < end:
        if not old_running():
            return True
        time.sleep(0.3)
    return not old_running()


def _old_autostart():
    if IS_MAC:
        return os.path.exists(LEGACY_LAUNCH_AGENT)
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, LEGACY_RUN_VALUE)
            return True
    except OSError:
        return False


def _remove_old_autostart():
    if IS_MAC:
        try:
            os.remove(LEGACY_LAUNCH_AGENT)
        except FileNotFoundError:
            pass
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        try:
            winreg.DeleteValue(k, LEGACY_RUN_VALUE)
        except FileNotFoundError:
            pass


def plan():
    """What the migration will do, as short lines for the first-run window."""
    items = []
    for name in COPY_FILES + COPY_DIRS:
        if os.path.exists(os.path.join(LEGACY_DIR, name)):
            items.append(name + ("/" if name in COPY_DIRS else ""))
    return {"copy": items, "autostart": _old_autostart()}


def migrate(set_new_autostart=None):
    """Copy the old data into ~/.aipet (never overwriting) and move autostart. Returns a list of what was done."""
    done = []
    os.makedirs(NEW_DIR, exist_ok=True)
    for name in COPY_FILES:
        src, dst = os.path.join(LEGACY_DIR, name), os.path.join(NEW_DIR, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            done.append(f"copied {name}")
    for name in COPY_DIRS:
        src = os.path.join(LEGACY_DIR, name)
        if not os.path.isdir(src):
            continue
        n = 0
        for root, _dirs, files in os.walk(src):
            for fn in files:
                if fn.endswith((".lock", ".new", ".tmp")):
                    continue
                s = os.path.join(root, fn)
                d = os.path.join(NEW_DIR, name, os.path.relpath(s, src))
                if not os.path.exists(d):
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                    shutil.copy2(s, d)
                    n += 1
        if n:
            done.append(f"copied {name}/ ({n} files)")
    if _old_autostart():
        try:
            _remove_old_autostart()
            if set_new_autostart:
                set_new_autostart(True)
            done.append("moved start-with-Windows / at-login to AIPet")
        except OSError as e:
            done.append(f"couldn't move the startup entry ({e}); switch it on again from the menu")
    try:
        with open(os.path.join(LEGACY_DIR, "MOVED-TO-AIPET.txt"), "w", encoding="utf-8") as f:
            f.write("Claude Pet is now AIPet. Its settings were copied to " + NEW_DIR + ".\n"
                    "This folder is still read while older hooks point at it. Once every target in AIPet's\n"
                    "'Claude Code hooks' menu shows a check mark, you can delete this folder.\n")
    except OSError:
        pass
    mark_done()
    mark_warned()  # the migration window already explained everything today
    return done


def mark_done():
    os.makedirs(NEW_DIR, exist_ok=True)
    with open(MARKER, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))


# --------------------------------------------------------------------------- hooks still on the old name (Claude Pet)
LEGACY_HOOK = re.compile(r"claude[-_]pet[-_]hook", re.I)


def uses_legacy_hook(key):
    """True if the target's settings.json still calls claude-pet-hook (they keep working, but should be updated)."""
    try:
        return bool(LEGACY_HOOK.search(hi.read_raw(key)[1]))
    except Exception:
        return False


def _hooks_files(base, max_depth):
    """hooks/hooks.json files under base, at most max_depth folders deep."""
    found = []
    base = os.path.normpath(base)
    for root, dirs, files in os.walk(base):
        depth = root[len(base):].count(os.sep)
        if depth >= max_depth:
            dirs[:] = []
        if os.path.basename(root) == "hooks" and "hooks.json" in files:
            found.append(os.path.join(root, "hooks.json"))
    return found


def legacy_plugins():
    """Installed copies of the plugin built by Claude Pet, whose hooks still call claude-pet-hook:
    [("cowork" | "cli", path)]. Cowork plugins live in the Claude app's data folder, CLI ones in ~/.claude/plugins."""
    roots = []
    if IS_MAC:
        roots.append(("cowork", os.path.expanduser("~/Library/Application Support/Claude/local-agent-mode-sessions")))
    elif os.name == "nt":
        if os.environ.get("APPDATA"):
            roots.append(("cowork", os.path.join(os.environ["APPDATA"], "Claude", "local-agent-mode-sessions")))
        if os.environ.get("LOCALAPPDATA"):
            roots += [("cowork", p) for p in glob.glob(os.path.join(
                os.environ["LOCALAPPDATA"], "Packages", "Claude_*", "LocalCache", "Roaming", "Claude",
                "local-agent-mode-sessions"))]
    roots.append(("cli", os.path.join(HOME, ".claude", "plugins")))
    out = []
    for kind, root in roots:
        if not os.path.isdir(root):
            continue
        for f in _hooks_files(root, 7):
            try:
                with open(f, encoding="utf-8") as fh:
                    if LEGACY_HOOK.search(fh.read()):
                        out.append((kind, f))
            except OSError:
                pass
    return out


def findings(targets, pets):
    """What still uses the old name, each as (what, how to fix). targets: {key: label} of reachable settings.json
    targets; pets: current session items (their "legacy" flag says they arrived through ~/.claude-pet)."""
    out, seen = [], set()
    for key, label in targets.items():
        if uses_legacy_hook(key):
            out.append((f"{label}: Claude Code hooks still call claude-pet-hook",
                        f"Menu: Claude Code hooks > {label} > Install / update hooks (backed up first)."))
            seen.add(key)
    kinds = {k for k, _ in legacy_plugins()}
    if "cowork" in kinds:
        out.append(("Claude app (Cowork): the old pet-hooks plugin is installed",
                    "Claude app > Customize > Plugins: uninstall pet-hooks, then upload aipet-cowork-plugin.zip "
                    "(menu: Claude Code hooks > Cowork shows where it is)."))
    if "cli" in kinds:
        out.append(("Claude Code CLI: the old pet-hooks plugin is installed",
                    "In a terminal: claude plugin uninstall pet-hooks@desktop-pet-local, then run the two commands "
                    "from Claude Code hooks > Cowork."))
    for it in pets:
        if not it.get("legacy"):
            continue
        if it.get("app") == "cowork":
            if "cowork" not in kinds:
                out.append((f"Cowork session '{it.get('title')}' reports through the old plugin",
                            "Claude app > Customize > Plugins: uninstall pet-hooks, upload aipet-cowork-plugin.zip."))
                kinds.add("cowork")
        elif it.get("env") == "wsl" and ("wsl:" + it.get("distro", "")) not in seen:
            seen.add("wsl:" + it.get("distro", ""))
            out.append((f"WSL {it.get('distro')}: sessions still use the old hook",
                        f"Menu: Claude Code hooks > WSL: {it.get('distro')} > Install / update hooks."))
        elif "other" not in seen:
            seen.add("other")
            out.append((f"Session '{it.get('title')}' still reports through the old hook",
                        "Menu: Claude Code hooks > Run setup again, and update every target marked out of date."))
    return out


def due_today():
    try:
        with open(WARNED, encoding="utf-8") as f:
            return f.read().strip() != time.strftime("%Y-%m-%d")
    except OSError:
        return True


def mark_warned():
    try:
        os.makedirs(os.path.dirname(WARNED), exist_ok=True)
        with open(WARNED, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d"))
    except OSError:
        pass


# --------------------------------------------------------------------------- first start after the rename (window)
def migration_window(APP_NAME, set_autostart):
    """Tk window shown before the app starts (its own Tk root).
    Explain the Claude Pet -> AIPet changes, close the old app (or ask the user to), copy the settings.
    Returns False if the user chose to quit."""
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.title(f"Claude Pet is now {APP_NAME}")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    grey, red = "#6b7280", "#b91c1c"
    p = plan()
    result = {"go": False}
    home = "~" if IS_MAC else "%USERPROFILE%"
    sep = "/" if IS_MAC else "\\"
    copied = ", ".join(p["copy"]) or "nothing to copy"
    lines = [
        ("Settings folder", f"{home}{sep}.claude-pet  ->  {home}{sep}.aipet\nCopied: {copied}. The old folder is kept "
                            "(older hooks still write there and AIPet keeps reading it); a note inside says when "
                            "you can delete it."),
        ("Claude Code hooks", "Your settings.json files still call the old claude-pet-hook. Right after this, AIPet "
                              "offers to update them (each file is backed up first). WSL distros that aren't running "
                              "can be updated later from the menu: Claude Code hooks."),
    ]
    if p["autostart"]:
        lines.append(("Start " + ("at login" if IS_MAC else "with Windows"),
                      "Moved from Claude Pet to AIPet."))
    lines += [
        ("Cowork plugin", f"Rebuilt as {hi.COWORK_ZIP}. In the Claude app: Customize > Plugins, uninstall the old "
                          f"{hi.PLUGIN_NAME} plugin and upload the new zip (menu: Claude Code hooks > Cowork)."),
        ("Claude Code CLI plugin", "Only if you installed it: run the two commands from Claude Code hooks > Cowork "
                                   "again (its folder moved)."),
        ("The old app", ("Delete ClaudePet.app" if IS_MAC else "Delete ClaudePet.exe") + " once AIPet is running."),
    ]
    tk.Label(root, text=f"Claude Pet has a new name: {APP_NAME}", font=(UI_FONT, 13, "bold")
             ).pack(anchor="w", padx=16, pady=(14, 2))
    tk.Label(root, text="Here is what changes on this computer:", fg=grey).pack(anchor="w", padx=16)
    body = tk.Frame(root)
    body.pack(fill="x", padx=16, pady=8)
    for head, text in lines:
        tk.Label(body, text=head, font=(UI_FONT, 9, "bold")).pack(anchor="w", pady=(6, 0))
        tk.Label(body, text=text, justify="left", wraplength=480, font=(UI_FONT, 9)).pack(anchor="w")
    status = tk.Label(root, fg=red, justify="left", wraplength=480, font=(UI_FONT, 9, "bold"))
    status.pack(anchor="w", padx=16)

    def refresh_status():
        status.config(text="Claude Pet is still running. AIPet will close it when you continue." if old_running()
                      else "")

    def ensure_closed():
        if not old_running():
            return True
        status.config(text="Closing Claude Pet...")
        root.update()
        while not close_old():
            if not messagebox.askretrycancel(
                    APP_NAME, "AIPet couldn't close Claude Pet by itself.\n\nQuit it yourself: right-click its "
                    + ("pet > Quit" if IS_MAC else "tray icon (near the clock) > Quit") + ", then press Retry.\n\n"
                    "Cancel quits AIPet without changing anything.", parent=root):
                return False
        return True

    def go(copy):
        if not ensure_closed():
            root.destroy()
            return
        try:
            if copy:
                done = migrate(set_new_autostart=set_autostart)
                messagebox.showinfo(APP_NAME, "Done:\n\n" + ("\n".join("- " + d for d in done) or "- nothing to copy")
                                    + "\n\nNext, AIPet offers to update your hooks.", parent=root)
            else:
                mark_done()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Copying the old settings failed:\n{e}\n\nAIPet starts with default "
                                           "settings; the old folder is untouched.", parent=root)
            mark_done()
        result["go"] = True
        root.destroy()

    row = tk.Frame(root)
    row.pack(fill="x", padx=16, pady=(8, 14))
    tk.Button(row, text="Continue", width=12, default="active", command=lambda: go(True)).pack(side="right")
    tk.Button(row, text="Start fresh (don't copy)", command=lambda: go(False)).pack(side="right", padx=8)
    tk.Button(row, text="Quit", width=8, command=root.destroy).pack(side="left")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    refresh_status()
    root.update_idletasks()
    root.geometry(f"+{max(0, (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2)}"
                  f"+{max(0, (root.winfo_screenheight() - root.winfo_reqheight()) // 3)}")
    root.mainloop()
    return result["go"]
