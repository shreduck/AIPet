"""A session's agent network: the main agent and the subagents it started, read from the transcript files.

Claude Code writes each subagent's transcript next to the session's own one:
    <session transcript without .jsonl>/subagents/agent-<id>.jsonl   (+ agent-<id>.meta.json: agentType, description)
Its assistant lines carry the model (message.model), the context used (message.usage) and the effort ("effort").

Codex writes each spawned agent its own rollout file; its first line (session_meta) names the parent thread
(parent_thread_id), a nickname and the task path. Model and effort come from turn_context lines, the context from
token_count events (with the model's context window).

Everything here is read-only and works without Tk. The background tracker reads each transcript once, then consumes
every appended complete record, keeping history current even while the view is closed. A view opened before the
tracker catches up can initially read the transcript tail.
"""
import datetime
import glob
import itertools
import json
import os
import re
import threading
import time

import aipet as core

MAIN_TAIL = 262144  # bytes read from the end of the main transcript
SUB_TAIL = 131072  # ... and of each subagent's
MAX_LOG = 240
RECENT = 4.0  # a subagent file written this recently counts as working
SKIP_TEXT = ("<", "[Request interrupted", "[Image", "Caveat:")

_TAILS = {}  # normalized path -> ((mtime_ns, size), parsed, complete-line offset, file identity)
_CODEX_META = {}  # rollout path -> its session_meta payload (never changes)
_CACHE_LOCK = threading.RLock()


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


def full(text, limit=60000):
    """A whole message for the log (what an agent said, its full result), trimmed only when huge."""
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


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


def _read_updates(path, size, offset=None):
    """Read complete JSONL records since the last read, keeping partial lines for the next poll."""
    cursor = {"offset": offset or 0, "cut": False}

    def records():
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            start = (max(0, end - size) if size is not None else 0) if offset is None else offset
            f.seek(start)
            cursor["cut"] = offset is None and start > 0
            if offset is None and start:
                f.readline()  # skip the first cut line of an initial tail read
            cursor["offset"] = f.tell()
            while True:
                raw = f.readline()
                if not raw or not raw.endswith(b"\n"):
                    break
                cursor["offset"] = f.tell()
                try:
                    entry = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    yield entry

    return records(), cursor


def _merge_info(previous, current):
    before = (previous["lines"] if previous else []) + current["lines"]
    results = _primary_results({"lines": before, "final_lines": current["final_lines"],
                                "state": "done" if current["done"] is True else "working"})
    current["final_lines"] = list(dict.fromkeys((previous["final_lines"] if previous else []) +
                                                sorted(results, key=lambda row: row[0])))[-MAX_LOG:]
    current["queries"] = list(dict.fromkeys((previous["queries"] if previous else []) + current["queries"]))[-MAX_LOG:]
    if not previous:
        current["lines"] = current["lines"][-MAX_LOG:]
        return current
    for key in ("model", "effort", "ctx", "ctx_max", "last_prompt_t", "closed_t"):
        current[key] = current.get(key) or previous.get(key) or current[key]
    current["first_t"] = previous["first_t"] or current["first_t"]
    if current["done"] is None:
        current["done"] = previous["done"]
    current["lines"] = sorted(dict.fromkeys(previous["lines"] + current["lines"]), key=lambda row: row[0])[-MAX_LOG:]
    return current


def _cached(path, size, parse, bootstrap=False):
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    key = str(path)
    if os.name == "nt":
        if key.startswith("\\\\?\\UNC\\"):
            key = "\\\\" + key[8:]
        elif key.startswith("\\\\?\\"):
            key = key[4:]
        key = os.path.normcase(os.path.abspath(key))
    identity = (st.st_dev, st.st_ino)
    hit = _TAILS.pop(key, None)
    if hit and (hit[3] != identity or st.st_size < hit[0][1]):
        hit = None  # the transcript was replaced or truncated; do not mix sessions
    if bootstrap and hit and hit[1].get("cut"):
        hit = None  # fill any history missed by an earlier foreground tail read
    if hit and hit[0] == stamp:
        _TAILS[key] = hit
        return hit[1]
    try:
        entries, cursor = _read_updates(path, None if bootstrap else size, hit[2] if hit else None)
        parsed = hit[1] if hit else None
        while True:
            batch = list(itertools.islice(entries, 128))
            if not batch:
                break
            update = parse(batch)
            update["tool_calls"] = _merge_tool_calls(parsed["tool_calls"] if parsed else [], update["tool_calls"])
            parsed = _merge_info(parsed, update)
        parsed = parsed or _empty()
    except OSError:
        if hit:
            _TAILS[key] = hit
        return None
    closed = parsed.get("closed_t") or (parsed.get("last_prompt_t") if parse is parse_claude else 0) or 0
    for call in parsed["tool_calls"]:
        if not call.get("finished_t") and call.get("started_t", closed) < closed:
            call["finished_t"] = closed  # a later turn cannot reactivate a tool with no recorded result
    parsed["mtime"] = st.st_mtime
    parsed["cut"] = cursor["cut"] or bool(hit and hit[1].get("cut"))
    if len(_TAILS) >= 128:
        _TAILS.pop(next(iter(_TAILS)))  # evict one old file rather than discard every active history
    _TAILS[key] = (stamp, parsed, cursor["offset"], identity)
    return parsed


def _empty():
    return {"model": "", "effort": "", "ctx": 0, "ctx_max": 0, "lines": [], "last_prompt_t": 0.0, "done": None,
            "first_t": 0.0, "tool_calls": [], "closed_t": 0.0, "final_lines": [], "queries": []}


def _tool_text(value):
    """Readable input/output text, preserving structured content."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return full(value)
    return full(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _merge_tool_calls(previous, current):
    by_id = {c["id"]: dict(c) for c in previous}
    for call in current:
        merged = by_id.setdefault(call["id"], {})
        started = merged.get("started_t")
        merged.update(call)
        if started:
            merged["started_t"] = started
    calls = sorted(by_id.values(), key=lambda c: c.get("finished_t") or c.get("started_t") or 0)
    # Keep outstanding calls even if their input has fallen out of the transcript tail.
    pending = [c for c in calls if not c.get("finished_t")]
    groups = {}
    for call in calls:
        if call.get("finished_t"):
            groups.setdefault(call.get("name", ""), []).append(call)
    finished = [c for group in groups.values() for c in group[-MAX_LOG // 2:]]
    return sorted(pending + finished, key=lambda c: c.get("started_t") or c.get("finished_t") or 0)


def _tool_failed(output):
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return False
    return isinstance(output, dict) and bool(output.get("isError") or output.get("is_error") or output.get("error"))


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
                    lines.append((t, "say", full(c["text"])))
                elif c.get("type") == "thinking" and str(c.get("thinking", "")).strip():
                    lines.append((t, "think", core._phrase(c["thinking"], 160)))
                elif c.get("type") == "tool_use":
                    name, args = str(c.get("name") or "tool"), c.get("input") or {}
                    cid = str(c.get("id") or f"{t}:{name}:{len(info['tool_calls'])}")
                    info["tool_calls"].append({"id": cid, "name": name, "input": _tool_text(args), "started_t": t})
                    if name in ("Agent", "Task"):
                        what = args.get("subagent_type") or "agent"
                        lines.append((t, "spawn", f"started {what}: {short(args.get('description'), 70)}"))
                    else:
                        arg = _tool_arg(name, args)
                        lines.append((t, "tool", f"{name}({arg})" if arg else name))
        else:
            for c in parts:
                if c.get("type") == "tool_result":
                    cid = str(c.get("tool_use_id") or "")
                    if cid:
                        info["tool_calls"].append({"id": cid, "output": _tool_text(c.get("content")),
                                                   "finished_t": t, "error": bool(c.get("is_error"))})
                    if c.get("is_error"):
                        body = c.get("content")
                        if isinstance(body, list):
                            body = " ".join(str(x.get("text", "")) for x in body if isinstance(x, dict))
                        lines.append((t, "error", short(body, 120)))
                elif c.get("type") == "text":
                    text = str(c.get("text", "")).strip()
                    if text and not text.startswith(SKIP_TEXT) and not e.get("isMeta"):
                        lines.append((t, "you", short(text, 160)))
                        info["queries"].append((t, short(text, 160)))
                        info["last_prompt_t"] = t
    info["tool_calls"] = _merge_tool_calls([], info["tool_calls"])
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
            info["closed_t"] = t
        elif kind == "response_item" and sub == "message":
            role = p.get("role")
            for c in p.get("content") or []:
                text = str((c or {}).get("text", "")).strip() if isinstance(c, dict) else ""
                if not text or text.startswith(SKIP_TEXT):
                    continue
                if role == "assistant":
                    lines.append((t, "say", full(text)))
                    if p.get("phase") == "final":
                        info["final_lines"].append((t, full(text)))
                elif role == "user":
                    lines.append((t, "you", short(text, 160)))
                    info["queries"].append((t, short(text, 160)))
                    info["last_prompt_t"] = t
        elif kind == "response_item" and sub == "reasoning":
            for s in p.get("summary") or []:
                if isinstance(s, dict) and str(s.get("text", "")).strip():
                    lines.append((t, "think", core._phrase(s["text"], 160)))
        elif kind == "response_item" and sub in ("function_call", "custom_tool_call"):
            name = str(p.get("name") or "tool")
            args = p.get("arguments") if sub == "function_call" else p.get("input")
            cid = str(p.get("call_id") or p.get("id") or f"{t}:{name}")
            info["tool_calls"].append({"id": cid, "name": name, "input": _tool_text(args), "started_t": t})
            if name == "spawn_agent":
                lines.append((t, "spawn", f"started {_tool_arg(name, args) or 'an agent'}"))
            elif name not in ("wait", "wait_agent"):
                arg = _tool_arg(name, args) if sub == "function_call" else _script_arg(args)
                lines.append((t, "tool", f"{name}({arg})" if arg else name))
        elif kind == "response_item" and sub in ("function_call_output", "custom_tool_call_output"):
            cid = str(p.get("call_id") or "")
            if cid:
                info["tool_calls"].append({"id": cid, "output": _tool_text(p.get("output")), "finished_t": t,
                                           "error": bool(p.get("is_error") or _tool_failed(p.get("output")))})
        elif kind == "event_msg" and sub in ("item_started", "item_completed"):
            call = p.get("item") or {}
            call_type = call.get("type")
            cid = str(call.get("id") or "")
            if cid and call_type in ("CommandExecution", "FileChange", "McpToolCall", "MCPToolCall"):
                if call_type == "CommandExecution":
                    name, args = "exec_command", {"command": call.get("command"), "cwd": call.get("cwd")}
                    output = call.get("aggregated_output", call.get("formatted_output", ""))
                elif call_type == "FileChange":
                    name, args, output = "apply_patch", call.get("changes"), call.get("stdout", "")
                else:
                    name = f"mcp__{call.get('server') or 'server'}__{call.get('tool') or 'tool'}"
                    args, output = call.get("arguments"), call.get("result")
                entry = {"id": cid, "name": name, "input": _tool_text(args), "started_t": t}
                if sub == "item_completed":
                    entry.update(output=_tool_text(output), finished_t=t,
                                 error=bool(call.get("error") or call.get("exit_code") or _tool_failed(output) or
                                            call.get("status") == "failed"))
                info["tool_calls"].append(entry)
    info["tool_calls"] = _merge_tool_calls([], info["tool_calls"])
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
        if meta:  # a newly created rollout may still be empty or have an incomplete first line
            _CODEX_META[path] = meta
        return meta
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
    candidates = []
    for folder in folders:
        for path in glob.glob(os.path.join(folder, "rollout-*.jsonl")):
            if os.path.basename(path) <= mine and folder == day:
                continue  # started before the parent
            meta = _codex_meta(path)
            if not meta.get("parent_thread_id"):
                continue
            if "guardian" in json.dumps(meta.get("source") or "") or meta.get("thread_source") == "guardian_review":
                continue
            candidates.append((str(meta.get("id") or os.path.basename(path)), path, meta))
    # this thread's children, then theirs (an agent spawned by a subagent names that subagent as its parent)
    found, parents = [], {str(thread_id)}
    while True:
        more = [c for c in candidates if str(c[2].get("parent_thread_id")) in parents and c not in found]
        if not more:
            return found
        found += more
        parents |= {c[0] for c in more}


# --------------------------------------------------------------------------- the network
def read_network(item, now=None, include_tools=False, bootstrap=False):
    with _CACHE_LOCK:
        return _read_network(item, now=now, include_tools=include_tools, bootstrap=bootstrap)


def _read_network(item, now=None, include_tools=False, bootstrap=False):
    """{"agents": [agent...], "log": [(t, index, kind, text)]} for one session item of the pet.
    agent: {"id", "name", "main", "model", "effort", "ctx", "ctx_max", "state", "thought", "lines", "kind"}
    state: working / needs_input / waiting (main agent waiting on subagents) / done / idle."""
    now = now or time.time()
    codex = item.get("agent") == "codex"
    transcript = core.transcript_file(item)
    if codex and transcript and item.get("session_id"):
        suffix = f"-{item['session_id']}.jsonl"
        if not os.path.basename(transcript).endswith(suffix):
            # Existing session files may point at a child until their next hook event.
            matches = glob.glob(os.path.join(os.path.dirname(transcript), f"rollout-*{suffix}"))
            transcript = max(matches, key=os.path.getmtime) if matches else ""
    parse = parse_codex if codex else parse_claude
    main_info = (_cached(transcript, MAIN_TAIL, parse, bootstrap=bootstrap) if transcript else None) or _empty()
    session_state = item.get("state") or "idle"
    running = dict(item.get("agent_ids") or {})
    wait_agent = str(item.get("wait_agent") or "")
    retention = core.DONE_TIMEOUT["v"] * 60 * 0.8  # 0 = never, just like the session setting

    subs = []
    files = (codex_subagent_files(transcript, item.get("session_id")) if codex
             else claude_subagent_files(transcript))
    turn_start = main_info["last_prompt_t"] or (now - 1800)
    # the task started before the part of a transcript that was read: the stats can miss its older entries
    missing = bool(main_info.get("cut") and not main_info["last_prompt_t"])
    for aid, path, meta in files:
        info = _cached(path, SUB_TAIL, parse, bootstrap=bootstrap)
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
                     "state": state, "mtime": info["mtime"], "first_t": info["first_t"] or info["mtime"], **{
                         k: info[k] for k in ("model", "effort", "ctx", "ctx_max", "lines")}})
    known = {s["id"] for s in subs}
    for aid, kind in running.items():  # running but no transcript found (older Claude Code): a bare node
        if aid not in known:
            subs.append({"id": aid, "name": kind or "subagent", "main": False, "kind": "", "path": "", "mtime": now,
                         "parent": "",
                         "first_t": now, "state": "needs_input" if wait_agent == aid and session_state == "needs_input"
                         else "working", "model": "", "effort": "", "ctx": 0, "ctx_max": 0, "lines": []})
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
            **{k: main_info[k] for k in ("model", "effort", "ctx", "ctx_max", "lines")}}
    agents = [main] + subs
    if include_tools:
        agents += tool_nodes(agents, now, retention)
    for a in agents:
        a["model_label"] = model_name(a["model"])
        a["thought"] = next((core._phrase(text, 160) if kind == "say" else text for _, kind, text in reversed(a["lines"])
                             if kind in ("think", "say", "tool", "spawn")), "")
        if a["state"] == "done":  # its result: the last line of its final reply (the log has all of it)
            a["thought"] = next((core._phrase(text, 160) for _, kind, text in reversed(a["lines"]) if kind == "say"),
                                a["thought"])
    if main_state == "waiting":
        waiting_on = [s["name"] for s in subs if s["state"] in ("working", "needs_input")]
        main["thought"] = "waiting for " + ", ".join(waiting_on[:3]) if waiting_on else main["thought"]
    log = sorted(((t, i, kind, text) for i, a in enumerate(agents) for t, kind, text in a["lines"]),
                 key=lambda x: x[0])[-MAX_LOG:]
    return {"agents": agents, "log": log, "turn_start": turn_start, "missing": missing}


def tool_nodes(agents, now, retention):
    """One child node and input/output log per unique tool for each calling agent."""
    nodes = []
    for agent in agents:
        grouped = {}
        for call in agent.get("tool_calls") or []:
            if call.get("name"):
                grouped.setdefault(call["name"], []).append(call)
        for name, calls in grouped.items():
            latest = max(c.get("finished_t") or c.get("started_t") or 0 for c in calls)
            pending = any(not c.get("finished_t") for c in calls)
            busy = pending and agent["state"] in ("working", "waiting", "needs_input")
            if retention and not busy and latest and now - latest >= retention:
                continue
            rows = []
            for call in calls:
                if "input" in call:
                    rows.append((call.get("started_t", 0), "input",
                                 f"{short(call['input'], 120)}\ncall {call['id']}\n{call['input']}"))
                if "output" in call:
                    rows.append((call.get("finished_t", 0), "error" if call.get("error") else "output",
                                 f"{short(call['output'], 120)}\ncall {call['id']}\n{call['output']}"))
            latest_call = max(calls, key=lambda c: c.get("finished_t") or c.get("started_t") or 0)
            state = "working" if busy else ("error" if latest_call.get("error") else "done")
            nodes.append({"id": "tool:" + json.dumps([agent["id"], name]), "name": name,
                          "tab_name": f"{name} · {agent['name']}", "main": False, "tool": True,
                          "mcp": is_mcp_tool(name),
                          "parent": agent["id"], "depth": agent.get("depth", 0) + 1,
                          "kind": f"{len(calls)} calls · {agent['name']}", "path": agent["path"],
                          "state": state, "mtime": latest, "first_t": min(c.get("started_t") or latest for c in calls),
                          "model": "", "effort": "", "ctx": 0, "ctx_max": 0,
                          "lines": sorted(rows, key=lambda r: r[0])[-MAX_LOG:]})
    return nodes


def is_mcp_tool(name):
    return bool(re.search(r"(?:^|[._])mcp(?:[._]|$)", str(name), re.IGNORECASE))


def default_log_filters():
    return {"show_tools": True, "primary": False, "tools": None, "mcp": None, "only_tool": None}


def available_log_tools(net, selected=-1, mcp=False):
    agents = net.get("agents", [])
    owner = agents[selected]["id"] if 0 <= selected < len(agents) else None
    return sorted({a["name"] for a in agents if a.get("tool") and
                   (owner is None or a["parent"] == owner) and is_mcp_tool(a["name"]) == mcp}, key=str.lower)


def _primary_results(agent):
    results = set(agent.get("final_lines") or [])
    candidate = None
    for t, kind, text in agent.get("lines", []):
        if kind == "you":
            if candidate:
                results.add(candidate)
            candidate = None
        elif kind == "say":
            candidate = (t, text)
        elif kind in ("tool", "spawn"):
            candidate = None  # a reply followed by more tool work was commentary
    if candidate and agent.get("state") in ("done", "idle", "error"):
        results.add(candidate)
    return results


def filtered_log_rows(net, selected, filters):
    """Filter before clipping history, so tool traffic cannot bury queries and final results."""
    agents = net.get("agents", [])
    owner = agents[selected]["id"] if 0 <= selected < len(agents) else None
    tools_by_owner = {}
    for a in agents:
        if a.get("tool"):
            tools_by_owner.setdefault(a["parent"], set()).add(a["name"])
    rows = []
    for i, a in enumerate(agents):
        if owner is not None and (a["parent"] if a.get("tool") else a["id"]) != owner:
            continue
        if a.get("tool"):
            if not filters["show_tools"] or filters["primary"]:
                continue
            name = a["name"]
            if filters.get("only_tool") and name != filters["only_tool"]:
                continue
            allowed = filters["mcp" if is_mcp_tool(name) else "tools"]
            if allowed is not None and name not in allowed:
                continue
            rows.extend((t, i, kind, text) for t, kind, text in a["lines"])
            continue
        if filters.get("only_tool") and not filters["primary"]:
            continue
        results = _primary_results(a) if filters["primary"] else set()
        if filters["primary"]:
            queries = a.get("queries") or [(t, text) for t, kind, text in a.get("lines", []) if kind == "you"]
            rows.extend((t, i, "you", text) for t, text in queries)
            rows.extend((t, i, "say", text) for t, text in results)
            continue
        for t, kind, text in a.get("lines", []):
            if kind in ("tool", "spawn", "error"):
                if not filters["show_tools"] or filters["tools"] is not None or filters["mcp"] is not None:
                    continue
                recorded = tools_by_owner.get(a["id"], set())
                if (kind == "tool" and text.partition("(")[0] in recorded) or (kind != "tool" and recorded):
                    continue  # the structured input/output rows already represent this call
            rows.append((t, i, kind, text))
    return sorted(rows, key=lambda row: row[0])[-MAX_LOG:]


class NetworkTracker(threading.Thread):
    """Keep agent and tool history warm for every live Claude/Codex session without any Tk window."""
    def __init__(self, get_items, interval=0.7):
        super().__init__(name="AIPet agent tracker", daemon=True)
        self.get_items, self.interval = get_items, interval
        self._stop_event = threading.Event()

    def poll(self):
        for item in list(self.get_items()):
            if item.get("source") != "CC" or not item.get("session_id"):
                continue
            try:
                read_network(item, include_tools=True, bootstrap=True)
            except Exception as exc:
                core.log_error(f"agent tracker: {exc!r}")

    def run(self):
        while not self._stop_event.is_set():
            try:
                self.poll()
            except Exception as exc:
                core.log_error(f"agent tracker: {exc!r}")
            self._stop_event.wait(self.interval)

    def stop(self):
        self._stop_event.set()


def network_stats(net):
    """What the current task (since your latest prompt) used, for the agent network's stats node:
    {"subagents": [(type, times)], "calls": [(name, model, ctx)] one per agent in start order (main first),
     "ctx_total": sum of their contexts, "tools": [(tool, times)], "skills": [(skill, times)]}."""
    start = net.get("turn_start") or 0
    types, tools, skills = {}, {}, {}
    calls = []
    for a in net.get("agents", []):
        if a.get("tool"):
            continue  # tool children have no model context and are not subagents
        calls.append((a["name"], a.get("model_label") or "", int(a.get("ctx") or 0)))
        if not a["main"]:
            base = a.get("kind") or a["name"].rsplit(" #", 1)[0]  # by type: general-purpose ×3
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
