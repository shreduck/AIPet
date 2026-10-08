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
        context, shared = app._context_menu_spec(pet), app._mac_menu_spec()
        self.assertEqual(context[0]['label'], 'Settings...')  # first in every menu
        self.assertEqual(shared[0]['label'], 'Settings...')
        self.assertEqual(context[1]['label'], 'This session')
        self.assertEqual(labels(context[3:]), labels(shared[2:]))
        menu = {e['label']: e for e in app._mac_menu_spec() if e}
        self.assertTrue({'Appearance', 'Behavior', 'Integrations', 'Permissions', 'Help'} <= menu.keys())
        self.assertNotIn('checked', menu['Hide pet'])
        permissions = {e['label']: e for e in menu['Permissions']['submenu'] if e}
        claude_toggle = permissions['Answer Claude Code prompts from the pet']
        self.assertTrue(claude_toggle['checked'])
        claude_toggle['action']()
        app.pet.set_claude_answers.assert_called_once_with(False)
        session = app._context_menu_spec(pet)[1]['submenu']
        next(e for e in session if e and e['label'] == 'Go to session window')['action']()
        app.pet.focus_session.assert_called_once_with(pet)
        app.pet.cfg['compact'] = True
        appearance = next(e for e in app._mac_menu_spec() if e and e['label'] == 'Appearance')['submenu']
        self.assertTrue(next(e for e in appearance if e and e['label'].startswith('Compact'))['checked'])
        app.pet._menu_session.return_value = None
        idle = app._context_menu_spec(SimpleNamespace(key='_none', data={}))[1]['submenu']
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
        with patch.object(aipet, 'fill_menu'), patch.object(aipet, 'work_area', return_value=None), \
                patch.object(aipet, 'NATIVE_WIN_MENU', False):
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

    def fake_user32(self, chosen=0):
        u = MagicMock()
        handles = iter(range(100, 200))
        u.CreatePopupMenu.side_effect = lambda: next(handles)
        u.AppendMenuW.return_value = 1
        u.GetAncestor.return_value = 42
        u.TrackPopupMenuEx.return_value = chosen
        return u

    def test_native_menu_mirrors_spec(self):
        u, actions = self.fake_user32(), []
        hide, mute = MagicMock(), MagicMock()
        spec = [{'label': 'Hide pet', 'action': hide, 'default': True}, None,
                {'label': 'Behavior', 'submenu': [{'label': 'Mute', 'action': mute, 'checked': True},
                                                  {'label': 'Q&A', 'action': None, 'enabled': False}]}]
        root = aipet.build_win_menu(u, spec, actions)
        self.assertEqual(root, 100)
        self.assertEqual(actions, [hide, mute, None])
        calls = [c.args for c in u.AppendMenuW.call_args_list]
        self.assertIn((100, 0, 1, 'Hide pet'), calls)
        self.assertIn((100, aipet.MF_SEPARATOR, 0, None), calls)
        self.assertIn((101, aipet.MF_CHECKED, 2, 'Mute'), calls)
        self.assertIn((101, aipet.MF_GRAYED, 3, 'Q&&A'), calls)
        self.assertIn((100, aipet.MF_POPUP, 101, 'Behavior'), calls)
        u.SetMenuDefaultItem.assert_called_once_with(100, 1, 0)

    def test_native_popup_returns_choice_and_cleans_up(self):
        mute = MagicMock()
        spec = [{'label': 'Hide pet', 'action': MagicMock()}, {'label': 'Mute', 'action': mute}]
        widget = MagicMock()
        widget.winfo_id.return_value = 7
        u = self.fake_user32(chosen=2)
        self.assertIs(aipet.popup_native_menu(widget, spec, 10, 20, api=u), mute)
        u.SetForegroundWindow.assert_called_once_with(42)
        self.assertEqual(u.TrackPopupMenuEx.call_args.args[2:5], (10, 20, 42))
        u.DestroyMenu.assert_called_once_with(100)
        self.assertIsNone(aipet.popup_native_menu(widget, spec, 10, 20, api=self.fake_user32(chosen=0)))

    def test_context_menu_runs_native_choice_after_closing(self):
        app = object.__new__(aipet.PetApp)
        app._menu_open, app.drag = False, None
        app.hide_tip, app.root, app.clickthru = MagicMock(), MagicMock(), None
        app.context_menu_spec, app.menu = MagicMock(return_value=[]), MagicMock()
        action = MagicMock(side_effect=lambda: self.assertFalse(app._menu_open))
        with patch.object(aipet, 'NATIVE_WIN_MENU', True), \
                patch.object(aipet, 'popup_native_menu', return_value=action) as native:
            self.assertEqual(app.on_menu(SimpleNamespace(x_root=5, y_root=6)), 'break')
        native.assert_called_once_with(app.root, [], 5, 6)
        action.assert_called_once_with()
        app.menu.tk_popup.assert_not_called()

    def test_context_menu_falls_back_to_tk_when_native_fails(self):
        app = object.__new__(aipet.PetApp)
        app._menu_open, app.drag = False, None
        app.hide_tip, app.root, app.clickthru = MagicMock(), MagicMock(), None
        app.context_menu_spec, app.menu = MagicMock(return_value=[]), MagicMock()
        with patch.object(aipet, 'NATIVE_WIN_MENU', True), \
                patch.object(aipet, 'popup_native_menu', side_effect=OSError('no user32')), \
                patch.object(aipet, 'fill_menu'), patch.object(aipet, 'popup_menu') as tk_popup:
            app.on_menu(SimpleNamespace(x_root=5, y_root=6))
            self.assertFalse(aipet.NATIVE_WIN_MENU)
        tk_popup.assert_called_once_with(app.menu, 5, 6)

    def test_details_opened_from_menu_stay_open_until_session_goes(self):
        app = object.__new__(aipet.PetApp)
        pinned, prompt = MagicMock(pinned=True), MagicMock(pinned=False)
        app.details = {'done-session': pinned, 'answered': prompt}
        items = [{'key': 'done-session', 'state': 'done'}, {'key': 'answered', 'state': 'working'}]
        app.sync_bubbles(items)
        pinned.update.assert_called_once_with(items[0])
        pinned.destroy.assert_not_called()
        prompt.destroy.assert_called_once_with()  # a prompt card still closes once it's answered elsewhere
        app.sync_bubbles([])
        pinned.destroy.assert_called_once_with()

    def test_control_click_does_not_focus_or_acknowledge_session(self):
        app = object.__new__(aipet.PetApp)
        app.drag, app.focus_session, app._acknowledge = None, MagicMock(), MagicMock()
        pet = SimpleNamespace(_on_badge='session', _on_bubble=True)
        with patch.object(aipet, 'IS_MAC', True):
            self.assertEqual(app.on_release(SimpleNamespace(state=4), pet), 'break')
        app.focus_session.assert_not_called()
        app._acknowledge.assert_not_called()
