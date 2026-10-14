"""Stage 0 migration checks (restructure.md): the architecture rules hold, and importing a module has no side effects.

Each import runs in a fresh interpreter with a throwaway home folder, so a module that starts a thread, creates a Tk
root or writes under ~/.aipet at import time is caught. Modules that need Tk are skipped when Tk is unavailable.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import check_architecture  # noqa: E402

GUI = ("tkinter", "_tkinter", "PIL", "pystray", "pyvda")
PROBE = r"""
import importlib, json, sys, threading
before = set(sys.modules)
importlib.import_module(sys.argv[1])
tk = sys.modules.get("tkinter")
print(json.dumps({
    "gui": sorted(m for m in set(sys.modules) - before if m.split(".")[0] in %r),
    "entry": sorted(m for m in sys.modules if m in %r),
    "threads": threading.active_count(),
    "tk_root": bool(tk is not None and getattr(tk, "_default_root", None) is not None),
}))
""" % (GUI, tuple(sorted(check_architecture.ENTRY_MODULES)))


def all_modules():
    names = []
    for path in sorted(list((ROOT / "aipetlib").rglob("*.py")) + list((ROOT / "aipet_hook_runtime").rglob("*.py"))):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        names.append(".".join(parts))
    return names


def headless(name):
    return any(name == p or name.startswith(p + ".") for p in check_architecture.HEADLESS)


def probe(name):
    with tempfile.TemporaryDirectory() as home:
        env = dict(os.environ, HOME=home, USERPROFILE=home, PYTHONDONTWRITEBYTECODE="1")
        done = subprocess.run([sys.executable, "-c", PROBE, name], cwd=str(ROOT), env=env, capture_output=True,
                              text=True, timeout=60)
        written = sorted(str(p.relative_to(home)) for p in Path(home).rglob("*"))
    return done, written


class ArchitectureTests(unittest.TestCase):
    def test_rules_and_ratchets_hold(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check_architecture.main([])
        self.assertEqual(code, 0, out.getvalue())


class ImportSideEffectTests(unittest.TestCase):
    def check(self, name):
        done, written = probe(name)
        self.assertEqual(done.returncode, 0, f"import {name} failed:\n{done.stderr[-2000:]}")
        report = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(written, [], f"import {name} wrote files under the home folder")
        self.assertEqual(report["threads"], 1, f"import {name} started a thread")
        self.assertFalse(report["tk_root"], f"import {name} created a Tk root")
        return report

    def test_headless_modules_import_without_gui_or_app(self):
        for name in filter(headless, all_modules()):
            with self.subTest(module=name):
                report = self.check(name)
                self.assertEqual(report["gui"], [], f"{name} imported a GUI toolkit")
                self.assertEqual(report["entry"], [], f"{name} imported an entry point")

    def test_ui_modules_import_without_side_effects(self):
        if importlib.util.find_spec("tkinter") is None:
            self.skipTest("Tk is not available")
        for name in all_modules():
            if headless(name):
                continue
            if name.rpartition(".")[2].startswith("mac_") and sys.platform != "darwin":
                continue  # loads the Objective-C runtime at import; only imported on macOS
            with self.subTest(module=name):
                self.check(name)


if __name__ == "__main__":
    unittest.main()
