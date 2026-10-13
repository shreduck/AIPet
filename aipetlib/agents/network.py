"""One session's agent network: the main agent, its subagents (and theirs), states, retention and tool nodes."""
import time

from aipet_codex import root_rollout

from ..runtime import preferences
from ..sessions import transcript_path
from ..text import phrase
from . import history
from .claude import claude_subagent_files, parse_claude
from .codex import codex_subagent_files, parse_codex
from .records import MAX_LOG, RECENT, _empty, model_name, token_totals
from .tools import tool_nodes


def _tokens(info):
    """An agent's token totals; partial while only the end of its transcript has been read."""
    totals = token_totals(info)
    return dict(totals, partial=bool(info.get("cut"))) if totals else None


def read_network(item, now=None, include_tools=False, bootstrap=False):
    with history._CACHE_LOCK:
        return _read_network(item, now=now, include_tools=include_tools, bootstrap=bootstrap)


def _read_network(item, now=None, include_tools=False, bootstrap=False):
    """{"agents": [agent...], "log": [(t, index, kind, text)]} for one session item of the pet.
    agent: {"id", "name", "main", "model", "effort", "ctx", "ctx_max", "state", "thought", "lines", "kind",
            "tokens": {"in", "cached", "out", "total", "partial"} or None, "usage_at": {line time: (prompt, output)}}
    state: working / needs_input / waiting (main agent waiting on subagents) / done / idle."""
    now = now or time.time()
    codex = item.get("agent") == "codex"
    transcript = transcript_path(item)
    if codex and item.get("session_id") and item.get("env") != "wsl":
        transcript = root_rollout(item["session_id"], transcript)
    parse = parse_codex if codex else parse_claude
    main_info = (history._cached(transcript, history.MAIN_TAIL, parse, bootstrap=bootstrap) if transcript else None) or _empty()
    session_state = item.get("state") or "idle"
    running = dict(item.get("agent_ids") or {})
    wait_agent = str(item.get("wait_agent") or "")
    retention = preferences.DONE_TIMEOUT["v"] * 60 * 0.8  # 0 = never, just like the session setting

    subs = []
    files = (codex_subagent_files(transcript, item.get("session_id")) if codex
             else claude_subagent_files(transcript))
    turn_start = main_info["last_prompt_t"] or (now - 1800)
    # the task started before the part of a transcript that was read: the stats can miss its older entries
    missing = bool(main_info.get("cut") and not main_info["last_prompt_t"])
    for aid, path, meta in files:
        info = history._cached(path, history.SUB_TAIL, parse, bootstrap=bootstrap)
        if not info:
            continue
        if codex:
            nick, task = meta.get("agent_nickname") or "", str(meta.get("agent_path") or "").rsplit("/", 1)[-1]
            name = f"{nick} ({task})" if nick and task else (nick or task or "agent")
            kind = str(meta.get("agent_role") or "")
            busy = aid in running or info["done"] is False or (info["done"] is None and now - info["mtime"] < RECENT)
        else:
            # its name: the short description it was started with ("Find the callers"), else its type
            kind = str(meta.get("agentType") or running.get(aid) or "subagent")
            name = " ".join(str(meta.get("description") or "").split())[:60] or kind
            busy = aid in running or now - info["mtime"] < RECENT
        state = "working" if busy and session_state in ("working", "needs_input") else "done"
        if session_state == "needs_input" and wait_agent and wait_agent == aid:
            state = "needs_input"
        # who started it: the main agent (""), or another subagent (an agent of an agent)
        parent = str(meta.get("parentAgentId") or meta.get("parent_thread_id") or "")
        if parent == str(item.get("session_id") or ""):
            parent = ""
        subs.append({"id": aid, "name": name, "main": False, "kind": kind if kind != name else "", "path": path,
                     "parent": parent,
                     "tool_calls": info["tool_calls"],
                     "final_lines": info["final_lines"],
                     "queries": info["queries"],
                     "_missing": bool(info.get("cut") and (info["first_t"] or 0) > turn_start + 1),
                     "state": state, "mtime": info["mtime"], "first_t": info["first_t"] or info["mtime"],
                     "tokens": _tokens(info), "usage_at": info.get("usage_at") or {}, **{
                         k: info[k] for k in ("model", "effort", "ctx", "ctx_max", "lines")}})
    known = {s["id"] for s in subs}
    for aid, kind in running.items():  # running but no transcript found (older Claude Code): a bare node
        if aid not in known:
            subs.append({"id": aid, "name": kind or "subagent", "main": False, "kind": "", "path": "", "mtime": now,
                         "parent": "",
                         "first_t": now, "state": "needs_input" if wait_agent == aid and session_state == "needs_input"
                         else "working", "model": "", "effort": "", "ctx": 0, "ctx_max": 0, "lines": [],
                         "tokens": None, "usage_at": {}})
    by_id = {s["id"]: s for s in subs}
    visible = {s["id"] for s in subs if not retention or s["state"] in ("working", "needs_input")
               or now - s["mtime"] < retention}
    # Keep ancestors while any descendant is visible, so expiry never flattens a live branch.
    pending = list(visible)
    while pending:
        parent = by_id[pending.pop()]["parent"]
        if parent in by_id and parent not in visible:
            visible.add(parent)
            pending.append(parent)
    subs = [s for s in subs if s["id"] in visible]
    for s in subs:
        missing = s.pop("_missing", False) or missing
    subs.sort(key=lambda s: s["first_t"])
    ids = {s["id"] for s in subs}
    for s in subs:  # a parent that isn't shown: hang it off the main agent
        if s["parent"] not in ids:
            s["parent"] = ""
    depth = {"": 0}
    for _ in range(len(subs)):  # depth = how many agents up to the main one (1 = started by the main agent)
        for s in subs:
            if s["parent"] in depth:
                depth[s["id"]] = depth[s["parent"]] + 1
    for s in subs:
        s["depth"] = depth.get(s["id"], 1)
    names = {}
    for s in subs:  # "Explore", "Explore" -> "Explore #1", "Explore #2"
        names[s["name"]] = names.get(s["name"], 0) + 1
    seen = {}
    for s in subs:
        if names[s["name"]] > 1:
            seen[s["name"]] = seen.get(s["name"], 0) + 1
            s["name"] = f"{s['name']} #{seen[s['name']]}"

    main_state = {"needs_input": "needs_input", "working": "working", "error": "error", "done": "done"}.get(
        session_state, "idle")
    if main_state == "needs_input" and wait_agent and any(s["id"] == wait_agent for s in subs):
        main_state = "waiting"
    elif main_state == "working" and any(s["state"] == "working" for s in subs) and \
            now - main_info.get("mtime", 0) > RECENT:
        main_state = "waiting"
    main = {"id": "", "name": "main", "main": True, "kind": "Codex" if codex else "Claude", "path": transcript,
            "state": main_state, "tool_calls": main_info["tool_calls"],
            "final_lines": main_info["final_lines"],
            "queries": main_info["queries"],
            "model_hint": main_info.get("model_hint", ""),
            "tokens": _tokens(main_info), "usage_at": main_info.get("usage_at") or {},
            **{k: main_info[k] for k in ("model", "effort", "ctx", "ctx_max", "lines")}}
    agents = [main] + subs
    if include_tools:
        agents += tool_nodes(agents, now, retention)
    for a in agents:
        a["model_label"] = model_name(a["model"])
        a["thought"] = next((phrase(text, 160) if kind == "say" else text for _, kind, text in reversed(a["lines"])
                             if kind in ("think", "say", "tool", "spawn")), "")
        if a["state"] == "done":  # its result: the last line of its final reply (the log has all of it)
            a["thought"] = next((phrase(text, 160) for _, kind, text in reversed(a["lines"]) if kind == "say"),
                                a["thought"])
    if main_state == "waiting":
        waiting_on = [s["name"] for s in subs if s["state"] in ("working", "needs_input")]
        main["thought"] = "waiting for " + ", ".join(waiting_on[:3]) if waiting_on else main["thought"]
    log = sorted(((t, i, kind, text) for i, a in enumerate(agents) for t, kind, text in a["lines"]),
                 key=lambda x: x[0])[-MAX_LOG:]
    return {"agents": agents, "log": log, "turn_start": turn_start, "missing": missing}
