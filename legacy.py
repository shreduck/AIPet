"""Compatibility exports for legacy migration; owners live in aipetlib."""
from aipetlib.integrations.legacy import (
    COPY_DIRS, COPY_FILES, HOME, IS_MAC, LEGACY_DIR, LEGACY_HOOK, LEGACY_LAUNCH_AGENT, LEGACY_MUTEX, LEGACY_RUN_VALUE, MARKER, NEW_DIR, NO_WINDOW, RUN_KEY, UI_FONT, WARNED, _hooks_files, _old_autostart, _remove_old_autostart, close_old, due_today, findings, legacy_plugins, mark_done, mark_warned, migrate, needs_migration, old_running, plan, uses_legacy_hook,
)
from aipetlib.ui.legacy_migration import migration_window
