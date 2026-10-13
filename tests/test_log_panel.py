"""LogPanel behaviour that needs no real window: dragging the split line resizes the logs."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class SplitDragTests(unittest.TestCase):
    def panel(self, side):
        from aipetlib.ui.agent_logs import LogPanel
        window = SimpleNamespace(opts={"log_side": side, "log_lines": 6, "log_cols": 60}, px=lambda n: n,
                                 f_main=MagicMock(), win=MagicMock())
        window.f_main.metrics.return_value = 10  # line height
        window.f_main.measure.return_value = 8  # character width
        window.win.winfo_height.return_value = window.win.winfo_width.return_value = 1000
        panel = LogPanel(window)
        panel.text, panel.tabs = MagicMock(), MagicMock()
        panel.text.winfo_height.return_value, panel.text.winfo_width.return_value = 60, 480
        panel.tabs.winfo_height.return_value = 20
        panel.size = MagicMock()
        return window, panel

    def test_dragging_the_split_up_gives_the_logs_more_lines(self):
        window, panel = self.panel("bottom")
        panel.split_start(SimpleNamespace(x_root=0, y_root=500))
        panel.split_move(SimpleNamespace(x_root=0, y_root=400))  # 100 px up: 10 more lines of 10 px
        self.assertEqual(window.opts["log_lines"], 16)
        panel.size.assert_called_once()

    def test_dragging_a_side_split_changes_the_width_in_characters(self):
        window, panel = self.panel("right")
        panel.split_start(SimpleNamespace(x_root=500, y_root=0))
        panel.split_move(SimpleNamespace(x_root=420, y_root=0))  # 80 px left: 10 more characters of 8 px
        self.assertEqual(window.opts["log_cols"], 70)


if __name__ == "__main__":
    unittest.main()
