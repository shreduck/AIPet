import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet
import aipetlib.app.preferences as app_preferences
import aipetlib.config as config
import aipetlib.permissions as permissions
import aipetlib.runtime.paths as paths
import aipetlib.runtime.preferences as preferences
import aipetlib.runtime.settings as runtime_settings
import aipetlib.ui.appearance_controls as appearance_controls
import aipetlib.ui.theme as theme
import aipetlib.ui.tooltips as tooltips
import support
import aipet_app
from aipetlib.app.agent_windows import AgentWindows
import aipet_settings as settings


def spec():
    act = MagicMock()
    return [
        {"label": "AIPet menu...", "action": act, "settings": True}, None,
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
    def test_addin_disclosure_keeps_editor_and_drafts_when_collapsed(self):
        window = self.menu_window()
        window._expanded_addins = set()
        card, row, controls = MagicMock(), MagicMock(), MagicMock()
        editor = dict(id='journal', properties=[dict(key='directory', value='/logs')], save=MagicMock())
        with patch.object(settings.tk, 'Button') as button, patch.object(settings.addin_settings, 'build') as build:
            window._inline_addin(card, row, controls, editor)
            build.assert_not_called()
            toggle = next(call.kwargs['command'] for call in button.return_value.configure.call_args_list
                          if 'command' in call.kwargs)
            toggle()
            build.assert_called_once_with(card, editor['properties'], editor['save'], font=window.font)
            build.return_value.pack.assert_called_with(after=row, fill='x', padx=14, pady=(0, 10))
            toggle()
            build.return_value.pack_forget.assert_called_once()
            toggle()
            build.assert_called_once()
            self.assertIn('journal', window._expanded_addins)

    def test_inline_addin_settings_affect_visible_signature_but_callbacks_do_not(self):
        a = dict(label='Journal', checked=False, addin_editor=dict(id='journal',
            properties=[dict(key='directory', value='/logs')], save=MagicMock()))
        b = dict(a, addin_editor=dict(a['addin_editor'], save=MagicMock()))
        self.assertEqual(settings.signature([a]), settings.signature([b]))
        b['addin_editor']['properties'] = [dict(key='directory', value='/new')]
        self.assertNotEqual(settings.signature([a]), settings.signature([b]))

    def menu_window(self):
        window = object.__new__(settings.SettingsWindow)
        window.spec = spec() + [dict(label='Live Management', icon='clock', submenu=[
            dict(label='Session · working', help='Latest message', action=MagicMock())])]
        window.page, window.zoom, window.bg = 'General', 1.0, '#ffffff'
        window.query = MagicMock()
        window.query.get.return_value = ''
        window._sig, window._nav_sig, window._content_sig = None, None, None
        window.zoom_fn = lambda: 1.0
        window.spec_fn = lambda: window.spec
        for name in ('canvas', 'nav', 'header', 'body', 'strip', '_nav_item', '_page_header',
                     '_entries', '_tag_wheel', '_sync_scrollregion', '_on_resize', '_render_search'):
            setattr(window, name, MagicMock())
        window.alive = lambda: True
        def reset_frame():
            window._nav_sig, window._content_sig = None, None
        window._build_frame = MagicMock(side_effect=reset_frame)
        patcher = patch.object(settings.tk, 'Frame')
        patcher.start()
        self.addCleanup(patcher.stop)
        return window

    def test_hidden_session_updates_leave_current_page_and_sidebar_intact(self):
        window = self.menu_window()
        window.refresh()
        nav_count = window._nav_item.call_count
        window._entries.reset_mock()
        window.body.winfo_children.reset_mock()
        for state in ('needs input', 'idle', 'working'):
            window.spec[-1]['submenu'][0]['label'] = 'Session · ' + state
            window.refresh()
        window._entries.assert_not_called()
        window.body.winfo_children.assert_not_called()
        self.assertEqual(window._nav_item.call_count, nav_count)
        window.page = 'Live Management'
        window._render(keep_scroll=False)
        window._entries.assert_called_once()
        self.assertEqual(window._entries.call_args.args[1][0]['label'], 'Session · working')

    def test_visible_updates_refresh_content_without_rebuilding_sidebar(self):
        window = self.menu_window()
        window.page = 'Live Management'
        window.refresh()
        nav_count = window._nav_item.call_count
        window._entries.reset_mock()
        window.spec[-1]['submenu'][0]['label'] = 'Session · needs input'
        window.refresh()
        window._entries.assert_called_once()
        self.assertEqual(window._nav_item.call_count, nav_count)
        window.refresh()
        window._entries.assert_called_once()

    def test_search_only_redraws_for_changes_to_matching_results(self):
        window = self.menu_window()
        window.query.get.return_value = 'Dark theme'
        window.refresh()
        window._render_search.reset_mock()
        window.spec[-1]['submenu'][0]['label'] = 'Session · idle'
        window.refresh()
        window._render_search.assert_not_called()
        window.spec[4]['submenu'][1]['checked'] = True
        window.refresh()
        window._render_search.assert_called_once()

    def test_help_theme_and_zoom_changes_still_update_the_page(self):
        window = self.menu_window()
        window.refresh()
        window._entries.reset_mock()
        window.spec[2]['help'] = 'Updated explanation'
        window.refresh()
        window._entries.assert_called_once()
        window._entries.reset_mock()
        with patch.dict(theme.T, name='changed-theme'):
            window.refresh()
        window._entries.assert_called_once()
        window._entries.reset_mock()
        window.zoom_fn = lambda: 1.2
        window.refresh()
        window._entries.assert_called_once()

    def test_tooltip_text_scale_is_independent_and_saved(self):
        pet = object.__new__(aipet.PetApp)
        pet.appearance = appearance_controls.Appearance(pet)
        pet.tooltips = tooltips.Tooltips(pet)
        pet.preferences = app_preferences.PetPreferences(pet)
        pet.cfg, pet.tooltips.tip = {"size": 1.35}, None
        with tempfile.TemporaryDirectory() as directory, \
                support.paths(HOME_DIR=directory), \
                support.paths(CONFIG_PATH=str(Path(directory) / 'config.json')):
            self.assertEqual(pet.tooltips.tooltip_text_scale(), 1.0)
            pet.tooltips.set_tooltip_text_scale(1.75)
            self.assertEqual(pet.cfg['size'], 1.35)
            self.assertEqual(runtime_settings.load_config()['tooltip_text_scale'], 1.75)
            self.assertEqual(pet.tooltips.tooltip_text_scale(), 1.75)
            pet.tooltips.set_tooltip_text_scale(1.0)
            self.assertEqual(runtime_settings.load_config()['tooltip_text_scale'], 1.0)

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
        pet.appearance = appearance_controls.Appearance(pet)
        pet.tooltips = tooltips.Tooltips(pet)
        pet.preferences = app_preferences.PetPreferences(pet)
        pet.cfg, pet.pets = {}, {}
        pet.placement = MagicMock()  # where the window sits: not what this test is about
        with tempfile.TemporaryDirectory() as directory, \
                support.paths(HOME_DIR=directory), \
                support.paths(CONFIG_PATH=str(Path(directory) / 'config.json')), \
                patch.dict(preferences.SCALE), patch.dict(preferences.ANSWER_WAIT), \
                patch.dict(preferences.DONE_TIMEOUT), patch.dict(preferences.HEALTH):
            pet.appearance.set_size(1.35)
            pet.preferences.set_answer_wait(75)
            pet.preferences.set_done_timeout(8)
            pet.preferences.set_health_interval(25)
            expected = {'size': 1.35, 'answer_wait_seconds': 75,
                        'done_timeout_minutes': 8, 'health_check_seconds': 25}
            self.assertEqual(pet.cfg, expected)
            self.assertEqual(json.loads(Path(paths.CONFIG_PATH).read_text()), expected)
            self.assertEqual((Path(directory) / 'answer-wait').read_text(), '75')
            self.assertEqual(preferences.SCALE['v'], 1.35 * preferences.SCALE_UNIT)
            self.assertEqual([preferences.ANSWER_WAIT['v'], preferences.DONE_TIMEOUT['v'], preferences.HEALTH['v']], [75, 8, 25])
            pet.placement.reposition.assert_called_once()
            pet.preferences.set_answer_wait(0)
            pet.preferences.set_done_timeout(0)
            pet.preferences.set_health_interval(0)
            self.assertEqual((Path(directory) / 'answer-wait').read_text(), '0')
            self.assertEqual([preferences.ANSWER_WAIT['v'], preferences.DONE_TIMEOUT['v'], preferences.HEALTH['v']], [0, 0, 0])

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
        with patch.dict(theme.T, name="dark"):
            self.assertNotEqual(light, settings.signature(a))

    def test_labels(self):
        self.assertEqual(settings.clean("Pet size..."), "Pet size")
        self.assertEqual(settings.page_name("Permissions (auto approve ON)"), "Permissions")
        for name, rows in settings.ICONS.items():
            self.assertEqual(len(rows), 9, name)
            self.assertTrue(all(len(r) == 9 for r in rows), name)

    @patch.object(aipet_app.app_menus.hook_backups, 'list_backups', return_value=[])
    @patch.object(permissions, 'auto_approve_rules', return_value={})
    def test_real_spec_has_icons_and_help_for_every_page(self, *_):
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
        pages = settings.pages_from_spec(app._mac_menu_spec())
        self.assertEqual([p[0] for p in pages],
                         ["General", "Live Management", "Appearance", "Behavior", "Integrations", "Permissions", "Help"])
        for name, icon, help_text, entries, _ in pages:
            self.assertIn(icon, settings.ICONS, name)
            self.assertTrue(help_text, name)
        toggles = [e for _, e in settings.flatten(app._mac_menu_spec()) if "checked" in e and not e.get("choice")]
        self.assertTrue(toggles)
        dark = next(e for e in toggles if e["label"] == "Dark theme")
        self.assertTrue(dark["help"])
        self.assertIs(dark["action"].__func__, aipet_app.TrayApp.toggle_theme)  # the very same action as the menu
        rows = {e['label']: e for _, e in settings.flatten(app._mac_menu_spec())}
        for label, setter in (("Pet size...", app.pet.appearance.set_size), ("Answer timeout...", app.pet.preferences.set_answer_wait),
                              ("Clear finished after...", app.pet.preferences.set_done_timeout),
                              ("Health check every...", app.pet.preferences.set_health_interval)):
            self.assertIs(rows[label]['slider']['set'], setter)
            self.assertIsNot(rows[label]['slider']['set'], rows[label]['action'])


if __name__ == "__main__":
    unittest.main()
