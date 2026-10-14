import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import aipet
import aipetlib.agents.codex as agent_codex
import aipetlib.agents.history as agent_history
import aipetlib.agents.network as agent_network
import aipetlib.agents.tracker as agent_tracker
from test_agents import claude_assistant, iso, write_lines
from test_tool_nodes import codex


class AgentTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        agent_history._TAILS.clear()
        agent_codex._CODEX_META.clear()
        self.now = time.time()

    def session(self, provider):
        path = os.path.join(self.tmp.name, "rollout-2026-10-12T12-00-00-root.jsonl" if provider == "codex"
                            else "root.jsonl")
        return {"source": "CC", "agent": provider, "session_id": "root", "transcript_path": path,
                "state": "working", "key": provider}

    def entries(self, provider):
        t = self.now
        if provider == "codex":
            return [codex(t, {"model": "gpt-example", "effort": "medium"}, "turn_context"),
                    codex(t, {"type": "function_call", "name": "Read", "call_id": "read",
                              "arguments": '{"path":"file"}'}),
                    {"type": "padding", "padding": "x" * 8000},
                    codex(t + 1, {"type": "function_call_output", "call_id": "read", "output": "contents"})]
        return [claude_assistant(t, [{"type": "tool_use", "name": "Read", "id": "read", "input": {"path": "file"}}]),
                {"type": "padding", "padding": "x" * 8000},
                {"type": "user", "timestamp": iso(t + 1), "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "read", "content": "contents"}]}}]

    def test_mid_session_open_recovers_old_input_and_model_for_both_providers(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider), patch.object(agent_history, "MAIN_TAIL", 300), \
                    patch.object(agent_tracker, "_ACTIVE", [False]), \
                    patch.object(agent_tracker, "_WAKE", threading.Event()):
                item = self.session(provider)
                write_lines(item["transcript_path"], self.entries(provider))
                foreground = agent_network.read_network(item, now=self.now + 2, include_tools=True)
                self.assertFalse(any(a.get("tool") for a in foreground["agents"]))
                tracker = agent_tracker.NetworkTracker(lambda: [item])
                tracker.poll()  # no agent window exists
                self.assertFalse(any(a.get("tool") for a in agent_tracker.latest(item["key"])["agents"]))
                agent_tracker.set_active(True)  # opening the window requests full history
                self.assertTrue(agent_tracker._WAKE.is_set())
                tracker.poll()  # the background worker catches up before publishing to the window
                reopened = agent_tracker.latest(item["key"])
                tool = next(a for a in reopened["agents"] if a.get("tool"))
                self.assertEqual(tool["name"], "Read")
                self.assertEqual([row[1] for row in tool["lines"]], ["input", "output"])
                self.assertTrue(reopened["agents"][0]["model"])
                self.assertFalse(reopened["missing"])

    def test_append_burst_larger_than_tail_does_not_lose_request(self):
        item = self.session("codex")
        write_lines(item["transcript_path"], [codex(self.now, {"type": "task_started"}, "event_msg")])
        tracker = agent_tracker.NetworkTracker(lambda: [item])
        with patch.object(agent_history, "MAIN_TAIL", 300):
            tracker.poll()
            with open(item["transcript_path"], "a", encoding="utf-8") as f:
                for entry in self.entries("codex"):
                    f.write(json.dumps(entry) + "\n")
            tracker.poll()
            net = agent_network.read_network(item, now=self.now + 2, include_tools=True)
        self.assertEqual(next(a for a in net["agents"] if a.get("tool"))["name"], "Read")

    def test_partial_line_is_consumed_when_completed(self):
        path = self.session("codex")["transcript_path"]
        write_lines(path, [codex(self.now, {"model": "model"}, "turn_context")])
        agent_history._cached(path, 300, agent_codex.parse_codex)
        encoded = json.dumps(codex(self.now, {"type": "function_call", "name": "Read", "call_id": "read",
                                             "arguments": "café"}), ensure_ascii=False).encode("utf-8")
        with open(path, "ab") as f:
            f.write(encoded[:80])
        self.assertEqual(agent_history._cached(path, 300, agent_codex.parse_codex)["tool_calls"], [])
        with open(path, "ab") as f:
            f.write(encoded[80:] + b"\n")
        self.assertIn("café", agent_history._cached(path, 300, agent_codex.parse_codex)["tool_calls"][0]["input"])

    def test_bootstrap_merges_calls_across_record_batches(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                item = self.session(provider)
                entries = self.entries(provider)
                entries[-1:-1] = [{"type": "padding"} for _ in range(300)]
                write_lines(item["transcript_path"], entries)
                agent_tracker.NetworkTracker(lambda: [item]).poll()
                net = agent_network.read_network(item, now=self.now + 2, include_tools=True)
                tool = next(a for a in net["agents"] if a.get("tool"))
                self.assertEqual([row[1] for row in tool["lines"]], ["input", "output"])
                self.assertIn("file", tool["lines"][0][2])
                self.assertIn("contents", tool["lines"][1][2])

    def test_unchanged_files_are_not_read_again(self):
        item = self.session("codex")
        write_lines(item["transcript_path"], self.entries("codex"))
        tracker = agent_tracker.NetworkTracker(lambda: [item])
        tracker.poll()
        with patch.object(agent_history, "_read_updates", wraps=agent_history._read_updates) as read:
            tracker.poll()
        read.assert_not_called()

    def test_truncated_file_does_not_keep_old_calls(self):
        path = self.session("codex")["transcript_path"]
        write_lines(path, self.entries("codex"))
        agent_history._cached(path, 10000, agent_codex.parse_codex)
        write_lines(path, [codex(self.now, {"model": "replacement"}, "turn_context")])
        info = agent_history._cached(path, 10000, agent_codex.parse_codex)
        self.assertEqual(info["model"], "replacement")
        self.assertEqual(info["tool_calls"], [])

    def test_background_worker_stops_without_a_window(self):
        polled = threading.Event()
        tracker = agent_tracker.NetworkTracker(lambda: polled.set() or [], interval=60)
        tracker.start()
        try:
            self.assertTrue(polled.wait(1))
        finally:
            tracker.stop()
            tracker.join(timeout=1)
        self.assertFalse(tracker.is_alive())

    def test_tracking_includes_sessions_beyond_visible_pet_limit(self):
        app = object.__new__(aipet.PetApp)
        app.cfg = {"max_pets": 1}
        app.wb, app._slots, app._slot_n = None, {}, 0
        items = [dict(self.session("codex"), key=str(i), changed=self.now) for i in range(2)]
        with patch.object(aipet, "read_claude_code_sessions", return_value=items), \
                patch.object(aipet, "disambiguate_titles"):
            shown = app.collect()
        self.assertEqual(len(shown), 1)
        self.assertEqual(len(app._agent_items), 2)


if __name__ == "__main__":
    unittest.main()
