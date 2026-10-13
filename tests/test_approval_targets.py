"""Approval target discovery uses the same rules as the approval settings UI."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aipet
import support


class ApprovalTargetTests(unittest.TestCase):
    def test_saved_rules_report_only_enabled_targets_in_sorted_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auto-approve.json"
            with support.paths(HOME_DIR=directory), support.paths(AUTO_APPROVE_PATH=str(path)):
                self.assertEqual(aipet.auto_approve_targets(), [])
                for key, enabled in (("wsl:Ubuntu", True), ("mac", False), ("codex:mac", True)):
                    self.assertTrue(aipet.save_auto_approve(key, dict(aipet.default_auto_rules(), enabled=enabled)))
                self.assertEqual(aipet.auto_approve_targets(), ["codex:mac", "wsl:Ubuntu"])
                self.assertTrue(aipet.save_auto_approve(None, None))
                self.assertEqual(aipet.auto_approve_targets(), [])

    def test_legacy_targets_and_unreadable_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auto-approve.json"
            with support.paths(AUTO_APPROVE_PATH=str(path)):
                path.write_text(json.dumps({"targets": ["mac", 1, "codex:mac"]}))
                self.assertEqual(aipet.auto_approve_targets(), ["codex:mac", "mac"])
                path.write_text("{")
                self.assertEqual(aipet.auto_approve_targets(), [])
