"""One-time introduction persistence, deferred startup and reusable clipboard guide."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import aipet_app
import aipet_guide
from aipetlib.app import addin_intro
from aipetlib.config import ConfigStore


class AddinIntroTests(unittest.TestCase):
    def test_alert_options_persist_and_apply_to_an_existing_window(self):
        app = object.__new__(aipet_app.TrayApp)
        app.pet = SimpleNamespace(cfg={})
        app._addin_alert_window, app.refresh_menu = MagicMock(), MagicMock()
        with patch.object(aipet_app.runtime_settings, 'save_setting', return_value=True) as save:
            self.assertTrue(app.set_addin_alert_option('addin_alert_topmost', False))
            save.assert_called_once_with('addin_alert_topmost', False)
            self.assertIs(app.pet.cfg['addin_alert_topmost'], False)
            app._addin_alert_window.set_addin_options.assert_called_once_with({'addin_alert_topmost': False})

    def app(self, cfg=None):
        return SimpleNamespace(root=MagicMock(), pet=SimpleNamespace(cfg=cfg or {}),
            startup_dialogs_open=MagicMock(return_value=False), copy_addin_guide=MagicMock(return_value=True),
            info=MagicMock())

    def test_dismissal_persists_and_a_new_app_does_not_show_the_introduction(self):
        with tempfile.TemporaryDirectory() as home:
            store = ConfigStore(str(Path(home)/'config.json'), home)
            app = self.app(store.load())
            intro = addin_intro.AddinIntro(app)
            with patch.object(addin_intro.theme, 'themed_dialog') as show, \
                    patch.object(addin_intro.settings, 'save_setting', side_effect=store.save_setting):
                intro.maybe_show()
                show.assert_called_once()
                self.assertFalse(store.load()['addins_intro_seen'])
                intro.maybe_show()
                show.assert_called_once()
                show.call_args.kwargs['on_result'](True)
                self.assertTrue(store.load()['addins_intro_seen'])
                addin_intro.AddinIntro(self.app(store.load())).maybe_show()
                show.assert_called_once()

    def test_intro_waits_for_setup_or_other_confirmation_to_finish(self):
        app = self.app()
        app.startup_dialogs_open.return_value = True
        intro = addin_intro.AddinIntro(app)
        with patch.object(addin_intro.theme, 'themed_dialog') as show:
            intro.maybe_show()
            app.root.after.assert_called_once_with(1000, intro.maybe_show)
            show.assert_not_called()
            self.assertNotIn('addins_intro_seen', app.pet.cfg)

    def test_copy_is_available_without_dismissing_the_introduction(self):
        app = self.app()
        intro = addin_intro.AddinIntro(app)
        with patch.object(addin_intro.theme, 'themed_dialog') as show, \
                patch.object(addin_intro.theme, 'theme_widget'), patch.object(addin_intro.tk, 'Frame'), \
                patch.object(addin_intro.tk, 'Button') as button, patch.object(addin_intro.tk, 'Label'), \
                patch.object(addin_intro.settings, 'save_setting') as save:
            intro.maybe_show()
            show.call_args.kwargs['content_builder'](MagicMock())
            self.assertEqual(button.call_args.kwargs['text'], 'Copy Markdown for AI')
            button.call_args.kwargs['command']()
            app.copy_addin_guide.assert_called_once_with(notify=False)
            save.assert_not_called()
            self.assertNotIn('addins_intro_seen', app.pet.cfg)

    def test_copy_uses_standalone_guide_with_current_interface_and_install_folder(self):
        app = object.__new__(aipet_app.TrayApp)
        app.root, app.addins, app.info = MagicMock(), MagicMock(), MagicMock()
        app.version = 'test'
        app.pet = SimpleNamespace(cfg={'addins': {'fixture': True}})
        app.addins.entries.return_value = [('fixture', {'name': 'Fixture'})]
        app.addins.get_settings.return_value = []
        self.assertTrue(app.copy_addin_guide(notify=False))
        copied = app.root.clipboard_append.call_args.args[0]
        for text in ('# Build an AIPet add-in', 'addin.json', 'api_version', 'get_settings',
                     'timeout', 'themed alert windows', 'account permissions', 'pet.warning(', 'window="pet"'):
            self.assertIn(text, copied)
        app.info.assert_not_called()
        self.assertNotIn('# AIPet configuration guide', copied)

    def test_standalone_guide_uses_requested_home(self):
        self.assertIn('/test-home', aipet_guide.build_addin_guide('/test-home'))


if __name__ == '__main__':
    unittest.main()
