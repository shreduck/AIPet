"""A session's agent network: the main agent and the subagents it started, read from the transcript files.

Claude Code writes each subagent's transcript next to the session's own one:
    <session transcript without .jsonl>/subagents/agent-<id>.jsonl   (+ agent-<id>.meta.json: agentType, description)
Its assistant lines carry the model (message.model), the context used (message.usage) and the effort ("effort").

Codex writes each spawned agent its own rollout file; its first line (session_meta) names the parent thread
(parent_thread_id), a nickname and the task path. Model and effort come from turn_context lines, the context from
token_count events (with the model's context window).

Everything here is read-only and works without Tk, so it can be tested on its own. Files are read from their tail and
only again when they changed.
"""
import datetime
import glob
import json
import os
import re
import time

import aipet as core

MAIN_TAIL = 262144  # bytes read from the end of the main transcript
SUB_TAIL = 131072  # ... and of each subagent's
MAX_SUBAGENTS = 8
MAX_LOG = 240
RECENT = 4.0  # a subagent file written this recently counts as working
SKIP_TEXT = ("<", "[Request interrupted", "[Image", "Caveat:")

_TAILS = {}  # path -> ((mtime_ns, size), parsed)
_CODEX_META = {}  # rollout path -> its session_meta payload (never changes)


# --------------------------------------------------------------------------- small helpers
def _ts(value):
    """ISO 8601 timestamp -> epoch seconds (0 when missing)."""
    if not value:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def short(text, limit=110):
    """One line, at most limit characters."""
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def fmt_tokens(n):
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


def model_name(model):
    """claude-opus-5-5 -> opus-5.5, claude-haiku-4-5-20251001 -> haiku-4.5; others unchanged."""
    m = str(model or "")
    if m.startswith("claude-"):
        parts = [p for p in m[7:].split("-") if not (p.isdigit() and len(p) == 8)]  # drop a date suffix
        if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
            return "-".join(parts[:-2]) + f"-{parts[-2]}.{parts[-1]}"
        return "-".join(parts)
    return m


def _tool_arg(name, args):
    """The interesting argument of a tool call, for a log line."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return short(args, 70)
    if not isinstance(args, dict):
        return ""
    for key in ("command", "cmd", "file_path", "path", "pattern", "url", "query", "description", "task_name",
                "skill", "prompt"):
        v = args.get(key)
        if v:
            if isinstance(v, list):
                v = " ".join(str(x) for x in v)
            return short(v, 70)
    return ""


def _script_arg(script):
    """Codex's exec tool runs a little script: the command it runs (cmd:"..."), else its first line."""
    text = str(script or "")
    m = re.search(r'cmd\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        return short(m.group(1).replace('\\\\', '\\'), 70)
    return short(text.strip().splitlines()[0] if text.strip() else "", 70)


def _read_tail(path, size):
    """The complete lines of the last `size` bytes of a file, parsed as JSON (bad lines skipped)."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        end = f.tell()
        f.seek(max(0, end - size))
        data = f.read()
    lines = data.decode("utf-8", "replace").splitlines()
    if end > size and lines:
        lines = lines[1:]  # the first one is cut
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _cached(path, size, parse):
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _TAILS.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        parsed = parse(_read_tail(path, size))
    except OSError:
        return None
    parsed["mtime"] = st.st_mtime
    parsed["cut"] = st.st_size > size  # only the file's tail was read
    if len(_TAILS) > 128:
        _TAILS.clear()
    _TAILS[path] = (stamp, parsed)
    return parsed


def _empty():
    return {"model": "", "effort": "", "ctx": 0, "ctx_max": 0, "lines": [], "last_prompt_t": 0.0, "done": None,
            "first_t": 0.0}


# --------------------------------------------------------------------------- Claude Code
def parse_claude(entries):
    """Model, effort, context and log lines of a Claude Code transcript (main or subagent).
    lines: [(t, kind, text)] with kind in you / say / think / tool / spawn / error."""
    info = _empty()
    lines = info["lines"]
    for e in entries:
        kind, msg = e.get("type"), e.get("message")
        if kind not in ("user", "assistant") or not isinstance(msg, dict):
            continue
        t = _ts(e.get("timestamp"))
        info["first_t"] = info["first_t"] or t
        content = msg.get("content")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else \
            [c for c in content or [] if isinstance(c, dict)]
        if kind == "assistant":
            info["model"] = msg.get("model") or e.get("requestedModel") or info["model"]
            info["effort"] = e.get("effort") or e.get("perTurnEffort") or info["effort"]
            usage = msg.get("usage") or {}
            ctx = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                       "cache_creation_input_tokens"))
            info["ctx"] = ctx or info["ctx"]
            for c in parts:
                if c.get("type") == "text" and str(c.get("text", "")).strip():
                    lines.append((t, "say", core._phrase(c["text"], 160)))
                elif c.get("type") == "thinking" and str(c.get("thinking", "")).strip():
                    lines.append((t, "think", core._phrase(c["thinking"], 160)))
                elif c.get("type") == "tool_use":
                    name, args = str(c.get("name") or "tool"), c.get("input") or {}
                    if name in ("Agent", "Task"):
                        what = args.get("subagent_type") or "agent"
                        lines.append((t, "spawn", f"started {what}: {short(args.get('description'), 70)}"))
                    else:
                        arg = _tool_arg(name, args)
                        lines.append((t, "tool", f"{name}({arg})" if arg else name))
        else:
            for c in parts:
                if c.get("type") == "tool_result":
                    if c.get("is_error"):
                        body = c.get("content")
                        if isinstance(body, list):
                            body = " ".join(str(x.get("text", "")) for x in body if isinstance(x, dict))
                        lines.append((t, "error", short(body, 120)))
                elif c.get("type") == "text":
                    text = str(c.get("text", "")).strip()
                    if text and not text.startswith(SKIP_TEXT) and not e.get("isMeta"):
                        lines.append((t, "you", short(text, 160)))
                        info["last_prompt_t"] = t
    return info


def claude_subagent_files(transcript):
    """[(agent id, transcript path, meta dict)] of a Claude Code session's subagents."""
    if not transcript or not transcript.endswith(".jsonl"):
        return []
    found = []
    for path in glob.glob(os.path.join(transcript[:-6], "subagents", "agent-*.jsonl")):
        aid = os.path.basename(path)[6:-6]
        meta = {}
        try:
            with open(path[:-6] + ".meta.json", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            pass
        found.append((aid, path, meta if isinstance(meta, dict) else {}))
    return found


# --------------------------------------------------------------------------- Codex
def parse_codex(entries):
    """Model, effort, context and log lines of a Codex rollout."""
    info = _empty()
    lines = info["lines"]
    started = finished = 0.0
    for e in entries:
        t = _ts(e.get("timestamp"))
        info["first_t"] = info["first_t"] or t
        p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        kind, sub = e.get("type"), p.get("type")
        if kind == "turn_context":
            info["model"] = p.get("model") or info["model"]
            info["effort"] = p.get("effort") or ((p.get("collaboration_mode") or {}).get("settings") or {}).get(
                "reasoning_effort") or info["effort"]
        elif kind == "event_msg" and sub == "token_count":
            data = p.get("info") or {}
            last = data.get("last_token_usage") or {}
            info["ctx"] = int(last.get("input_tokens") or 0) + int(last.get("output_tokens") or 0) or info["ctx"]
            info["ctx_max"] = int(data.get("model_context_window") or 0) or info["ctx_max"]
        elif kind == "event_msg" and sub == "task_started":
            started = t
        elif kind == "event_msg" and sub in ("task_complete", "turn_aborted"):
            finished = t
        elif kind == "response_item" and sub == "message":
            role = p.get("role")
            for c in p.get("content") or []:
                text = str((c or {}).get("text", "")).strip() if isinstance(c, dict) else ""
                if not text or text.startswith(SKIP_TEXT):
                    continue
                if role == "assistant":
                    lines.append((t, "say", core._phrase(text, 160)))
                elif role == "user":
                    lines.append((t, "you", short(text, 160)))
                    info["last_prompt_t"] = t
        elif kind == "response_item" and sub == "reasoning":
            for s in p.get("summary") or []:
                if isinstance(s, dict) and str(s.get("text", "")).strip():
                    lines.append((t, "think", core._phrase(s["text"], 160)))
        elif kind == "response_item" and sub in ("function_call", "custom_tool_call"):
            name = str(p.get("name") or "tool")
            args = p.get("arguments") if sub == "function_call" else p.get("input")
            if name == "spawn_agent":
                lines.append((t, "spawn", f"started {_tool_arg(name, args) or 'an agent'}"))
            elif name not in ("wait", "wait_agent"):
                arg = _tool_arg(name, args) if sub == "function_call" else _script_arg(args)
                lines.append((t, "tool", f"{name}({arg})" if arg else name))
    info["done"] = bool(finished and finished >= started) if (started or finished) else None
    return info


def _codex_meta(path):
    if path not in _CODEX_META:
        meta = {}
        try:
            with open(path, "rb") as f:
                first = json.loads(f.readline().decode("utf-8", "replace") or "{}")
            if first.get("type") == "session_meta" and isinstance(first.get("payload"), dict):
                meta = first["payload"]
        except (OSError, ValueError):
            pass
        if len(_CODEX_META) > 2000:
            _CODEX_META.clear()
        _CODEX_META[path] = meta
    return _CODEX_META[path]


def codex_subagent_files(rollout, thread_id):
    """[(id, path, meta)] of the agents a Codex thread spawned: rollouts written since the parent's, in its day folder
    and the next one, whose session_meta names it as the parent. Guardian (auto review) threads are left out."""
    if not rollout or not thread_id:
        return []
    day = os.path.dirname(rollout)
    folders = [day]
    try:  # .../YYYY/MM/DD: the next day too, for a session that runs past midnight
        y, m, d = (int(x) for x in day.replace("\\", "/").rstrip("/").split("/")[-3:])
        nxt = datetime.date(y, m, d) + datetime.timedelta(days=1)
        folders.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(day))),
                                    f"{nxt.year:04d}", f"{nxt.month:02d}", f"{nxt.day:02d}"))
    except (ValueError, TypeError):
        pass
    mine = os.path.basename(rollout)
    found = []
    for folder in folders:
        for path in glob.glob(os.path.join(folder, "rollout-*.jsonl")):
            if os.path.basename(path) <= mine and folder == day:
                continue  # started before the parent
            meta = _codex_meta(path)
            if str(meta.get("parent_thread_id") or "") != str(thread_id):
                continue
            if "guardian" in json.dumps(meta.get("source") or "") or meta.get("thread_source") == "guardian_review":
                continue
            found.append((str(meta.get("id") or os.path.basename(path)), path, meta))
    return found


# --------------------------------------------------------------------------- the network
def read_network(item, now=None):
    """{"agents": [agent...], "log": [(t, index, kind, text)]} for one session item of the pet.
    agent: {"id", "name", "main", "model", "effort", "ctx", "ctx_max", "state", "thought", "lines", "kind"}
    state: working / needs_input / waiting (main agent waiting on subagents) / done / idle."""
    now = now or time.time()
    codex = item.get("agent") == "codex"
    transcript = core.transcript_file(item)
    parse = parse_codex if codex else parse_claude
    main_info = (_cached(transcript, MAIN_TAIL, parse) if transcript else None) or _empty()
    session_state = item.get("state") or "idle"
    running = dict(item.get("agent_ids") or {})
    wait_agent = str(item.get("wait_agent") or "")

    subs = []
    files = (codex_subagent_files(transcript, item.get("session_id")) if codex
             else claude_subagent_files(transcript))
    turn_start = main_info["last_prompt_t"] or (now - 1800)
    # the task started before the part of a transcript that was read: the stats can miss its older entries
    missing = bool(main_info.get("cut") and not main_info["last_prompt_t"])
    for aid, path, meta in files:
        info = _cached(path, SUB_TAIL, parse)
        if not info:
            continue
        if aid not in running and info["mtime"] < turn_start - 1:
            continue  # from an earlier turn
        missing = missing or bool(info.get("cut") and (info["first_t"] or 0) > turn_start + 1)
        if codex:
            nick, task = meta.get("agent_nickname") or "", str(meta.get("agent_path") or "").rsplit("/", 1)[-1]
            name = f"{nick} ({task})" if nick and task else (nick or task or "agent")
            busy = info["done"] is False or (info["done"] is None and now - info["mtime"] < RECENT)
        else:
            name = meta.get("agentType") or running.get(aid) or "subagent"
            busy = aid in running or now - info["mtime"] < RECENT
        state = "working" if busy and session_state in ("working", "needs_input") else "done"
        if session_state == "needs_input" and wait_agent and wait_agent == aid:
            state = "needs_input"
        subs.append({"id": aid, "name": name, "main": False, "kind": meta.get("description") or "", "path": path,
                     "state": state, "mtime": info["mtime"], "first_t": info["first_t"] or info["mtime"], **{
                         k: info[k] for k in ("model", "effort", "ctx", "ctx_max", "lines")}})
    known = {s["id"] for s in subs}
    for aid, kind in running.items():  # running but no transcript found (older Claude Code): a bare node
        if aid not in known:
            subs.append({"id": aid, "name": kind or "subagent", "main": False, "kind": "", "path": "", "mtime": now,
                         "first_t": now, "state": "needs_input" if wait_agent == aid and session_state == "needs_input"
                         else "working", "model": "", "effort": "", "ctx": 0, "ctx_max": 0, "lines": []})
    subs.sort(key=lambda s: s["first_t"])
    subs = subs[-MAX_SUBAGENTS:]
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
            "state": main_state, **{k: main_info[k] for k in ("model", "effort", "ctx", "ctx_max", "lines")}}
    agents = [main] + subs
    for a in agents:
        a["model_label"] = model_name(a["model"])
        a["thought"] = next((text for _, kind, text in reversed(a["lines"]) if kind in ("think", "say", "tool",
                                                                                        "spawn")), "")
        if a["state"] == "done":
            a["thought"] = next((text for _, kind, text in reversed(a["lines"]) if kind == "say"), a["thought"])
    if main_state == "waiting":
        waiting_on = [s["name"] for s in subs if s["state"] in ("working", "needs_input")]
        main["thought"] = "waiting for " + ", ".join(waiting_on[:3]) if waiting_on else main["thought"]
    log = sorted(((t, i, kind, text) for i, a in enumerate(agents) for t, kind, text in a["lines"]),
                 key=lambda x: x[0])[-MAX_LOG:]
    return {"agents": agents, "log": log, "turn_start": turn_start, "missing": missing}


def network_stats(net):
    """What the current task (since your latest prompt) used, for the agent network's stats node:
    {"subagents": [(type, times)], "calls": [(name, model, ctx)] one per agent in start order (main first),
     "ctx_total": sum of their contexts, "tools": [(tool, times)], "skills": [(skill, times)]}."""
    start = net.get("turn_start") or 0
    types, tools, skills = {}, {}, {}
    calls = []
    for a in net.get("agents", []):
        calls.append((a["name"], a.get("model_label") or "", int(a.get("ctx") or 0)))
        if not a["main"]:
            base = a["name"].rsplit(" #", 1)[0]
            types[base] = types.get(base, 0) + 1
        for t, kind, text in a.get("lines", []):
            if kind not in ("tool", "spawn") or (t and t < start - 1):
                continue
            if kind == "spawn":
                tools["Agent"] = tools.get("Agent", 0) + 1
                continue
            name, _, arg = text.partition("(")
            name = name.strip() or "tool"
            tools[name] = tools.get(name, 0) + 1
            if name == "Skill" and arg:
                skill = arg.rstrip(")").strip()
                skills[skill] = skills.get(skill, 0) + 1
    order = lambda d: sorted(d.items(), key=lambda kv: (-kv[1], kv[0].lower()))  # noqa: E731
    return {"subagents": order(types), "calls": calls, "ctx_total": sum(c[2] for c in calls),
            "tools": order(tools), "skills": order(skills), "missing": bool(net.get("missing"))}
