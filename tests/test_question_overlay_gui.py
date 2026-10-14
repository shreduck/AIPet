"""Opt-in real Tk component regressions; fixture never touches a live session.

Run with AIPET_NATIVE_WINDOW_TEST=1. On macOS, clicks also pass through
PanelMouseBridge's native coordinate delivery (OS event posting is not used).
"""
import copy
import os
import sys
import unittest
from types import SimpleNamespace


@unittest.skipUnless(os.environ.get("AIPET_NATIVE_WINDOW_TEST") == "1", "requires an opt-in desktop")
class QuestionOverlayGuiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from aipetlib.ui import agent_prompts as component
        if sys.platform == "darwin":
            import aipet
            self.root = aipet.MacPetWindow()
        else:
            self.root = tk.Tk()
        self.root.title("AIPet isolated question parity fixture")
        self.root.geometry("720x640+80+80")
        self.sent, self.destinations = [], []
        self.waiting = False
        from unittest.mock import patch
        for target, name, fake in ((component.permission_rules, "hook_waiting", lambda _: self.waiting),
                                   (component.common, "prompt_agent",
                                    lambda net, item: 0 if item.get("state") == "needs_input" else None),
                                   (component.common, "suggestion_label", lambda suggestion: str(suggestion))):
            stub = patch.object(target, name, fake)
            stub.start()
            self.addCleanup(stub.stop)
        self.window = w = SimpleNamespace(win=self.root, P={"node": "#20242b", "tx": "#dddddd",
            "hi": "#ffffff", "red": "#ff9999", "mu": "#aaaaaa", "bar": "#15191f",
            "line": "#444444", "redbg": "#442222"}, px=lambda n: int(n),
            f_small=("Arial", 11), f_main=("Arial", 12), f_bold=("Arial", 12, "bold"),
            key="isolated-fixture", theme="dark", s=1,
            net={"agents": [{"name": "Codex", "id": "main"}]})
        w.pet = SimpleNamespace(answers_enabled=lambda _: True,
            open_detail=lambda key: self.destinations.append(("card", key)),
            go_to_prompt=lambda key: self.destinations.append(("session", key)),
            send_answer=lambda key, behavior, **kw: self.sent.append((key, behavior, kw)) or True)
        w.body = tk.Frame(self.root, bg=w.P["node"])
        w.body.pack(fill="both", expand=True)
        canvas = tk.Canvas(w.body, bg=w.P["node"], highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        w.graph = SimpleNamespace(canvas=canvas, draw=lambda: None)  # graph rendering is outside this fixture
        w.footbar = tk.Frame(self.root, height=32)
        w.footbar.pack(side="bottom", fill="x")
        w.settings = SimpleNamespace(open=False, toggle=lambda _: None)  # the settings menu stays closed
        w.prompt = component.PromptPanel(w)
        w.prompt.overlay = tk.Frame(self.root, bg=w.P["node"])
        w.prompt.chip = tk.Label(canvas)
        self.component = component
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def show(self, request, agent="codex"):
        self.window.item = {"agent": agent, "state": "needs_input", "request": request}
        self.window.prompt.draw()
        self.window.prompt.open(True)
        self.root.update()

    def question(self):
        return {"id": "fixture-question", "kind": "question", "source": "codex-question",
                "questions": [{"question": "Where should this isolated fixture run?", "options": [
                    {"label": "Local", "description": "Keep the fixture on this computer"},
                    {"label": "Remote", "description": "Use another computer"}]}]}

    def texts(self):
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        return [str(w.cget("text")) for w in descendants(self.window.prompt.overlay) if "text" in w.keys()]

    def click(self, widget):
        if sys.platform == "darwin":
            import mac_statusbar as mac
            bridge = mac.PanelMouseBridge(self.root)
            try:
                x = widget.winfo_rootx() + widget.winfo_width() // 2
                y = widget.winfo_rooty() + widget.winfo_height() // 2
                bridge._dispatch(1, 1, x, y, 0)
                bridge._dispatch(2, 1, x, y, 0)
            finally:
                bridge.close()
        else:
            widget.event_generate("<Button-1>", x=4, y=4)
        self.root.update()

    def test_readonly_codex_options_and_native_destination_click(self):
        self.show(self.question())
        texts = self.texts()
        self.assertIn("Local — Keep the fixture on this computer", texts)
        self.assertIn("Remote — Use another computer", texts)
        self.assertIn("# answer this question in Codex · go to window opens the session", texts)
        self.assertEqual([label for label, _ in self.window.prompt.options], ["full card", "go to window"])
        self.click(self.window.prompt.opt_labels[1])
        self.assertEqual(self.destinations, [("session", "isolated-fixture")])
        self.assertEqual(self.sent, [])
        self.assertFalse(self.window.prompt.overlay_open)

    def test_same_id_changed_question_rebuilds_visible_widgets(self):
        question = self.question()
        self.show(question)
        revised = copy.deepcopy(question)
        revised["questions"][0]["question"] = "Which revised destination?"
        revised["questions"][0]["options"][0]["label"] = "Revised"
        self.show(revised)
        self.assertIn("Which revised destination?", self.texts())
        self.assertIn("Revised — Keep the fixture on this computer", self.texts())

    def test_full_card_codex_guidance_and_native_go_button(self):
        import aipet
        import aipetlib.ui.sprites as aipet_sprites
        from unittest.mock import patch
        item = {"key": "isolated-fixture", "agent": "codex", "state": "needs_input",
                "title": "Isolated question fixture", "request": self.question()}
        app = SimpleNamespace(root=self.root, cfg={"codex_answers": True}, details={}, _last_items=[item],
            answers_enabled=lambda _: True, timed_minutes=lambda: 5,
            go_to_prompt=lambda key: self.destinations.append(("session", key)))
        card = None
        try:
            with patch.object(aipet_sprites, "load_sprites", return_value=None):
                card = aipet.Detail(app, item["key"], item)
            self.root.update()
            self.assertEqual(card.note.cget("text"), "Codex is waiting for your answer in its own window: "
                "press Go to window and answer there (the pet can't type into Codex).")
            self.assertEqual(card.q_row.winfo_manager(), "")
            self.assertEqual(card._qvars, [])
            if sys.platform == "darwin":
                x = card.btn_go.winfo_rootx() + card.btn_go.winfo_width() // 2
                y = card.btn_go.winfo_rooty() + card.btn_go.winfo_height() // 2
                card._mouse_bridge._dispatch(1, 1, x, y, 0)
                card._mouse_bridge._dispatch(2, 1, x, y, 0)
            else:
                card.btn_go.invoke()
            self.root.update()
            self.assertEqual(self.destinations, [("session", "isolated-fixture")])
        finally:
            if card and card.win.winfo_exists():
                card.close()

    def test_answerable_claude_question_still_sends_selected_answer(self):
        self.waiting = True
        question = self.question()
        question.pop("source")
        self.show(question, agent="claude")
        self.assertTrue(self.window.prompt.radio)
        self.click(self.window.prompt.opt_labels[0])
        self.assertEqual(self.sent, [("isolated-fixture", "allow", {"answers": {
            "Where should this isolated fixture run?": "Local"}})])

    def test_several_questions_one_at_a_time_then_sent_together(self):
        self.waiting = True
        self.show({"id": "fixture-carousel", "kind": "question", "questions": [
            {"question": "First?", "options": [{"label": "One"}, {"label": "Two"}]},
            {"question": "Second (any)?", "multiSelect": True, "options": [{"label": "A"}, {"label": "B"}]}]},
            agent="claude")
        self.assertIn("First?", self.texts())
        self.click(self.window.prompt.opt_labels[1])  # Two: on to the second question
        self.assertIn("Second (any)?  (pick any)", self.texts())
        labels = [label for label, _ in self.window.prompt.options]
        self.click(self.window.prompt.opt_labels[labels.index("B")])
        self.click(self.window.prompt.opt_labels[labels.index("send answers")])
        self.assertEqual(self.sent, [("isolated-fixture", "allow", {"answers": {"First?": "Two",
                                                                               "Second (any)?": ["B"]}})])

    def test_permission_actions_and_prompt_completion_preserve_existing_behavior(self):
        self.waiting = True
        self.show({"id": "fixture-permission", "tool": "TestTool", "detail": "No command is executed"})
        self.assertEqual([label for label, _ in self.window.prompt.options],
                         ["allow once", "aipet: always (whitelist)", "deny", "full card", "answer in codex"])
        self.click(self.window.prompt.opt_labels[2])
        self.assertEqual(self.sent[0][1], "deny")
        self.window.item["state"] = "working"
        self.window.prompt.draw()
        self.root.update()
        self.assertFalse(self.window.prompt.overlay_open)
        self.assertEqual(self.window.prompt.chip.winfo_manager(), "")


if __name__ == "__main__":
    unittest.main()
