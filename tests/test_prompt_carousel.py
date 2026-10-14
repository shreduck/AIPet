"""The agent window's prompt shows several questions one at a time (a carousel) and sends them together."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def questions():
    return [{"question": "Which fruit?", "header": "Fruit", "multiSelect": False,
             "options": [{"label": "Apple"}, {"label": "Pear"}]},
            {"question": "Which lights?", "header": "Lights", "multiSelect": True,
             "options": [{"label": "Red"}, {"label": "Green"}, {"label": "Blue"}]},
            {"question": "Which size?", "multiSelect": False, "options": [{"label": "S"}, {"label": "L"}]}]


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class CarouselTests(unittest.TestCase):
    def setUp(self):
        from aipetlib.ui import agent_prompts as component
        fake_tk = MagicMock()
        fake_tk.TclError = type("TclError", (Exception,), {})
        fake_tk.Label.return_value.winfo_reqwidth.return_value = 40  # widget sizes for laying out the answers
        for target, name, value in ((component, "tk", fake_tk),
                                    (component.permission_rules, "hook_waiting", lambda _: True),
                                    (component.common, "prompt_agent", lambda net, item: 0),
                                    (component.permission_rules, "timed_grants", lambda: self.grants)):
            stub = patch.object(target, name, value)
            stub.start()
            self.addCleanup(stub.stop)
        self.sent = []
        self.grants, self.confirm = {}, True

        def start_timed_approve(item):  # the pet's confirmation dialog: True once the grant runs
            if self.confirm:
                self.grants[item["session_id"]] = {"until": 9e9}
            return self.confirm
        pet = SimpleNamespace(answers_enabled=lambda _: True, open_detail=lambda key: None,
                              go_to_prompt=lambda key: None, timed_minutes=lambda: 15,
                              start_timed_approve=start_timed_approve,
                              send_answer=lambda key, behavior, **kw: self.sent.append((behavior, kw)) or True)
        self.window = SimpleNamespace(
            win=MagicMock(), P={k: "#000000" for k in ("node", "red", "mu", "hi", "tx", "bar", "line", "redbg", "blue")},
            px=lambda n: n, f_small=MagicMock(), f_main=MagicMock(), f_bold=MagicMock(), key="k", theme="dark", s=1,
            net={"agents": [{"name": "main", "id": ""}]}, pet=pet, settings=SimpleNamespace(open=False),
            graph=SimpleNamespace(canvas=MagicMock(), draw=lambda: None), footbar=MagicMock(), body=MagicMock())
        self.prompt = component.PromptPanel(self.window)
        self.prompt.build()
        self.prompt.place = lambda: None  # where it sits on screen is not what these tests are about

    def show(self, qs):
        self.window.item = {"agent": "claude", "state": "needs_input",
                            "request": {"id": "r1", "kind": "question", "questions": qs, "answerable": True}}
        self.prompt.draw()
        self.prompt.open(True)

    def labels(self):
        return [label for label, _ in self.prompt.options]

    def choose(self, label):
        self.prompt.choose(self.labels().index(label))

    def test_one_question_at_a_time_then_all_sent_together(self):
        self.show(questions())
        self.assertEqual(self.labels(), ["Apple", "Pear", "your own answer:", "next question ›", "full card",
                                         "go to window"])
        self.choose("Pear")  # pick one: on to the next question
        self.assertEqual(self.prompt.q_index, 1)
        self.assertEqual(self.labels()[:4], ["Red", "Green", "Blue", "your own answer:"])
        self.choose("Blue")
        self.choose("Red")  # pick any: ticks, stays on the question
        self.assertEqual(self.prompt.q_index, 1)
        self.choose("next question ›")
        self.assertEqual(self.prompt.q_index, 2)
        self.assertIn("send answers", self.labels())
        self.choose("L")  # the last one answered: everything goes
        self.assertEqual(self.sent, [("allow", {"answers": {
            "Which fruit?": "Pear", "Which lights?": ["Red", "Blue"], "Which size?": "L"}})])

    def test_arrows_move_between_questions_and_picks_are_kept(self):
        self.show(questions())
        self.choose("Apple")
        self.prompt.step_question(-1)  # back to the first one: still answered
        self.assertEqual(self.prompt.q_index, 0)
        self.assertEqual(self.prompt.answer_of(0), "Apple")
        self.prompt.step_question(-1)  # wraps round to the last
        self.assertEqual(self.prompt.q_index, 2)

    def test_next_needs_an_answer_and_sending_jumps_to_what_is_left(self):
        self.show(questions())
        self.prompt.show_question(1)
        self.choose("next question ›")  # nothing ticked yet
        self.assertEqual(self.prompt.q_index, 1)
        self.assertEqual(self.sent, [])
        self.choose("Green")
        self.choose("next question ›")
        self.assertEqual(self.prompt.q_index, 2)
        self.choose("S")  # the first question is still open: back to it, nothing sent yet
        self.assertEqual(self.prompt.q_index, 0)
        self.assertEqual(self.sent, [])

    def test_own_answer_in_pick_one_and_pick_any(self):
        self.show(questions())
        entry = self.prompt.own[1]
        entry.get.return_value = "Banana"
        self.prompt.send_own()  # typed + enter on a pick-one question: that is its answer
        self.assertEqual(self.prompt.answer_of(0), "Banana")
        self.assertEqual(self.prompt.q_index, 1)
        self.prompt.own[1].get.return_value = "Purple"
        self.prompt.send_own()  # on a pick-any question: ticks your own answer
        self.choose("Red")
        self.assertEqual(self.prompt.answer_of(1), ["Red", "Purple"])

    def test_a_single_question_is_sent_on_the_first_pick(self):
        self.show(questions()[:1])
        self.assertNotIn("next question ›", self.labels())
        self.choose("Apple")
        self.assertEqual(self.sent, [("allow", {"answers": {"Which fruit?": "Apple"}})])

    def test_a_new_prompt_starts_at_its_first_question(self):
        self.show(questions())
        self.choose("Pear")
        self.window.item["request"] = dict(self.window.item["request"], id="r2")
        self.prompt.draw()
        self.assertEqual(self.prompt.q_index, 0)
        self.assertFalse(any(self.prompt.is_answered(k) for k in range(3)))


    # ---- a permission: auto approve this session for a while, as on the card
    def permission(self, session_id="s1"):
        self.window.item = {"agent": "claude", "state": "needs_input", "session_id": session_id,
                            "request": {"id": "p1", "tool": "Bash", "detail": "ls", "answerable": True}}
        self.prompt.sig = None
        self.prompt.draw()
        self.prompt.open(True)

    def test_permission_offers_a_timed_auto_approve(self):
        self.permission()
        self.assertEqual(self.labels(), ["allow once", "aipet: always (whitelist)",
                                         "aipet: auto approve this session for 15 min…", "deny", "full card",
                                         "go to window"])
        self.choose("aipet: auto approve this session for 15 min…")
        self.assertIn("s1", self.grants)
        self.assertEqual(self.sent, [("allow", {"answers": None})])

    def test_cancelled_confirmation_answers_nothing(self):
        self.confirm = False
        self.permission()
        self.choose("aipet: auto approve this session for 15 min…")
        self.assertEqual(self.sent, [])

    def test_while_auto_approving_it_just_allows(self):
        self.grants["s1"] = {"until": 9e9}
        self.permission()
        self.choose("aipet: auto approving this session · allow")
        self.assertEqual(self.sent, [("allow", {"answers": None})])

    def test_no_session_no_timed_choice(self):
        self.permission(session_id=None)
        self.assertNotIn("aipet: auto approve this session for 15 min…", self.labels())


if __name__ == "__main__":
    unittest.main()
