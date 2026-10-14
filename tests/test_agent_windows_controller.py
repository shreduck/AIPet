"""The tray app's agent network windows: one for every session, or one per session, and switching between them."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class FakeWindow:
    made = []

    def __init__(self, root, pet, key, pinned=False, on_options=None, cascade=0, at=None):
        self.key, self.pinned, self.cascade, self.at, self.open = key, pinned, cascade, at, True
        self.on_options = on_options
        self.shown, self.options = [], None
        self.win = SimpleNamespace(geometry=lambda: "600x400+10+20")
        FakeWindow.made.append(self)

    def alive(self):
        return self.open

    def close(self):
        self.open = False

    def show_session(self, key):
        self.shown.append(key)

    def raise_(self):
        pass

    def set_options(self, opts):
        self.options = opts


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class AgentWindowsTests(unittest.TestCase):
    def setUp(self):
        from aipetlib.app import agent_windows
        FakeWindow.made = []
        for target, name, value in ((agent_windows.agent_window, "AgentNetworkWindow", FakeWindow),
                                    (agent_windows.runtime_settings, "save_setting", MagicMock())):
            stub = patch.object(target, name, value)
            stub.start()
            self.addCleanup(stub.stop)
        items = [{"key": f"cc:{k}", "source": "CC", "session_id": k, "state": "working", "agent": "claude"}
                 for k in "abc"]
        self.app = SimpleNamespace(root=MagicMock(), pet=SimpleNamespace(cfg={}, _last_items=items),
                                   refresh_menu=MagicMock())
        self.windows = agent_windows.AgentWindows(self.app)

    def separate(self, on):
        from aipetlib.ui.agent_common import OPTIONS_KEY
        self.app.pet.cfg[OPTIONS_KEY] = {"separate": on}

    def test_one_window_for_every_session_switches_session(self):
        self.windows.show("cc:a")
        self.windows.show("cc:b")
        self.assertEqual([w.key for w in FakeWindow.made], ["cc:a"])
        self.assertEqual(FakeWindow.made[0].shown, ["cc:b"])
        self.assertTrue(self.windows.is_open())

    def test_one_window_per_session(self):
        self.separate(True)
        self.windows.show()
        self.assertEqual(sorted(w.key for w in FakeWindow.made), ["cc:a", "cc:b", "cc:c"])
        self.assertTrue(all(w.pinned for w in FakeWindow.made))

    def test_toggle_closes_what_is_open(self):
        self.windows.toggle("cc:a")
        self.windows.toggle()
        self.assertFalse(FakeWindow.made[0].open)
        self.assertFalse(self.windows.is_open())
        self.assertEqual(self.app.refresh_menu.call_count, 2)

    def test_switching_mode_reopens_where_the_window_was(self):
        self.windows.show("cc:a")
        self.windows.toggle_separate()  # one per session now
        self.assertFalse(FakeWindow.made[0].open)
        reopened = FakeWindow.made[1:]
        self.assertEqual(sorted(w.key for w in reopened), ["cc:a", "cc:b", "cc:c"])
        self.assertTrue(all(w.at == "600x400+10+20" for w in reopened))

    def test_a_changed_look_reaches_the_other_windows(self):
        self.separate(True)
        self.windows.show()
        first, *others = FakeWindow.made
        self.windows.options_changed({"alpha": 80}, first, "alpha")
        self.assertIsNone(first.options)
        self.assertTrue(all(w.options == {"alpha": 80} for w in others))


if __name__ == "__main__":
    unittest.main()
