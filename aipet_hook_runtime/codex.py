"""Read-only Codex rollout identity and discovery, shared by the app and standalone hooks."""
import glob
import json
import os
import re
import time

_META = {}
_LOOKUPS = {}


def session_meta(path):
    if path in _META:
        return _META[path]
    try:
        with open(path, "rb") as stream:
            first = json.loads(stream.readline().decode("utf-8", "replace"))
        meta = dict(first["payload"]) if first.get("type") == "session_meta" else {}
    except (OSError, ValueError, TypeError, KeyError):
        return {}
    source = meta.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    if isinstance(spawn, dict):
        for key in ("parent_thread_id", "agent_nickname", "agent_path", "agent_role"):
            if not meta.get(key) and spawn.get(key):
                meta[key] = spawn[key]
    if meta:
        if len(_META) >= 2048:
            _META.clear()
        _META[path] = meta
    return meta


def internal_thread(meta):
    source = meta.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    return (meta.get("thread_source") in ("guardian_review", "memory_consolidation", "thread_summary") or
            isinstance(subagent, dict) and subagent.get("other") in ("guardian", "memory_consolidation", "thread_summary"))


def root_rollout(session_id, transcript="", previous=""):
    """Accept resumed filenames, keep child hooks on their root, and recover missing saved paths."""
    if not session_id:
        return ""
    sid = str(session_id)
    pattern = "rollout-*-" + glob.escape(sid) + "*.jsonl"
    name = re.compile(r"^rollout-.*-" + re.escape(sid) + r"(?:_[^/\\]+)?\.jsonl$")

    def valid(path):
        if not path or not name.match(os.path.basename(path)) or not os.path.isfile(path):
            return False
        meta = session_meta(path)
        return (not meta.get("id") or str(meta["id"]) == sid) and not internal_thread(meta)

    for path in (transcript, previous):
        if valid(path):
            return path
    for folder in dict.fromkeys(os.path.dirname(p) for p in (transcript, previous) if p):
        matches = [p for p in glob.glob(os.path.join(folder, pattern)) if valid(p)]
        if matches:
            return max(matches, key=os.path.getmtime)
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    key = (home, sid)
    cached = _LOOKUPS.get(key)
    if cached and time.monotonic() - cached[0] < 5:
        return cached[1] if valid(cached[1]) else ""
    matches = [p for p in glob.glob(os.path.join(home, "sessions", "*", "*", "*", pattern)) if valid(p)]
    path = max(matches, key=os.path.getmtime) if matches else ""
    if len(_LOOKUPS) >= 512:
        _LOOKUPS.clear()
    _LOOKUPS[key] = (time.monotonic(), path)
    return path


def question_event(ctx, transition):
    req = {"tool": str(ctx.data.get("tool_name")), "description": "", "detail": "", "id": ctx.hook.request_id(ctx.data),
           "t": time.time(), "kind": "question", "source": "codex-question", "agent": ctx.aid,
           "tool_use_id": ctx.use_id,
           "sig": ctx.hook.tool_signature(ctx.data), "shown": True, "questions": ctx.hook.codex_questions(ctx.data)}
    transition.requests = [r for r in transition.requests if r.get("source") != "transcript"] + [req]
    transition.state, transition.message = "needs_input", ctx.hook.permission_message(transition.requests[0])
    transition.wait_agent = transition.requests[0].get("agent", ctx.aid)


def interrupt(ctx, transition):
    transition.state, transition.message, transition.main_stopped = "done", "", False
    transition.requests, transition.wait_agent = [], ""
