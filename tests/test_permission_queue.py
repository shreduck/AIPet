"""Several permission prompts at once (parallel tool calls / subagents): answering one must not mark the session as
working while Claude Code is still waiting on the others."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import aipet_hook as hook
from aipet_hook_runtime import config as hook_config
from aipet_hook_runtime import diagnostics as hook_diagnostics
from aipet_hook_runtime import hosts as hook_hosts
from aipet_hook_runtime import io as hook_io
from aipet_hook_runtime import requests as hook_requests
from aipet_hook_runtime import sessions as hook_sessions
from aipet_hook_runtime import waiting as hook_waiting


def call(i, cmd):
    return {"session_id": "s", "tool_use_id": f"toolu_{i}", "tool_name": "Bash", "tool_input": {"command": cmd}}


class PermissionQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "s.json")
        self.target = os.path.join(self.tmp.name, "sessions", "s.json")
        os.makedirs(os.path.dirname(self.target))
        self.patches = [patch.object(hook_config, "AGENT", "claude"), patch.object(hook_hosts, "find_claude_pid", return_value=None),
                        patch.object(hook_hosts, "find_host_window", return_value=(None, "")),
                        patch.object(hook_diagnostics, "debug_log"), patch.object(hook_hosts, "is_cowork", return_value=False)]
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
        hook_sessions._update_session(self.path, self.target, name, data, False)
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
        with patch.object(hook_waiting, "pet_alive", return_value=True), patch.object(hook_waiting, "answers_enabled", return_value=True), \
                patch.object(hook_waiting, "await_answer", return_value="allow"), patch.object(hook_io, "write_stdout") as out:
            hook_waiting.answer_flow(self.tmp.name, self.path, "", first)
        out.assert_called_once_with(hook_requests.decision_output("allow"))
        rec = self.record()
        self.assertEqual(rec["state"], "needs_input")
        self.assertEqual(rec["request"]["id"], second)
        self.event("PostToolUse", call(1, "rm a"))  # the allowed tool runs: the second prompt must stay
        self.assertEqual(self.record()["request"]["id"], second)
        with patch.object(hook_waiting, "pet_alive", return_value=True), patch.object(hook_waiting, "answers_enabled", return_value=True), \
                patch.object(hook_waiting, "await_answer", return_value="allow"), patch.object(hook_io, "write_stdout"):
            hook_waiting.answer_flow(self.tmp.name, self.path, "", second)
        self.assertEqual(self.record()["state"], "working")

    def test_claude_code_own_always_allow_is_passed_back_only_when_it_offered_it(self):
        rule = {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "rm:*"}], "behavior": "allow",
                "destination": "localSettings"}
        self.event("UserPromptSubmit", {"session_id": "s", "prompt": "go"})
        rid = self.event("PermissionRequest", dict(call(1, "rm a"), permission_suggestions=[rule]))
        self.assertEqual(self.record()["request"]["suggestions"], [rule])
        forged = dict(rule, rules=[{"toolName": "Bash", "ruleContent": "*"}])
        for picked, expected in (([rule], [rule]), ([forged], None)):
            hook_waiting.LAST_ANSWER.clear()
            hook_waiting.LAST_ANSWER.update(behavior="allow", permissions=picked)
            with patch.object(hook_waiting, "pet_alive", return_value=True), \
                    patch.object(hook_waiting, "answers_enabled", return_value=True), \
                    patch.object(hook_waiting, "await_answer", return_value="allow"), patch.object(hook_io, "write_stdout") as out:
                hook_waiting.answer_flow(self.tmp.name, self.path, "", rid)
            out.assert_called_once_with(hook_requests.decision_output("allow", None, expected))
            if expected:
                self.assertIn('"updatedPermissions"', out.call_args.args[0])
            rid = self.event("PermissionRequest", dict(call(1, "rm a"), permission_suggestions=[rule]))
        hook_waiting.LAST_ANSWER.clear()

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
        with patch.object(hook_waiting, "pet_alive", return_value=True), patch.object(hook_waiting, "answers_enabled", return_value=True), \
                patch.object(hook_io.time, "sleep", side_effect=tick), patch.object(hook_io.time, "time", side_effect=range(0, 10**6, 3)):
            # the second prompt is not on screen, but it's still pending: no early give-up
            self.assertEqual(hook_waiting._await(answer, answer + ".waiting", self.tmp.name, 0, self.path, second), "deny")

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

    def test_tool_failure_or_denial_also_settles_its_prompt(self):
        for ending in ("PostToolUseFailure", "PermissionDenied"):
            with self.subTest(ending=ending):
                os.path.exists(self.path) and os.remove(self.path)
                self.event("PermissionRequest", call(1, "make"))
                done = dict(call(1, "make"))
                done["tool_input"] = dict(done["tool_input"], timeout=600000, description="added later")  # extra fields
                self.event(ending, done)
                self.assertEqual(self.record()["state"], "working")

    def test_subagent_stopping_drops_its_prompts(self):
        sub = dict(call(1, "mvn test"), agent_id="tester")
        self.event("PermissionRequest", sub)
        self.event("Stop", {"session_id": "s"})  # the main turn ends while the tester asks
        self.assertEqual(self.record()["state"], "needs_input")
        hook_sessions._update_session(self.path, self.target, "SubagentStop",
                             {"session_id": "s", "agent_id": "tester", "hook_event_name": "SubagentStop"}, False)
        rec = self.record()
        self.assertEqual((rec["state"], rec["requests"]), ("done", []))

    def test_api_error_shows_why_the_turn_stopped(self):
        self.event("UserPromptSubmit", {"session_id": "s", "prompt": "go"})
        hook_sessions._update_session(self.path, self.target, "StopFailure",
                             {"session_id": "s", "hook_event_name": "StopFailure", "error_type": "rate_limit"}, False)
        self.event("Stop", {"session_id": "s"})
        rec = self.record()
        self.assertEqual((rec["state"], rec["error_kind"]), ("error", "rate_limit"))
        self.assertIn("Usage limit", rec["message"])
        self.event("UserPromptSubmit", {"session_id": "s", "prompt": "again"})
        self.assertEqual((self.record()["state"], self.record()["error_kind"]), ("working", ""))

    def test_prompts_never_announced_stop_showing_after_a_while(self):
        import aipetlib.permissions as permissions
        self.event("PermissionRequest", call(1, "ls"))
        self.event("Notification", {"session_id": "s", "notification_type": "permission_prompt",
                                    "message": "Claude needs your permission to use Bash"})
        rec = self.record()
        self.assertTrue(rec["notifies"] and rec["requests"][0]["shown"])
        self.assertFalse(permissions.unconfirmed_prompts(rec, now=rec["requests"][0]["t"] + 60))  # shown: really waiting
        self.event("PostToolUse", call(1, "ls"))
        self.event("PermissionRequest", call(2, "cat x"))  # settled by auto mode: never announced
        rec = self.record()
        t = rec["requests"][0]["t"]
        self.assertFalse(permissions.unconfirmed_prompts(rec, now=t + 3))  # give the Notification time
        self.assertTrue(permissions.unconfirmed_prompts(rec, now=t + 30))


if __name__ == "__main__":
    unittest.main()
