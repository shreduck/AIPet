"""Timed auto approve: one session approves its permission prompts by itself for N minutes."""
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet
import aipetlib.config as config
import aipetlib.permissions as permissions
import aipetlib.text as textfmt
import aipetlib.app.answers as answers_module
import support
import aipet_app
from aipetlib.app.agent_windows import AgentWindows
import aipet_hook as hook
from aipet_hook_runtime import approvals as hook_approvals
from aipet_hook_runtime import waiting as hook_waiting


class TimedApproveTests(unittest.TestCase):
    def test_async_confirmation_rechecks_current_prompt_instead_of_allowing_stale_request(self):
        from aipetlib.ui.permission_card import PermissionCard
        initial = dict(key='k', session_id='s-1', state='needs_input', request=dict(id='old', subject='safe', tool='Bash'))
        current = dict(initial, request=dict(id='new', subject='danger', tool='Bash'))
        permissions.save_auto_approve(permissions.auto_approve_key_for(initial),
                                     dict(permissions.default_auto_rules(), blacklist=['danger']))
        app = SimpleNamespace(cfg={}, root=MagicMock(), _last_items=[initial])
        app.answers = answers_module.Answers(app)
        card = object.__new__(PermissionCard)
        card.app, card.key, card.win = app, 'k', MagicMock()
        card._save_timed_minutes, card.answer, card.update = MagicMock(), MagicMock(), MagicMock()
        with patch.object(answers_module.theme, 'themed_dialog', return_value=MagicMock()) as dialog:
            card.auto_approve_for_a_while()
            app._last_items = [current]
            dialog.call_args.kwargs['on_result']('reject_blacklist')
        card.answer.assert_called_once_with('deny', auto_approved=True)
        card.update.assert_not_called()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "auto-approve.json")
        self.p = support.paths(AUTO_APPROVE_PATH=self.path)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_grant_is_stored_beside_the_rules_and_expires(self):
        permissions.save_auto_approve("windows", dict(permissions.default_auto_rules(), enabled=True, whitelist=["ls"]))
        self.assertTrue(permissions.set_timed_grant("s-1", 10, "esign"))
        permissions.save_auto_approve("wsl:U", permissions.default_auto_rules())  # saving rules keeps the grant
        with open(self.path) as f:
            data = json.load(f)
        self.assertEqual(data["targets"]["windows"]["whitelist"], ["ls"])
        grant = permissions.timed_grants()["s-1"]
        self.assertAlmostEqual(grant["until"], time.time() + 600, delta=5)
        self.assertEqual(permissions.timed_grants(now=time.time() + 601), {})  # expired
        permissions.stop_timed_grant("s-1")
        self.assertEqual(permissions.timed_grants(), {})
        self.assertEqual(textfmt.fmt_left(65), "1:05")

    def test_hook_approves_only_that_session_only_while_running(self):
        permissions.set_timed_grant("s-1", 10)
        base = self.tmp.name
        with patch.object(hook_waiting, "pet_alive", return_value=True):
            self.assertEqual(hook_approvals.timed_decision(base, "s-1"), "timed")
            self.assertIsNone(hook_approvals.timed_decision(base, "s-2"))  # other sessions keep asking
            self.assertIsNone(hook_approvals.timed_decision(base, "s-1", now=time.time() + 601))  # timer over
        with patch.object(hook_waiting, "pet_alive", return_value=False):
            self.assertIsNone(hook_approvals.timed_decision(base, "s-1"))  # AIPet closed

    def test_start_asks_first(self):
        app = object.__new__(aipet.PetApp)
        app.cfg, app.root = {"timed_approve_minutes": 15}, MagicMock()
        app.answers = answers_module.Answers(app)
        item = {"session_id": "s-1", "title": "esign", "conv": "PdfBox"}
        with patch.object(answers_module.theme, "themed_dialog", return_value=False) as ask:
            self.assertFalse(app.answers.start_timed(item))
        self.assertIn("15 min", ask.call_args.kwargs["heading"])
        self.assertFalse(ask.call_args.kwargs["enter_confirms"])  # Enter must not start it
        self.assertIn(("Approve all", True, "primary"), ask.call_args.kwargs["buttons"])
        self.assertIn(("Pause on blacklist", "blacklist", "warning"), ask.call_args.kwargs["buttons"])
        self.assertIn(("Reject all in blacklist", "reject_blacklist", "danger"), ask.call_args.kwargs["buttons"])
        self.assertEqual(permissions.timed_grants(), {})
        with patch.object(answers_module.theme, "themed_dialog", return_value=True):
            self.assertTrue(app.answers.start_timed(item))
        self.assertIn("s-1", permissions.timed_grants())

    def test_blacklist_mode_uses_live_rules_even_when_disabled_or_allow_all(self):
        key = "windows"
        permissions.save_auto_approve(key, dict(permissions.default_auto_rules(), enabled=False,
                                                allow_all=True, blacklist=["rm ", "Bash\\(danger"]))
        permissions.set_timed_grant("s-1", 10, respect_blacklist=True)
        self.assertTrue(permissions.timed_grants()["s-1"]["respect_blacklist"])
        with patch.object(hook_waiting, "pet_alive", return_value=True):
            def decide(command):
                return hook_approvals.timed_decision(self.tmp.name, "s-1", key=key,
                    data={"tool_name": "Bash", "tool_input": {"command": command}})
            self.assertEqual(decide("git status"), "timed")  # no whitelist required
            self.assertEqual(decide("echo ok; rm file"), "blocked")
            self.assertEqual(decide("danger"), "blocked")  # Tool(command) also checked
            for black in (["["], None, [".*"]):
                permissions.save_auto_approve(key, {"blacklist": black})
                self.assertEqual(decide("git status"), "blocked")
            permissions.save_auto_approve(key, {"blacklist": []})
            self.assertEqual(decide("git status"), "timed")

    def test_blacklist_confirmation_is_parented_to_triggering_window(self):
        app = SimpleNamespace(cfg={"timed_approve_minutes": 15}, root=MagicMock())
        answers = answers_module.Answers(app)
        parent = MagicMock()
        with patch.object(answers_module.theme, "themed_dialog", return_value="blacklist") as ask:
            self.assertTrue(answers.start_timed({"session_id": "s-1"}, parent=parent))
        self.assertIs(ask.call_args.args[0], parent)
        self.assertTrue(permissions.timed_grants()["s-1"]["respect_blacklist"])

    @patch.object(aipet_app.app_menus.hook_backups, "list_backups", return_value=[])
    @patch.object(permissions, "auto_approve_rules", return_value={})
    def test_live_management_and_right_click_countdowns(self, *_):
        app = object.__new__(aipet_app.TrayApp)
        app.setup = aipet_app.SetupFlow(app)
        app.cowork = aipet_app.CoworkFlow(app, MagicMock())
        app.legacy = aipet_app.LegacyFlow(app)
        app.hooks = aipet_app.HookJobs(app, MagicMock())
        app.pet = MagicMock(cfg=dict(config.DEFAULT_CONFIG))
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
        permissions.set_timed_grant("s-1", 10, "esign")
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

    def test_card_confirmation_answers_current_prompt_and_follows_allow_once(self):
        from aipetlib.ui.permission_card import PermissionCard
        for mode in (True, 'blacklist', 'reject_blacklist'):
            with self.subTest(mode=mode):
                permissions.stop_timed_grant('s-1')
                item = dict(key='k', session_id='s-1', state='needs_input', request=dict(id='r1'))
                app = SimpleNamespace(cfg={}, root=MagicMock(), _last_items=[item])
                permissions.save_auto_approve(permissions.auto_approve_key_for(item), {'blacklist': []})
                app.answers = answers_module.Answers(app)
                app.answers.send = MagicMock(return_value=True)
                card = object.__new__(PermissionCard)
                card.app, card.key, card.win = app, 'k', MagicMock()
                card._save_timed_minutes, card.update, card.destroy = MagicMock(), MagicMock(), MagicMock()
                card.pinned = False
                card._after = None
                card.win.winfo_exists.return_value = True
                card.win.destroy.side_effect = lambda: setattr(card.win.winfo_exists, 'return_value', False)
                app.details = {'k': card}
                with patch.object(answers_module.theme, 'themed_dialog', return_value=mode) as ask:
                    card.auto_approve_for_a_while()
                    app.answers.send.assert_not_called()
                    ask.call_args.kwargs['on_result'](mode)
                self.assertIn('selected mode also applies to the prompt on screen', ask.call_args.kwargs['text'])
                if mode == 'blacklist':
                    app.answers.send.assert_not_called()
                    card.update.assert_not_called()
                    card.win.destroy.assert_called_once()
                    self.assertNotIn('k', app.details)
                else:
                    app.answers.send.assert_called_once_with('k', 'allow', auto_approved=True)
                    self.assertEqual(card.sent, 'Allow once')
                    self.assertEqual(card._sent_id, 'r1')
                    card.update.assert_called_once_with(item)
                    aipet.PetApp.sync_bubbles(app, [dict(item, state='working')])
                    card.destroy.assert_called_once()
                self.assertEqual(permissions.timed_grants()['s-1']['respect_blacklist'], mode in ('blacklist', 'reject_blacklist'))
                self.assertEqual(permissions.timed_grants()['s-1']['reject_blacklist'], mode == 'reject_blacklist')

    def test_cancelled_card_confirmation_does_not_answer_or_close(self):
        from aipetlib.ui.permission_card import PermissionCard
        item = dict(key='k', session_id='s-1', state='needs_input', request=dict(id='r1'))
        app = SimpleNamespace(cfg={}, root=MagicMock(), _last_items=[item])
        app.answers = answers_module.Answers(app)
        app.answers.send = MagicMock()
        card = object.__new__(PermissionCard)
        card.app, card.key, card.win = app, 'k', MagicMock()
        card._save_timed_minutes, card.update, card.close = MagicMock(), MagicMock(), MagicMock()
        with patch.object(answers_module.theme, 'themed_dialog', return_value=False) as ask:
            card.auto_approve_for_a_while()
            ask.call_args.kwargs['on_result'](False)
        app.answers.send.assert_not_called()
        card.update.assert_not_called()
        card.close.assert_not_called()
        self.assertEqual(permissions.timed_grants(), {})

    def test_active_blacklist_timer_can_switch_modes_or_cancel_without_allowing(self):
        from aipetlib.ui.permission_card import PermissionCard
        for confirm in (False, True):
            with self.subTest(confirm=confirm):
                permissions.set_timed_grant('s-1', 10, respect_blacklist=True)
                item = dict(key='k', session_id='s-1', state='needs_input', request=dict(id='r1'))
                app = SimpleNamespace(cfg={}, root=MagicMock(), _last_items=[item])
                app.answers = answers_module.Answers(app)
                card = object.__new__(PermissionCard)
                card.app, card.key, card.win = app, 'k', MagicMock()
                card._save_timed_minutes, card.answer = MagicMock(), MagicMock()
                with patch.object(answers_module.theme, 'themed_dialog', return_value=confirm) as ask:
                    card.auto_approve_for_a_while()
                    ask.call_args.kwargs['on_result'](confirm)
                ask.assert_called_once()
                self.assertEqual(permissions.timed_grants()['s-1']['respect_blacklist'], not confirm)
                if confirm:
                    card.answer.assert_called_once_with('allow', auto_approved=True)
                else:
                    card.answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
