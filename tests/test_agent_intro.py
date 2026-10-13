import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import aipet
import support
import aipet_app


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
        app.show_agent_network = MagicMock()
        app.refresh_menu = MagicMock()
        return app

    def test_accept_decline_and_close_are_remembered_across_restarts(self):
        for result in (True, False, None):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as tmp, \
                    support.paths(HOME_DIR=tmp), \
                    support.paths(CONFIG_PATH=os.path.join(tmp, "config.json")):
                app = self.app()
                with patch.object(aipet, "themed_dialog",
                                  side_effect=lambda *args, **kw: kw["on_result"](result)) as dialog:
                    app.offer_agent_network()
                    app.offer_agent_network()
                    restarted = self.app(aipet.load_config())
                    restarted.offer_agent_network()
                dialog.assert_called_once()
                self.assertTrue(restarted.pet.cfg["agent_network_intro_seen"])
                self.assertEqual(app.show_agent_network.call_count, int(bool(result)))
                restarted.show_agent_network.assert_not_called()

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
                with patch.object(aipet, "themed_dialog") as dialog, patch.object(aipet, "save_setting") as save:
                    app.offer_agent_network()
                dialog.assert_not_called()
                save.assert_not_called()
                app.root.after.assert_called_once_with(1500, app.offer_agent_network)


if __name__ == "__main__":
    unittest.main()
