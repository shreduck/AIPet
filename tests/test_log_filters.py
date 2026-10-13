import unittest
from unittest.mock import MagicMock

import aipetlib.agents.log_filters as agent_log_filters
import aipetlib.agents.tools as agent_tools
import aipet_agents_window as window


def agent(aid, name, lines, state="working", **extra):
    return dict(id=aid, name=name, main=not aid, parent="", lines=lines, state=state, **extra)


def tool(name, parent, t):
    return dict(id=f"tool:{parent}:{name}", name=name, main=False, tool=True, parent=parent, state="done",
                lines=[(t, "input", name + " input"), (t + 0.1, "output", name + " output")])


class LogFilterTests(unittest.TestCase):
    def setUp(self):
        self.net = {"agents": [
            agent("", "main", [(1, "you", "question"), (2, "say", "commentary"),
                                (3, "tool", "Read(file)"), (4, "say", "answer"), (5, "you", "next question"),
                                (6, "think", "thinking"), (7, "tool", "Write(file)"), (8, "say", "still working")],
                  final_lines=[(4, "answer")], queries=[(1, "question"), (5, "next question")]),
            agent("child", "child", [(2, "you", "child task"), (3, "tool", "Read(child)"),
                                       (4, "say", "child result")], state="done"),
            tool("Read", "", 3), tool("Write", "", 7), tool("mcp__demo__search", "", 3.5),
            tool("Read", "child", 3),
        ], "log": []}
        self.filters = agent_log_filters.default_log_filters()

    def test_selected_agent_includes_its_tools_and_excludes_other_agents(self):
        rows = agent_log_filters.filtered_log_rows(self.net, 0, self.filters)
        self.assertEqual({i for _, i, _, _ in rows}, {0, 2, 3, 4})
        self.assertNotIn("Read(file)", [text for _, _, _, text in rows])  # no duplicate summary

    def test_hide_tools_removes_inputs_outputs_and_call_summaries(self):
        self.filters["show_tools"] = False
        rows = agent_log_filters.filtered_log_rows(self.net, -1, self.filters)
        self.assertTrue(all(not self.net["agents"][i].get("tool") for _, i, _, _ in rows))
        self.assertFalse(any(kind in ("input", "output", "tool", "spawn") for _, _, kind, _ in rows))

    def test_primary_shows_queries_and_final_results_without_commentary(self):
        self.filters["primary"] = True
        rows = agent_log_filters.filtered_log_rows(self.net, 0, self.filters)
        self.assertEqual([(kind, text) for _, _, kind, text in rows],
                         [("you", "question"), ("say", "answer"), ("you", "next question")])
        child = agent_log_filters.filtered_log_rows(self.net, 1, self.filters)
        self.assertEqual([(kind, text) for _, _, kind, text in child],
                         [("you", "child task"), ("say", "child result")])

    def test_primary_uses_preserved_queries_even_when_tool_traffic_fills_log(self):
        self.net["agents"][0]["lines"] = [(10 + n, "tool", "Read(file)") for n in range(240)]
        self.net["log"] = [(10 + n, 2, "input", "traffic") for n in range(240)]
        self.filters["primary"] = True
        self.assertEqual(len(agent_log_filters.filtered_log_rows(self.net, 0, self.filters)), 3)

    def test_normal_tools_and_mcp_have_independent_selections(self):
        self.filters.update(tools={"Read"}, mcp=set())
        rows = agent_log_filters.filtered_log_rows(self.net, -1, self.filters)
        names = {self.net["agents"][i]["name"] for _, i, _, _ in rows if self.net["agents"][i].get("tool")}
        self.assertEqual(names, {"Read"})
        self.filters.update(tools=set(), mcp={"mcp__demo__search"})
        rows = agent_log_filters.filtered_log_rows(self.net, -1, self.filters)
        names = {self.net["agents"][i]["name"] for _, i, _, _ in rows if self.net["agents"][i].get("tool")}
        self.assertEqual(names, {"mcp__demo__search"})

    def test_dropdown_lists_unique_tools_for_selected_agent(self):
        self.assertEqual(agent_log_filters.available_log_tools(self.net, -1), ["Read", "Write"])
        self.assertEqual(agent_log_filters.available_log_tools(self.net, 1), ["Read"])
        self.assertEqual(agent_log_filters.available_log_tools(self.net, 0, mcp=True), ["mcp__demo__search"])
        self.assertTrue(agent_tools.is_mcp_tool("functions.mcp__demo__search"))
        self.assertTrue(agent_tools.is_mcp_tool("mcp.demo.search"))
        self.assertFalse(agent_tools.is_mcp_tool("Read"))

    def view(self):
        view = object.__new__(window.AgentNetworkWindow)
        view.net = self.net
        view.sel = -1
        view.logs = window.agent_logs.LogPanel(view)
        view._draw_graph = MagicMock()
        for name in ("draw_tabs", "draw", "update_filters"):
            setattr(view.logs, name, MagicMock())
        return view

    def test_tool_node_filters_under_owner_and_agent_selection_keeps_filters(self):
        view = self.view()
        view.select(2)
        self.assertEqual(view.sel, 0)
        rows = agent_log_filters.filtered_log_rows(view.net, view.sel, view.logs.filters)
        self.assertEqual({i for _, i, _, _ in rows}, {2})
        before = dict(view.logs.filters)
        view.select(1)
        self.assertEqual(view.logs.filters, before)
        rows = agent_log_filters.filtered_log_rows(view.net, view.sel, view.logs.filters)
        self.assertEqual({i for _, i, _, _ in rows}, {5})
        view.logs.clear_filters()
        self.assertEqual(view.logs.filters, agent_log_filters.default_log_filters())

    def test_tool_node_selects_mcp_filter_when_appropriate(self):
        view = self.view()
        view.select(4)
        self.assertEqual(view.logs.filters["mcp"], {"mcp__demo__search"})
        self.assertEqual(view.logs.filters["tools"], set())
        rows = agent_log_filters.filtered_log_rows(view.net, view.sel, view.logs.filters)
        self.assertEqual({i for _, i, _, _ in rows}, {4})

    def test_agent_keyboard_navigation_skips_tool_nodes(self):
        view = self.view()
        view.sel = 1
        view.select = MagicMock()
        view.step_agent(1)
        view.select.assert_called_once_with(-1)


if __name__ == "__main__":
    unittest.main()
