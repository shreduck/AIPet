import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet
import aipet_app
import aipet_settings as settings


def spec():
    act = MagicMock()
    return [
        {"label": "Settings...", "action": act, "settings": True}, None,
        {"label": "Hide pet", "action": act, "help": "Hides it."},
        None,
        {"label": "Appearance", "icon": "palette", "help": "Looks.", "submenu": [
            {"label": "Pet style", "choice": True, "submenu": [
                {"label": "Robot", "action": act, "checked": True}, {"label": "Duck", "action": act, "checked": False}]},
            {"label": "Dark theme", "action": act, "checked": False, "help": "Dark windows."},
        ]},
        {"label": "Permissions (auto approve ON)", "icon": "shield", "submenu": [
            {"label": "Auto approve", "submenu": [
                {"label": "Claude Code", "submenu": [{"label": "This PC", "action": act, "checked": True}]}]},
        ]},
        None,
        {"label": "Quit AIPet", "action": act},
    ]


class SettingsSpecTests(unittest.TestCase):
    def test_wheel_stops_at_edges_and_consumes_events(self):
        window = object.__new__(settings.SettingsWindow)
        window.canvas = MagicMock()
        with patch.object(settings, 'IS_MAC', True):
            for view, delta in (((0.0, 0.5), 1), ((0.5, 1.0), -1), ((0.0, 1.0), -10)):
                window.canvas.yview.return_value = view
                self.assertEqual(window._wheel(SimpleNamespace(delta=delta, state=0)), 'break')
            window.canvas.yview_scroll.assert_not_called()
            window.canvas.yview.return_value = (0.2, 0.7)
            self.assertEqual(window._wheel(SimpleNamespace(delta=-2, state=0)), 'break')
            window.canvas.yview_scroll.assert_called_once_with(2, 'units')

    def test_scrolling_does_not_reconfigure_unchanged_content(self):
        window = object.__new__(settings.SettingsWindow)
        window.canvas, window._body_id, window._scrollregion = MagicMock(), 1, None
        window.canvas.bbox.return_value = (0, 0, 800, 1400)
        for _ in range(10):
            window._sync_scrollregion()
        window.canvas.configure.assert_called_once_with(scrollregion=(0, 0, 800, 1400))
        window.canvas.bbox.return_value = (0, 0, 800, 900)
        window._sync_scrollregion()
        window.canvas.configure.assert_called_with(scrollregion=(0, 0, 800, 900))

    def test_inline_slider_changes_update_and_persist_settings(self):
        pet = object.__new__(aipet.PetApp)
        pet.cfg, pet.pets = {}, {}
        pet.reposition, pet._sync_usage_padding = MagicMock(), MagicMock()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(aipet, 'HOME_DIR', directory), \
                patch.object(aipet, 'CONFIG_PATH', str(Path(directory) / 'config.json')), \
                patch.dict(aipet.SCALE), patch.dict(aipet.ANSWER_WAIT), \
                patch.dict(aipet.DONE_TIMEOUT), patch.dict(aipet.HEALTH):
            pet.set_size(1.35)
            pet.set_answer_wait(75)
            pet.set_done_timeout(8)
            pet.set_health_interval(25)
            expected = {'size': 1.35, 'answer_wait_seconds': 75,
                        'done_timeout_minutes': 8, 'health_check_seconds': 25}
            self.assertEqual(pet.cfg, expected)
            self.assertEqual(json.loads(Path(aipet.CONFIG_PATH).read_text()), expected)
            self.assertEqual((Path(directory) / 'answer-wait').read_text(), '75')
            self.assertEqual(aipet.SCALE['v'], 1.35 * aipet.SCALE_UNIT)
            self.assertEqual([aipet.ANSWER_WAIT['v'], aipet.DONE_TIMEOUT['v'], aipet.HEALTH['v']], [75, 8, 25])
            pet.reposition.assert_called_once()
            pet.set_answer_wait(0)
            pet.set_done_timeout(0)
            pet.set_health_interval(0)
            self.assertEqual((Path(directory) / 'answer-wait').read_text(), '0')
            self.assertEqual([aipet.ANSWER_WAIT['v'], aipet.DONE_TIMEOUT['v'], aipet.HEALTH['v']], [0, 0, 0])

    def test_pages_follow_the_menu(self):
        pages = settings.pages_from_spec(spec())
        self.assertEqual([p[0] for p in pages], ["General", "Appearance", "Permissions"])
        general = [e["label"] for e in pages[0][3] if e]
        self.assertEqual(general, ["Hide pet", "Quit AIPet"])  # the Settings entry itself is left out
        self.assertEqual(pages[2][4], "Permissions (auto approve ON)")  # full label kept for the status pill

    def test_search_reaches_nested_items_with_their_path(self):
        rows = list(settings.flatten(settings.pages_from_spec(spec())[2][3]))
        self.assertEqual([(path, e["label"]) for path, e in rows], [(("Auto approve", "Claude Code"), "This PC")])
        appearance = [e["label"] for _, e in settings.flatten(settings.pages_from_spec(spec())[1][3])]
        self.assertEqual(appearance, ["Pet style", "Dark theme"])  # a pick-one list stays one row

    def test_signature_changes_with_state_and_theme(self):
        a, b = spec(), spec()
        self.assertEqual(settings.signature(a), settings.signature(b))
        b[4]["submenu"][1]["checked"] = True
        self.assertNotEqual(settings.signature(a), settings.signature(b))
        light = settings.signature(a)
        with patch.dict(aipet.T, name="dark"):
            self.assertNotEqual(light, settings.signature(a))

    def test_labels(self):
        self.assertEqual(settings.clean("Pet size..."), "Pet size")
        self.assertEqual(settings.page_name("Permissions (auto approve ON)"), "Permissions")
        for name, rows in settings.ICONS.items():
            self.assertEqual(len(rows), 9, name)
            self.assertTrue(all(len(r) == 9 for r in rows), name)

    @patch.object(aipet_app.hi, 'list_backups', return_value=[])
    @patch.object(aipet, 'auto_approve_rules', return_value={})
    def test_real_spec_has_icons_and_help_for_every_page(self, *_):
        app = object.__new__(aipet_app.TrayApp)
        app.pet = MagicMock(cfg=dict(aipet.DEFAULT_CONFIG))
        app.status, app.distros, app.codex_targets = {}, [], [('Local Codex', 'codex:local')]
        app.c_auto, app.update_info = [], None
        app.hidden = app.c_muted = app.c_notify = app.c_autostart = False
        app.c_wb, app.version = 'off', 'test'
        pages = settings.pages_from_spec(app._mac_menu_spec())
        self.assertEqual([p[0] for p in pages],
                         ["General", "Appearance", "Behavior", "Integrations", "Permissions", "Help"])
        for name, icon, help_text, entries, _ in pages:
            self.assertIn(icon, settings.ICONS, name)
            self.assertTrue(help_text, name)
        toggles = [e for _, e in settings.flatten(app._mac_menu_spec()) if "checked" in e and not e.get("choice")]
        self.assertTrue(toggles)
        dark = next(e for e in toggles if e["label"] == "Dark theme")
        self.assertTrue(dark["help"])
        self.assertIs(dark["action"].__func__, aipet_app.TrayApp.toggle_theme)  # the very same action as the menu
        rows = {e['label']: e for _, e in settings.flatten(app._mac_menu_spec())}
        for label, setter in (("Pet size...", app.pet.set_size), ("Answer timeout...", app.pet.set_answer_wait),
                              ("Clear finished after...", app.pet.set_done_timeout),
                              ("Health check every...", app.pet.set_health_interval)):
            self.assertIs(rows[label]['slider']['set'], setter)
            self.assertIsNot(rows[label]['slider']['set'], rows[label]['action'])


if __name__ == "__main__":
    unittest.main()
