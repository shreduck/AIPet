import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet
import aipetlib.ui.native_menu as native_menu
import aipetlib.app.preferences as app_preferences
import aipetlib.config as config
import aipetlib.geometry as geometry
import aipetlib.permissions as permissions_owner
import aipetlib.ui.appearance_controls as appearance_controls
import aipetlib.ui.tooltips as tooltips
import aipet_app
from aipetlib.app.pointer import Pointer
from aipetlib.app.agent_windows import AgentWindows


class MenuTests(unittest.TestCase):
    def setUp(self):
        # Menus list live timed auto-approvals from ~/.aipet: never let the machine running the tests change them
        grants = patch.object(permissions_owner, 'timed_grants', return_value={})
        grants.start()
        self.addCleanup(grants.stop)

    @patch.object(aipet_app.app_menus.hook_backups, 'list_backups', return_value=[])
    @patch.object(permissions_owner, 'auto_approve_rules', return_value={})
    def test_tooltip_text_slider_is_beside_pet_size_in_both_menus(self, *_):
        app = self.make_app()
        for spec in (app._context_menu_spec(SimpleNamespace(key='_none', data={})),
                     app._mac_menu_spec(settings=True)):
            appearance = next(e for e in spec if e and e['label'] == 'Appearance')['submenu']
            labels = [e['label'] for e in appearance if e]
            self.assertEqual(labels[labels.index('Pet size...') + 1], 'Tooltip text size...')
            entry = next(e for e in appearance if e and e['label'] == 'Tooltip text size...')
            self.assertEqual(entry['slider']['value'], 1.0)
            entry['action']()
            app.pet.appearance.open_tooltip_text_slider.assert_called()
            entry['slider']['set'](1.5)
            app.pet.tooltips.set_tooltip_text_scale.assert_called_with(1.5)

    @patch.object(aipet_app.app_menus.hook_backups, 'list_backups', return_value=[])
    @patch.object(permissions_owner, 'auto_approve_rules', return_value={})
    def test_sound_choices_on_mac_and_windows(self, *_):
        app = self.make_app()
        for mac, name, system_label in ((True, 'posix', 'macOS sounds'), (False, 'nt', 'Windows sounds')):
            with patch.object(aipet_app.app_menus, 'IS_MAC', mac), patch.object(aipet_app.os, 'name', name):
                for selected in ('chimes', 'system'):
                    app.pet.cfg['sound_style'] = selected
                    behavior = next(e for e in app._mac_menu_spec() if e and e['label'] == 'Behavior')
                    sounds = next(e for e in behavior['submenu'] if e and e['label'] == 'Sounds and notifications')
                    choices = next(e for e in sounds['submenu'] if e and e['label'] == 'Sound style')['submenu']
                    self.assertEqual([e['label'] for e in choices], ['AIPet chimes (default)', system_label])
                    self.assertEqual([e['checked'] for e in choices], [selected == 'chimes', selected == 'system'])
                    for entry, value in zip(choices, ('chimes', 'system')):
                        entry['action']()
                        app.pet.preferences.set_sound_style.assert_called_with(value)

    @patch.object(aipet_app.app_menus.hook_backups, 'list_backups', return_value=[])
    @patch.object(permissions_owner, 'auto_approve_rules', return_value={})
    def test_tray_menu_built_off_the_tk_thread_never_calls_tk(self, *_):
        """The Windows tray builds its menu on pystray's thread. A Tk call from there (an agent window's
        winfo_exists) waits for the Tk thread, and after Quit forever: the process and its tray icon stayed alive."""
        import threading
        app = self.make_app()
        app._tk_thread = threading.current_thread()
        calls = []
        window = MagicMock()
        window.alive.side_effect = lambda: calls.append(threading.current_thread().name) or True
        app.agent_windows.wins = {'k': window}
        self.assertTrue(app.agent_windows.is_open())  # on the Tk thread: asks the window and caches the answer
        self.assertEqual(calls, ['MainThread'])
        found = {}

        def walk(spec):
            for e in spec or []:
                if e:
                    yield e
                    yield from walk(e.get('submenu'))

        def tray():
            try:
                found['checked'] = [e['checked'] for e in walk(app._mac_menu_spec())
                                    if e['label'] == 'Agent network window']
            except Exception as e:  # reported below
                found['error'] = e

        worker = threading.Thread(target=tray, name='tray')
        worker.start()
        worker.join(5)
        self.assertEqual(calls, ['MainThread'])  # nothing asked Tk from the tray thread
        self.assertNotIn('error', found)
        self.assertTrue(found['checked'] and all(found['checked']))  # the cached answer
        app.agent_windows.wins = {}
        self.assertFalse(app.agent_windows.is_open())

    def make_app(self):
        app = object.__new__(aipet_app.TrayApp)
        app.setup = aipet_app.SetupFlow(app)
        app.cowork = aipet_app.CoworkFlow(app, MagicMock())
        app.legacy = aipet_app.LegacyFlow(app)
        app.hooks = aipet_app.HookJobs(app, MagicMock())
        app.pet = MagicMock(cfg=dict(config.DEFAULT_CONFIG))
        app.status, app.distros, app.codex_targets = {}, [], [('Local Codex', 'codex:local')]
        app.approvals, app.updates = aipet_app.Approvals(app), MagicMock(info=None)
        app.approvals.targets_on = []
        app.hidden = app.c_muted = app.c_notify = app.c_autostart = False
        app.c_wb, app.version = 'off', 'test'
        app.agent_windows = AgentWindows(app)
        return app

    @patch.object(aipet_app.app_menus.hook_backups, 'list_backups', return_value=[])
    @patch.object(permissions_owner, 'auto_approve_rules', return_value={})
    def test_context_and_status_menus_share_groups_and_current_values(self, *_):
        app = self.make_app()
        pet = SimpleNamespace(key='session', data={'source': 'CC', 'cwd': '/tmp', 'state': 'working'})
        app.pet._menu_session.return_value = pet.data
        def labels(spec):
            return [(entry['label'], labels(entry['submenu']) if entry.get('submenu') else [])
                    for entry in spec if entry is not None]
        context, shared = app._context_menu_spec(pet), app._mac_menu_spec()
        self.assertEqual(context[0]['label'], 'AIPet menu...')  # first in every menu
        self.assertEqual(shared[0]['label'], 'AIPet menu...')
        self.assertEqual(context[1]['label'], 'Agent network window')  # right below the AIPet menu
        self.assertEqual(context[2]['label'], 'Fix fast cancel')  # above This session
        self.assertEqual(context[3]['label'], 'This session')
        self.assertEqual(labels(context[5:]), labels(shared[2:]))
        menu = {e['label']: e for e in app._mac_menu_spec() if e}
        self.assertTrue({'Appearance', 'Behavior', 'Integrations', 'Permissions', 'Help'} <= menu.keys())
        looks = [e['label'] for e in menu['Appearance']['submenu'] if e]
        self.assertIn('Reset agent network appearance', looks)  # also in the tray / menu bar and the AIPet menu
        self.assertIn('Reset agent network appearance',
                      [e['label'] for e in next(x for x in context if x and x['label'] == 'Appearance')['submenu'] if e])
        self.assertNotIn('checked', menu['Hide pet'])
        permissions = {e['label']: e for e in menu['Permissions']['submenu'] if e}
        claude_toggle = permissions['Answer Claude Code prompts from the pet']
        self.assertTrue(claude_toggle['checked'])
        claude_toggle['action']()
        app.pet.answers.set_claude_answers.assert_called_once_with(False)
        session = app._context_menu_spec(pet)[3]['submenu']
        next(e for e in session if e and e['label'] == 'Go to session window')['action']()
        app.pet.focus_session.assert_called_once_with(pet)
        app.pet.cfg['compact'] = True
        appearance = next(e for e in app._mac_menu_spec() if e and e['label'] == 'Appearance')['submenu']
        self.assertTrue(next(e for e in appearance if e and e['label'].startswith('Compact'))['checked'])
        app.pet._menu_session.return_value = None
        idle = app._context_menu_spec(SimpleNamespace(key='_none', data={}))[3]['submenu']
        self.assertTrue(all(not e['enabled'] for e in idle if e))

    def test_countdowns_read_timed_grants_where_the_tests_isolate_them(self):
        """A timed auto approve running on the machine that runs the tests must not reach the menus under test."""
        app = self.make_app()
        self.assertEqual(app._timed_countdown_items(), [])  # setUp's patch: no grants
        with patch.object(permissions_owner, 'timed_grants', return_value={'s1': {'title': 'live', 'until': 9e9}}):
            self.assertEqual(len(app._timed_countdown_items()), 1)

    @patch.object(aipet_app.app_menus.hook_backups, 'list_backups', return_value=[])
    @patch.object(permissions_owner, 'auto_approve_rules', return_value={})
    def test_fix_fast_cancel_marks_the_session_finished(self, *_):
        app = self.make_app()
        working = {'key': 'cc:a', 'source': 'CC', 'state': 'working', 'title': 'ai-pet', 'conv': 'Fix bug', 'path': 'a'}
        done = {'key': 'cc:b', 'source': 'CC', 'state': 'done', 'title': 'notes', 'path': 'b'}
        pet = SimpleNamespace(key='cc:a', data=dict(working))
        app.pet._menu_session.return_value = working
        fix = app._context_menu_spec(pet)[2]
        self.assertTrue(fix['enabled'])
        fix['action']()
        app.pet.finish_session.assert_called_once_with(working)
        app.pet._menu_session.return_value = done  # nothing busy: nothing to fix
        self.assertFalse(app._context_menu_spec(SimpleNamespace(key='cc:b', data=dict(done)))[2]['enabled'])
        # compact mode: one pet for all, so pick the session
        app.pet._last_items = [working, done]
        compact = SimpleNamespace(key='compact', data={'members': [working, done], 'source': 'CC'})
        choices = app._context_menu_spec(compact)[2]['submenu']
        self.assertEqual([c['label'] for c in choices], ['ai-pet \u00b7 Fix bug'])
        app.pet.finish_session.reset_mock()
        choices[0]['action']()
        app.pet.finish_session.assert_called_once_with(working)

    def test_context_menu_keeps_click_handling_and_recovers_on_error(self):
        app = object.__new__(aipet.PetApp)
        app.appearance = appearance_controls.Appearance(app)
        app.tooltips = tooltips.Tooltips(app)
        app.preferences = app_preferences.PetPreferences(app)
        app.pointer = Pointer(app)
        app._menu_open, app.pointer.drag = False, ['old drag']
        app.tooltips.hide_tip, app.root, app.clickthru = MagicMock(), MagicMock(), MagicMock()
        app.context_menu_spec = MagicMock(return_value=[])
        app.menu = MagicMock()
        def popup(*args):
            self.assertTrue(app._menu_open)
            app.clickthru.set.assert_called_with(False)
            raise RuntimeError('menu test failure')
        app.menu.tk_popup.side_effect = popup
        pet = SimpleNamespace(_on_badge='old badge', _on_bubble=True)
        with patch.object(native_menu, 'fill_menu'), patch.object(geometry, 'work_area', return_value=None), \
                patch.object(native_menu, 'NATIVE_WIN_MENU', False):
            with self.assertRaisesRegex(RuntimeError, 'menu test failure'):
                app.on_menu(SimpleNamespace(x_root=100, y_root=100), pet)
        self.assertFalse(app._menu_open)
        self.assertIsNone(app.pointer.drag)
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
                with patch.object(geometry, 'work_area', return_value=bounds):
                    native_menu.popup_menu(menu, *click)
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
        root = native_menu.build_win_menu(u, spec, actions)
        self.assertEqual(root, 100)
        self.assertEqual(actions, [hide, mute, None])
        calls = [c.args for c in u.AppendMenuW.call_args_list]
        self.assertIn((100, 0, 1, 'Hide pet'), calls)
        self.assertIn((100, native_menu.MF_SEPARATOR, 0, None), calls)
        self.assertIn((101, native_menu.MF_CHECKED, 2, 'Mute'), calls)
        self.assertIn((101, native_menu.MF_GRAYED, 3, 'Q&&A'), calls)
        self.assertIn((100, native_menu.MF_POPUP, 101, 'Behavior'), calls)
        u.SetMenuDefaultItem.assert_called_once_with(100, 1, 0)

    def test_native_popup_returns_choice_and_cleans_up(self):
        mute = MagicMock()
        spec = [{'label': 'Hide pet', 'action': MagicMock()}, {'label': 'Mute', 'action': mute}]
        widget = MagicMock()
        widget.winfo_id.return_value = 7
        u = self.fake_user32(chosen=2)
        self.assertIs(native_menu.popup_native_menu(widget, spec, 10, 20, api=u), mute)
        u.SetForegroundWindow.assert_called_once_with(42)
        self.assertEqual(u.TrackPopupMenuEx.call_args.args[2:5], (10, 20, 42))
        u.DestroyMenu.assert_called_once_with(100)
        self.assertIsNone(native_menu.popup_native_menu(widget, spec, 10, 20, api=self.fake_user32(chosen=0)))

    def test_context_menu_runs_native_choice_after_closing(self):
        app = object.__new__(aipet.PetApp)
        app.appearance = appearance_controls.Appearance(app)
        app.tooltips = tooltips.Tooltips(app)
        app.preferences = app_preferences.PetPreferences(app)
        app.pointer = Pointer(app)
        app._menu_open, app.pointer.drag = False, None
        app.tooltips.hide_tip, app.root, app.clickthru = MagicMock(), MagicMock(), None
        app.context_menu_spec, app.menu = MagicMock(return_value=[]), MagicMock()
        action = MagicMock(side_effect=lambda: self.assertFalse(app._menu_open))
        with patch.object(native_menu, 'NATIVE_WIN_MENU', True), \
                patch.object(native_menu, 'popup_native_menu', return_value=action) as native:
            self.assertEqual(app.on_menu(SimpleNamespace(x_root=5, y_root=6)), 'break')
        native.assert_called_once_with(app.root, [], 5, 6)
        action.assert_called_once_with()
        app.menu.tk_popup.assert_not_called()

    def test_context_menu_falls_back_to_tk_when_native_fails(self):
        app = object.__new__(aipet.PetApp)
        app.appearance = appearance_controls.Appearance(app)
        app.tooltips = tooltips.Tooltips(app)
        app.preferences = app_preferences.PetPreferences(app)
        app.pointer = Pointer(app)
        app._menu_open, app.pointer.drag = False, None
        app.tooltips.hide_tip, app.root, app.clickthru = MagicMock(), MagicMock(), None
        app.context_menu_spec, app.menu = MagicMock(return_value=[]), MagicMock()
        with patch.object(native_menu, 'NATIVE_WIN_MENU', True), \
                patch.object(native_menu, 'popup_native_menu', side_effect=OSError('no user32')), \
                patch.object(native_menu, 'fill_menu'), patch.object(native_menu, 'popup_menu') as tk_popup:
            app.on_menu(SimpleNamespace(x_root=5, y_root=6))
            self.assertFalse(native_menu.NATIVE_WIN_MENU)
        tk_popup.assert_called_once_with(app.menu, 5, 6)

    def test_details_opened_from_menu_stay_open_until_session_goes(self):
        app = object.__new__(aipet.PetApp)
        app.appearance = appearance_controls.Appearance(app)
        app.tooltips = tooltips.Tooltips(app)
        app.preferences = app_preferences.PetPreferences(app)
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
        app.appearance = appearance_controls.Appearance(app)
        app.tooltips = tooltips.Tooltips(app)
        app.preferences = app_preferences.PetPreferences(app)
        app.pointer = Pointer(app)
        app.focus_session, app._acknowledge = MagicMock(), MagicMock()
        pet = SimpleNamespace(_on_badge='session', _on_bubble=True)
        with patch('aipetlib.app.pointer.IS_MAC', True):
            self.assertEqual(app.pointer.release(SimpleNamespace(state=4), pet), 'break')
        app.focus_session.assert_not_called()
        app._acknowledge.assert_not_called()
