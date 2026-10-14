"""Platform command quoting and macOS Python discovery."""
import os
import re
import shlex
import shutil
import subprocess
import sys
from . import locations as hook_locations
from . import protocol as hook_protocol
from . import targets as hook_targets

IS_MAC = sys.platform == "darwin"


def fwd(path):
    return path.replace("\\", "/")


def windows_hook_command(check=True):
    if getattr(sys, "frozen", False):
        exe = os.path.join(hook_locations.INSTALL_DIR, "hook", "aipet-hook.exe")
        if check and not os.path.exists(exe):
            raise RuntimeError("Bundled hook executable is missing - rebuild with build.bat.")
        return f'"{fwd(exe)}"'
    # running from source: use pythonw so no console window flashes
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    return f'"{fwd(pyw)}" "{fwd(os.path.join(hook_locations.INSTALL_DIR, "aipet_hook.py"))}"'


def _shell_neutral(path):
    """path with forward slashes and no spaces, so it runs the same unquoted in Git Bash and in PowerShell (quoting
    differs: PowerShell treats a quoted path followed by arguments as a string, not a command). Folders with spaces are
    replaced by their 8.3 short names; the file name is kept, so the command still says aipet-hook / aipet_hook.
    None if a space remains (short names switched off on that drive)."""
    folder, name = os.path.split(path)
    if " " in folder:
        folder = _short_path(folder)
    result = fwd(os.path.join(folder, name))
    return None if any(ch.isspace() for ch in result) else result


def windows_statusline_command(check=True):
    """The hook command for Claude Code's status line on Windows. Claude Code runs status line commands through Git
    Bash, or through PowerShell when Git Bash is absent, so the command must work in both: unquoted, forward slashes,
    no spaces. Falls back to the quoted hook command when Git Bash is installed, and refuses otherwise."""
    if getattr(sys, "frozen", False):
        exe = os.path.join(hook_locations.INSTALL_DIR, "hook", "aipet-hook.exe")
        if check and not os.path.exists(exe):
            raise RuntimeError("Bundled hook executable is missing - rebuild with build.bat.")
        paths = [exe]
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        paths = [pyw if os.path.exists(pyw) else sys.executable, os.path.join(hook_locations.INSTALL_DIR, "aipet_hook.py")]
    neutral = [_shell_neutral(p) for p in paths]
    if all(neutral):
        return " ".join(neutral)
    from aipet_hook_runtime.hosts import find_statusline_bash
    if find_statusline_bash():
        return windows_hook_command(check)  # quoted: fine, Claude Code uses Git Bash here
    raise RuntimeError("The path to AIPet's hook contains spaces and Git Bash isn't installed, so Claude Code would run "
                       "the status line through PowerShell, where it can't start. Install Git Bash, or switch on short "
                       "file names for that drive. Your settings were kept.")


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
    return f"{shlex.quote(py)} {shlex.quote(os.path.join(hook_locations.INSTALL_DIR, 'aipet_hook.py'))}"


def _mac_builtin_cmd():
    return shlex.quote(os.path.join(hook_locations.INSTALL_DIR, "hook", "aipet-hook"))


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
    if not os.path.exists(os.path.join(hook_locations.INSTALL_DIR, "hook", "aipet-hook")):
        raise RuntimeError("The built-in hook isn't part of this build.\nInstall Python 3 and choose it instead.")
    return _mac_builtin_cmd()


def _expected_command(key):
    if hook_targets.is_codex(key):
        if hook_targets._base(key) == "windows":
            return codex_windows_command(check=False)
        if hook_targets._base(key) == "mac":
            return {c + " " + hook_protocol.CODEX_FLAG for c in mac_hook_commands()}
        return wsl_hook_command(hook_targets._distro(key)) + " " + hook_protocol.CODEX_FLAG
    if key == "windows":
        return windows_hook_command(check=False)
    if key == "mac":
        return mac_hook_commands()
    return wsl_hook_command(key[4:])


def codex_windows_command(check=True):
    """The hook command for Codex on Windows. Codex's docs don't say which shell runs hook commands there, so use a
    form every shell runs as-is: an unquoted path with backslashes (a quoted path is only a string in PowerShell).
    Folders with spaces get their 8.3 short name; the file name is kept so the hook stays recognisable."""
    cmd = windows_hook_command(check)
    parts = [p.strip('"') for p in re.findall(r'"[^"]*"|\S+', cmd)]
    out = []
    for p in parts:
        p = p.replace("/", "\\")
        if " " in p:
            p = os.path.join(_short_path(os.path.dirname(p)), os.path.basename(p))
        out.append(p)
    return " ".join(out + [hook_protocol.CODEX_FLAG])


def _short_path(path):
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024):
            return buf.value
    except Exception:
        pass
    return path


def wsl_hook_command(distro):
    pet_dir = hook_targets._wslpath(distro, hook_locations.PET_DIR)
    script = hook_targets._wslpath(distro, os.path.join(hook_locations.INSTALL_DIR, "aipet_hook.py"))
    return f"AIPET_DIR={shlex.quote(pet_dir)} python3 {shlex.quote(script)}"


def local_hook_command():
    return mac_hook_command() if IS_MAC else windows_hook_command()
