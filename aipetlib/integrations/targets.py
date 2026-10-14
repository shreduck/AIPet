"""Local and WSL target keys, raw configuration access and distro enumeration."""
import os
import subprocess
import sys
from . import locations as hook_locations

IS_MAC = sys.platform == "darwin"
LOCAL = "mac" if IS_MAC else "windows"

def is_codex(key):
    return key.startswith("codex:")


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


IGNORED_DISTROS = {"docker-desktop", "docker-desktop-data", "rancher-desktop", "rancher-desktop-data"}


def _base(key):
    return key[6:] if is_codex(key) else key


def is_local(key):
    return _base(key) in ("windows", "mac")


def _distro(key):
    return _base(key)[4:]


def codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


WSL_CODEX_FILE = '"${CODEX_HOME:-$HOME/.codex}/hooks.json"'


WSL_CLAUDE_FILE = '"$HOME/.claude/settings.json"'


def _sh(distro, script, input_bytes=None, timeout=90):
    p = _wsl(["-d", distro, "--exec", "sh", "-c", script], input_bytes, timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _wsl(args, input_bytes=None, timeout=60):
    return subprocess.run(["wsl.exe", *args], input=input_bytes, capture_output=True,
                          timeout=timeout, creationflags=NO_WINDOW)


def local_file(key):
    return os.path.join(codex_home(), "hooks.json") if is_codex(key) else hook_locations.WINDOWS_SETTINGS


def describe(key):
    if is_local(key):
        return local_file(key)
    return f"{'~/.codex/hooks.json' if is_codex(key) else '~/.claude/settings.json'} in WSL '{_distro(key)}'"


def read_raw(key):
    """Return (exists, text) of the target's settings.json, byte-exact."""
    if is_local(key):
        try:
            with open(local_file(key), "rb") as f:
                return True, f.read().decode("utf-8")
        except FileNotFoundError:
            return False, ""
    distro = _distro(key)
    rc, out, err = _sh(distro, f'f={WSL_CODEX_FILE if is_codex(key) else WSL_CLAUDE_FILE}; '
                               'if [ -f "$f" ]; then printf "EXISTS\\n"; cat "$f"; else printf "ABSENT\\n"; fi')
    if rc != 0 or "\n" not in out:
        raise RuntimeError(f"WSL '{distro}' didn't respond: {err.strip()[:200]}")
    flag, text = out.split("\n", 1)
    return flag.strip() == "EXISTS", text


def write_raw(key, text):
    """Write text to the target's settings.json; text=None deletes the file."""
    if is_local(key):
        target = local_file(key)
        if text is None:
            try:
                os.remove(target)
            except FileNotFoundError:
                pass
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "wb") as f:
            f.write(text.encode("utf-8"))
        os.replace(tmp, target)
        return
    distro = _distro(key)
    f = WSL_CODEX_FILE if is_codex(key) else WSL_CLAUDE_FILE
    if text is None:
        rc, _, err = _sh(distro, f'rm -f {f}')
    else:
        rc, _, err = _sh(distro, f'f={f}; mkdir -p "$(dirname "$f")" && cat > "$f.tmp" && mv "$f.tmp" "$f"',
                         text.encode("utf-8"))
    if rc != 0:
        raise RuntimeError(f"Writing settings in WSL '{distro}' failed: {err.strip()[:200]}")


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
