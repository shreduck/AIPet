"""Approval storage failures and shared app/hook recovery, without user files."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from aipet_hook_runtime import approvals as hook_approvals, storage
from aipetlib import permissions


class ApprovalStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / ".aipet"
        self.base.mkdir()
        self.original = self.base / "auto-approve.json"
        self.redirect = Path(storage.locator_path(str(self.base)))
        self.custom = self.root / "custom rules"
        self.paths = support.paths(HOME_DIR=str(self.base), AUTO_APPROVE_PATH=str(self.original))
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.rules = dict(enabled=True, allow_all=False, whitelist=["git status"], blacklist=["rm"])

    def test_default_and_custom_location_are_shared_by_hooks_and_reopened_app(self):
        until = time.time() + 600
        self.original.write_text(json.dumps({"targets": {"other": self.rules},
                                             "timed": {"session": {"until": until}}}))
        self.assertEqual(permissions.approval_path(), str(self.original))
        self.assertTrue(permissions.save_auto_approve("mac", self.rules, directory=str(self.custom)))
        self.assertEqual(json.loads(self.redirect.read_text()),
                         {"path": str(self.custom / "auto-approve.json")})
        self.assertEqual(permissions.auto_approve_rules(), {"mac": self.rules, "other": self.rules})
        with patch.object(hook_approvals.hook_waiting, "pet_alive", return_value=True):
            self.assertEqual(hook_approvals.auto_approve_rules(str(self.base), "mac"), self.rules)
            self.assertEqual(hook_approvals.timed_decision(str(self.base), "session"), "timed")
        # Future writes, including all-off, must update the active redirected file.
        self.assertTrue(permissions.save_auto_approve(None, None))
        self.assertEqual(permissions.auto_approve_targets(), [])
        self.assertTrue(json.loads(self.original.read_text())["targets"]["other"]["enabled"])
        self.assertEqual(permissions.timed_grants()["session"]["until"], until)

    def test_write_denied_reports_error_keeps_old_rules_and_can_recover(self):
        self.assertTrue(permissions.save_auto_approve("mac", self.rules))
        before = self.original.read_bytes()
        with patch.object(storage.os, "replace", side_effect=PermissionError("Read-only rules folder")):
            self.assertFalse(permissions.save_auto_approve("mac", dict(self.rules, allow_all=True)))
        self.assertIn("Read-only", permissions.approval_save_error())
        self.assertEqual(self.original.read_bytes(), before)
        self.assertEqual(list(self.base.glob("*.tmp")), [])
        self.assertTrue(permissions.save_auto_approve("mac", self.rules, directory=str(self.custom)))
        self.assertEqual(permissions.approval_save_error(), "")

    def test_unreadable_existing_rules_do_not_get_silently_overwritten(self):
        self.original.write_text("not JSON")
        self.assertFalse(permissions.save_auto_approve("mac", self.rules))
        self.assertEqual(self.original.read_text(), "not JSON")
        self.assertTrue(permissions.save_auto_approve("mac", self.rules, directory=str(self.custom)))
        self.assertEqual(permissions.auto_approve_rules(), {"mac": self.rules})

    def test_locator_failure_does_not_activate_new_rules(self):
        self.assertTrue(permissions.save_auto_approve("mac", dict(self.rules, enabled=False)))
        replace = storage.os.replace

        def deny_locator(src, dst):
            if os.fspath(dst) == str(self.redirect):
                raise PermissionError("Cannot save selected location")
            return replace(src, dst)

        with patch.object(storage.os, "replace", side_effect=deny_locator):
            self.assertFalse(permissions.save_auto_approve("mac", self.rules, directory=str(self.custom)))
        self.assertIn("Cannot save selected location", permissions.approval_save_error())
        self.assertFalse(self.redirect.exists())
        self.assertEqual(permissions.auto_approve_targets(), [])

    def test_broken_locator_fails_closed_instead_of_enabling_stale_default(self):
        self.assertTrue(permissions.save_auto_approve("mac", dict(self.rules, allow_all=True)))
        for data in ("bad JSON", '{"path":"relative/path"}', '{"path":null}', "[]", "null"):
            self.redirect.write_text(data)
            self.assertEqual(permissions.auto_approve_targets(), [])
            with patch.object(hook_approvals.hook_waiting, "pet_alive", return_value=True):
                self.assertIsNone(hook_approvals.auto_approve_rules(str(self.base), "mac"))
            self.assertFalse(permissions.save_auto_approve("mac", self.rules))
        self.assertTrue(permissions.save_auto_approve("mac", self.rules, directory=str(self.custom)))
        self.assertEqual(permissions.auto_approve_rules(), {"mac": self.rules})

    @unittest.skipIf(os.name == "nt", "WSL /mnt translation is for POSIX readers")
    def test_wsl_reader_translates_windows_custom_folder(self):
        self.redirect.write_text(json.dumps({"path": r"C:\Users\friend\rules\auto-approve.json"}))
        self.assertEqual(storage.approval_path(str(self.base)),
                         "/mnt/c/Users/friend/rules/auto-approve.json")


if __name__ == "__main__":
    unittest.main()
