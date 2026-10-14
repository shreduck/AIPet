"""Tool counts for the whole session and this task (tool nodes, stats node), and folding the agent window's logs."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import aipetlib.agents.stats as agent_stats
import aipetlib.agents.tools as agent_tools


def call(cid, name, t):
    return {"id": cid, "name": name, "input": "{}", "started_t": t, "finished_t": t + 1, "output": "ok"}


def main_agent(calls, lines=()):
    return {"id": "", "name": "main", "main": True, "depth": 0, "state": "done", "path": "t.jsonl",
            "tool_calls": calls, "lines": list(lines), "ctx": 0, "tokens": None}


class CountTests(unittest.TestCase):
    def test_tool_node_counts_the_session_and_this_task(self):
        calls = [call(f"r{k}", "Read", 100 + k) for k in range(12)] + [call("g", "Grep", 105)]
        nodes = {n["name"]: n for n in agent_tools.tool_nodes([main_agent(calls)], now=120, retention=0,
                                                                turn_start=111)}
        self.assertEqual((nodes["Read"]["calls"], nodes["Read"]["task_calls"]), (12, 2))
        self.assertEqual(nodes["Read"]["kind"], "12 calls · 2 this task · main")
        self.assertEqual(nodes["Grep"]["kind"], "1 calls · 0 this task · main")

    def test_stats_list_this_task_and_the_session(self):
        calls = [call(f"r{k}", "Read", 100 + k) for k in range(12)] + [call("t", "Task", 101),
                                                                         call("w", "wait_agent", 102)]
        lines = [(111, "tool", "Read(a)"), (111.5, "tool", "Read(b)"), (99, "tool", "Read(old)")]
        st = agent_stats.network_stats({"agents": [main_agent(calls, lines)], "turn_start": 110})
        self.assertEqual(dict(st["tools"]), {"Read": 2})  # this task
        self.assertEqual(st["session_tools"], {"Read": 12, "Agent": 1})  # waiting is not a tool call

    @unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
    def test_stats_drop_down_shows_both_columns(self):
        from aipetlib.ui.agent_graph import GraphView
        view = GraphView(SimpleNamespace(stats={"tools": [("Read", 2)], "skills": [],
                                                "session_tools": {"Read": 12, "Grep": 13}, "missing": False}))
        lines = [text for text, _ in view.stat_lines("tools")]
        self.assertEqual(lines[0].split(), ["tool", "this", "task", "session"])
        self.assertEqual(lines[1].split(), ["Read", "2", "12"])
        self.assertEqual(lines[2].split(), ["Grep", "-", "13"])  # used before this task only


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class FoldLogsTests(unittest.TestCase):
    def panel(self, folded, side="bottom"):
        from aipetlib.ui.agent_logs import LogPanel
        window = SimpleNamespace(opts={"log_side": side, "log_collapsed": folded, "log_lines": 6}, px=lambda n: n,
                                 graph=SimpleNamespace(canvas=MagicMock()))
        panel = LogPanel(window)
        for name in ("sep", "box", "filterbar", "text"):
            setattr(panel, name, MagicMock())
        return panel

    def test_folded_logs_keep_only_their_tabs(self):
        panel = self.panel(True)
        panel.arrange()
        panel.text.pack.assert_not_called()
        panel.filterbar.pack.assert_not_called()
        panel.sep.pack.assert_not_called()  # nothing to drag
        panel.box.pack.assert_called_once()  # the tabs, with [+ logs]
        panel.window.graph.canvas.pack.assert_called_once_with(side="bottom", fill="both", expand=True)

    def test_unfolded_logs_come_back_in_order(self):
        panel = self.panel(False, side="right")
        order = MagicMock()
        for name in ("filterbar", "text", "box", "sep"):
            getattr(panel, name).pack.side_effect = lambda *a, n=name, **k: order(n)
        panel.arrange()
        self.assertEqual([c.args[0] for c in order.call_args_list], ["filterbar", "text", "box", "sep"])

    def test_folded_logs_are_not_drawn(self):
        panel = self.panel(True)
        panel.window.net = None  # drawing would need it
        panel.draw()  # returns at once

    def test_l_and_the_button_toggle_and_save(self):
        from aipetlib.ui.agent_controls import WindowControls
        win = SimpleNamespace(opts={"log_collapsed": False}, logs=MagicMock(), graph=SimpleNamespace(center_next=False),
                              settings=SimpleNamespace(open=False, spin_until=0), _apply_options=MagicMock(),
                              _save=MagicMock(), _refresh=MagicMock())
        WindowControls.tool(win, "fold_logs")
        self.assertTrue(win.opts["log_collapsed"])
        win.logs.arrange.assert_called_once()
        win.logs.draw.assert_not_called()
        win._save.assert_called_with("fold_logs")
        WindowControls.tool(win, "fold_logs")
        self.assertFalse(win.opts["log_collapsed"])
        win.logs.invalidate.assert_called_once()
        win.logs.draw.assert_called_once()


if __name__ == "__main__":
    unittest.main()
