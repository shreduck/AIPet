"""Setup target discovery; stopped WSL distros are not started implicitly."""
import os
import shutil
import sys
from . import commands as hook_commands
from . import installer as hook_installer
from . import locations as hook_locations
from . import protocol as hook_protocol
from . import targets as hook_targets

IS_MAC = sys.platform == "darwin"


def detect_windows():
    """True if Claude Code looks present on this PC: config folder, CLI on PATH, or the VS Code extension."""
    if os.path.isdir(os.path.join(hook_locations.HOME, ".claude")) or shutil.which("claude"):
        return True
    try:
        return any(n.lower().startswith("anthropic.claude-code")
                   for n in os.listdir(os.path.join(hook_locations.HOME, ".vscode", "extensions")))
    except OSError:
        return False


def probe_wsl(distro):
    """One wsl call -> {"python": bool, "claude": bool}. Only call this for a distro that is already running
    (or that the user agreed to start)."""
    rc, out, err = hook_targets._sh(distro, 'p=no; c=no; x=no; command -v python3 >/dev/null 2>&1 && p=yes; '
                               '{ [ -d "$HOME/.claude" ] || [ -x "$HOME/.local/bin/claude" ] || '
                               'command -v claude >/dev/null 2>&1; } && c=yes; '
                               '{ [ -d "${CODEX_HOME:-$HOME/.codex}" ] || command -v codex >/dev/null 2>&1; } && x=yes; '
                               'echo "python=$p claude=$c codex=$x"', timeout=60)
    if rc != 0:
        raise RuntimeError(f"WSL '{distro}' didn't respond: {err.strip()[:200]}")
    return {"python": "python=yes" in out, "claude": "claude=yes" in out, "codex": "codex=yes" in out}


def detect_codex_local():
    """True if Codex looks present on this machine: its home folder or the CLI on PATH."""
    return os.path.isdir(hook_targets.codex_home()) or bool(shutil.which("codex"))


def codex_in_wsl(distro):
    """Quick check for a running distro (used by the menus)."""
    rc, out, _ = hook_targets._sh(distro, '{ [ -d "${CODEX_HOME:-$HOME/.codex}" ] || command -v codex >/dev/null 2>&1; } '
                             '&& echo yes || echo no', timeout=30)
    return rc == 0 and out.strip().endswith("yes")


def _kind(st):
    if st == hook_protocol.STALE:
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
            targets.append({"key": "mac", "label": "This Mac", "kind": _kind(hook_installer.status("mac")), "python": hook_commands.find_mac_python(),
                            "note": "terminal, VS Code and the Claude desktop app's Code tab"})
        else:
            targets.append({"key": "mac", "label": "This Mac", "kind": "missing", "note": "Claude Code not found"})
    elif os.name == "nt":
        label = "This PC (Windows)"
        if detect_windows():
            targets.append({"key": "windows", "label": label, "kind": _kind(hook_installer.status("windows")),
                            "note": "terminal, VS Code and the Claude desktop app's Code tab"})
        else:
            targets.append({"key": "windows", "label": label, "kind": "missing", "note": "Claude Code not found"})
    if (os.name == "nt" or IS_MAC) and detect_codex_local():
        key = "codex:" + hook_targets.LOCAL
        targets.append({"key": key, "label": "Codex - " + ("This Mac" if IS_MAC else "This PC (Windows)"),
                        "kind": _kind(hook_installer.status(key)), "note": CODEX_NOTE})
    for name, state in hook_targets.list_distros():
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
        if not info["claude"] and not info.get("codex"):
            hidden += 1
            continue
        for agent, present in (("Claude Code", info["claude"]), ("Codex", info.get("codex"))):
            if not present:
                continue
            k = key if agent == "Claude Code" else "codex:" + key
            lab = label if agent == "Claude Code" else f"Codex - {label}"
            if not info["python"]:
                targets.append({"key": k, "label": lab, "kind": "needs_python",
                                "note": f"{agent} found, but python3 is missing (sudo apt install python3)"})
            else:
                targets.append({"key": k, "label": lab, "kind": _kind(hook_installer.status(k)),
                                "note": "terminal and VS Code Remote-WSL" if agent == "Claude Code" else CODEX_NOTE})
    return targets, hidden


CODEX_NOTE = "Codex CLI. After installing, run /hooks in Codex once and trust the AIPet hooks."
