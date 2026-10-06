"""
Install / remove Claude Pet hooks in Claude Code's settings.json,
for Windows itself and for each auto-detected WSL distro.

* Existing hooks are preserved; only entries whose command mentions
  claude-pet-hook / claude_pet_hook are added or removed.
* Every change is preceded by a timestamped backup kept on the Windows side in
  ~/.claude-pet/backups/<target>/ (WSL backups included), so
  browsing backups never boots a distro. The oldest backup is never pruned.
* Backups are byte-exact; "file did not exist" is recorded too (*.absent).
* Restoring first backs up the current file, so every restore can be undone.
* A settings.json that isn't valid JSON is never overwritten by install/remove.

Targets are keys: "windows", "mac" or "wsl:<distro>".
"""
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime

HOOK_EVENTS = [
    ("SessionStart", None),
    ("UserPromptSubmit", None),
    ("PostToolUse", "*"),
    ("Notification", None),
    ("Stop", None),
    ("SessionEnd", None),
    ("SubagentStart", None),  # lets the pet see a subagent the moment it starts (even one running a single long command)
    ("SubagentStop", None),  # ... and drop it when it finishes
    ("PermissionRequest", None),  # gives the pet the command/description behind a permission prompt (observe only)
]
HOOK_TIMEOUTS = {"PermissionRequest": 310}  # seconds; the hook waits at most 300 s (the slider's maximum) for a click
MARKER = re.compile(r"claude[-_]pet[-_]hook", re.I)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IGNORED_DISTROS = {"docker-desktop", "docker-desktop-data", "rancher-desktop", "rancher-desktop-data"}
IS_MAC = sys.platform == "darwin"
LOCAL = "mac" if IS_MAC else "windows"  # key of the machine this app runs on


def is_local(key):
    return key in ("windows", "mac")


HOME = os.path.expanduser("~")
PET_DIR = os.path.join(HOME, ".claude-pet")
# Deliberately NOT under %LOCALAPPDATA%: when the app is started from a packaged (MSIX) app such as
# Claude Desktop, Windows redirects AppData writes into a private per-package copy that other
# processes (CLI, VS Code, WSL) cannot see, which silently breaks the installed hooks.
INSTALL_DIR = os.path.join(PET_DIR, "bin")
WINDOWS_SETTINGS = os.path.join(HOME, ".claude", "settings.json")
BACKUP_ROOT = os.path.join(PET_DIR, "backups")
STALE = "installed · update needed"
KEEP_BACKUPS = 20  # newest N kept, plus the oldest (original) one


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def fwd(path):
    return path.replace("\\", "/")


# --------------------------------------------------------------------------- settings merging (pure)
def parse_settings(text):
    text = (text or "").strip()
    if not text:
        return {}
    data = json.loads(text)  # raises -> caller refuses to overwrite
    if not isinstance(data, dict):
        raise ValueError("settings.json is not a JSON object")
    return data


def _strip_ours(groups):
    out = []
    for g in groups:
        if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
            out.append(g)
            continue
        kept = [h for h in g["hooks"] if not (isinstance(h, dict) and MARKER.search(str(h.get("command", ""))))]
        if kept or not g["hooks"]:
            out.append({**g, "hooks": kept})
    return out


def merge_hooks(settings, command):
    settings = dict(settings or {})
    hooks = dict(settings.get("hooks") or {})
    for event, matcher in HOOK_EVENTS:
        groups = _strip_ours(hooks.get(event) or [])
        entry = {"hooks": [{"type": "command", "command": command,
                            **({"timeout": HOOK_TIMEOUTS[event]} if event in HOOK_TIMEOUTS else {})}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        groups.append(entry)
        hooks[event] = groups
    settings["hooks"] = hooks
    return settings


def remove_hooks(settings):
    settings = dict(settings or {})
    hooks = dict(settings.get("hooks") or {})
    for event in list(hooks):
        if isinstance(hooks[event], list):
            groups = _strip_ours(hooks[event])
            if groups:
                hooks[event] = groups
            else:
                del hooks[event]
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return settings


def has_hooks(settings):
    return bool(MARKER.search(json.dumps((settings or {}).get("hooks", {}))))


def dump(settings):
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- deployed files
def _same_file(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ca, cb = fa.read(1 << 20), fb.read(1 << 20)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


def _sync_file(src, dst):
    """Copy src over dst only if different, via a temp file + atomic replace, so a hook that
    is executing right now never sees a half-written or missing file."""
    if os.path.exists(dst) and _same_file(src, dst):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".new"
    shutil.copyfile(src, tmp)
    shutil.copymode(src, tmp)  # keep the executable bit (macOS hook binary)
    try:
        os.replace(tmp, dst)
    except OSError:  # target busy (hook running): keep the working copy, retry on next start
        try:
            os.remove(tmp)
        except OSError:
            pass


def deploy_files(only_if_deployed=False):
    """Copy the hook (exe + script) to ~/.claude-pet/bin so settings can point at a stable path.

    With only_if_deployed=True this refreshes an existing deployment and does nothing
    otherwise, so merely starting the app never writes hook files; Install does.
    Unchanged files are left untouched; changed ones are swapped in atomically.
    """
    if only_if_deployed and not os.path.isdir(INSTALL_DIR):
        return
    os.makedirs(INSTALL_DIR, exist_ok=True)
    os.makedirs(os.path.join(PET_DIR, "sessions"), exist_ok=True)
    _sync_file(resource_path("claude_pet_hook.py"), os.path.join(INSTALL_DIR, "claude_pet_hook.py"))
    src = resource_path("hook")
    if os.path.isdir(src):
        for root, _dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            for name in files:
                try:
                    _sync_file(os.path.join(root, name), os.path.normpath(os.path.join(INSTALL_DIR, "hook", rel, name)))
                except OSError:
                    pass  # leave the existing copy in place


def windows_hook_command(check=True):
    if getattr(sys, "frozen", False):
        exe = os.path.join(INSTALL_DIR, "hook", "claude-pet-hook.exe")
        if check and not os.path.exists(exe):
            raise RuntimeError("Bundled hook executable is missing - rebuild with build.bat.")
        return f'"{fwd(exe)}"'
    # running from source: use pythonw so no console window flashes
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    return f'"{fwd(pyw)}" "{fwd(os.path.join(INSTALL_DIR, "claude_pet_hook.py"))}"'


# --------------------------------------------------------------------------- macOS: finding Python / hook command
MAC_PYTHON_CANDIDATES = [
    "/opt/homebrew/bin/python3",                                   # Homebrew (Apple silicon)
    "/usr/local/bin/python3",                                      # Homebrew (Intel) / python.org symlink
    "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",  # python.org installer
]
MAC_STUB_PYTHON = "/usr/bin/python3"


def clt_installed():
    """True if Apple's Command Line Tools are installed, i.e. /usr/bin/python3 is real and not a stub."""
    try:
        p = subprocess.run(["xcode-select", "-p"], capture_output=True, timeout=10)
        path = p.stdout.decode("utf-8", "replace").strip()
        return p.returncode == 0 and bool(path) and os.path.exists(os.path.join(path, "usr", "bin", "python3"))
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_mac_python():
    """Path of a working python3 on macOS, or None.

    /usr/bin/python3 is skipped unless the Command Line Tools exist: without them it is a stub that
    pops up Apple's "install developer tools" dialog the moment it is executed.
    """
    cands = list(MAC_PYTHON_CANDIDATES)
    found = shutil.which("python3")
    if found:
        cands.append(found)
    cands.append(MAC_STUB_PYTHON)
    tried = set()
    for c in cands:
        if c in tried or not (os.path.isfile(c) and os.access(c, os.X_OK)):
            continue
        tried.add(c)
        if os.path.realpath(c) == MAC_STUB_PYTHON and not clt_installed():
            continue
        try:
            if subprocess.run([c, "--version"], capture_output=True, timeout=15).returncode == 0:
                return c
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def _mac_script_cmd(py):
    return f"{shlex.quote(py)} {shlex.quote(os.path.join(INSTALL_DIR, 'claude_pet_hook.py'))}"


def _mac_builtin_cmd():
    return shlex.quote(os.path.join(INSTALL_DIR, "hook", "claude-pet-hook"))


def mac_hook_commands():
    """Every command that counts as an up-to-date Mac install (system Python and built-in variants)."""
    cmds = {_mac_builtin_cmd()}
    py = find_mac_python()
    if py:
        cmds.add(_mac_script_cmd(py))
    return cmds


def mac_hook_command(runtime=None):
    """runtime: "python" (system Python 3, faster), "builtin" (bundled binary) or None = python if found."""
    py = find_mac_python()
    if runtime is None:
        runtime = "python" if py else "builtin"
    if runtime == "python":
        if not py:
            raise RuntimeError("Python 3 wasn't found on this Mac.\nInstall it, or choose the built-in hook.")
        return _mac_script_cmd(py)
    if not os.path.exists(os.path.join(INSTALL_DIR, "hook", "claude-pet-hook")):
        raise RuntimeError("The built-in hook isn't part of this build.\nInstall Python 3 and choose it instead.")
    return _mac_builtin_cmd()


# --------------------------------------------------------------------------- migration from the old AppData location
def legacy_dirs():
    """Old install locations that hold backups: the real AppData folder and any MSIX-private copy."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return []
    found = [os.path.join(base, "ClaudePet")]
    found += glob.glob(os.path.join(base, "Packages", "*", "LocalCache", "Local", "ClaudePet"))
    return [d for d in found if os.path.isdir(os.path.join(d, "backups"))]


def migrate_legacy_backups():
    """Copy backups from the old locations into ~/.claude-pet/backups (never deletes or overwrites).

    Returns the number of files copied. Runs until a legacy folder has been seen once.
    """
    marker = os.path.join(BACKUP_ROOT, ".legacy-migrated")
    if os.path.exists(marker):
        return 0
    copied, seen = 0, False
    for legacy in legacy_dirs():
        seen = True
        root = os.path.join(legacy, "backups")
        for target in os.listdir(root):
            src_dir = os.path.join(root, target)
            if not os.path.isdir(src_dir):
                continue
            dst_dir = os.path.join(BACKUP_ROOT, target)
            os.makedirs(dst_dir, exist_ok=True)
            for name in os.listdir(src_dir):
                dst = os.path.join(dst_dir, name)
                if not os.path.exists(dst):
                    shutil.copy2(os.path.join(src_dir, name), dst)
                    copied += 1
    if seen:
        os.makedirs(BACKUP_ROOT, exist_ok=True)
        open(marker, "w").close()
    return copied


def hooks_state(settings, command):
    """Compare the Claude Pet hook entries with the command we would install now.

    "missing"   no Claude Pet hooks at all
    "outdated"  at least one entry uses a different command (e.g. an old location)
    "partial"   right command, but some of the events are not hooked
    "current"   every event uses exactly `command`
    """
    ok = {command} if isinstance(command, str) else set(command)
    ours, bad_timeout = {}, False
    for event, groups in ((settings or {}).get("hooks") or {}).items():
        if not isinstance(groups, list):
            continue
        for g in groups:
            for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                if isinstance(h, dict) and MARKER.search(str(h.get("command", ""))):
                    ours.setdefault(event, []).append(h.get("command", ""))
                    if event in HOOK_TIMEOUTS and h.get("timeout") != HOOK_TIMEOUTS[event]:
                        bad_timeout = True
    if not ours:
        return "missing"
    if any(c not in ok for cmds in ours.values() for c in cmds):
        return "outdated"
    if bad_timeout or any(event not in ours for event, _ in HOOK_EVENTS):
        return "partial"
    return "current"


def _expected_command(key):
    if key == "windows":
        return windows_hook_command(check=False)
    if key == "mac":
        return mac_hook_commands()
    return wsl_hook_command(key[4:])


# --------------------------------------------------------------------------- raw file access per target
def _sh(distro, script, input_bytes=None, timeout=90):
    p = _wsl(["-d", distro, "--exec", "sh", "-c", script], input_bytes, timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _wsl(args, input_bytes=None, timeout=60):
    return subprocess.run(["wsl.exe", *args], input=input_bytes, capture_output=True,
                          timeout=timeout, creationflags=NO_WINDOW)


def describe(key):
    return WINDOWS_SETTINGS if is_local(key) else f"~/.claude/settings.json in WSL '{key[4:]}'"


def read_raw(key):
    """Return (exists, text) of the target's settings.json, byte-exact."""
    if is_local(key):
        try:
            with open(WINDOWS_SETTINGS, "rb") as f:
                return True, f.read().decode("utf-8")
        except FileNotFoundError:
            return False, ""
    distro = key[4:]
    rc, out, err = _sh(distro, 'f="$HOME/.claude/settings.json"; '
                               'if [ -f "$f" ]; then printf "EXISTS\\n"; cat "$f"; else printf "ABSENT\\n"; fi')
    if rc != 0 or "\n" not in out:
        raise RuntimeError(f"WSL '{distro}' didn't respond: {err.strip()[:200]}")
    flag, text = out.split("\n", 1)
    return flag.strip() == "EXISTS", text


def write_raw(key, text):
    """Write text to the target's settings.json; text=None deletes the file."""
    if is_local(key):
        if text is None:
            try:
                os.remove(WINDOWS_SETTINGS)
            except FileNotFoundError:
                pass
            return
        os.makedirs(os.path.dirname(WINDOWS_SETTINGS), exist_ok=True)
        tmp = WINDOWS_SETTINGS + ".tmp"
        with open(tmp, "wb") as f:
            f.write(text.encode("utf-8"))
        os.replace(tmp, WINDOWS_SETTINGS)
        return
    distro = key[4:]
    if text is None:
        rc, _, err = _sh(distro, 'rm -f "$HOME/.claude/settings.json"')
    else:
        rc, _, err = _sh(distro, 'mkdir -p "$HOME/.claude" && f="$HOME/.claude/settings.json" && '
                                 'cat > "$f.tmp" && mv "$f.tmp" "$f"', text.encode("utf-8"))
    if rc != 0:
        raise RuntimeError(f"Writing settings in WSL '{distro}' failed: {err.strip()[:200]}")


# --------------------------------------------------------------------------- backups
def backup_dir(key):
    return os.path.join(BACKUP_ROOT, re.sub(r"[^A-Za-z0-9_.-]", "_", key.replace(":", "-")))


def _save_backup(key, exists, text, reason):
    d = backup_dir(key)
    os.makedirs(d, exist_ok=True)
    now = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"
    slug = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-") or "backup"
    ext = ".json" if exists else ".absent"
    n = 0
    while True:  # never overwrite a backup made in the same millisecond
        path = os.path.join(d, f"{stamp}{'' if n == 0 else f'-{n:02d}'}__{slug}{ext}")
        if not os.path.exists(path):
            break
        n += 1
    with open(path, "xb") as f:
        f.write(text.encode("utf-8") if exists else b"")
    _prune(d)
    return path


def make_backup(key, reason="manual"):
    exists, text = read_raw(key)
    return _save_backup(key, exists, text, reason)


def _backup_files(d):
    try:
        return sorted(f for f in os.listdir(d) if f.endswith((".json", ".absent")))
    except FileNotFoundError:
        return []


def _prune(d):
    files = _backup_files(d)
    if len(files) > KEEP_BACKUPS + 1:
        for f in files[1:-KEEP_BACKUPS]:  # keep files[0] (original) and the newest N
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass


def list_backups(key):
    """Newest first: [{path, when, reason, absent, size, pet_hooks, valid_json, original}]."""
    d = backup_dir(key)
    files = _backup_files(d)
    out = []
    for i, name in enumerate(files):
        path = os.path.join(d, name)
        stem, ext = os.path.splitext(name)
        stamp, _, reason = stem.partition("__")
        try:
            when = datetime.strptime(stamp[:15], "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            when = stamp
        absent = ext == ".absent"
        text = ""
        if not absent:
            try:
                with open(path, "rb") as f:
                    text = f.read().decode("utf-8", "replace")
            except OSError:
                continue
        try:
            parse_settings(text)
            valid = True
        except Exception:
            valid = False
        out.append({
            "path": path, "when": when, "reason": reason.replace("-", " ") or "backup",
            "absent": absent, "size": len(text.encode("utf-8")),
            "pet_hooks": bool(MARKER.search(text)), "valid_json": valid, "original": i == 0,
        })
    return list(reversed(out))


def restore_backup(key, path):
    absent = path.endswith(".absent")
    text = None
    if not absent:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
    exists, current = read_raw(key)
    if (not absent and exists and current == text) or (absent and not exists):
        return f"{describe(key)} already matches that backup - nothing changed."
    _save_backup(key, exists, current, "before restore")
    write_raw(key, text)
    what = "removed (it didn't exist at that point)" if absent else "restored"
    return f"{describe(key)} {what}.\nThe previous version was saved as a 'before restore' backup."


# --------------------------------------------------------------------------- install / remove / status
def _apply(key, transform, reason):
    """Read -> transform -> backup -> write, skipping everything if nothing changes."""
    exists, text = read_raw(key)
    new_text = dump(transform(parse_settings(text)))
    if exists and new_text == text:
        return False
    _save_backup(key, exists, text, reason)
    write_raw(key, new_text)
    return True


def status(key):
    try:
        if not is_local(key) and not _has_python(key[4:]):
            return "needs python3"
        _, text = read_raw(key)
        settings = parse_settings(text)
        if not has_hooks(settings):
            return "not installed"
        if getattr(sys, "frozen", False):  # a source run would use a different command; don't flag that
            try:
                if hooks_state(settings, _expected_command(key)) != "current":
                    return STALE
            except Exception:
                pass
        return "installed"
    except json.JSONDecodeError:
        return "settings.json unreadable"
    except Exception:
        return "unreachable"


def install(key, runtime=None):
    """runtime only matters on macOS: "python" or "builtin" (see mac_hook_command)."""
    deploy_files()
    if key == "windows":
        command = windows_hook_command()
        where = "Claude Code on Windows (terminal + VS Code)"
    elif key == "mac":
        command = mac_hook_command(runtime)
        where = "Claude Code on this Mac (terminal + VS Code)"
    else:
        distro = key[4:]
        if not _has_python(distro):
            raise RuntimeError(f"python3 isn't installed in WSL '{distro}'.\n"
                               f"Install it there (e.g. sudo apt install python3) and try again.")
        command = wsl_hook_command(distro)
        where = f"Claude Code in WSL '{distro}' (terminal + VS Code Remote-WSL)"
    changed = _apply(key, lambda s: merge_hooks(s, command), "before install")
    if not changed:
        return f"Hooks for {where} are already up to date."
    return f"Hooks installed for {where}.\nA backup was taken first. New sessions will appear in the pet."


def uninstall(key):
    _, text = read_raw(key)
    if not has_hooks(parse_settings(text)):
        return f"No Claude Pet hooks found in {describe(key)}."
    _apply(key, remove_hooks, "before remove")
    return f"Claude Pet hooks removed from {describe(key)}.\nA backup was taken first."


# --------------------------------------------------------------------------- WSL helpers
def _decode_wsl_list(raw):
    # wsl.exe --list writes UTF-16LE
    text = raw.decode("utf-16-le", "ignore") if b"\x00" in raw else raw.decode("utf-8", "ignore")
    return text.replace("\x00", "")


def parse_wsl_verbose(text):
    """Parse `wsl -l -v` -> [(name, state)]."""
    out = []
    for line in text.splitlines()[1:]:
        parts = line.replace("*", " ", 1).split()
        if len(parts) >= 3:
            name, state = " ".join(parts[:-2]), parts[-2]
            if name.lower() not in IGNORED_DISTROS:
                out.append((name, state))
    return out


def list_distros():
    """Return [(name, state)], e.g. [("Ubuntu", "Running"), ("Debian", "Stopped")]. Never boots a distro."""
    if os.name != "nt":
        return []
    try:
        res = parse_wsl_verbose(_decode_wsl_list(_wsl(["--list", "--verbose"], timeout=15).stdout))
        if res:
            return res
        names = _decode_wsl_list(_wsl(["--list", "--quiet"], timeout=15).stdout).splitlines()
        return [(n.strip(), "Unknown") for n in names if n.strip() and n.strip().lower() not in IGNORED_DISTROS]
    except (OSError, subprocess.TimeoutExpired):
        return []


def _wslpath(distro, win_path):
    p = _wsl(["-d", distro, "--exec", "wslpath", "-u", win_path], timeout=60)
    out = p.stdout.decode("utf-8", "replace").strip()
    if p.returncode != 0 or not out:
        raise RuntimeError(f"Couldn't map {win_path} into WSL '{distro}' (is /mnt/c mounted?)")
    return out


def _has_python(distro):
    rc, out, _ = _sh(distro, "command -v python3 >/dev/null 2>&1 && echo yes || echo no")
    return rc == 0 and out.strip().endswith("yes")


def wsl_hook_command(distro):
    pet_dir = _wslpath(distro, PET_DIR)
    script = _wslpath(distro, os.path.join(INSTALL_DIR, "claude_pet_hook.py"))
    return f"CLAUDE_PET_DIR={shlex.quote(pet_dir)} python3 {shlex.quote(script)}"


# --------------------------------------------------------------------------- detection for the setup window
def detect_windows():
    """True if Claude Code looks present on this PC: config folder, CLI on PATH, or the VS Code extension."""
    if os.path.isdir(os.path.join(HOME, ".claude")) or shutil.which("claude"):
        return True
    try:
        return any(n.lower().startswith("anthropic.claude-code")
                   for n in os.listdir(os.path.join(HOME, ".vscode", "extensions")))
    except OSError:
        return False


def probe_wsl(distro):
    """One wsl call -> {"python": bool, "claude": bool}. Only call this for a distro that is already running
    (or that the user agreed to start)."""
    rc, out, err = _sh(distro, 'p=no; c=no; command -v python3 >/dev/null 2>&1 && p=yes; '
                               '{ [ -d "$HOME/.claude" ] || [ -x "$HOME/.local/bin/claude" ] || '
                               'command -v claude >/dev/null 2>&1; } && c=yes; echo "python=$p claude=$c"', timeout=60)
    if rc != 0:
        raise RuntimeError(f"WSL '{distro}' didn't respond: {err.strip()[:200]}")
    return {"python": "python=yes" in out, "claude": "claude=yes" in out}


def _kind(st):
    if st == STALE:
        return "outdated"
    if st.startswith("installed"):
        return "installed"
    if st == "not installed":
        return "ready"
    return "blocked"  # settings.json unreadable / target unreachable


def detect_targets(check_stopped=False):
    """Targets for the setup window -> (targets, hidden_count).

    Each target is {key, label, kind, note}; kind is one of
      ready        Claude Code found, hooks not installed
      installed    hooks installed (and up to date, when this build can tell)
      outdated     hooks installed but pointing at an old location or missing events
      blocked      settings.json can't be read as JSON, so nothing would be changed
      needs_python WSL distro has Claude Code but no python3
      unknown      WSL distro not running; not probed so it is never started without asking
      missing      Claude Code not found (Windows only)
    WSL distros without Claude Code are left out and counted in hidden_count.
    """
    targets, hidden = [], 0
    if IS_MAC:
        if detect_windows():
            targets.append({"key": "mac", "label": "This Mac", "kind": _kind(status("mac")), "python": find_mac_python(),
                            "note": "terminal, VS Code and the Claude desktop app's Code tab"})
        else:
            targets.append({"key": "mac", "label": "This Mac", "kind": "missing", "note": "Claude Code not found"})
    elif os.name == "nt":
        label = "This PC (Windows)"
        if detect_windows():
            targets.append({"key": "windows", "label": label, "kind": _kind(status("windows")),
                            "note": "terminal, VS Code and the Claude desktop app's Code tab"})
        else:
            targets.append({"key": "windows", "label": label, "kind": "missing", "note": "Claude Code not found"})
    for name, state in list_distros():
        key, label = "wsl:" + name, f"WSL: {name}"
        if state.lower() != "running" and not check_stopped:
            targets.append({"key": key, "label": label, "kind": "unknown",
                            "note": "not running - can't check for Claude Code without starting it"})
            continue
        try:
            info = probe_wsl(name)
        except Exception:
            targets.append({"key": key, "label": label, "kind": "unknown", "note": "didn't respond"})
            continue
        if not info["claude"]:
            hidden += 1
        elif not info["python"]:
            targets.append({"key": key, "label": label, "kind": "needs_python",
                            "note": "Claude Code found, but python3 is missing (sudo apt install python3)"})
        else:
            targets.append({"key": key, "label": label, "kind": _kind(status(key)),
                            "note": "terminal and VS Code Remote-WSL"})
    return targets, hidden


# --------------------------------------------------------------------------- Claude desktop app (Cowork) + CLI plugin
# Cowork runs its own Claude Code with a private CLAUDE_CONFIG_DIR, so it never reads ~/.claude/settings.json.
# Its hooks only come from plugins, and plugins can only be added from the Claude app's UI (no file to write to),
# so the pet builds a ready-made plugin zip the user uploads. Tested: Cowork runs plugin hooks on the host
# (Windows), not in its Linux sandbox, so the same hook command as settings.json works there.
PLUGIN_NAME = "pet-hooks"  # names starting with "claude-" are reserved for Anthropic's own plugins
PLUGIN_MARKET = "desktop-pet-local"
PLUGIN_VERSION = "1.0.0"
COWORK_ZIP = "claude-pet-cowork-plugin.zip"
PLUGIN_MARKET_DIR = os.path.join(PET_DIR, "plugin-marketplace")  # stable path for `claude plugin marketplace add`
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)  # fixed timestamps -> identical bytes for identical content


def app_dir():
    """Folder holding ClaudePet.exe (or this script when running from source)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def local_hook_command():
    return mac_hook_command() if IS_MAC else windows_hook_command()


def plugin_hooks(command):
    """hooks.json for the plugin: the same events, matchers and timeouts as the settings.json install."""
    hooks = {}
    for event, matcher in HOOK_EVENTS:
        entry = {"hooks": [{"type": "command", "command": command,
                            **({"timeout": HOOK_TIMEOUTS[event]} if event in HOOK_TIMEOUTS else {})}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        hooks[event] = [entry]
    return {"hooks": hooks}


def plugin_files(command):
    """{relative path: bytes} of the plugin's root folder."""
    readme = (
        "Claude Pet hooks for the Claude desktop app (Cowork) and the Claude Code CLI.\n\n"
        "Generated by Claude Pet for this computer: the hooks call the Claude Pet hook installed in\n"
        f"{PET_DIR}. Regenerate it from Claude Pet (Claude Code hooks > Cowork) if that moves.\n\n"
        "Don't use it in the same place as Claude Pet's settings.json hooks, or every event reaches the pet twice.\n")
    return {
        ".claude-plugin/plugin.json": dump({
            "name": PLUGIN_NAME, "version": PLUGIN_VERSION,
            "description": "Shows your Claude sessions on the Claude Pet desktop companion.",
            "author": {"name": "Claude Pet"}}).encode(),
        "hooks/hooks.json": dump(plugin_hooks(command)).encode(),
        "README.md": readme.encode(),
    }


def _write_if_changed(path, data):
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _zip_bytes(files):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(files):
            info = zipfile.ZipInfo(rel, _ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, files[rel])
    return buf.getvalue()


def build_plugin():
    """Deploy the hook, then write the Cowork zip next to the exe (or to ~/.claude-pet if that folder is read-only)
    and a local marketplace for the CLI. Returns {"zip": path, "market": dir, "command": hook command}.
    Files are only rewritten when their content changes."""
    deploy_files()
    command = local_hook_command()
    files = plugin_files(command)
    data = _zip_bytes(files)
    zip_path, err = None, None
    for d in (app_dir(), PET_DIR):
        try:
            _write_if_changed(os.path.join(d, COWORK_ZIP), data)
            zip_path = os.path.join(d, COWORK_ZIP)
            break
        except OSError as e:
            err = e
    if not zip_path:
        raise RuntimeError(f"Couldn't write {COWORK_ZIP}: {err}")
    market = {".claude-plugin/marketplace.json": dump({
        "name": PLUGIN_MARKET, "owner": {"name": "Claude Pet"},
        "metadata": {"description": "Local marketplace generated by Claude Pet for this computer."},
        "plugins": [{"name": PLUGIN_NAME, "source": "./" + PLUGIN_NAME,
                     "description": "Shows your Claude sessions on the Claude Pet desktop companion."}]}).encode()}
    market.update({f"{PLUGIN_NAME}/{rel}": b for rel, b in files.items()})
    for rel, b in market.items():
        _write_if_changed(os.path.join(PLUGIN_MARKET_DIR, *rel.split("/")), b)
    return {"zip": zip_path, "market": PLUGIN_MARKET_DIR, "command": command}


def cli_plugin_commands(market_dir=PLUGIN_MARKET_DIR):
    return [f'claude plugin marketplace add "{market_dir}"',
            f"claude plugin install {PLUGIN_NAME}@{PLUGIN_MARKET}"]
