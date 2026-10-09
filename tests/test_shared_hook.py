"""Windows folder build: aipet-hook.exe next to AIPet.exe shares _internal; Install copies it plus the runtime files
listed in hook_files.txt to ~/.aipet/bin/hook."""
import os
import tempfile
import unittest
from unittest.mock import patch

import hooks_installer as hi


class SharedHookDeployTests(unittest.TestCase):
    def test_hook_and_listed_runtime_files_are_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = os.path.join(tmp, "AIPet")
            internal = os.path.join(app, "_internal")
            os.makedirs(os.path.join(internal, "certifi"))
            for name, data in (("python313.dll", b"py"), ("base_library.zip", b"zip"), ("certifi/cacert.pem", b"pem"),
                               ("tk86t.dll", b"tk")):
                with open(os.path.join(internal, name), "wb") as f:
                    f.write(data)
            with open(os.path.join(app, "aipet-hook.exe"), "wb") as f:
                f.write(b"hook")
            with open(os.path.join(internal, "hook_files.txt"), "w") as f:
                f.write("python313.dll\nbase_library.zip\ncertifi/cacert.pem\n../outside.txt\nmissing.dll\n")
            bin_dir = os.path.join(tmp, "bin")
            stale = os.path.join(bin_dir, "hook", "_internal", "python314.dll")  # left by an older build
            os.makedirs(os.path.dirname(stale))
            open(stale, "wb").close()
            with patch.object(hi.sys, "frozen", True, create=True), \
                    patch.object(hi.sys, "_MEIPASS", internal, create=True), \
                    patch.object(hi.sys, "executable", os.path.join(app, "AIPet.exe")), \
                    patch.object(hi, "INSTALL_DIR", bin_dir):
                hi._deploy_shared_hook()
            hook = os.path.join(bin_dir, "hook")
            self.assertTrue(os.path.isfile(os.path.join(hook, "aipet-hook.exe")))
            for name in ("python313.dll", "base_library.zip", os.path.join("certifi", "cacert.pem")):
                self.assertTrue(os.path.isfile(os.path.join(hook, "_internal", name)), name)
            self.assertFalse(os.path.exists(os.path.join(hook, "_internal", "tk86t.dll")))  # not the hook's
            self.assertFalse(os.path.exists(os.path.join(hook, "outside.txt")))  # never outside _internal
            self.assertFalse(os.path.exists(stale))  # leftovers of older builds are cleaned up

    def test_source_runs_and_single_file_builds_do_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(hi, "INSTALL_DIR", tmp):
            hi._deploy_shared_hook()  # not frozen
            self.assertEqual(os.listdir(tmp), [])

    def test_busy_file_is_moved_aside_and_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "new.exe"), os.path.join(tmp, "hook", "aipet-hook.exe")
            os.makedirs(os.path.dirname(dst))
            with open(src, "wb") as f:
                f.write(b"new")
            with open(dst, "wb") as f:
                f.write(b"old")
            real_replace, calls = os.replace, []

            def busy_once(a, b):  # the first overwrite fails like a running exe on Windows; renames work
                calls.append((a, b))
                if len(calls) == 1:
                    raise PermissionError("in use")
                return real_replace(a, b)
            with patch.object(hi.os, "replace", side_effect=busy_once):
                hi._sync_file(src, dst)
            with open(dst, "rb") as f:
                self.assertEqual(f.read(), b"new")
            aside = [n for n in os.listdir(os.path.dirname(dst)) if n.startswith("aipet-hook.exe.old-")]
            self.assertEqual(len(aside), 1)  # the running copy, removed on a later sync


if __name__ == "__main__":
    unittest.main()
