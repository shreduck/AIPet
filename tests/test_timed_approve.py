"""Timed auto approve: one session approves its permission prompts by itself for N minutes."""
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet
import aipetlib.app.answers as answers_module
import support
import aipet_app
from aipetlib.app.agent_windows import AgentWindows
import aipet_hook as hook


class TimedApproveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "auto-approve.json")
        self.p = support.paths(AUTO_APPROVE_PATH=self.path)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_grant_is_stored_beside_the_rules_and_expires(self):
        aipet.save_auto_approve("windows", dict(aipet.default_auto_rules(), enabled=True, whitelist=["ls"]))
        self.assertTrue(aipet.set_timed_grant("s-1", 10, "esign"))
        aipet.save_auto_approve("wsl:U", aipet.default_auto_rules())  # saving rules keeps the grant
        with open(self.path) as f:
            data = json.load(f)
        self.assertEqual(data["targets"]["windows"]["whitelist"], ["ls"])
        grant = aipet.timed_grants()["s-1"]
        self.assertAlmostEqual(grant["until"], time.time() + 600, delta=5)
        self.assertEqual(aipet.timed_grants(now=time.time() + 601), {})  # expired
        aipet.stop_timed_grant("s-1")
        self.assertEqual(aipet.timed_grants(), {})
        self.assertEqual(aipet.fmt_left(65), "1:05")

    def test_hook_approves_only_that_session_only_while_running(self):
        aipet.set_timed_grant("s-1", 10)
        base = self.tmp.name
        with patch.object(hook, "pet_alive", return_value=True):
            self.assertEqual(hook.timed_decision(base, "s-1"), "timed")
            self.assertIsNone(hook.timed_decision(base, "s-2"))  # other sessions keep asking
            self.assertIsNone(hook.timed_decision(base, "s-1", now=time.time() + 601))  # timer over
        with patch.object(hook, "pet_alive", return_value=False):
            self.assertIsNone(hook.timed_decision(base, "s-1"))  # AIPet closed

    def test_start_asks_first(self):
        app = object.__new__(aipet.PetApp)
        app.cfg, app.root = {"timed_approve_minutes": 15}, MagicMock()
        app.answers = aipet.Answers(app)
        item = {"session_id": "s-1", "title": "esign", "conv": "PdfBox"}
        with patch.object(answers_module.theme, "themed_dialog", return_value=False) as ask:
            self.assertFalse(app.answers.start_timed(item))
        self.assertIn("15 min", ask.call_args.kwargs["heading"])
        self.assertFalse(ask.call_args.kwargs["enter_confirms"])  # Enter must not start it
        self.assertEqual(aipet.timed_grants(), {})
        with patch.object(answers_module.theme, "themed_dialog", return_value=True):
            self.assertTrue(app.answers.start_timed(item))
        self.assertIn("s-1", aipet.timed_grants())

    @patch.object(aipet_app.app_menus.hook_backups, "list_backups", return_value=[])
    @patch.object(aipet.permissions, "auto_approve_rules", return_value={})
    def test_live_management_and_right_click_countdowns(self, *_):
        app = object.__new__(aipet_app.TrayApp)
        app.setup = aipet_app.SetupFlow(app)
        app.cowork = aipet_app.CoworkFlow(app, MagicMock())
        app.legacy = aipet_app.LegacyFlow(app)
        app.hooks = aipet_app.HookJobs(app, MagicMock())
        app.pet = MagicMock(cfg=dict(aipet.DEFAULT_CONFIG))
        app.pet.answers.timed_minutes.return_value = 10
        app.status, app.distros, app.codex_targets = {}, [], []
        app.approvals, app.updates = aipet_app.Approvals(app), MagicMock(info=None)
        app.approvals.targets_on = []
        app.hidden = app.c_muted = app.c_notify = app.c_autostart = False
        app.c_wb, app.version = "off", "test"
        app.agent_windows = AgentWindows(app)
        app.pet._last_items = [
            {"source": "CC", "session_id": "s-1", "title": "esign", "state": "working", "agent_types": ["tester"]},
            {"source": "CC", "session_id": "s-2", "title": "notes", "state": "idle", "agent_types": []}]
        aipet.set_timed_grant("s-1", 10, "esign")
        live = next(e for e in app._mac_menu_spec() if e and e["label"] == "Live Management")["submenu"]
        esign = next(e for e in live if e and e["label"].startswith("esign"))
        self.assertIn("⏱", esign["label"])  # countdown on the session's entry
        labels = [e["label"] for e in esign["submenu"] if e]
        self.assertTrue(labels[0].startswith("Auto approving"))
        self.assertIn("Stop auto approving", labels)
        self.assertIn("• tester", labels)
        notes = next(e for e in live if e and e["label"].startswith("notes"))
        self.assertTrue(notes["submenu"][0]["label"].startswith("Auto approve for 10 min"))
        app.pet._menu_session.return_value = None
        context = app._context_menu_spec(SimpleNamespace(key="_none", data={}))
        self.assertEqual(context[1]["label"], "Agent network window")  # right under the AIPet menu
        self.assertTrue(context[2]["label"].startswith("⏱ Stop auto approve: esign"))  # then the countdowns
        app.refresh_menu = MagicMock()
        context[2]["action"]()
        app.pet.answers.stop_timed.assert_called_once_with("s-1")


if __name__ == "__main__":
    unittest.main()
