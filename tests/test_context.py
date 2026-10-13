"""Context in use beside the pet: what the tracker publishes, how the window is found, and how it is drawn."""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import aipetlib.agents.context as context
import aipetlib.agents.history as agent_history
import aipetlib.agents.tracker as agent_tracker
from test_agents import claude_assistant, claude_user, iso, write_lines


def reading(agent="claude", used=84_000, reported_max=0, model="claude-opus-5-5", hint="", title="s"):
    return {"agent": agent, "used": used, "reported_max": reported_max, "model": model, "hint": hint,
            "effort": "high", "title": title, "updated": 0}


class WindowTests(unittest.TestCase):
    def test_codex_reports_its_window(self):
        self.assertEqual(context.window(reading("codex", reported_max=258_400)), (258_400, True))

    def test_claude_window_is_estimated_from_the_model(self):
        self.assertEqual(context.window(reading()), (200_000, False))
        self.assertEqual(context.window(reading(hint="claude-opus-5-5[1m]")), (1_000_000, False))
        self.assertEqual(context.window(reading(used=350_000)), (1_000_000, False))  # past 200k: must be larger

    def test_config_overrides_the_estimate(self):
        self.assertEqual(context.window(reading(), {"opus": 500_000}), (500_000, True))
        self.assertEqual(context.window(reading(), {"sonnet": 500_000}), (200_000, False))
        self.assertEqual(context.window(reading(), {"opus": True}), (200_000, False))  # not a size


class BannerRowTests(unittest.TestCase):
    def test_one_row_per_ai_with_the_fullest_session_first(self):
        items = [{"context": reading(used=20_000, title="a")}, {"context": reading(used=150_000, title="b")},
                 {"context": reading("codex", used=45_000, reported_max=258_400)}, {"title": "no reading"}]
        rows = context.banner_rows(items)
        self.assertEqual([r["agent"] for r in rows], ["claude", "codex"])
        self.assertEqual(rows[0]["title"], "b")
        self.assertEqual([s["title"] for s in rows[0]["sessions"]], ["b", "a"])
        self.assertEqual(context.label(rows[0]), "CL ctx 150k/~200k")
        self.assertEqual(context.label(rows[1]), "CX ctx 45k/258k")
        self.assertEqual(rows[0]["percent"], 75)

    def test_tooltips_say_where_the_window_comes_from(self):
        claude = context.banner_rows([{"context": reading()}, {"context": reading(used=1000, title="x")}])[0]
        text = context.tooltip(claude)
        self.assertIn("84,000 of 200,000 tokens in use (42%)", text)
        self.assertIn("estimated from the model", text)
        self.assertIn("x: 1k / ~200k (0%)", text)
        codex = context.banner_rows([{"context": reading("codex", reported_max=100_000)}])[0]
        self.assertIn("reported by Codex", context.tooltip(codex))
        self.assertEqual(context.describe(claude), "Context 84k / ~200k (42%) · opus-5.5 high")


class TrackerPublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        agent_history._TAILS.clear()
        self.addCleanup(context.publish, {})

    def test_tracker_publishes_each_sessions_main_context(self):
        now = time.time()
        claude_path = os.path.join(self.tmp.name, "root.jsonl")
        write_lines(claude_path, [claude_user(now - 5, "hi"),
                                  dict(claude_assistant(now - 4, [{"type": "text", "text": "hello"}], ctx=12_000),
                                       requestedModel="claude-opus-5-5[1m]")])
        codex_path = os.path.join(self.tmp.name, "rollout-2026-10-12T12-00-00-cx.jsonl")
        write_lines(codex_path, [{"type": "event_msg", "timestamp": iso(now - 3), "payload": {
            "type": "token_count", "info": {"last_token_usage": {"input_tokens": 30_000, "output_tokens": 500},
                                            "model_context_window": 258_400}}}])
        items = [{"source": "CC", "agent": "claude", "session_id": "root", "transcript_path": claude_path,
                  "state": "working", "key": "cl", "title": "proj"},
                 {"source": "CC", "agent": "codex", "session_id": "cx", "transcript_path": codex_path,
                  "state": "working", "key": "cx", "env": "wsl"},
                 {"source": "WB", "key": "wb"}]
        agent_tracker.NetworkTracker(lambda: items).poll()
        cl, cx = context.get("cl"), context.get("cx")
        self.assertEqual((cl["agent"], cl["used"], cl["hint"]), ("claude", 12_002, "claude-opus-5-5[1m]"))
        self.assertEqual(context.window(cl), (1_000_000, False))
        self.assertEqual((cx["agent"], cx["used"], cx["reported_max"]), ("codex", 30_500, 258_400))
        self.assertIsNone(context.get("wb"))
        agent_tracker.NetworkTracker(lambda: items[:1]).poll()
        self.assertIsNone(context.get("cx"))  # gone sessions drop out of the snapshot


class PetBannerTests(unittest.TestCase):
    def pet(self, data, cfg=None):
        import aipet
        pet = aipet.Pet.__new__(aipet.Pet)
        pet.canvas = MagicMock()
        pet._char_w = lambda font: 3
        pet.app = SimpleNamespace(cfg=cfg or {})
        pet.data = data
        return pet

    def texts(self, pet):
        return [call.kwargs["text"] for call in pet.canvas.create_text.call_args_list]

    def test_context_comes_first_then_the_usage_limits(self):
        pet = self.pet({"agent": "claude", "context": reading(), "usage": [{"window": "5h", "percent": 42}]})
        self.assertGreater(pet._draw_usage(), 0)
        self.assertEqual(self.texts(pet), ["CL ctx 84k/~200k", "CL 5h 42%"])
        kinds = [d.get("kind") for d in pet._usage_details.values()]
        self.assertEqual(kinds, ["context", None])

    def test_banner_switch_and_window_override(self):
        pet = self.pet({"agent": "claude", "context": reading()}, {"context_banner": False})
        self.assertEqual(pet._draw_usage(), 0)
        self.assertEqual(self.texts(pet), [])
        pet = self.pet({"agent": "claude", "context": reading()}, {"context_windows": {"opus": 1_000_000}})
        pet._draw_usage()
        self.assertEqual(self.texts(pet), ["CL ctx 84k/1M"])

    def test_compact_pet_shows_one_row_per_ai(self):
        pet = self.pet({"everyone": [{"agent": "claude", "context": reading(used=10_000)},
                                     {"agent": "claude", "context": reading(used=90_000)},
                                     {"agent": "codex", "context": reading("codex", 5_000, 100_000)}]})
        pet._draw_usage()
        self.assertEqual(self.texts(pet), ["CL ctx 90k/~200k", "CX ctx 5k/100k"])


class CollectTests(unittest.TestCase):
    def test_collect_attaches_the_latest_reading(self):
        import aipet
        from unittest.mock import patch
        app = object.__new__(aipet.PetApp)
        app.cfg = {"max_pets": 5}
        app.wb, app._slots, app._slot_n = None, {}, 0
        self.addCleanup(context.publish, {})
        item = {"key": "cl", "source": "CC", "agent": "claude", "state": "working", "changed": time.time()}
        context.publish({"cl": reading()})
        with patch.object(aipet, "read_claude_code_sessions", return_value=[item]), \
                patch.object(aipet, "disambiguate_titles"):
            shown = app.collect()
        self.assertEqual(shown[0]["context"]["used"], 84_000)
        context.publish({})
        with patch.object(aipet, "read_claude_code_sessions", return_value=[item]), \
                patch.object(aipet, "disambiguate_titles"):
            shown = app.collect()
        self.assertNotIn("context", shown[0])


if __name__ == "__main__":
    unittest.main()
