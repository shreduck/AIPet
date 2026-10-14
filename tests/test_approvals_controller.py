"""The tray app's auto approve controller: which targets the menus list, how they are labelled, turning all off."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class ApprovalsTests(unittest.TestCase):
    def setUp(self):
        from aipetlib.app import approvals
        self.module = approvals
        self.rules = {}
        for name, value in (("auto_approve_rules", lambda: self.rules), ("auto_approve_targets", lambda: ["codex:old"]),
                            ("save_auto_approve", MagicMock(return_value=True))):
            stub = patch.object(approvals.permissions, name, value)
            stub.start()
            self.addCleanup(stub.stop)
        self.app = SimpleNamespace(distros=[("Ubuntu", "Running")], codex_targets=[("Local Codex", "codex:local")],
                                   status={}, refresh_menu=MagicMock(), presentation=SimpleNamespace(invalidate=MagicMock()))
        self.approvals = approvals.Approvals(self.app)

    def test_sections_list_every_target_and_any_left_on(self):
        sections = dict(self.approvals.sections())
        self.assertIn(("WSL: Ubuntu", "wsl:Ubuntu"), sections["Claude Code"])
        self.assertEqual(sections["Codex"], [("Local Codex", "codex:local")])
        self.assertEqual(sections["Not detected now"], [("codex:old", "codex:old")])  # on, so it can be switched off

    def test_labels_say_what_the_rules_do(self):
        self.rules = {"wsl:Ubuntu": {"enabled": True, "whitelist": ["git status", "# note", "ls"]},
                      "codex:local": {"enabled": True, "allow_all": True}}
        self.app.status["wsl:Ubuntu"] = "not installed"
        self.assertEqual(self.approvals.label("WSL", "wsl:Ubuntu"), "WSL - 2 whitelist rules  (hooks not installed)...")
        self.assertEqual(self.approvals.label("Codex", "codex:local"), "Codex - allow all...")

    def test_all_off_saves_and_refreshes(self):
        self.approvals.all_off()
        self.module.permissions.save_auto_approve.assert_called_once_with(None, None)
        self.assertEqual(self.approvals.targets_on, ["codex:old"])  # read again from the file
        self.app.presentation.invalidate.assert_called_once()
        self.app.refresh_menu.assert_called_once()


if __name__ == "__main__":
    unittest.main()
