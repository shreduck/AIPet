"""Conversation names, transcript identity and prompt fallback parsing."""
import json
import os
from . import codex as hook_codex
from . import requests as hook_requests

def request_from_transcript(path, max_bytes=300000):
    """Fallback for sessions that only send a Notification (the VS Code extension): the tool call that has no result
    yet in the transcript is the one asking for permission. Reads just the tail of the file."""
    try:
        if not path or not os.path.isfile(path):
            return {}
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # drop the partial first line
            lines = f.read().decode("utf-8", "replace").splitlines()
        uses, done = {}, set()
        for line in lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            content = (obj.get("message") or {}).get("content") if isinstance(obj, dict) else None
            for blk in content if isinstance(content, list) else []:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "tool_use":
                    uses[blk.get("id")] = blk
                elif blk.get("type") == "tool_result":
                    done.add(blk.get("tool_use_id"))
        pending = [b for i, b in uses.items() if i not in done]
        if not pending:
            return {}
        blk = pending[-1]
        req = hook_requests.build_request({"tool_name": blk.get("name"), "tool_input": blk.get("input"), "tool_use_id": blk.get("id")})
        req["source"] = "transcript"  # not answerable: no PermissionRequest hook is waiting for a click
        if len(pending) > 1:
            req["description"] = (req["description"] + " " if req["description"] else "") + "(%d tool calls pending; showing the latest)" % len(pending)
        return req
    except Exception:
        return {}


def conversation_title(path, max_bytes=400000):
    """(name the user gave it, name Claude Code gave it) from the transcript, "" where there is none: custom-title lines
    (customTitle) and agent-name lines (agentName; older versions wrote summary / ai-title lines instead). Reads only the
    end of the file; the caller keeps the first generated name, so a long transcript doesn't lose it."""
    try:
        if not path or not os.path.isfile(path):
            return "", ""
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()
            lines = f.read().decode("utf-8", "replace").splitlines()
        custom = agent_name = generated = ""
        for line in lines:
            low = line.lower()
            if "title" not in low and "agent-name" not in low and '"summary"' not in line:  # cheap pre-filter
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            kind = str(obj.get("type", "")) if isinstance(obj, dict) else ""
            if kind == "custom-title":
                custom = str(obj.get("customTitle") or obj.get("title") or "") or custom
            elif kind == "agent-name":
                agent_name = str(obj.get("agentName") or "") or agent_name
            elif kind in ("summary", "ai-title"):
                generated = str(obj.get("aiTitle") or obj.get("title") or obj.get("summary") or "") or generated
        clean = lambda t: " ".join(t.split())[:60]  # noqa: E731
        return clean(custom), clean(agent_name or generated)
    except Exception:
        return "", ""


def codex_thread_name(session_id, max_bytes=2_000_000):
    """Codex's name for a thread, as its UI shows it: the newest entry for this id in <CODEX_HOME>/session_index.jsonl
    ({"id", "thread_name", "updated_at"}). The hook runs where Codex runs (Windows or WSL), so ~ is the right home."""
    try:
        home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
        path = os.path.join(home, "session_index.jsonl")
        if not session_id or not os.path.isfile(path):
            return ""
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()
            lines = f.read().decode("utf-8", "replace").splitlines()
        name = ""
        for line in lines:
            if session_id not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("id") == session_id and obj.get("thread_name"):
                name = str(obj["thread_name"])
        return " ".join(name.split())[:60]
    except Exception:
        return ""


def codex_session_transcript(session_id, transcript, previous=""):
    """Keep the root rollout: Codex's active subagent hooks carry the child's path."""
    return hook_codex.root_rollout(session_id, transcript, previous)
