"""Claude Code transcripts: parsing and subagent discovery."""
import glob
import json
import os

from ..text import phrase
from .records import SKIP_TEXT, _empty, _tool_arg, _ts, full, short
from .tools import _merge_tool_calls, _tool_text


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
                    lines.append((t, "think", phrase(c["thinking"], 160)))
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
