"""Hook and optional usage installation orchestration."""
import json
import os
import re
import sys
from . import backups as hook_backups
from . import commands as hook_commands
from . import deployment as hook_deployment
from . import locations as hook_locations
from . import protocol as hook_protocol
from . import settings as hook_settings
from . import targets as hook_targets

IS_MAC = sys.platform == "darwin"


def usage_record_path(key):
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", key)
    return os.path.join(hook_locations.PET_DIR, "statuslines", name + ".json")


def usage_record(key):
    try:
        with open(usage_record_path(key), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def usage_enabled(key):
    try:
        _, text = hook_targets.read_raw(key)
        status = hook_settings.parse_settings(text).get("statusLine")
        return isinstance(status, dict) and bool(hook_protocol.MARKER.search(str(status.get("command", "")))) and "--usage" in status.get("command", "")
    except Exception:
        return False


def set_usage(key, on):
    if hook_targets.is_codex(key):
        raise ValueError("The status-line collector is for Claude Code only")
    _, text = hook_targets.read_raw(key)
    settings = hook_settings.parse_settings(text)
    record = usage_record(key)
    if on:
        hook_deployment.deploy_files()
        command = hook_commands.windows_statusline_command() if key == "windows" else (
            hook_commands.mac_hook_command() if key == "mac" else hook_commands.wsl_hook_command(hook_targets._distro(key)))
        previous = settings.get("statusLine")
        if usage_enabled(key):
            previous = (previous or {}).get("aipet_previous", record.get("previous"))
        if previous and (not isinstance(previous, dict) or previous.get("type") != "command"):
            raise RuntimeError("This status line type cannot be safely wrapped. Your settings were kept.")
        if previous and hook_targets.is_local(key):
            from aipet_hook_runtime.hosts import find_statusline_bash
            if not find_statusline_bash():
                raise RuntimeError("Bash was not found. Install Git Bash before enabling a collector with an existing status line.")
        result = hook_settings.merge_usage(settings, command, previous=previous)
        path = usage_record_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"previous": previous, "installed": result.get("statusLine")}, f, indent=2)
        os.replace(path + ".tmp", path)
        _apply(key, lambda s: result, "before enabling usage status line")
    else:
        _apply(key, lambda s: hook_settings.remove_usage(s, record.get("previous"), record.get("installed")), "before restoring status line")
    return "Claude usage status-line collector " + ("enabled" if on else "disabled") + ". Restart Claude Code to apply."


def migrate_legacy_usage(key):
    _, text = hook_targets.read_raw(key)
    status = hook_settings.parse_settings(text).get("statusLine")
    if isinstance(status, dict) and "aipet_previous" in status and usage_enabled(key):
        return _apply(key, hook_settings.remove_usage, "restore status line changed by v0.3.1")
    return False


def _apply(key, transform, reason):
    """Read -> transform -> backup -> write, skipping everything if nothing changes."""
    exists, text = hook_targets.read_raw(key)
    new_text = hook_settings.dump(transform(hook_settings.parse_settings(text)))
    if exists and new_text == text:
        return False
    hook_backups._save_backup(key, exists, text, reason)
    hook_targets.write_raw(key, new_text)
    return True


def status(key):
    try:
        if not hook_targets.is_local(key) and not hook_targets._has_python(hook_targets._distro(key)):
            return "needs python3"
        _, text = hook_targets.read_raw(key)
        settings = hook_settings.parse_settings(text)
        if not hook_settings.has_hooks(settings):
            return "not installed"
        if getattr(sys, "frozen", False):  # a source run would use a different command; don't flag that
            try:
                if hook_settings.hooks_state(settings, hook_commands._expected_command(key), *hook_protocol.events_for(key)) != "current":
                    return hook_protocol.STALE
            except Exception:
                pass
        return "installed"
    except json.JSONDecodeError:
        return "settings.json unreadable"
    except Exception:
        return "unreachable"


def install(key, runtime=None):
    """runtime only matters on macOS: "python" or "builtin" (see mac_hook_command)."""
    hook_deployment.deploy_files()
    if hook_targets.is_codex(key):
        return _install_codex(key, runtime)
    if key == "windows":
        command = hook_commands.windows_hook_command()
        where = "Claude Code on Windows (terminal + VS Code)"
    elif key == "mac":
        command = hook_commands.mac_hook_command(runtime)
        where = "Claude Code on this Mac (terminal + VS Code)"
    else:
        distro = key[4:]
        if not hook_targets._has_python(distro):
            raise RuntimeError(f"python3 isn't installed in WSL '{distro}'.\n"
                               f"Install it there (e.g. sudo apt install python3) and try again.")
        command = hook_commands.wsl_hook_command(distro)
        where = f"Claude Code in WSL '{distro}' (terminal + VS Code Remote-WSL)"
    changed = _apply(key, lambda s: hook_settings.merge_hooks(s, command), "before install")
    if not changed:
        return f"Hooks for {where} are already up to date."
    return f"Hooks installed for {where}.\nA backup was taken first. New sessions will appear in the pet."


def _install_codex(key, runtime=None):
    base = hook_targets._base(key)
    if base == "windows":
        command, where = hook_commands.codex_windows_command(), "Codex on Windows"
    elif base == "mac":
        command, where = hook_commands.mac_hook_command(runtime) + " " + hook_protocol.CODEX_FLAG, "Codex on this Mac"
    else:
        distro = hook_targets._distro(key)
        if not hook_targets._has_python(distro):
            raise RuntimeError(f"python3 isn't installed in WSL '{distro}'.\n"
                               f"Install it there (e.g. sudo apt install python3) and try again.")
        command, where = hook_commands.wsl_hook_command(distro) + " " + hook_protocol.CODEX_FLAG, f"Codex in WSL '{distro}'"
    events, timeouts = hook_protocol.events_for(key)
    changed = _apply(key, lambda s: hook_settings.merge_hooks(s, command, events, timeouts), "before install")
    trust = ("\n\nOne more step in Codex: hooks only run once you trust them. Start Codex, type /hooks, and trust "
             "the AIPet hooks (Codex asks again only if they change).")
    if not changed:
        return f"Codex hooks for {where} are already up to date." + trust
    return f"Hooks installed for {where} ({hook_targets.describe(key)}).\nA backup was taken first." + trust


def uninstall(key):
    _, text = hook_targets.read_raw(key)
    if not hook_settings.has_hooks(hook_settings.parse_settings(text)):
        return f"No AIPet hooks found in {hook_targets.describe(key)}."
    record = usage_record(key)
    _apply(key, lambda s: hook_settings.remove_hooks(s, record.get("previous"), record.get("installed")), "before remove")
    return f"AIPet hooks removed from {hook_targets.describe(key)}.\nA backup was taken first."
