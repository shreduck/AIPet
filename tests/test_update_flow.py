"""The tray app's update controller: what it remembers, when it asks, and what the menus see."""
import importlib.util
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import support


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class UpdateFlowTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        paths = support.paths(HOME_DIR=self.home.name)
        paths.__enter__()
        self.addCleanup(paths.__exit__, None, None, None)

    def flow(self, version="v1.0.0", saved=None):
        from aipetlib.app.updates import UpdateFlow
        if saved is not None:
            with open(os.path.join(self.home.name, "update.json"), "w") as f:
                json.dump(saved, f)
        self.app = SimpleNamespace(version=version, root=MagicMock(), pet=SimpleNamespace(cfg={}),
                                   ui=lambda fn: fn(), info=MagicMock(), refresh_menu=MagicMock())
        return UpdateFlow(self.app)

    def test_a_newer_release_found_earlier_shows_in_the_menus(self):
        self.assertEqual(self.flow(saved={"latest": {"tag": "v1.2.0"}}).info, {"tag": "v1.2.0"})
        self.assertIsNone(self.flow(saved={"latest": {"tag": "v0.9.0"}}).info)

    def test_check_finds_a_new_release_and_asks_once(self):
        from aipetlib.app import updates
        flow = self.flow()
        flow.dialog = MagicMock()
        latest = {"tag": "v2.0.0", "url": "u"}
        with patch.object(updates.releases, "latest_release", return_value=latest), \
                patch.object(updates.threading, "Thread", side_effect=lambda target, daemon: SimpleNamespace(
                    start=target)):
            flow.check()
            flow.check()  # told once per version
        self.assertEqual(flow.info, latest)
        flow.dialog.assert_called_once_with(latest, True)
        self.assertEqual(flow.state.load()["notified"], "v2.0.0")
        self.app.refresh_menu.assert_called()

    def test_skipping_a_version_is_remembered(self):
        from aipetlib.app import updates
        flow = self.flow()
        with patch.object(updates.theme, "themed_dialog", return_value="skip"):
            flow.dialog({"tag": "v2.0.0"}, True)
        self.assertEqual(flow.state.load()["skipped"], "v2.0.0")

    def test_automatic_checks_switch(self):
        flow = self.flow()
        with patch("aipetlib.app.updates.runtime_settings.save_setting") as save:
            flow.toggle_auto()
        self.assertFalse(self.app.pet.cfg["update_check"])
        save.assert_called_once_with("update_check", False)


if __name__ == "__main__":
    unittest.main()
