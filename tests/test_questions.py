"""Claude's AskUserQuestion through the PermissionRequest hook (format confirmed against a real Cowork session):
input {"questions": [{"question", "header", "multiSelect", "options": [{"label", "description"}]}]}, answered with
allow + updatedInput = the same input plus {"answers": {question: label, or [labels] when multi-select}}."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import aipet_hook as hook

QUESTIONS = {"questions": [
    {"question": "Which fruit?", "header": "Fruit", "multiSelect": False,
     "options": [{"label": "Apple", "description": "Crunchy"}, {"label": "Banana", "description": "Yellow"}]},
    {"question": "Which lights?", "header": "Lights", "multiSelect": True,
     "options": [{"label": "Amber", "description": ""}, {"label": "Red", "description": ""}]}]}


class QuestionTests(unittest.TestCase):
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
        self.data = {"session_id": "s", "hook_event_name": "PermissionRequest", "tool_name": "AskUserQuestion",
                     "tool_input": QUESTIONS, "_aipet_request_id": "q1"}

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def record(self):
        with open(self.path) as f:
            return json.load(f)

    def test_question_is_recorded_for_the_popup(self):
        hook._update_session(self.path, self.target, "PermissionRequest", self.data, False)
        rec = self.record()
        self.assertEqual(rec["state"], "needs_input")
        self.assertEqual(rec["message"], "Claude has a question for you")
        req = rec["request"]
        self.assertEqual(req["kind"], "question")
        self.assertEqual([q["question"] for q in req["questions"]], ["Which fruit?", "Which lights?"])
        self.assertTrue(req["questions"][1]["multiSelect"])
        self.assertEqual(req["questions"][0]["options"][0], {"label": "Apple", "description": "Crunchy"})

    def test_answers_go_back_with_the_original_questions(self):
        hook._update_session(self.path, self.target, "PermissionRequest", self.data, False)
        answers = {"Which fruit?": "Apple", "Which lights?": ["Red", "something else"]}

        def click(*_a, **_k):
            hook.LAST_ANSWER.clear()
            hook.LAST_ANSWER.update(behavior="allow", answers=answers)
            return "allow"
        with patch.object(hook, "pet_alive", return_value=True), patch.object(hook, "answers_enabled", return_value=True), \
                patch.object(hook, "await_answer", side_effect=click), patch.object(hook, "write_stdout") as out:
            hook.answer_flow(self.tmp.name, self.path, "", "q1", QUESTIONS)
        decision = json.loads(out.call_args.args[0])["hookSpecificOutput"]["decision"]
        self.assertEqual(decision["behavior"], "allow")
        self.assertEqual(decision["updatedInput"], dict(QUESTIONS, answers=answers))
        self.assertEqual(self.record()["state"], "working")

    def test_allow_without_answers_leaves_it_to_claude_code(self):
        hook._update_session(self.path, self.target, "PermissionRequest", self.data, False)

        def click(*_a, **_k):
            hook.LAST_ANSWER.clear()
            hook.LAST_ANSWER.update(behavior="allow")
            return "allow"
        with patch.object(hook, "pet_alive", return_value=True), patch.object(hook, "answers_enabled", return_value=True), \
                patch.object(hook, "await_answer", side_effect=click), patch.object(hook, "write_stdout") as out:
            hook.answer_flow(self.tmp.name, self.path, "", "q1", QUESTIONS)
        out.assert_not_called()

    def test_auto_approve_never_answers_a_question(self):
        with patch.object(hook, "read_stdin", return_value=json.dumps(dict(self.data, _aipet_request_id=None))), \
                patch.object(hook, "is_wsl", return_value=False), \
                patch.object(hook, "sessions_dir", return_value=os.path.dirname(self.target)), \
                patch.object(hook, "usage", None), patch.object(hook, "claude_usage", None), \
                patch.object(hook, "auto_decision", return_value="all") as auto, \
                patch.object(hook, "answers_enabled", return_value=False), patch.object(hook, "write_stdout") as out:
            hook.main()
        auto.assert_not_called()
        out.assert_not_called()

    def test_other_tools_are_not_questions(self):
        self.assertEqual(hook.question_list({"tool_name": "Bash", "tool_input": QUESTIONS}), [])


if __name__ == "__main__":
    unittest.main()
