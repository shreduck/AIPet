import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hooks_installer as hi
from tools.share_mac_runtime import share_runtime


class MacRuntimeSharingTests(unittest.TestCase):
    def test_library_and_framework_layouts_deploy_independently(self):
        for relative in ('libpython3.12.dylib', 'Python.framework/Versions/3.12/Python'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = root / 'AIPet.app'
                frameworks = bundle / 'Contents/Frameworks'
                app_python = frameworks / relative
                hook_python = frameworks / 'hook/_internal' / relative
                for file in (app_python, hook_python):
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(b'same Python runtime')
                self.assertEqual(share_runtime(bundle), len(b'same Python runtime'))
                shared = frameworks / 'hook/_internal/Python.framework' if relative.startswith('Python.framework') else hook_python
                self.assertTrue(shared.is_symlink())
                self.assertFalse(shared.readlink().is_absolute())
                self.assertEqual(hook_python.resolve(), app_python.resolve())
                self.assertEqual(share_runtime(bundle), 0)  # safe to repeat
                for name in ('aipet_hook.py', 'aipet_usage.py', 'aipet_claude_usage.py'):
                    (frameworks / name).write_text('# test')
                target = root / 'deployed'
                with patch.object(hi, 'resource_path', lambda *parts: str(frameworks.joinpath(*parts))), \
                        patch.object(hi, 'INSTALL_DIR', str(target)), patch.object(hi, 'PET_DIR', str(root / 'config')):
                    hi.deploy_files()
                installed = target / 'hook/_internal' / relative
                self.assertFalse(installed.is_symlink())
                app_python.unlink()
                self.assertEqual(installed.read_bytes(), b'same Python runtime')

    def test_different_runtime_is_never_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frameworks = root / 'Contents/Frameworks'
            internal = frameworks / 'hook/_internal'
            internal.mkdir(parents=True)
            (frameworks / 'libpython3.12.dylib').write_bytes(b'app')
            duplicate = internal / 'libpython3.12.dylib'
            duplicate.write_bytes(b'hook')
            with self.assertRaisesRegex(RuntimeError, 'differ'):
                share_runtime(root)
            self.assertFalse(duplicate.is_symlink())
            self.assertEqual(duplicate.read_bytes(), b'hook')
