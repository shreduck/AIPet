import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import aipet
import aipet_app


class ConfigWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "config.json")
        self.p = patch.object(aipet, "CONFIG_PATH", self.path)
        self.p.start()
        app = self.app = object.__new__(aipet_app.TrayApp)
        app.pet = MagicMock(cfg=aipet.load_config())
        app.refresh_menu, app.info, app.set_style, app._set_notify = MagicMock(), MagicMock(), MagicMock(), MagicMock()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_edits_are_applied_through_the_same_setters_without_writing_back(self):
        self.write({"theme": "dark", "compact": True, "done_timeout_minutes": 9, "sound_style": "system"})
        saved = []
        with patch.object(aipet, "save_setting", side_effect=lambda k, v: saved.append(k)):
            self.assertEqual(self.app.apply_config_file(), [])
        self.app.pet.apply_theme.assert_called_once_with("dark")
        self.app.pet.toggle_compact.assert_called_once_with(True)
        self.app.pet.set_done_timeout.assert_called_once_with(9)
        self.assertEqual(self.app.pet.cfg["sound_style"], "system")  # read where it's used
        self.app.refresh_menu.assert_called_once()
        self.assertFalse(aipet.SAVE_PAUSED["v"])

    def test_unchanged_file_and_nested_defaults_do_nothing(self):
        self.write({"theme": self.app.pet.cfg["theme"], "workbench": {}})  # the app's own save, say
        self.assertEqual(self.app.apply_config_file(), [])
        self.app.pet.apply_theme.assert_not_called()
        self.app.info.assert_not_called()

    def test_broken_json_waits_for_a_valid_file(self):
        with open(self.path, "w") as f:
            f.write('{"theme": "dark",')  # an editor mid-save
        self.assertEqual(self.app.apply_config_file(), [])
        self.app.pet.apply_theme.assert_not_called()

    def test_restart_only_keys_are_reported(self):
        self.write({"workbench": {"enabled": True, "mcp_url": "http://x"}})
        self.assertEqual(self.app.apply_config_file(), ["workbench"])
        self.app.info.assert_called_once()

    def test_paused_save_writes_nothing(self):
        aipet.SAVE_PAUSED["v"] = True
        try:
            self.assertTrue(aipet.save_setting("theme", "dark"))
        finally:
            aipet.SAVE_PAUSED["v"] = False
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
