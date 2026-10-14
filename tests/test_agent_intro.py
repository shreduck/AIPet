import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import aipet
import support
import aipet_app
from aipetlib.app import agent_windows


class AgentIntroTests(unittest.TestCase):
    def setUp(self):
        setup = patch.object(aipet_app, "setup_is_done", return_value=True)
        setup.start()
        self.addCleanup(setup.stop)

    def app(self, cfg=None):
        app = object.__new__(aipet_app.TrayApp)
        app.pet = MagicMock(cfg=cfg or {})
        app.root = MagicMock()
        app.root.grab_current.return_value = None
        app.setup_win = None
        app.busy = False
        app.agent_windows = agent_windows.AgentWindows(app)
        app.agent_windows.show = MagicMock()
        app.refresh_menu = MagicMock()
        return app

    def test_accept_decline_and_close_are_remembered_across_restarts(self):
        for result in (True, False, None):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as tmp, \
                    support.paths(HOME_DIR=tmp), \
                    support.paths(CONFIG_PATH=os.path.join(tmp, "config.json")):
                app = self.app()
                with patch.object(agent_windows.theme, "themed_dialog",
                                  side_effect=lambda *args, **kw: kw["on_result"](result)) as dialog:
                    app.agent_windows.offer_intro()
                    app.agent_windows.offer_intro()
                    restarted = self.app(aipet.load_config())
                    restarted.agent_windows.offer_intro()
                dialog.assert_called_once()
                self.assertTrue(restarted.pet.cfg["agent_network_intro_seen"])
                self.assertEqual(app.agent_windows.show.call_count, int(bool(result)))
                restarted.agent_windows.show.assert_not_called()

    def test_waits_for_setup_and_existing_dialogs(self):
        for reason in ("setup", "busy", "dialog"):
            with self.subTest(reason=reason):
                app = self.app()
                if reason == "setup":
                    app.setup_win = MagicMock()
                elif reason == "busy":
                    app.busy = True
                else:
                    app.root.grab_current.return_value = MagicMock()
                with patch.object(agent_windows.theme, "themed_dialog") as dialog, \
                        patch.object(agent_windows.runtime_settings, "save_setting") as save:
                    app.agent_windows.offer_intro()
                dialog.assert_not_called()
                save.assert_not_called()
                app.root.after.assert_called_once_with(1500, app.agent_windows.offer_intro)


if __name__ == "__main__":
    unittest.main()
