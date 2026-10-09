import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import hooks_installer as hi
from tools.share_mac_runtime import share_runtime


class MacRuntimeSharingTests(unittest.TestCase):
    def test_cowork_plugin_generation_keeps_mac_bundle_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_dir = root / 'AIPet.app/Contents/MacOS'
            app_dir.mkdir(parents=True)
            executable = app_dir / 'AIPet'
            executable.write_bytes(b'signed executable')
            config = root / 'config'
            with patch.object(hi, 'IS_MAC', True), \
                    patch.object(hi, 'app_dir', return_value=str(app_dir)), \
                    patch.object(hi, 'PET_DIR', str(config)), \
                    patch.object(hi, 'PLUGIN_MARKET_DIR', str(config / 'plugin-marketplace')), \
                    patch.object(hi, 'deploy_files'), \
                    patch.object(hi, 'local_hook_command', return_value='test-hook'):
                result = hi.build_plugin()
                archive = Path(result['zip'])
                self.assertEqual(archive, config / hi.COWORK_ZIP)
                with zipfile.ZipFile(archive) as plugin:
                    self.assertIsNone(plugin.testzip())
                    self.assertIn('hooks/hooks.json', plugin.namelist())
                modified = archive.stat().st_mtime_ns
                self.assertEqual(hi.build_plugin(), result)
                self.assertEqual(archive.stat().st_mtime_ns, modified)
            self.assertEqual(list(app_dir.iterdir()), [executable])
            self.assertEqual(executable.read_bytes(), b'signed executable')

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

    def test_identical_hook_files_link_to_the_app_and_deploy_as_copies(self):
        from tools.share_mac_runtime import share_identical_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contents = root / 'AIPet.app/Contents'
            for area, rel, app_data, hook_data in (
                    ('Frameworks', 'lib-dynload/unicodedata.so', b'same', b'same'),
                    ('Resources', 'base_library.zip', b'zip', b'zip'),
                    ('Frameworks', 'lib-dynload/_json.so', b'app', b'hook')):  # differs: stays a copy
                for path, data in ((contents / area / rel, app_data), (contents / area / 'hook/_internal' / rel, hook_data)):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            self.assertEqual(share_identical_files(root / 'AIPet.app'), len(b'same') + len(b'zip'))
            linked = contents / 'Frameworks/hook/_internal/lib-dynload/unicodedata.so'
            self.assertTrue(linked.is_symlink())
            self.assertFalse(linked.readlink().is_absolute())
            self.assertFalse((contents / 'Frameworks/hook/_internal/lib-dynload/_json.so').is_symlink())
            self.assertTrue((contents / 'Resources/hook/_internal/base_library.zip').is_symlink())
            self.assertEqual(share_identical_files(root / 'AIPet.app'), 0)  # safe to repeat
            frameworks = contents / 'Frameworks'
            for name in ('aipet_hook.py', 'aipet_usage.py', 'aipet_claude_usage.py'):
                (frameworks / name).write_text('# test')
            target = root / 'deployed'
            with patch.object(hi, 'resource_path', lambda *parts: str(frameworks.joinpath(*parts))), \
                    patch.object(hi, 'INSTALL_DIR', str(target)), patch.object(hi, 'PET_DIR', str(root / 'config')):
                hi.deploy_files()
            installed = target / 'hook/_internal/lib-dynload/unicodedata.so'
            self.assertFalse(installed.is_symlink())
            self.assertEqual(installed.read_bytes(), b'same')
