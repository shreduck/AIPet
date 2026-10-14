"""The agent network windows take their networks from the background tracker, never reading transcripts on the UI
thread: what the tracker publishes, how a window uses it, and that it equals what the window used to read."""
import importlib.util
import unittest
from unittest.mock import patch

import aipetlib.agents.network as agent_network
import aipetlib.agents.tracker as agent_tracker
import test_agents  # its Claude session fixture (not its tests)


class PublishedNetworkTests(unittest.TestCase):
    """Reuses the Claude session (main agent, two subagents, tool calls) of test_agents."""
    item = test_agents.ClaudeNetworkTests.item

    def setUp(self):
        test_agents.ClaudeNetworkTests.setUp(self)
        agent_tracker._NETWORKS.clear()
        self.addCleanup(agent_tracker._NETWORKS.clear)

    def test_without_tool_nodes_equals_a_read_without_tools(self):
        item = self.item()
        with_tools = agent_network.read_network(item, now=self.now, include_tools=True)
        plain = agent_network.read_network(item, now=self.now, include_tools=False)
        self.assertTrue(any(a.get("tool") for a in with_tools["agents"]))  # the fixture has tool nodes
        filtered = agent_network.without_tools(with_tools)
        self.assertEqual(filtered["agents"], plain["agents"])
        self.assertEqual(filtered["log"], [row for row in plain["log"] if row in filtered["log"]])
        self.assertTrue(all(row[1] < len(plain["agents"]) for row in filtered["log"]))

    def test_tracker_publishes_each_session_and_forgets_gone_ones(self):
        item = dict(self.item(), source="CC", key="cc:sess")
        items = [item]
        tracker = agent_tracker.NetworkTracker(lambda: items)
        tracker.poll()
        net = agent_tracker.latest("cc:sess")
        self.assertEqual([a["name"] for a in net["agents"] if not a.get("tool")], ["main", "find callers", "Plan"])
        items.clear()
        tracker.poll()
        self.assertIsNone(agent_tracker.latest("cc:sess"))

    def test_stop_wakes_the_tracker_at_once(self):
        tracker = agent_tracker.NetworkTracker(lambda: [], interval=60)
        tracker.start()
        self.assertTrue(agent_tracker.running() or tracker.is_alive())
        tracker.stop()
        tracker.join(2)
        self.assertFalse(tracker.is_alive())
        self.assertFalse(agent_tracker.running())


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class WindowNetworkTests(unittest.TestCase):
    def window(self, tools=True):
        from aipetlib.ui.agent_window import AgentNetworkWindow
        win = object.__new__(AgentNetworkWindow)
        win.opts = {"tools": tools}
        return win

    def net(self):
        return {"agents": [{"id": "", "name": "main"}, {"id": "tool:x", "name": "Bash", "tool": True}],
                "log": [(1, 0, "say", "hi"), (2, 1, "input", "ls")]}

    def test_with_the_tracker_running_the_window_never_reads_transcripts(self):
        net = self.net()
        with patch.object(agent_tracker, "running", return_value=True), \
                patch.object(agent_tracker, "latest", return_value=net), \
                patch.object(agent_network, "read_network", side_effect=AssertionError("read on the UI thread")):
            self.assertIs(self.window()._network({"key": "k"}), net)
            plain = self.window(tools=False)._network({"key": "k"})
        self.assertEqual([a["name"] for a in plain["agents"]], ["main"])
        self.assertEqual(plain["log"], [(1, 0, "say", "hi")])

    def test_a_session_not_read_yet_shows_empty_and_wakes_the_tracker(self):
        with patch.object(agent_tracker, "running", return_value=True), \
                patch.object(agent_tracker, "latest", return_value=None), \
                patch.object(agent_tracker, "wake") as wake:
            self.assertEqual(self.window()._network({"key": "k"}), {"agents": [], "log": []})
        wake.assert_called_once()

    def test_without_a_tracker_the_window_reads_itself(self):
        net = self.net()
        with patch.object(agent_tracker, "running", return_value=False), \
                patch.object(agent_network, "read_network", return_value=net) as read:
            self.assertIs(self.window(tools=False)._network({"key": "k"}), net)
        read.assert_called_once_with({"key": "k"}, include_tools=False)


if __name__ == "__main__":
    unittest.main()
