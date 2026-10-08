import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet
import aipet_app


class MenuTests(unittest.TestCase):
    def make_app(self):
        app = object.__new__(aipet_app.TrayApp)
        app.pet = MagicMock(cfg=dict(aipet.DEFAULT_CONFIG))
        app.status, app.distros, app.codex_targets = {}, [], [('Local Codex', 'codex:local')]
        app.c_auto, app.update_info = [], None
        app.hidden = app.c_muted = app.c_notify = app.c_autostart = False
        app.c_wb, app.version = 'off', 'test'
        return app

    @patch.object(aipet_app.hi, 'list_backups', return_value=[])
    @patch.object(aipet, 'auto_approve_rules', return_value={})
    def test_context_and_status_menus_share_groups_and_current_values(self, *_):
        app = self.make_app()
        pet = SimpleNamespace(key='session', data={'source': 'CC', 'cwd': '/tmp', 'state': 'working'})
        app.pet._menu_session.return_value = pet.data
        def labels(spec):
            return [(entry['label'], labels(entry['submenu']) if entry.get('submenu') else [])
                    for entry in spec if entry is not None]
        self.assertEqual(labels(app._context_menu_spec(pet)[2:]), labels(app._mac_menu_spec()))
        menu = {e['label']: e for e in app._mac_menu_spec() if e}
        self.assertTrue({'Appearance', 'Behavior', 'Integrations', 'Permissions', 'Help'} <= menu.keys())
        self.assertNotIn('checked', menu['Hide pet'])
        permissions = {e['label']: e for e in menu['Permissions']['submenu'] if e}
        claude_toggle = permissions['Answer Claude Code prompts from the pet']
        self.assertTrue(claude_toggle['checked'])
        claude_toggle['action']()
        app.pet.set_claude_answers.assert_called_once_with(False)
        session = app._context_menu_spec(pet)[0]['submenu']
        next(e for e in session if e and e['label'] == 'Go to session window')['action']()
        app.pet.focus_session.assert_called_once_with(pet)
        app.pet.cfg['compact'] = True
        appearance = next(e for e in app._mac_menu_spec() if e and e['label'] == 'Appearance')['submenu']
        self.assertTrue(next(e for e in appearance if e and e['label'].startswith('Compact'))['checked'])
        app.pet._menu_session.return_value = None
        idle = app._context_menu_spec(SimpleNamespace(key='_none', data={}))[0]['submenu']
        self.assertTrue(all(not e['enabled'] for e in idle if e))

    def test_context_menu_keeps_click_handling_and_recovers_on_error(self):
        app = object.__new__(aipet.PetApp)
        app._menu_open, app.drag = False, ['old drag']
        app.hide_tip, app.root, app.clickthru = MagicMock(), MagicMock(), MagicMock()
        app.context_menu_spec = MagicMock(return_value=[])
        app.menu = MagicMock()
        def popup(*args):
            self.assertTrue(app._menu_open)
            app.clickthru.set.assert_called_with(False)
            raise RuntimeError('menu test failure')
        app.menu.tk_popup.side_effect = popup
        pet = SimpleNamespace(_on_badge='old badge', _on_bubble=True)
        with patch.object(aipet, 'fill_menu'), patch.object(aipet, 'work_area', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'menu test failure'):
                app.on_menu(SimpleNamespace(x_root=100, y_root=100), pet)
        self.assertFalse(app._menu_open)
        self.assertIsNone(app.drag)
        self.assertIsNone(pet._on_badge)
        self.assertFalse(pet._on_bubble)

    def test_popup_fits_above_screen_edge_on_each_monitor(self):
        menu = MagicMock()
        menu.winfo_reqwidth.return_value = 250
        menu.winfo_reqheight.return_value = 350
        for bounds, click, expected in [
            ((0, 25, 1920, 1080), (1800, 1000), (1662, 642)),
            ((-1920, 25, 0, 1080), (-100, 1000), (-258, 642)),
            ((0, 25, 1920, 1080), (100, 100), (100, 100)),
        ]:
            with self.subTest(bounds=bounds, click=click):
                with patch.object(aipet, 'work_area', return_value=bounds):
                    aipet.popup_menu(menu, *click)
                menu.tk_popup.assert_called_with(*expected)

    def test_control_click_does_not_focus_or_acknowledge_session(self):
        app = object.__new__(aipet.PetApp)
        app.drag, app.focus_session, app._acknowledge = None, MagicMock(), MagicMock()
        pet = SimpleNamespace(_on_badge='session', _on_bubble=True)
        with patch.object(aipet, 'IS_MAC', True):
            self.assertEqual(app.on_release(SimpleNamespace(state=4), pet), 'break')
        app.focus_session.assert_not_called()
        app._acknowledge.assert_not_called()
