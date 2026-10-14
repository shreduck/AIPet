"""Navigating queued requests changes presentation, never the requests themselves."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import aipet
import support
from aipetlib.prompt_navigation import PromptNavigation
from aipetlib.app.answers import Answers


def session(key="cc:fixture", agent="codex"):
    requests = [{"id": key + "-fruit", "kind": "question", "source": "codex-question",
                 "agent": "fruit-agent", "questions": [{"question": "Which fruit?",
                     "options": [{"label": "Apple"}, {"label": "Pear"}]}]},
                {"id": key + "-hat", "kind": "question", "source": "codex-question",
                 "agent": "hat-agent", "questions": [{"question": "Which hat?",
                     "options": [{"label": "Cap"}, {"label": "Beanie"}]}]}]
    return dict(key=key, sid=key, source="CC", title="Isolated navigation fixture", agent=agent,
                state="needs_input", request=requests[0], requests=requests, pending=2, changed=0)


def application(items):
    app = aipet.PetApp.__new__(aipet.PetApp)
    app.cfg = {"max_pets": 10}
    app.wb = None
    app._slots, app._slot_n, app._last_items = {}, 0, copy.deepcopy(items)
    app._agent_items = app._last_items[:]
    app.details = {}
    app.focused, app.sent = [], []
    app.focus_key = app.focused.append
    app.answers = Answers(app)
    app.answers.send = lambda key, behavior: app.sent.append((key, behavior,
        next(i for i in app._last_items if i["key"] == key)["request"]["id"])) or False
    app.refresh = lambda: setattr(app, "_last_items", app.collect())
    return app


class PromptNavigationTests(unittest.TestCase):
    def test_arrows_move_both_directions_without_focus_or_release(self):
        raw = session()
        original = copy.deepcopy(raw)
        app = application([raw])
        with patch.object(aipet, "read_claude_code_sessions", side_effect=lambda _: [copy.deepcopy(raw)]):
            for step, index in ((-1, 1), (1, 0), (1, 1), (-1, 0)):
                self.assertTrue(app.answers.step_prompt(raw["key"], step))
                for _ in range(3):
                    app.refresh()
                    self.assertEqual(app._last_items[0]["request"], raw["requests"][index])
        self.assertEqual(app.sent, [])
        self.assertEqual(app.focused, [])
        self.assertEqual(raw, original)

    def test_wraparound_persistent_polling_preserves_all_input(self):
        raw = session()
        original = copy.deepcopy(raw)
        app = application([raw])
        with patch.object(aipet, "read_claude_code_sessions", side_effect=lambda _: [copy.deepcopy(raw)]):
            self.assertTrue(app.answers.go_to_prompt(raw["key"]))
            for _ in range(4):
                app.refresh()
                self.assertEqual(app._last_items[0]["request"]["id"], raw["requests"][1]["id"])
                self.assertEqual(app._last_items[0]["wait_agent"], "hat-agent")
                self.assertEqual(app._agent_items[0]["request"], raw["requests"][1])
            self.assertTrue(app.answers.go_to_prompt(raw["key"]))
            self.assertEqual(app._last_items[0]["request"], raw["request"])
        self.assertEqual(raw, original)
        self.assertEqual(app._last_items[0]["requests"], original["requests"])
        self.assertEqual(app.sent, [(raw["key"], "release", raw["requests"][0]["id"]),
                                    (raw["key"], "release", raw["requests"][1]["id"])])
        self.assertEqual(app.focused, [raw["key"], raw["key"]])

    def test_sessions_select_independently_and_removal_discards_stale_selection(self):
        first, second = session(), session("cc:other")
        nav = PromptNavigation()
        nav.advance(first)
        projected = nav.project([first, second])
        self.assertEqual(projected[0]["request"], first["requests"][1])
        self.assertEqual(projected[1]["request"], second["requests"][0])
        first = dict(first, requests=[first["request"]], pending=1)
        self.assertEqual(nav.project([first])[0]["request"], first["request"])
        self.assertEqual(nav.selected, {})
        nav.advance(second)
        nav.project([])
        self.assertEqual(nav.selected, {})

    def test_working_state_and_single_request_do_not_advance(self):
        for item in (dict(session(), state="working"), dict(session(), requests=[session()["request"]])):
            with self.subTest(item=item):
                app = application([item])
                self.assertFalse(app.answers.go_to_prompt(item["key"]))
                self.assertEqual(app.focused, [item["key"]])
                self.assertFalse(app.answers.navigation.selected)

    def test_claude_navigation_never_releases_hook(self):
        item = session(agent="claude")
        app = application([item])
        with patch.object(aipet, "read_claude_code_sessions", return_value=[item]):
            self.assertTrue(app.answers.go_to_prompt(item["key"]))
        self.assertEqual(app.sent, [])

    def test_immediate_advance_does_not_start_an_extra_poll(self):
        item = session()
        app = application([item])
        app.refresh = lambda: self.fail("navigation must not schedule another poll loop")
        self.assertTrue(app.answers.go_to_prompt(item["key"]))
        self.assertEqual(app._agent_items[0]["request"], item["requests"][1])

    def test_selected_hook_answer_and_release_target_current_request(self):
        item = session()
        for request in item["requests"]:
            request.update(source="hook", answerable=True)
        app = application([item])
        app.answers.send = Answers.send.__get__(app.answers)
        app.answers.enabled = lambda _: True
        with tempfile.TemporaryDirectory() as directory, support.paths(HOME_DIR=directory), \
                patch.object(aipet, "read_claude_code_sessions", return_value=[item]):
            from aipetlib.permissions import answer_name
            answers = Path(directory) / "answers"
            answers.mkdir()
            for request in item["requests"]:
                (answers / (answer_name(dict(item, request=request)) + ".waiting")).touch()
            self.assertTrue(app.answers.go_to_prompt(item["key"]))
            names = [answer_name(dict(item, request=r)) for r in item["requests"]]
            self.assertEqual(json.loads((answers / (names[0] + ".json")).read_text())["behavior"], "release")
            self.assertFalse((answers / (names[1] + ".json")).exists())
            self.assertTrue(app.answers.send(item["key"], "allow", answers={"Which hat?": "Cap"}))
            self.assertEqual(json.loads((answers / (names[1] + ".json")).read_text())["answers"],
                             {"Which hat?": "Cap"})
            self.assertTrue(app.answers.go_to_prompt(item["key"]))
            self.assertEqual(json.loads((answers / (names[1] + ".json")).read_text())["behavior"], "release")
        self.assertEqual(len(item["requests"]), 2)


@unittest.skipUnless(os.environ.get("AIPET_NATIVE_WINDOW_TEST") == "1", "requires an opt-in desktop")
class PromptNavigationGuiTests(unittest.TestCase):
    def test_native_request_arrows_keep_answer_drafts(self):
        from test_question_overlay_gui import QuestionOverlayGuiTests
        import aipetlib.ui.sprites as sprites
        import aipetlib.ui.permission_card as component
        fixture = QuestionOverlayGuiTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        raw = session(agent="claude")
        for request in raw["requests"]:
            request.update(source="hook", answerable=True)
        raw["requests"][1]["questions"][0]["multiSelect"] = True
        raw["requests"].append({"id": "permission-interleave", "source": "hook", "answerable": True,
                                "tool": "FixtureTool", "detail": "No command is executed"})
        raw["pending"] = 3
        app = application([raw])
        app.root, app.details = fixture.root, {}
        app.answers.enabled, app.answers.timed_minutes = lambda _: True, lambda: 5
        with patch.object(component.permissions, "hook_waiting", return_value=True), \
                patch.object(sprites, "load_sprites", return_value=None):
            card = aipet.Detail(app, raw["key"], raw)
            app.details[raw["key"]] = card
            self.addCleanup(lambda: card.close() if card.win.winfo_exists() else None)
            fixture.root.update()
            q, multi, choice, picks, own, own_on = card._qvars[0]
            own.set("Banana draft")
            choice.set("Pear")
            for position, (arrow, _) in enumerate((card.prompt_arrows[1], card.prompt_arrows[1],
                                                   card.prompt_arrows[0], card.prompt_arrows[0])):
                if os.sys.platform == "darwin":
                    x = arrow.winfo_rootx() + arrow.winfo_width() // 2
                    y = arrow.winfo_rooty() + arrow.winfo_height() // 2
                    card._mouse_bridge._dispatch(1, 1, x, y, 0)
                    card._mouse_bridge._dispatch(2, 1, x, y, 0)
                else:
                    fixture.click(arrow)
                fixture.root.update()
                if position == 0:
                    _, _, _, hat_picks, hat_own, _ = card._qvars[0]
                    hat_picks["Beanie"].set(True)
                    hat_own.set("Top hat draft")
                elif position == 2:
                    _, _, _, hat_picks, hat_own, hat_own_on = card._qvars[0]
                    self.assertTrue(hat_picks["Beanie"].get())
                    self.assertEqual(hat_own.get(), "Top hat draft")
                    self.assertTrue(hat_own_on.get())
            q, multi, choice, picks, own, own_on = card._qvars[0]
            self.assertEqual(choice.get(), "Pear")
            self.assertEqual(own.get(), "Banana draft")
            self.assertEqual(app.sent, [])
            self.assertEqual(app.focused, [])

    def capture(self, widget, name):
        directory = os.environ.get("AIPET_PROMPT_SCREENSHOT_DIR")
        if directory and os.sys.platform == "darwin":
            import subprocess
            import mac_statusbar as mac
            window = mac.window_titled(widget.title())
            number = mac.send(window, "windowNumber", restype=mac.c_long)
            subprocess.run(["/usr/sbin/screencapture", "-x", "-l", str(number),
                            str(Path(directory) / name)], check=True)

    def test_native_go_keeps_card_on_next_request_and_poll_preserves_it(self):
        from test_question_overlay_gui import QuestionOverlayGuiTests
        import aipetlib.ui.sprites as sprites
        fixture = QuestionOverlayGuiTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        raw = session()
        original = copy.deepcopy(raw)
        app = application([raw])
        app.root, app.details = fixture.root, {}
        app.answers.enabled, app.answers.timed_minutes = lambda _: True, lambda: 5
        fixture.window.pet = app
        with patch.object(aipet, "read_claude_code_sessions", side_effect=lambda _: [copy.deepcopy(raw)]), \
                patch.object(sprites, "load_sprites", return_value=None):
            card = aipet.Detail(app, raw["key"], raw)
            app.details[raw["key"]] = card
            self.addCleanup(lambda: card.close() if card.win.winfo_exists() else None)
            def refresh():
                app._last_items = app.collect()
                card.update(app._last_items[0])
                fixture.window.item = app._last_items[0]
                fixture.window.prompt.draw()
            app.refresh = refresh
            refresh()
            fixture.root.update()
            self.capture(card.win, "fruit-card.png")
            left, right = [arrow for arrow, _ in card.prompt_arrows]
            self.assertTrue(left.winfo_viewable())
            self.assertTrue(right.winfo_viewable())
            self.assertLessEqual(left.winfo_rootx() + left.winfo_width(), card.body.winfo_rootx())
            self.assertGreaterEqual(right.winfo_rootx(), card.body.winfo_rootx() + card.body.winfo_width())
            for direction, index in ((right, 1), (left, 0), (left, 1), (right, 0)):
                if os.sys.platform == "darwin":
                    x = direction.winfo_rootx() + direction.winfo_width() // 2
                    y = direction.winfo_rooty() + direction.winfo_height() // 2
                    card._mouse_bridge._dispatch(1, 1, x, y, 0)
                    card._mouse_bridge._dispatch(2, 1, x, y, 0)
                else:
                    fixture.click(direction)
                fixture.root.update()
                self.assertEqual(card._req_id, raw["requests"][index]["id"])
                refresh()
                self.assertEqual(card._req_id, raw["requests"][index]["id"])
            self.assertEqual(app.sent, [])
            self.assertEqual(app.focused, [])
            if os.sys.platform == "darwin":
                x = card.btn_go.winfo_rootx() + card.btn_go.winfo_width() // 2
                y = card.btn_go.winfo_rooty() + card.btn_go.winfo_height() // 2
                card._mouse_bridge._dispatch(1, 1, x, y, 0)
                card._mouse_bridge._dispatch(2, 1, x, y, 0)
            else:
                card.btn_go.invoke()
            fixture.root.update()
            self.assertTrue(card.win.winfo_exists())
            self.assertEqual(card._req_id, raw["requests"][1]["id"])
            fixture.window.item = app._last_items[0]
            fixture.window.prompt.draw()
            fixture.window.prompt.open(True)
            fixture.root.update()
            self.assertIn("Which hat?", fixture.texts())
            self.capture(card.win, "hat-card.png")
            self.capture(fixture.root, "hat-agent-panel.png")
            for _ in range(3):
                refresh()
                fixture.root.update()
                self.assertEqual(card._req_id, raw["requests"][1]["id"])
                self.assertIn("Which hat?", fixture.texts())
            self.assertEqual(app.focused, [raw["key"]])
            self.assertEqual(raw, original)
            self.assertEqual(app._last_items[0]["requests"], original["requests"])
            raw["requests"] = raw["requests"][:1]
            raw["pending"] = 1
            refresh()
            fixture.root.update()
            self.assertFalse(left.winfo_viewable())
            self.assertFalse(right.winfo_viewable())
            card.btn_go.invoke()
            fixture.root.update()
            self.assertFalse(card.win.winfo_exists())
            self.assertEqual(app.focused, [raw["key"], raw["key"]])
