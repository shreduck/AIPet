import json
import os
import tempfile
import time
import unittest

import aipet_agents as agents


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def write_lines(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def claude_user(t, text):
    return {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": text}}


def claude_assistant(t, parts, model="claude-opus-5-5", ctx=1000, effort="high"):
    return {"type": "assistant", "timestamp": iso(t), "effort": effort,
            "message": {"role": "assistant", "model": model, "content": parts,
                        "usage": {"input_tokens": 2, "cache_read_input_tokens": ctx, "cache_creation_input_tokens": 0}}}


class ClaudeNetworkTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        agents._TAILS.clear()
        now = time.time()
        self.now = now
        self.transcript = os.path.join(self.dir.name, "sess.jsonl")
        write_lines(self.transcript, [
            claude_user(now - 100, "old question"),
            claude_user(now - 60, "fix the login loop"),
            claude_assistant(now - 59, [{"type": "thinking", "thinking": ""},
                                        {"type": "text", "text": "Looking into it.\nI'll ask two helpers."},
                                        {"type": "tool_use", "name": "Agent",
                                         "input": {"subagent_type": "Explore", "description": "find callers"}}],
                             ctx=84000),
            {"type": "user", "timestamp": iso(now - 58), "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "boom", "is_error": True}]}},
        ])
        sub = os.path.join(self.dir.name, "sess", "subagents")
        write_lines(os.path.join(sub, "agent-aaa.jsonl"), [
            claude_user(now - 58, "find callers of refreshToken"),
            claude_assistant(now - 57, [{"type": "tool_use", "name": "Grep", "input": {"pattern": "refreshToken("}}],
                             model="claude-haiku-5-5", ctx=22000, effort="medium"),
            claude_assistant(now - 50, [{"type": "text", "text": "found 3 callers of refreshToken()"}],
                             model="claude-haiku-5-5", ctx=23000, effort="medium"),
        ])
        with open(os.path.join(sub, "agent-aaa.meta.json"), "w") as f:
            json.dump({"agentType": "Explore", "description": "find callers"}, f)
        write_lines(os.path.join(sub, "agent-bbb.jsonl"), [
            claude_user(now - 55, "plan the fix"),
            claude_assistant(now - 54, [{"type": "tool_use", "name": "Bash", "input": {"command": "npm test -- auth"}}],
                             ctx=12000),
        ])
        with open(os.path.join(sub, "agent-bbb.meta.json"), "w") as f:
            json.dump({"agentType": "Plan"}, f)
        old = os.path.join(sub, "agent-old.jsonl")  # from an earlier turn
        write_lines(old, [claude_user(now - 100, "earlier")])
        os.utime(old, (now - 99, now - 99))
        for name in ("agent-aaa.jsonl", "agent-bbb.jsonl"):
            os.utime(os.path.join(sub, name), (now - 50, now - 50))

    def item(self, **kw):
        base = {"agent": "claude", "transcript_path": self.transcript, "state": "needs_input",
                "session_id": "sess", "agent_ids": {"bbb": "Plan"}, "wait_agent": "bbb"}
        base.update(kw)
        return base

    def test_main_and_subagents_with_model_effort_and_context(self):
        net = agents.read_network(self.item(), now=self.now)
        by = {a["name"]: a for a in net["agents"]}
        self.assertEqual(list(by), ["main", "Explore", "Plan"])  # the old turn's agent is left out
        main = by["main"]
        self.assertEqual((main["model_label"], main["effort"], agents.fmt_tokens(main["ctx"])), ("opus-5.5", "high", "84k"))
        self.assertEqual(main["state"], "waiting")  # the prompt on screen is Plan's
        self.assertEqual(by["Plan"]["state"], "needs_input")
        self.assertEqual(by["Explore"]["state"], "done")
        self.assertEqual(by["Explore"]["thought"], "found 3 callers of refreshToken()")
        self.assertEqual(by["Explore"]["model_label"], "haiku-5.5")
        self.assertIn("waiting for Plan", main["thought"])

    def test_shared_log_is_in_time_order_with_each_agents_lines(self):
        net = agents.read_network(self.item(), now=self.now)
        names = [a["name"] for a in net["agents"]]
        rows = [(names[i], kind, text) for _, i, kind, text in net["log"]]
        self.assertEqual(rows[:2], [("main", "you", "old question"), ("main", "you", "fix the login loop")])
        self.assertIn(("main", "spawn", "started Explore: find callers"), rows)
        self.assertIn(("main", "error", "boom"), rows)
        self.assertIn(("Explore", "tool", "Grep(refreshToken()"), rows)
        self.assertIn(("Plan", "tool", "Bash(npm test -- auth)"), rows)
        self.assertEqual([r[0] for r in net["log"]], sorted(r[0] for r in net["log"]))
        self.assertNotIn("earlier", [r[2] for r in rows])  # the old turn's subagent stays out of the log too

    def test_running_agent_without_transcript_still_shows(self):
        net = agents.read_network(self.item(agent_ids={"zzz": "tester"}, wait_agent="", state="working"), now=self.now)
        tester = next(a for a in net["agents"] if a["id"] == "zzz")
        self.assertEqual((tester["name"], tester["state"]), ("tester", "working"))

    def test_same_type_gets_numbered(self):
        sub = os.path.join(self.dir.name, "sess", "subagents")
        with open(os.path.join(sub, "agent-bbb.meta.json"), "w") as f:
            json.dump({"agentType": "Explore"}, f)
        names = [a["name"] for a in agents.read_network(self.item(), now=self.now)["agents"]]
        self.assertEqual(names, ["main", "Explore #1", "Explore #2"])


class CodexNetworkTests(unittest.TestCase):
    def test_spawned_rollouts_become_subagents(self):
        with tempfile.TemporaryDirectory() as root:
            agents._TAILS.clear()
            agents._CODEX_META.clear()
            now = time.time()
            day = os.path.join(root, "2026", "10", "07")
            parent_id = "01a115c8-6f99"
            parent = os.path.join(day, f"rollout-2026-10-07T10-53-39-{parent_id}.jsonl")
            write_lines(parent, [
                {"type": "session_meta", "timestamp": iso(now - 30), "payload": {"id": parent_id}},
                {"type": "turn_context", "timestamp": iso(now - 30), "payload": {"model": "gpt-6-luna", "effort": "low"}},
                {"type": "event_msg", "timestamp": iso(now - 30), "payload": {"type": "task_started"}},
                {"type": "response_item", "timestamp": iso(now - 29), "payload": {
                    "type": "message", "role": "user", "content": [{"type": "input_text", "text": "say hello x3"}]}},
                {"type": "response_item", "timestamp": iso(now - 28), "payload": {
                    "type": "function_call", "name": "spawn_agent", "arguments": json.dumps({"task_name": "hello_one"})}},
                {"type": "response_item", "timestamp": iso(now - 27), "payload": {
                    "type": "custom_tool_call", "name": "exec",
                    "input": 'await tools.exec_command({cmd:"git status","workdir":"x"})'}},
                {"type": "event_msg", "timestamp": iso(now - 26), "payload": {"type": "token_count", "info": {
                    "last_token_usage": {"input_tokens": 20000, "output_tokens": 70}, "model_context_window": 258400}}},
            ])
            child = os.path.join(day, "rollout-2026-10-07T10-54-40-01a115c9-5cc.jsonl")
            write_lines(child, [
                {"type": "session_meta", "timestamp": iso(now - 28), "payload": {
                    "id": "child-1", "parent_thread_id": parent_id, "agent_nickname": "Faraday",
                    "agent_path": "/root/hello_one"}},
                {"type": "event_msg", "timestamp": iso(now - 28), "payload": {"type": "task_started"}},
                {"type": "response_item", "timestamp": iso(now - 27), "payload": {
                    "type": "reasoning", "summary": [{"type": "summary_text", "text": "Just say hello"}]}},
                {"type": "response_item", "timestamp": iso(now - 26), "payload": {
                    "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]}},
                {"type": "event_msg", "timestamp": iso(now - 26), "payload": {"type": "task_complete"}},
            ])
            guardian = os.path.join(day, "rollout-2026-10-07T10-54-41-01a115c9-ggg.jsonl")
            write_lines(guardian, [{"type": "session_meta", "payload": {
                "id": "g", "parent_thread_id": parent_id, "source": {"subagent": {"other": "guardian"}}}}])
            net = agents.read_network({"agent": "codex", "transcript_path": parent, "state": "working",
                                       "session_id": parent_id}, now=now)
            main, sub = net["agents"]
            self.assertEqual((main["model_label"], main["effort"], main["ctx"], main["ctx_max"]),
                             ("gpt-6-luna", "low", 20070, 258400))
            self.assertEqual((sub["name"], sub["state"], sub["thought"]), ("Faraday (hello_one)", "done", "hello"))
            texts = [text for _, _, _, text in net["log"]]
            self.assertIn("started hello_one", texts)
            self.assertIn("exec(git status)", texts)
            self.assertIn("Just say hello", texts)


class WindowLogicTests(unittest.TestCase):
    def test_picks_the_session_that_needs_you_and_the_asking_agent(self):
        import aipet_agents_window as win
        items = [{"key": "a", "source": "CC", "session_id": "1", "state": "working"},
                 {"key": "b", "source": "CC", "session_id": "2", "state": "needs_input"},
                 {"key": "w", "source": "WB", "state": "needs_input"}]
        self.assertEqual(win.pick_session(items)["key"], "b")
        self.assertEqual(win.pick_session(items, "a")["key"], "a")
        self.assertIsNone(win.pick_session([]))
        net = {"agents": [{"id": ""}, {"id": "x1"}, {"id": "x2"}]}
        asking = {"state": "needs_input", "request": {"id": "r", "agent": "x2"}}
        self.assertEqual(win.prompt_agent(net, asking), 2)
        self.assertEqual(win.prompt_agent(net, {"state": "needs_input", "request": {"id": "r"}}), 0)
        self.assertIsNone(win.prompt_agent(net, {"state": "working", "request": {}}))


class WindowOptionTests(unittest.TestCase):
    def test_options_load_and_snap(self):
        import aipet_agents_window as win
        opts = win.load_options({"agent_window": {"alpha": 73, "node_alpha": 60, "on_top": "yes", "frameless": True,
                                                  "scale": 400, "compact": 1}})
        self.assertEqual((opts["alpha"], opts["node_alpha"], opts["on_top"], opts["frameless"], opts["scale"],
                          opts["compact"]), (75, 50, False, True, 150, False))
        self.assertEqual(win.load_options({}), win.DEFAULT_OPTIONS)
        self.assertEqual(win.load_options({"agent_window": {"alpha": 5, "scale": 10}})["alpha"], 30)
        self.assertEqual(win.key_color("#fbfaf7"), "#fbfaf6")  # see-through key: one notch off the background

    def test_needed_size_follows_scale_agents_and_compact(self):
        import aipet_agents_window as win
        small, big = win.needed_size(2, 0.5, 9, 8), win.needed_size(2, 1.5, 27, 24)
        self.assertLess(small[0], big[0])
        self.assertLess(small[1], big[1])
        self.assertLess(win.needed_size(2, 1, 18, 16, compact=True)[1], win.needed_size(2, 1, 18, 16)[1])
        self.assertGreater(win.needed_size(8, 1, 18, 16)[0], win.needed_size(2, 1, 18, 16)[0])  # two columns


class HelperTests(unittest.TestCase):
    def test_model_names_and_tokens(self):
        self.assertEqual(agents.model_name("claude-haiku-4-5-20251001"), "haiku-4.5")
        self.assertEqual(agents.model_name("claude-opus-5-5"), "opus-5.5")
        self.assertEqual(agents.model_name("gpt-6.1-sol"), "gpt-6.1-sol")
        self.assertEqual([agents.fmt_tokens(n) for n in (0, 950, 84000, 1_000_000)], ["", "950", "84k", "1M"])


if __name__ == "__main__":
    unittest.main()
