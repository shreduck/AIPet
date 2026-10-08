"""Several permission prompts at once (parallel tool calls / subagents): answering one must not mark the session as
working while Claude Code is still waiting on the others."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import aipet_hook as hook


def call(i, cmd):
    return {"session_id": "s", "tool_use_id": f"toolu_{i}", "tool_name": "Bash", "tool_input": {"command": cmd}}


class PermissionQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "s.json")
        self.target = os.path.join(self.tmp.name, "sessions", "s.json")
        os.makedirs(os.path.dirname(self.target))
        self.patches = [patch.object(hook, "AGENT", "claude"), patch.object(hook, "find_claude_pid", return_value=None),
                        patch.object(hook, "find_host_window", return_value=(None, "")),
                        patch.object(hook, "debug_log"), patch.object(hook, "is_cowork", return_value=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def event(self, name, data):
        data = dict(data, hook_event_name=name)
        if name == "PermissionRequest":
            data.pop("tool_use_id", None)  # Claude Code's PermissionRequest carries none
            data["_aipet_request_id"] = "req-" + data["tool_input"]["command"]
        hook._update_session(self.path, self.target, name, data, False)
        return data.get("_aipet_request_id")

    def record(self):
        with open(self.path) as f:
            return json.load(f)

    def ask_two(self):
        self.event("UserPromptSubmit", {"session_id": "s", "prompt": "go"})
        for i, cmd in ((1, "rm a"), (2, "rm b")):  # parallel calls: both PreToolUse first, then both prompts
            self.event("PreToolUse", call(i, cmd))
        return [self.event("PermissionRequest", call(i, cmd)) for i, cmd in ((1, "rm a"), (2, "rm b"))]

    def test_prompts_queue_and_link_to_their_tool_calls(self):
        first, second = self.ask_two()
        rec = self.record()
        self.assertEqual(rec["state"], "needs_input")
        self.assertEqual([r["id"] for r in rec["requests"]], [first, second])
        self.assertEqual([r["tool_use_id"] for r in rec["requests"]], ["toolu_1", "toolu_2"])
        self.assertEqual(rec["request"]["id"], first)  # the oldest is on screen

    def test_answering_one_in_the_terminal_keeps_waiting_for_the_other(self):
        first, second = self.ask_two()
        self.event("PostToolUse", call(1, "rm a"))
        rec = self.record()
        self.assertEqual(rec["state"], "needs_input")
        self.assertEqual([r["id"] for r in rec["requests"]], [second])
        self.assertIn("Bash", rec["message"])
        self.event("PostToolUse", call(2, "rm b"))
        self.assertEqual(self.record()["state"], "working")

    def test_answering_from_the_pet_shows_the_next_prompt(self):
        first, second = self.ask_two()
        with patch.object(hook, "pet_alive", return_value=True), patch.object(hook, "answers_enabled", return_value=True), \
                patch.object(hook, "await_answer", return_value="allow"), patch.object(hook, "write_stdout") as out:
            hook.answer_flow(self.tmp.name, self.path, "", first)
        out.assert_called_once_with(hook.decision_output("allow"))
        rec = self.record()
        self.assertEqual(rec["state"], "needs_input")
        self.assertEqual(rec["request"]["id"], second)
        self.event("PostToolUse", call(1, "rm a"))  # the allowed tool runs: the second prompt must stay
        self.assertEqual(self.record()["request"]["id"], second)
        with patch.object(hook, "pet_alive", return_value=True), patch.object(hook, "answers_enabled", return_value=True), \
                patch.object(hook, "await_answer", return_value="allow"), patch.object(hook, "write_stdout"):
            hook.answer_flow(self.tmp.name, self.path, "", second)
        self.assertEqual(self.record()["state"], "working")

    def test_waiting_hook_keeps_waiting_while_its_prompt_is_queued(self):
        first, second = self.ask_two()
        answer = os.path.join(self.tmp.name, "answers", "x.json")
        os.makedirs(os.path.dirname(answer))
        calls = {"n": 0}

        def tick(_):
            calls["n"] += 1
            if calls["n"] == 1:
                with open(answer, "w") as f:
                    json.dump({"behavior": "deny"}, f)
        with patch.object(hook, "pet_alive", return_value=True), patch.object(hook, "answers_enabled", return_value=True), \
                patch.object(hook.time, "sleep", side_effect=tick), patch.object(hook.time, "time", side_effect=range(0, 10**6, 3)):
            # the second prompt is not on screen, but it's still pending: no early give-up
            self.assertEqual(hook._await(answer, answer + ".waiting", self.tmp.name, 0, self.path, second), "deny")

    def test_new_turn_clears_the_queue(self):
        self.ask_two()
        self.event("UserPromptSubmit", {"session_id": "s", "prompt": "never mind"})
        rec = self.record()
        self.assertEqual((rec["state"], rec["requests"], rec["request"]), ("working", [], {}))

    def test_single_prompt_still_settles_on_its_tool_finishing(self):
        self.event("PreToolUse", call(1, "ls"))
        self.event("PermissionRequest", call(1, "ls"))
        self.event("PreToolUse", call(2, "cat x"))  # a parallel call starting doesn't answer it
        self.assertEqual(self.record()["state"], "needs_input")
        self.event("PostToolUse", call(1, "ls"))
        self.assertEqual(self.record()["state"], "working")


if __name__ == "__main__":
    unittest.main()
