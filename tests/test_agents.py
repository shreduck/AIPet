import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

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
        write_lines(old, [claude_user(now - 300, "earlier")])
        os.utime(old, (now - 299, now - 299))
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
        self.assertEqual(list(by), ["main", "find callers", "Plan"])  # named after their description, else type
        self.assertEqual((by["find callers"]["kind"], by["Plan"]["kind"]), ("Explore", ""))
        main = by["main"]
        self.assertEqual((main["model_label"], main["effort"], agents.fmt_tokens(main["ctx"])), ("opus-5.5", "high", "84k"))
        self.assertEqual(main["state"], "waiting")  # the prompt on screen is Plan's
        self.assertEqual(by["Plan"]["state"], "needs_input")
        self.assertEqual(by["find callers"]["state"], "done")
        self.assertEqual(by["find callers"]["thought"], "found 3 callers of refreshToken()")
        self.assertEqual(by["find callers"]["model_label"], "haiku-5.5")
        self.assertIn("waiting for Plan", main["thought"])

    def test_shared_log_is_in_time_order_with_each_agents_lines(self):
        net = agents.read_network(self.item(), now=self.now)
        names = [a["name"] for a in net["agents"]]
        rows = [(names[i], kind, text) for _, i, kind, text in net["log"]]
        self.assertEqual(rows[:2], [("main", "you", "old question"), ("main", "you", "fix the login loop")])
        self.assertIn(("main", "spawn", "started Explore: find callers"), rows)
        self.assertIn(("main", "error", "boom"), rows)
        self.assertIn(("find callers", "tool", "Grep(refreshToken()"), rows)
        self.assertIn(("Plan", "tool", "Bash(npm test -- auth)"), rows)
        self.assertEqual([r[0] for r in net["log"]], sorted(r[0] for r in net["log"]))
        self.assertNotIn("earlier", [r[2] for r in rows])  # the old turn's subagent stays out of the log too

    def test_stats_list_subagents_every_context_and_tools(self):
        stats = agents.network_stats(agents.read_network(self.item(), now=self.now))
        self.assertEqual(stats["subagents"], [("Explore", 1), ("Plan", 1)])
        self.assertEqual([c[0] for c in stats["calls"]], ["main", "find callers", "Plan"])  # one row per agent, in order
        self.assertEqual(stats["ctx_total"], 84002 + 23002 + 12002)
        self.assertEqual(dict(stats["tools"]), {"Agent": 1, "Grep": 1, "Bash": 1})
        self.assertEqual(stats["skills"], [])

    def test_stats_warn_only_when_the_task_starts_before_what_was_read(self):
        from unittest.mock import patch
        self.assertFalse(agents.network_stats(agents.read_network(self.item(), now=self.now))["missing"])
        with open(self.transcript, "a", encoding="utf-8") as f:  # a long task: its prompt falls out of the tail
            for k in range(40):
                f.write(json.dumps(claude_assistant(self.now - 40 + k, [{"type": "tool_use", "name": "Read",
                                                                         "input": {"file_path": "x" * 200}}])) + "\n")
        agents._TAILS.clear()
        with patch.object(agents, "MAIN_TAIL", 4000):
            self.assertTrue(agents.network_stats(agents.read_network(self.item(), now=self.now))["missing"])
        agents._TAILS.clear()

    def test_an_agent_started_by_a_subagent_hangs_off_that_subagent(self):
        sub = os.path.join(self.dir.name, "sess", "subagents")
        write_lines(os.path.join(sub, "agent-ccc.jsonl"), [claude_user(self.now - 52, "nested job")])
        with open(os.path.join(sub, "agent-ccc.meta.json"), "w") as f:
            json.dump({"agentType": "Nested", "parentAgentId": "bbb", "spawnDepth": 2}, f)
        os.utime(os.path.join(sub, "agent-ccc.jsonl"), (self.now - 50, self.now - 50))
        by = {a["name"]: a for a in agents.read_network(self.item(), now=self.now)["agents"]}
        self.assertEqual((by["Nested"]["parent"], by["Nested"]["depth"]), ("bbb", 2))
        self.assertEqual((by["Plan"]["parent"], by["Plan"]["depth"]), ("", 1))

    def test_running_agent_without_transcript_still_shows(self):
        net = agents.read_network(self.item(agent_ids={"zzz": "tester"}, wait_agent="", state="working"), now=self.now)
        tester = next(a for a in net["agents"] if a["id"] == "zzz")
        self.assertEqual((tester["name"], tester["state"]), ("tester", "working"))

    def test_same_type_gets_numbered(self):
        sub = os.path.join(self.dir.name, "sess", "subagents")
        with open(os.path.join(sub, "agent-bbb.meta.json"), "w") as f:
            json.dump({"agentType": "Explore"}, f)
        with open(os.path.join(sub, "agent-aaa.meta.json"), "w") as f:
            json.dump({"agentType": "Explore"}, f)  # no descriptions: named after the type, numbered
        names = [a["name"] for a in agents.read_network(self.item(), now=self.now)["agents"]]
        self.assertEqual(names, ["main", "Explore #1", "Explore #2"])

    def test_finished_agents_survive_new_turn_until_eighty_percent_timeout(self):
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps(claude_user(self.now, "next task")) + "\n")
        with patch.dict(agents.core.DONE_TIMEOUT, v=3):
            item = self.item(agent_ids={}, state="working", wait_agent="")
            before = agents.read_network(item, now=self.now + 93)
            self.assertEqual([a["id"] for a in before["agents"]], ["", "aaa", "bbb"])
            after = agents.read_network(item, now=self.now + 94)
            self.assertEqual([a["id"] for a in after["agents"]], [""])

    def test_reusing_an_agent_resets_its_retention(self):
        path = os.path.join(self.dir.name, "sess", "subagents", "agent-aaa.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(claude_assistant(self.now + 90, [{"type": "text", "text": "reused"}])) + "\n")
        os.utime(path, (self.now + 90, self.now + 90))
        with patch.dict(agents.core.DONE_TIMEOUT, v=3):
            item = self.item(agent_ids={}, state="working", wait_agent="")
            self.assertEqual([a["id"] for a in agents.read_network(item, now=self.now + 200)["agents"]],
                             ["", "aaa"])
            self.assertEqual(len(agents.read_network(item, now=self.now + 234)["agents"]), 1)

    def test_retention_follows_changes_to_session_setting(self):
        item = self.item(agent_ids={}, state="done", wait_agent="")
        with patch.dict(agents.core.DONE_TIMEOUT, v=1):
            self.assertEqual(len(agents.read_network(item, now=self.now)["agents"]), 1)
        with patch.dict(agents.core.DONE_TIMEOUT, v=5):
            self.assertEqual(len(agents.read_network(item, now=self.now)["agents"]), 3)

    def test_never_setting_keeps_finished_agents(self):
        with patch.dict(agents.core.DONE_TIMEOUT, v=0):
            net = agents.read_network(self.item(agent_ids={}, state="done"), now=self.now + 10000)
            self.assertEqual({a["id"] for a in net["agents"]}, {"", "aaa", "bbb", "old"})

    def test_expired_parent_stays_while_its_child_is_running(self):
        sub = os.path.join(self.dir.name, "sess", "subagents")
        write_lines(os.path.join(sub, "agent-ccc.jsonl"), [claude_user(self.now, "nested task")])
        with open(os.path.join(sub, "agent-ccc.meta.json"), "w") as f:
            json.dump({"agentType": "Nested", "parentAgentId": "aaa"}, f)
        with patch.dict(agents.core.DONE_TIMEOUT, v=3):
            net = agents.read_network(self.item(agent_ids={"ccc": "Nested"}, state="working", wait_agent=""),
                                      now=self.now + 1000)
            by = {a["id"]: a for a in net["agents"]}
            self.assertEqual(set(by), {"", "aaa", "ccc"})
            self.assertEqual((by["ccc"]["parent"], by["ccc"]["depth"]), ("aaa", 2))

    def test_more_than_twelve_recent_agents_are_kept(self):
        sub = os.path.join(self.dir.name, "sess", "subagents")
        for i in range(13):
            path = os.path.join(sub, f"agent-extra{i}.jsonl")
            write_lines(path, [claude_user(self.now - 10, "task")])
            os.utime(path, (self.now - 10, self.now - 10))
        with patch.dict(agents.core.DONE_TIMEOUT, v=3):
            net = agents.read_network(self.item(), now=self.now)
            self.assertEqual(len(net["agents"]), 16)  # main, original two, and thirteen new agents


class CodexNetworkTests(unittest.TestCase):
    def test_child_hooks_keep_root_transcript_and_user_prompt(self):
        import aipet_hook as hook
        with tempfile.TemporaryDirectory() as root, ExitStack() as stack:
            for name, value in (("AGENT", "codex"), ("find_claude_pid", None),
                                ("find_host_window", (None, "")), ("is_cowork", False),
                                ("codex_thread_name", ""), ("debug_log", None)):
                stack.enter_context(patch.object(hook, name, value) if name == "AGENT"
                                    else patch.object(hook, name, return_value=value))
            parent = os.path.join(root, "rollout-2026-10-12T16-05-50-root.jsonl")
            child = os.path.join(root, "rollout-2026-10-12T16-06-00-child.jsonl")
            write_lines(parent, [{"type": "session_meta", "payload": {"id": "root"}}])
            record = os.path.join(root, "root.json")
            base = {"session_id": "root", "cwd": root}
            hook._update_session(record, record, "UserPromptSubmit",
                                 dict(base, transcript_path=parent, prompt="test nesting"), False)
            for event in ("SubagentStart", "UserPromptSubmit", "PostToolUse", "SubagentStop"):
                hook._update_session(record, record, event,
                                     dict(base, agent_id="child", agent_type="default",
                                          transcript_path=child, prompt="child task"), False)
                current = hook.read_json(record)
                self.assertEqual(current["transcript_path"], parent, event)
                self.assertEqual(current["last_prompt"], "test nesting", event)
            # Repair a session file already overwritten by a previous hook version.
            self.assertEqual(hook.codex_session_transcript("root", child, child), parent)

    def test_metadata_is_retried_after_empty_rollout_is_written(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "rollout-child.jsonl")
            write_lines(path, [])
            self.assertEqual(agents._codex_meta(path), {})
            write_lines(path, [{"type": "session_meta", "payload": {"id": "child"}}])
            self.assertEqual(agents._codex_meta(path)["id"], "child")

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
            grandchild = os.path.join(day, "rollout-2026-10-07T10-55-00-01a115c9-777.jsonl")
            write_lines(grandchild, [{"type": "session_meta", "timestamp": iso(now - 20), "payload": {
                "id": "child-2", "parent_thread_id": "child-1", "agent_nickname": "Noether",
                "agent_path": "/root/hello_one/deeper"}}])
            guardian = os.path.join(day, "rollout-2026-10-07T10-54-41-01a115c9-ggg.jsonl")
            write_lines(guardian, [{"type": "session_meta", "payload": {
                "id": "g", "parent_thread_id": parent_id, "source": {"subagent": {"other": "guardian"}}}}])
            net = agents.read_network({"agent": "codex", "transcript_path": parent, "state": "working",
                                       "session_id": parent_id}, now=now)
            main, sub, deeper = net["agents"]
            self.assertEqual((deeper["name"], deeper["parent"], deeper["depth"]), ("Noether (deeper)", "child-1", 2))
            self.assertEqual((main["model_label"], main["effort"], main["ctx"], main["ctx_max"]),
                             ("gpt-6-luna", "low", 20070, 258400))
            self.assertEqual((sub["name"], sub["state"], sub["thought"]), ("Faraday (hello_one)", "done", "hello"))
            texts = [text for _, _, _, text in net["log"]]
            self.assertIn("started hello_one", texts)
            self.assertIn("exec(git status)", texts)
            self.assertIn("Just say hello", texts)
            # During work an old hook record can point at a child; keep names and the full tree.
            active = agents.read_network({"agent": "codex", "transcript_path": child, "state": "working",
                                         "session_id": parent_id,
                                         "agent_ids": {"child-1": "default", "child-2": "default"}}, now=now)
            self.assertEqual([a["name"] for a in active["agents"]],
                             ["main", "Faraday (hello_one)", "Noether (deeper)"])
            self.assertEqual((active["agents"][2]["parent"], active["agents"][2]["depth"]), ("child-1", 2))
            self.assertEqual(active["agents"][0]["path"], parent)
            # A new root prompt does not remove finished Codex agents before their timer expires.
            with open(parent, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "response_item", "timestamp": iso(now + 10), "payload": {
                    "type": "message", "role": "user", "content": [{"text": "next task"}]}}) + "\n")
            for path in (child, grandchild):
                os.utime(path, (now, now))
            item = {"agent": "codex", "transcript_path": parent, "state": "working", "session_id": parent_id}
            with patch.dict(agents.core.DONE_TIMEOUT, v=3):
                self.assertEqual(len(agents.read_network(item, now=now + 143)["agents"]), 3)
                self.assertEqual(len(agents.read_network(item, now=now + 144)["agents"]), 1)
                # Reuse the nested agent: even an expired parent stays to preserve the branch.
                with open(grandchild, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"type": "event_msg", "timestamp": iso(now + 200),
                                        "payload": {"type": "task_started"}}) + "\n")
                os.utime(grandchild, (now + 200, now + 200))
                resumed = agents.read_network(item, now=now + 1000)
                self.assertEqual([a["id"] for a in resumed["agents"]], ["", "child-1", "child-2"])
                self.assertEqual((resumed["agents"][2]["state"], resumed["agents"][2]["depth"]), ("working", 2))


class WindowLogicTests(unittest.TestCase):
    def test_node_spacing_does_not_expand_to_fill_tall_windows(self):
        import aipet_agents_window as win
        view = object.__new__(win.AgentNetworkWindow)
        view.s, view.gz = 1.5, 1.0
        view._graph_line_height = lambda: 12 * view.s * view.gz
        net = {"agents": [{"id": ""}] + [{"id": name, "depth": 1, "parent": "", "tool": True}
                                        for name in ("apply_patch", "exec", "exec_command")]}
        for scale, zoom in ((s, z) for s in (0.75, 1.0, 1.5, 2.25) for z in (0.4, 1.0, 2.0)):
            view.s, view.gz = scale, zoom
            relative = []
            for height in (300, 2000):
                with patch.object(win, "IS_MAC", True):
                    boxes, _ = view._layout(net, 1000, height)
                main = boxes[0]
                children = boxes[1:]
                relative.append([round(y - main[1], 1) for _, y, _, _ in children])
                for upper, lower in zip(children, children[1:]):
                    gap = lower[1] - upper[1] - upper[3]
                    self.assertGreater(gap, 0)
                    self.assertLessEqual(gap, view.gpx(12) + 1)
                self.assertLessEqual(children[0][0] - main[0] - main[2], view.gpx(40))
            for short, tall in zip(*relative):
                self.assertAlmostEqual(short, tall, delta=1)

    def test_windows_layout_keeps_existing_spacing_and_scaling(self):
        import aipet_agents_window as win
        view = object.__new__(win.AgentNetworkWindow)
        view.s, view.gz = 1.5, 1.0
        view._graph_line_height = lambda: 12 * view.s * view.gz
        net = {"agents": [{"id": ""}] + [{"id": str(i), "depth": 1} for i in range(3)]}
        with patch.object(win, "IS_MAC", False):
            for scale in (1.0, 1.5, 2.0):
                view.s = scale
                for zoom in (0.4, 1.0, 2.0):
                    view.gz = zoom
                    short, _ = view._layout(net, 1000, 300)
                    tall, _ = view._layout(net, 1000, 2000)
                    self.assertEqual([b[2:] for b in short], [b[2:] for b in tall])
                    self.assertEqual(tall[1][0] - tall[0][0] - tall[0][2], view.gpx(70))
                    self.assertGreater(tall[-1][1] - tall[1][1], short[-1][1] - short[1][1])

    def graph_window(self):
        import aipet_agents_window as win
        view = object.__new__(win.AgentNetworkWindow)
        view.canvas = MagicMock()
        view.canvas.winfo_width.return_value = 500
        view.canvas.winfo_height.return_value = 350
        view.gz = 1.0
        view._robots = {}
        view._title_rects = []
        view._scroll_to = MagicMock()
        view._update_zoombar = MagicMock()
        view.px = lambda n: n
        view.zoombar = MagicMock()
        view.zoombar.winfo_reqheight.return_value = 20
        view._stats_size = lambda: (150, 50)
        return view

    def auto_fit_window(self):
        view = self.graph_window()
        view.opts = {"auto_fit": True}
        view.canvas.winfo_ismapped.return_value = True
        view.blocks = [("one", {}, {"agents": [{"id": ""}, {"id": "child"}]})]
        view.fit_all = MagicMock()
        return view

    def test_auto_fit_on_add_remove_and_same_count_replacement(self):
        view = self.auto_fit_window()
        view._maybe_auto_fit()
        view.fit_all.assert_called_once()
        view.fit_all.reset_mock()
        view.blocks[0][2]["agents"].reverse()  # reordering and normal updates preserve the view
        view._maybe_auto_fit()
        view.fit_all.assert_not_called()
        view.blocks[0][2]["agents"].append({"id": "tool:Read"})
        view._maybe_auto_fit()
        self.assertEqual(view.fit_all.call_count, 1)
        view.blocks[0][2]["agents"].pop()
        view._maybe_auto_fit()
        self.assertEqual(view.fit_all.call_count, 2)
        view.blocks[0][2]["agents"][0] = {"id": "replacement"}
        view._maybe_auto_fit()
        self.assertEqual(view.fit_all.call_count, 3)

    def test_auto_fit_covers_other_sessions_and_empty_graph(self):
        view = self.auto_fit_window()
        view._maybe_auto_fit()
        view.fit_all.reset_mock()
        view.blocks.append(("two", {}, {"agents": [{"id": ""}]}))
        view._maybe_auto_fit()
        self.assertEqual(view.fit_all.call_count, 1)
        view.blocks.clear()
        view._maybe_auto_fit()
        self.assertEqual(view.fit_all.call_count, 2)

    def test_auto_fit_disabled_does_not_change_view_and_enabling_fits_immediately(self):
        view = self.auto_fit_window()
        view.opts["auto_fit"] = False
        view._maybe_auto_fit()
        view.fit_all.assert_not_called()
        view.opts["auto_fit"] = True
        view._maybe_auto_fit(force=True)
        view.fit_all.assert_called_once()
        view.fit_all.reset_mock()
        view._maybe_auto_fit()  # manual zoom/pan is kept until the next membership change
        view.fit_all.assert_not_called()

    def test_auto_fit_waits_for_window_to_be_visible(self):
        view = self.auto_fit_window()
        view.canvas.winfo_ismapped.return_value = False
        view._maybe_auto_fit()
        view.fit_all.assert_not_called()
        self.assertTrue(view._auto_fit_pending)
        view.canvas.winfo_ismapped.return_value = True
        view._maybe_auto_fit()
        view.fit_all.assert_called_once()
        self.assertFalse(view._auto_fit_pending)

    def test_center_uses_node_gravity_center_and_keeps_zoom(self):
        view = self.graph_window()
        view.gz = 1.7
        view._content = (3000, 3000)  # empty layout space must not affect centering
        view._node_rects = [(0, 0, 100, 100), (400, 0, 500, 100), (400, 200, 500, 300)]
        view.center_view(by_user=True)
        left, top = view._scroll_to.call_args.args
        self.assertAlmostEqual(left, (50 + 450 + 450) / 3 - 250)
        self.assertAlmostEqual(top, (50 + 50 + 250) / 3 - 175)
        self.assertEqual(view.gz, 1.7)

    def test_fit_all_includes_titles_and_fits_below_previous_zoom_limit(self):
        view = self.graph_window()

        def redraw(force=False):
            z = view.gz
            view._node_rects = [(0, 100 * z, 2000 * z, 1100 * z)]
            view._title_rects = [(0, 0, 2000 * z, 100 * z)]

        view._draw_graph = redraw
        redraw()
        view.fit_all()
        self.assertLess(view.gz, 0.4)
        left, top = view._scroll_to.call_args.args
        x0, y0, x1, y1 = view._graph_bounds()
        self.assertGreaterEqual(x0 - left, 10)
        self.assertLessEqual(x1 - left, 490)
        self.assertGreaterEqual(y0 - top, 30)
        self.assertLessEqual(y1 - top, 290)
        self.assertFalse(view._fit_view)
        view._update_zoombar.assert_called_once()

    def test_empty_graph_center_and_fit_do_nothing(self):
        view = self.graph_window()
        view._node_rects = []
        view.center_view(by_user=True)
        view.fit_all()
        view._scroll_to.assert_not_called()
        self.assertEqual(view.gz, 1.0)

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
    def test_both_scales_default_to_100_and_preserve_explicit_preferences(self):
        import aipet_agents_window as win
        for cfg in ({}, {"agent_window": {}}, {"agent_window": {"text_scale": "bad", "scale": False}}):
            opts = win.load_options(cfg)
            self.assertEqual((opts["scale"], opts["text_scale"]), (100, 100))
        opts = win.load_options({"agent_window": {"scale": 75, "text_scale": 135}})
        self.assertEqual((opts["scale"], opts["text_scale"]), (75, 135))

    def test_auto_fit_preference_is_saved_in_graph_options(self):
        import aipet_agents_window as win
        self.assertFalse(win.load_options({})["auto_fit"])
        self.assertTrue(win.load_options({"agent_window": {"auto_fit": True}})["auto_fit"])
        self.assertFalse(win.load_options({"agent_window": {"auto_fit": "yes"}})["auto_fit"])

    def test_options_load_and_snap(self):
        import aipet_agents_window as win
        opts = win.load_options({"agent_window": {"alpha": 73, "node_alpha": 0, "bg_alpha": 62, "on_top": "yes",
                                                  "frameless": True, "scale": 400, "separate": 1}})
        self.assertEqual((opts["alpha"], opts["node_alpha"], opts["bg_alpha"], opts["on_top"], opts["frameless"],
                          opts["scale"], opts["separate"]), (75, 30, 60, False, True, 150, False))  # opacity >= 30%
        self.assertEqual([win.dither(v) for v in (100, 90, 75, 50, 30)], ["", "", "gray75", "gray50", "gray25"])
        self.assertEqual(win.load_options({}), win.DEFAULT_OPTIONS)
        self.assertEqual(win.load_options({"agent_window": {"alpha": 5, "scale": 10}})["alpha"], 30)
        self.assertEqual(win.key_color("#fbfaf7"), "#fbfaf6")  # see-through key: one notch off the background

    def test_logs_beside_the_graph_need_width_not_height(self):
        import aipet_agents_window as win
        below, beside = win.needed_size(2, 1, 18, 16), win.needed_size(2, 1, 18, 16, side="right")
        self.assertGreater(beside[0], below[0])
        self.assertLess(beside[1], below[1])
        self.assertEqual(win.load_options({"agent_window": {"log_side": "up"}})["log_side"], "bottom")

    def test_geometry_kept_when_switching_compact(self):
        import aipet_agents_window as win
        self.assertEqual(win.parse_geometry("620x480+100+-20"), (620, 480, 100, -20))
        self.assertIsNone(win.parse_geometry(""))

    def test_colour_mix_for_fading(self):
        import aipet_agents_window as win
        self.assertEqual(win.mix("#000000", "#ffffff", 0), "#000000")
        self.assertEqual(win.mix("#000000", "#ffffff", 1), "#ffffff")
        self.assertEqual(win.mix("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(win.mix("systemTransparent", "#ffffff", 0.5), "systemTransparent")
        self.assertEqual(win.load_options({})["tidy"], False)

    def test_needed_size_follows_scale_agents_and_compact(self):
        import aipet_agents_window as win
        small, big = win.needed_size(2, 0.5, 9, 8), win.needed_size(2, 1.5, 27, 24)
        self.assertLess(small[0], big[0])
        self.assertLess(small[1], big[1])
        self.assertLess(win.needed_size(2, 1, 18, 16, compact=True)[1], win.needed_size(2, 1, 18, 16)[1])
        self.assertGreater(win.needed_size(8, 1, 18, 16)[0], win.needed_size(2, 1, 18, 16)[0])  # two columns


class FullReplyTests(unittest.TestCase):
    def test_log_keeps_the_whole_reply_and_the_label_its_last_line(self):
        info = agents.parse_claude([claude_assistant(time.time(), [
            {"type": "text", "text": "Summary: 3 callers.\n\nDetails below.\nTo retry, ask again."}])])
        self.assertEqual(info["lines"][0][2], "Summary: 3 callers.\n\nDetails below.\nTo retry, ask again.")
        self.assertEqual(agents.full("x" * 70000)[-1], "…")
        self.assertEqual(agents.full("x" * 7000), "x" * 7000)  # long results are kept whole


class HelperTests(unittest.TestCase):
    def test_model_names_and_tokens(self):
        self.assertEqual(agents.model_name("claude-haiku-4-5-20251001"), "haiku-4.5")
        self.assertEqual(agents.model_name("claude-opus-5-5"), "opus-5.5")
        self.assertEqual(agents.model_name("gpt-6.1-sol"), "gpt-6.1-sol")
        self.assertEqual([agents.fmt_tokens(n) for n in (0, 950, 84000, 1_000_000)], ["", "950", "84k", "1M"])


if __name__ == "__main__":
    unittest.main()
