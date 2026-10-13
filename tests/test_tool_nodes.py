import json
import os
import tempfile
import unittest

import aipetlib.agents.claude as agent_claude
import aipetlib.agents.codex as agent_codex
import aipetlib.agents.history as agent_history
import aipetlib.agents.network as agent_network
import aipetlib.agents.records as agent_records
import aipetlib.agents.stats as agent_stats
import aipetlib.agents.tools as agent_tools
from test_agents import claude_assistant, iso, write_lines


def codex(t, payload, kind="response_item"):
    return {"type": kind, "timestamp": iso(t), "payload": payload}


def caller(aid, calls, depth=0, state="working"):
    return {"id": aid, "name": aid or "main", "depth": depth, "state": state,
            "path": "transcript.jsonl", "tool_calls": calls}


class ToolNodeTests(unittest.TestCase):
    def test_claude_repeated_mcp_calls_match_outputs_and_errors_by_id(self):
        name = "mcp__demo__search"
        info = agent_claude.parse_claude([
            claude_assistant(100, [{"type": "tool_use", "id": "one", "name": name, "input": {"q": "first"}},
                                   {"type": "tool_use", "id": "two", "name": name, "input": {"q": "second"}}]),
            {"type": "user", "timestamp": iso(101), "message": {"content": [
                {"type": "tool_result", "tool_use_id": "two", "content": "second result"},
                {"type": "tool_result", "tool_use_id": "one", "content": "first failed", "is_error": True}]}},
        ])
        nodes = agent_tools.tool_nodes([caller("child", info["tool_calls"], depth=2)], now=102, retention=144)
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual((node["name"], node["parent"], node["depth"]), (name, "child", 3))
        self.assertEqual(len(node["lines"]), 4)
        calls = {c["id"]: c for c in info["tool_calls"]}
        self.assertEqual(calls["two"]["output"], "second result")
        self.assertTrue(calls["one"]["error"])
        self.assertIn('"q": "first"', calls["one"]["input"])

    def test_codex_function_custom_and_native_tools_have_inputs_and_outputs(self):
        info = agent_codex.parse_codex([
            codex(100, {"type": "function_call", "call_id": "mcp", "name": "mcp__demo__search",
                        "arguments": '{"query":"hello"}'}),
            codex(101, {"type": "custom_tool_call", "call_id": "script", "name": "exec", "input": "do_work()"}),
            codex(102, {"type": "function_call_output", "call_id": "mcp", "output": '{"result":"found"}'}),
            codex(103, {"type": "custom_tool_call_output", "call_id": "script", "output": "script output"}),
            codex(104, {"type": "item_started", "item": {"type": "CommandExecution", "id": "command",
                                                         "command": ["pwd"], "cwd": "/work"}}, "event_msg"),
            codex(105, {"type": "item_completed", "item": {"type": "CommandExecution", "id": "command",
                        "command": ["pwd"], "cwd": "/work", "aggregated_output": "/work", "exit_code": 0}},
                  "event_msg"),
        ])
        nodes = agent_tools.tool_nodes([caller("", info["tool_calls"])], now=106, retention=144)
        self.assertEqual({n["name"] for n in nodes}, {"mcp__demo__search", "exec", "exec_command"})
        self.assertTrue(all(n["state"] == "done" and len(n["lines"]) == 2 for n in nodes))
        command = next(c for c in info["tool_calls"] if c["id"] == "command")
        self.assertEqual(command["started_t"], 104)
        self.assertEqual(command["output"], "/work")

    def test_each_calling_agent_gets_its_own_tool_node(self):
        calls = [{"id": "call", "name": "Read", "input": "file", "started_t": 100}]
        nodes = agent_tools.tool_nodes([caller("", calls), caller("child", calls, depth=1)], now=101, retention=144)
        self.assertEqual(len({n["id"] for n in nodes}), 2)
        self.assertEqual([(n["parent"], n["depth"]) for n in nodes], [("", 1), ("child", 2)])
        self.assertTrue(all(n["state"] == "working" for n in nodes))

    def test_native_mcp_error_result_marks_tool_as_error(self):
        info = agent_codex.parse_codex([codex(100, {"type": "item_completed", "item": {
            "type": "McpToolCall", "id": "mcp-call", "server": "demo", "tool": "search",
            "arguments": {"q": "hello"}, "result": {"isError": True, "content": [{"text": "failed"}]}},
        }, "event_msg")])
        node = agent_tools.tool_nodes([caller("", info["tool_calls"])], 101, 144)[0]
        self.assertEqual((node["name"], node["state"]), ("mcp__demo__search", "error"))
        self.assertEqual([row[1] for row in node["lines"]], ["input", "error"])

    def test_new_turn_does_not_reactivate_an_unmatched_old_tool_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rollout.jsonl")
            write_lines(path, [
                codex(100, {"type": "function_call", "name": "Read", "call_id": "one", "arguments": "file"}),
                codex(110, {"type": "task_complete"}, "event_msg"),
                codex(200, {"type": "task_started"}, "event_msg"),
            ])
            info = agent_history._cached(path, 10000, agent_codex.parse_codex)
            node = agent_tools.tool_nodes([caller("", info["tool_calls"])], 201, 144)[0]
            self.assertEqual(node["state"], "done")

    def test_busy_tool_history_does_not_evict_other_unique_tools(self):
        previous = [{"id": "read", "name": "Read", "started_t": 100, "finished_t": 101}]
        many = [{"id": str(i), "name": "Write", "started_t": 102 + i, "finished_t": 103 + i}
                for i in range(300)]
        calls = agent_tools._merge_tool_calls(previous, many)
        self.assertIn("Read", {c["name"] for c in calls})
        self.assertEqual(sum(c["name"] == "Write" for c in calls), agent_records.MAX_LOG // 2)

    def test_result_after_request_leaves_tail_keeps_tool_identity_and_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rollout.jsonl")
            write_lines(path, [codex(100, {"type": "function_call", "name": "Read", "call_id": "one",
                                          "arguments": '{"path":"file"}'})])
            agent_history._cached(path, 240, agent_codex.parse_codex)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(codex(101, {"type": "message", "role": "assistant",
                                             "content": [{"text": "x" * 1000}]})) + "\n")
                f.write(json.dumps(codex(102, {"type": "function_call_output", "call_id": "one",
                                             "output": "contents"})) + "\n")
            info = agent_history._cached(path, 240, agent_codex.parse_codex)
            nodes = agent_tools.tool_nodes([caller("", info["tool_calls"])], now=103, retention=144)
            self.assertEqual(nodes[0]["name"], "Read")
            self.assertEqual(nodes[0]["state"], "done")
            self.assertEqual(len(nodes[0]["lines"]), 2)

    def test_finished_tools_expire_and_active_tools_stay(self):
        calls = [{"id": "call", "name": "Read", "input": "file", "started_t": 100, "finished_t": 110}]
        self.assertEqual(len(agent_tools.tool_nodes([caller("", calls)], 253, 144)), 1)
        self.assertEqual(agent_tools.tool_nodes([caller("", calls)], 254, 144), [])
        self.assertEqual(len(agent_tools.tool_nodes([caller("", calls)], 1000, 0)), 1)
        calls[0].pop("finished_t")
        self.assertEqual(agent_tools.tool_nodes([caller("", calls)], 1000, 144)[0]["state"], "working")

    def test_graph_tools_do_not_change_subagent_or_context_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rollout-2026-10-12T12-00-00-root.jsonl")
            write_lines(path, [codex(100, {"type": "function_call", "name": "Read", "call_id": "one",
                                          "arguments": '{"path":"file"}'})])
            item = {"agent": "codex", "session_id": "root", "transcript_path": path, "state": "working"}
            plain = agent_network.read_network(item, now=101)
            tools = agent_network.read_network(item, now=101, include_tools=True)
            self.assertEqual(len(tools["agents"]), 2)
            self.assertEqual(agent_stats.network_stats(plain), agent_stats.network_stats(tools))
            self.assertEqual(tools["agents"][1]["depth"], 1)
            self.assertIn("input", [kind for _, _, kind, _ in tools["log"]])


if __name__ == "__main__":
    unittest.main()
