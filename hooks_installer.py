"""Compatibility exports for the installer; internal callers use aipetlib.integrations owners."""
from aipetlib.integrations.locations import (
    BACKUP_ROOT, HOME, INSTALL_DIR, PET_DIR, PLUGIN_MARKET_DIR, WINDOWS_SETTINGS, app_dir, resource_path,
)
from aipetlib.integrations.protocol import (
    CODEX_FLAG, CODEX_HOOK_EVENTS, CODEX_HOOK_TIMEOUTS, HOOK_EVENTS, HOOK_TIMEOUTS, MARKER, STALE, events_for,
)
from aipetlib.integrations.targets import (
    IGNORED_DISTROS, IS_MAC, LOCAL, NO_WINDOW, WSL_CLAUDE_FILE, WSL_CODEX_FILE, _base, _decode_wsl_list, _distro, _has_python, _sh, _wsl, _wslpath, codex_home, describe, is_codex, is_local, list_distros, local_file, parse_wsl_verbose, read_raw, write_raw,
)
from aipetlib.integrations.settings import (
    _strip_ours, dump, has_hooks, hooks_state, merge_hooks, merge_usage, parse_settings, remove_hooks, remove_usage,
)
from aipetlib.integrations.deployment import (
    _deploy_shared_hook, _same_file, _sync_file, deploy_files,
)
from aipetlib.integrations.commands import (
    MAC_PYTHON_CANDIDATES, MAC_STUB_PYTHON, _expected_command, _mac_builtin_cmd, _mac_script_cmd, _shell_neutral, _short_path, clt_installed, codex_windows_command, find_mac_python, fwd, local_hook_command, mac_hook_command, mac_hook_commands, windows_hook_command, windows_statusline_command, wsl_hook_command,
)
from aipetlib.integrations.backups import (
    KEEP_BACKUPS, _backup_files, _prune, _save_backup, backup_dir, legacy_dirs, list_backups, make_backup, migrate_legacy_backups, restore_backup,
)
from aipetlib.integrations.installer import (
    _apply, _install_codex, install, migrate_legacy_usage, set_usage, status, uninstall, usage_enabled, usage_record, usage_record_path,
)
from aipetlib.integrations.discovery import (
    CODEX_NOTE, _kind, codex_in_wsl, detect_codex_local, detect_targets, detect_windows, probe_wsl,
)
from aipetlib.integrations.cowork import (
    COWORK_ZIP, PLUGIN_MARKET, PLUGIN_NAME, PLUGIN_VERSION, _ZIP_TIME, _write_if_changed, _zip_bytes, build_plugin, cli_plugin_commands, plugin_files, plugin_hooks,
)
