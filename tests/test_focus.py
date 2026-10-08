import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet


class GoToWindowTests(unittest.TestCase):
    def test_desktop_app_session_goes_to_the_app_even_with_a_matching_terminal_open(self):
        d = {"agent": "claude", "entry": "claude-desktop", "env": "wsl", "distro": "Ubuntu", "cwd": "/home/x/esign-online"}
        cowork = {"key": "cc:other", "hwnd": 500, "app": "cowork", "cwd": "C:/x"}  # recorded the same app window
        with patch.object(aipet, "terminal_windows", return_value=[(100, "Ubuntu: esign-online")]), \
                patch.object(aipet, "claude_app_windows", return_value=[500]), \
                patch.object(aipet, "focus_hwnd", return_value=True) as focus:
            self.assertTrue(aipet.focus_wsl_terminal(d, [cowork]))
        focus.assert_called_once_with(500)

    def test_terminal_session_still_matches_its_terminal(self):
        d = {"agent": "claude", "entry": "cli", "env": "wsl", "distro": "Ubuntu", "cwd": "/home/x/esign-online"}
        with patch.object(aipet, "terminal_windows", return_value=[(100, "Ubuntu: esign-online")]), \
                patch.object(aipet, "claude_app_windows", return_value=[500]), \
                patch.object(aipet, "focus_hwnd", return_value=True) as focus:
            aipet.focus_wsl_terminal(d, [])
        focus.assert_called_once_with(100)

    def test_stale_cowork_window_falls_back_to_the_app(self):
        app = object.__new__(aipet.PetApp)
        app._last_items = []
        pet = SimpleNamespace(key="cc:s", data={"source": "CC", "hwnd": 123, "app": "cowork", "entry": "local-agent",
                                               "agent": "claude", "env": "windows"})
        with patch.object(aipet.os, "name", "nt"), patch.object(aipet, "focus_hwnd", return_value=False), \
                patch.object(aipet, "focus_wsl_terminal") as fallback:
            app.focus_session(pet)
        fallback.assert_called_once()

    def test_a_window_that_came_to_the_front_needs_no_fallback(self):
        app = object.__new__(aipet.PetApp)
        app._last_items = []
        pet = SimpleNamespace(key="cc:s", data={"source": "CC", "hwnd": 123, "env": "wsl"})
        with patch.object(aipet.os, "name", "nt"), patch.object(aipet, "focus_hwnd", return_value=True), \
                patch.object(aipet, "focus_wsl_terminal") as fallback:
            app.focus_session(pet)
        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
