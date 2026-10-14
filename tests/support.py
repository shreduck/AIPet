"""Shared test helpers (not a test module)."""
import contextlib
import importlib
from unittest.mock import patch
import os
import tempfile
from pathlib import Path

# Modules that hold AIPet's path names (HOME_DIR, CONFIG_PATH, AUTO_APPROVE_PATH, SESSIONS_DIR, LEGACY_SESSIONS_DIR).
# The owner is aipetlib.runtime.paths; others keep a copy of a name while code still reads it there.
PATH_MODULES = ("aipetlib.runtime.paths", "aipet", "aipetlib.permissions", "aipetlib.sessions")


class paths:
    """Point every module holding one of these path names at test values, so a test never reads or writes the real
    ~/.aipet, wherever the code under test reads the path from. A context manager, or start() / stop() like patch."""

    def __init__(self, **values):
        self.values, self.stack = values, None

    def start(self):
        self.stack = contextlib.ExitStack()
        for name in PATH_MODULES:
            module = importlib.import_module(name)
            for key, value in self.values.items():
                if hasattr(module, key):
                    self.stack.enter_context(patch.object(module, key, value))
        return self

    def stop(self):
        self.stack.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


@contextlib.contextmanager
def installation():
    """Keep every installer destination inside one temporary home, even if an operation mock is missed."""
    from aipetlib.integrations import locations
    with tempfile.TemporaryDirectory(prefix="aipet-installer-test-") as directory, contextlib.ExitStack() as stack:
        root = Path(directory)
        values = dict(HOME=str(root), PET_DIR=str(root / '.aipet'), INSTALL_DIR=str(root / '.aipet/bin'),
                      WINDOWS_SETTINGS=str(root / '.claude/settings.json'), BACKUP_ROOT=str(root / '.aipet/backups'),
                      PLUGIN_MARKET_DIR=str(root / '.aipet/plugin-marketplace'))
        for name, value in values.items():
            stack.enter_context(patch.object(locations, name, value))
        stack.enter_context(patch.object(locations, 'app_dir', return_value=str(root)))
        stack.enter_context(patch.dict(os.environ, CODEX_HOME=str(root / '.codex')))
        yield root
