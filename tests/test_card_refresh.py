"""The permission / question card is refreshed on every pet poll; it lays itself out again only when what it shows
changed, and otherwise just updates its ticking texts (re-packing it every poll left options unpainted on Windows)."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class CardRefreshTests(unittest.TestCase):
    def setUp(self):
        from aipetlib.ui import permission_card
        self.module = permission_card
        card = self.card = object.__new__(permission_card.PermissionCard)
        card.app = SimpleNamespace(answers=SimpleNamespace(enabled=lambda _: True, timed_minutes=lambda: 10), cfg={})
        card.sent, card._view, card._timed_grant = None, None, False
        card._render, card._tick_texts = MagicMock(), MagicMock()
        self.waiting = True
        for name, fake in (("hook_waiting", lambda item: self.waiting), ("timed_grants", lambda: {}),
                           ("hook_stop", lambda item, folder: None)):
            stub = patch.object(permission_card.permissions, name, fake)
            stub.start()
            self.addCleanup(stub.stop)

    def item(self, rid="r1", **extra):
        return dict({"key": "k", "state": "needs_input", "title": "proj", "changed": 1.0, "session_id": "s",
                     "request": {"id": rid, "kind": "question", "questions": [{"question": "Which?"}]}}, **extra)

    def test_an_unchanged_card_only_ticks(self):
        for _ in range(5):
            self.card.update(self.item())
        self.assertEqual(self.card._render.call_count, 1)
        self.assertEqual(self.card._tick_texts.call_count, 4)

    def test_what_the_card_shows_changing_lays_it_out_again(self):
        self.card.update(self.item())
        self.card.update(self.item(rid="r2"))  # the next prompt
        self.waiting = False
        self.card.update(self.item(rid="r2"))  # the hook stopped waiting: no answer buttons any more
        self.card.update(self.item(rid="r2", title="renamed"))
        self.card.sent, self.card._sent_id = "Answer", "r2"  # as send_answers leaves it
        self.card.update(self.item(rid="r2", title="renamed"))
        self.assertEqual(self.card._render.call_count, 5)
        self.assertEqual(self.card._tick_texts.call_count, 0)


if __name__ == "__main__":
    unittest.main()
