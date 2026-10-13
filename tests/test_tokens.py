"""Tokens each agent used (nodes, stats, log lines) and the context window on agent-network nodes."""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import aipet
import aipet_app
import aipetlib.agents.context as context
import aipetlib.agents.history as agent_history
from aipetlib.agents.claude import parse_claude
from aipetlib.agents.codex import parse_codex
from aipetlib.agents.records import token_totals
from aipetlib.agents.stats import network_stats
from aipetlib.ui.agent_logs import LogPanel
from test_agents import iso, write_lines


def reply(t, rid, parts, used=(10, 1000, 50, 5), model="claude-opus-5-5"):
    """One Claude transcript entry of response rid; used = (input, cache read, cache creation, output)."""
    return {"type": "assistant", "timestamp": iso(t), "uuid": f"u{t}",
            "message": {"id": rid, "role": "assistant", "model": model, "content": parts,
                        "usage": {"input_tokens": used[0], "cache_read_input_tokens": used[1],
                                  "cache_creation_input_tokens": used[2], "output_tokens": used[3]}}}


def say(text):
    return [{"type": "text", "text": text}]


def tool(name):
    return [{"type": "tool_use", "id": f"t-{name}", "name": name, "input": {}}]


class ClaudeTokenTests(unittest.TestCase):
    def test_a_response_split_over_entries_counts_once_with_its_last_usage(self):
        info = parse_claude([
            reply(100, "r1", [{"type": "thinking", "thinking": "hmm"}], used=(10, 1000, 50, 1)),
            reply(101, "r1", say("first"), used=(10, 1000, 50, 30)),  # same response: the final copy counts
            reply(110, "r2", tool("Bash"), used=(5, 1060, 0, 20)),
        ])
        self.assertEqual(token_totals(info), {"in": 65, "cached": 2060, "out": 50, "total": 2175})
        # each request's usage sits on its first line: (prompt it sent, tokens it answered with)
        self.assertEqual(info["usage_at"], {_ts(100): (1060, 30), _ts(110): (1065, 20)})

    def test_no_usage_no_totals(self):
        self.assertIsNone(token_totals(parse_claude([{"type": "user", "timestamp": iso(1), "message": {
            "role": "user", "content": "hi"}}])))

    def test_reads_in_several_batches_count_a_split_response_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            agent_history._TAILS.clear()
            write_lines(path, [reply(100, "r1", [{"type": "thinking", "thinking": "plan"}], used=(10, 1000, 0, 2))])
            first = agent_history._cached(path, agent_history.MAIN_TAIL, parse_claude)
            self.assertEqual(token_totals(first)["total"], 1012)
            with open(path, "a", encoding="utf-8") as f:  # the same response goes on, then a new one
                f.write(json.dumps(reply(101, "r1", say("done"), used=(10, 1000, 0, 40))) + "\n")
                f.write(json.dumps(reply(120, "r2", say("more"), used=(3, 1050, 0, 7))) + "\n")
            os.utime(path, (time.time() + 5, time.time() + 5))
            merged = agent_history._cached(path, agent_history.MAIN_TAIL, parse_claude)
            self.assertEqual(token_totals(merged), {"in": 13, "cached": 2050, "out": 47, "total": 2110})
            self.assertEqual(merged["usage_at"], {_ts(100): (1010, 40), _ts(120): (1053, 7)})


class CodexTokenTests(unittest.TestCase):
    def test_the_latest_session_total_and_each_turn_on_its_line(self):
        def count(t, total, last):
            return {"type": "event_msg", "timestamp": iso(t), "payload": {"type": "token_count", "info": {
                "total_token_usage": total, "last_token_usage": last, "model_context_window": 258400}}}
        message = {"type": "response_item", "timestamp": iso(10), "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]}}
        info = parse_codex([
            message,
            count(11, {"input_tokens": 9000, "cached_input_tokens": 6000, "output_tokens": 300},
                  {"input_tokens": 9000, "output_tokens": 300}),
            count(20, {"input_tokens": 20000, "cached_input_tokens": 15000, "output_tokens": 700},
                  {"input_tokens": 11000, "output_tokens": 400}),
        ])
        self.assertEqual(token_totals(info), {"in": 5000, "cached": 15000, "out": 700, "total": 20700})
        self.assertEqual(info["usage_at"], {_ts(10): (11000, 400)})


class NodeAndLogTests(unittest.TestCase):
    def test_node_line_shows_the_window_and_the_sum(self):
        claude = {"ctx": 84_000, "ctx_max": 0, "model": "claude-opus-5-5",
                  "tokens": {"in": 1, "cached": 1_200_000, "out": 45_000, "total": 1_245_001, "partial": False}}
        self.assertEqual(context.node_line(claude), "ctx 84k/~200k 42% · Σ 1.2M")
        self.assertEqual(context.node_line(claude, {"opus": 1_000_000}), "ctx 84k/1M 8% · Σ 1.2M")
        codex = {"ctx": 45_000, "ctx_max": 258_400, "model": "gpt-5.5",
                 "tokens": {"in": 5, "cached": 5, "out": 5, "total": 15, "partial": True}}
        self.assertEqual(context.node_line(codex), "ctx 45k/258k 17% · Σ ≥15")
        self.assertEqual(context.node_line({"ctx": 0, "tokens": None}), "")
        self.assertEqual(context.tokens_text(claude["tokens"]), "Σ 1.2M tokens (in 1, cached 1.2M, out 45k)")
        self.assertEqual(context.line_usage((84_000, 1200)), "in 84k · out 1k")

    def test_stats_sum_the_tokens_of_every_agent(self):
        net = {"agents": [{"name": "main", "main": True, "ctx": 10, "tokens": {"total": 1000}, "lines": []},
                          {"name": "find", "main": False, "kind": "Explore", "ctx": 5, "tokens": None, "lines": []},
                          {"name": "Bash", "tool": True, "main": False, "lines": []}]}
        stats = network_stats(net)
        self.assertEqual(stats["calls"], [("main", "", 10, 1000), ("find", "", 5, 0)])
        self.assertEqual(stats["tokens_total"], 1000)

    def test_log_shows_a_request_once_on_its_first_line(self):
        ags = [{"usage_at": {5.0: (100, 7)}}, {"usage_at": {}}]
        rows = [(5.0, 0, "think", "a"), (5.0, 0, "say", "b"), (6.0, 0, "say", "c"), (5.0, 1, "say", "d")]
        self.assertEqual(LogPanel.row_usage(rows, ags), [(100, 7), None, None, None])


class MenuSwitchTests(unittest.TestCase):
    def test_context_banner_switch_in_the_appearance_menu(self):
        app = object.__new__(aipet_app.TrayApp)
        app.pet = MagicMock(cfg=dict(aipet.DEFAULT_CONFIG))
        app.status, app.distros, app.codex_targets = {}, [], []
        app.c_auto, app.update_info = [], None
        app.hidden = app.c_muted = app.c_notify = app.c_autostart = False
        app.c_wb, app.version = 'off', 'test'
        with patch.object(aipet_app.hi, 'list_backups', return_value=[]), \
                patch.object(aipet, 'auto_approve_rules', return_value={}), \
                patch.object(aipet.permissions, 'timed_grants', return_value={}):
            looks = next(e for e in app._mac_menu_spec() if e and e['label'] == 'Appearance')['submenu']
            switch = next(e for e in looks if e and e['label'] == 'Context beside the pet')
            self.assertTrue(switch['checked'])
            switch['action']()
            app.pet.set_context_banner.assert_called_once_with(False)

    def test_setting_saves_and_follows_the_menu(self):
        pet = object.__new__(aipet.PetApp)
        pet.cfg = {}
        pet.context_banner_var = MagicMock(get=MagicMock(return_value=True))
        with patch.object(aipet, 'save_setting') as save:
            pet.set_context_banner(False)
        self.assertFalse(pet.cfg["context_banner"])
        save.assert_called_once_with("context_banner", False)
        pet.context_banner_var.set.assert_called_once_with(False)


def _ts(t):
    from aipetlib.agents.records import _ts as parse
    return parse(iso(t))


if __name__ == "__main__":
    unittest.main()
