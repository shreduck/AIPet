"""Independent rollout and hook-state regressions for outstanding Codex questions."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aipetlib.codex_questions import _QuestionReader, question_list, reconcile_questions


def event(kind, payload, timestamp="2026-10-13T03:00:00Z"):
    return {"type": kind, "timestamp": timestamp, "payload": payload}


def ask(call="q1", tool="request_user_input"):
    return event("response_item", {"type": "function_call", "call_id": call, "name": "functions." + tool,
        "arguments": json.dumps({"questions": [{"question": "Which destination?", "header": "Place",
            "options": [{"label": "Local", "description": "This computer"}, {"label": "Remote"}]}]})})


def output(call="q1", result=None):
    return event("response_item", {"type": "function_call_output", "call_id": call,
        "output": json.dumps(result if result is not None else {"answers": {"place": "Local"}})})


class CodexQuestionProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "rollout.jsonl"
        self.reader = _QuestionReader()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, entries, mode="w"):
        with self.path.open(mode) as stream:
            for entry in entries:
                stream.write(json.dumps(entry) + "\n")

    def ids(self, snapshot=None):
        snapshot = self.reader.read(self.path) if snapshot is None else snapshot
        return [q["id"] for q in snapshot["pending"]]

    def test_blocking_call_appears_before_hook_or_output_then_answer_and_cancel_clear(self):
        for result in ({"answers": {"place": "Local"}}, {"cancelled": True}, {"error": "cancelled"}):
            with self.subTest(result=result):
                self.write([ask()])
                snapshot = self.reader.read(self.path)
                self.assertEqual(self.ids(snapshot), ["q1"])
                request = snapshot["pending"][0]
                self.assertEqual(request["source"], "codex-question")
                self.assertEqual(request["questions"][0]["options"][0]["description"], "This computer")
                self.write([output(result=result)], "a")
                self.assertEqual(self.ids(), [])

    def test_async_acceptance_survives_unrelated_tool_until_reply_or_turn_closure(self):
        self.write([ask(tool="request_user_input_async"), output(result={"accepted": True}),
                    event("response_item", {"type": "function_call_output", "call_id": "unrelated", "output": "ok"})])
        self.assertEqual(self.ids(), ["q1"])
        reply = '<send_user_message_question_reply>' + json.dumps([
            {"questionItemId": '["request_user_input_async","q1",0]', "answer": "Local"}]) + '</send_user_message_question_reply>'
        self.write([event("response_item", {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": reply}]})], "a")
        self.assertEqual(self.ids(), [])
        self.write([ask(tool="request_user_input_async"), output(result={"accepted": True}),
                    event("event_msg", {"type": "task_complete"})])
        self.assertEqual(self.ids(), [])

    def test_blocking_question_clears_on_turn_end_abort_and_new_user_message(self):
        for ending in (event("event_msg", {"type": "task_complete"}),
                       event("event_msg", {"type": "turn_aborted"}),
                       event("event_msg", {"type": "task_started"}),
                       event("response_item", {"type": "message", "role": "user", "content": [
                           {"type": "input_text", "text": "Please do a different task"}]})):
            with self.subTest(ending=ending):
                self.write([ask(), ending])
                self.assertEqual(self.ids(), [])

    def test_partial_record_waits_for_newline_and_cached_poll_does_not_open_file(self):
        encoded = (json.dumps(ask()) + "\n").encode()
        self.path.write_bytes(encoded[:len(encoded) // 2])
        self.assertEqual(self.ids(), [])
        with self.path.open("ab") as stream:
            stream.write(encoded[len(encoded) // 2:-1])
        self.assertEqual(self.ids(), [])
        with self.path.open("ab") as stream:
            stream.write(b"\n")
        self.assertEqual(self.ids(), ["q1"])
        with patch("builtins.open", side_effect=AssertionError("cached read opened rollout")):
            self.assertEqual(self.ids(), ["q1"])

    def test_truncation_atomic_rotation_and_same_size_rewrite_reset_pending(self):
        self.write([ask()])
        self.assertEqual(self.ids(), ["q1"])
        self.path.write_text("")
        self.assertEqual(self.ids(), [])
        self.write([ask()])
        self.assertEqual(self.ids(), ["q1"])
        self.path.write_text("{}\n")
        self.assertEqual(self.ids(), [])
        self.write([ask()])
        self.assertEqual(self.ids(), ["q1"])
        replacement = self.path.with_suffix(".new")
        replacement.write_text(json.dumps(ask("q2")) + "\n")
        os.replace(replacement, self.path)
        self.assertEqual(self.ids(), ["q2"])
        old = self.path.stat()
        self.write([ask("q3")])
        os.utime(self.path, ns=(old.st_atime_ns, old.st_mtime_ns + 1000000))
        self.assertEqual(self.ids(), ["q3"])

    def test_initial_tail_is_bounded_but_incremental_pending_survives_large_output(self):
        noise = event("response_item", {"type": "function_call_output", "call_id": "other", "output": "x" * 40000})
        self.write([noise] * 70 + [ask()])
        opened = []
        original = open
        class TrackingFile:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def seek(self, where):
                opened.append(where)
                return self.stream.seek(where)
        with patch("builtins.open", side_effect=lambda *args, **kw: TrackingFile(original(*args, **kw))):
            self.assertEqual(self.ids(), ["q1"])
        self.assertGreater(opened[0], 0)
        self.assertLessEqual(self.path.stat().st_size - opened[0], 2 * 1024 * 1024)
        self.write([noise] * 70, "a")
        self.assertEqual(self.ids(), ["q1"])
        self.write([output()], "a")
        self.assertEqual(self.ids(), [])

    def test_malformed_valid_json_is_ignored_without_breaking_later_question(self):
        for malformed in ({"questions": 42}, {"questions": {}}, {"questions": [{"question": "Bad", "options": 42}]},
                          {"questions": [{"question": "Bad", "options": {"label": "Wrong container"}}]}):
            with self.subTest(malformed=malformed):
                question_list(malformed)
        bad = [None, [], event("response_item", {"type": "message", "role": "user", "content": 42}),
               event("response_item", {"type": "message", "role": "user", "content": [
                   {"type": "input_text", "text": None}, {"type": "input_text", "text": 42}]}),
               event("response_item", {"type": "function_call", "name": "request_user_input", "call_id": "bad",
                   "arguments": '{"questions": 42}'})]
        self.write(bad + [ask()])
        self.assertEqual(self.ids(), ["q1"])

    def test_permission_coexists_question_completion_does_not_clear_permission_or_mutate_hook(self):
        permission = {"id": "p1", "kind": "permission", "source": "hook", "t": 1, "agent": "child"}
        record = {"state": "needs_input", "message": "Needs permission", "request": permission,
                  "requests": [permission], "updated": 1, "changed": 1}
        original = copy.deepcopy(record)
        self.write([ask()])
        projected = reconcile_questions(record, self.reader.read(self.path))
        self.assertEqual([r["id"] for r in projected["requests"]], ["p1", "q1"])
        self.assertEqual(projected["request"], permission)
        self.assertEqual(projected["message"], "Needs permission")
        self.write([output()], "a")
        finished = reconcile_questions(projected, self.reader.read(self.path))
        self.assertEqual(finished["state"], "needs_input")
        self.assertEqual(finished["requests"], [permission])
        self.assertEqual(record, original)

    def test_missing_rollout_preserves_hook_question_and_answered_hook_question_clears(self):
        self.write([ask()])
        snapshot = self.reader.read(self.path)
        record = reconcile_questions({"state": "working", "updated": 1}, snapshot)
        self.path.unlink()
        missing = self.reader.read(self.path)
        self.assertIsNone(missing)
        self.assertEqual(reconcile_questions(record, missing), record)
        self.write([ask(), output()])
        settled = reconcile_questions(record, self.reader.read(self.path))
        self.assertEqual(settled["state"], "working")
        self.assertEqual(settled["requests"], [])

    def test_idle_done_error_hook_states_do_not_resurrect_old_questions(self):
        self.write([ask()])
        snapshot = self.reader.read(self.path)
        for state in ("idle", "done", "error"):
            with self.subTest(state=state):
                record = {"state": state, "updated": snapshot["pending"][0]["t"] + 1}
                projected = reconcile_questions(record, snapshot)
                self.assertEqual(projected["state"], state)
                self.assertEqual(projected["requests"], [])


if __name__ == "__main__":
    unittest.main()
