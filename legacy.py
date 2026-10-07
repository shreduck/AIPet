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
recognised by name, see hooks_installer.MARKER).
"""
import os
import shutil
import subprocess
import sys
import time

IS_MAC = sys.platform == "darwin"
HOME = os.path.expanduser("~")
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
    return done


def mark_done():
    os.makedirs(NEW_DIR, exist_ok=True)
    with open(MARKER, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
