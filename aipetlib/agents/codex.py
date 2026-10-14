"""Codex rollouts: parsing, rollout metadata and descendant discovery."""
import datetime
import glob
import os

from aipet_hook_runtime.codex import internal_thread, session_meta

from ..text import phrase
from .records import SKIP_TEXT, _empty, _script_arg, _tool_arg, _ts, full, short
from .tools import _merge_tool_calls, _tool_failed, _tool_text
_CODEX_META = {}  # rollout path -> its session_meta payload (never changes)


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
            total = data.get("total_token_usage") or {}
            cached = int(total.get("cached_input_tokens") or 0)
            if total:  # cumulative for the whole session: the latest one is the total
                info["tok_abs"] = (max(0, int(total.get("input_tokens") or 0) - cached), cached,
                                   int(total.get("output_tokens") or 0))
            if last and lines:  # this request's tokens on the line it answered with
                info["usage_at"][lines[-1][0]] = (int(last.get("input_tokens") or 0), int(last.get("output_tokens") or 0))
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
                    lines.append((t, "think", phrase(s["text"], 160)))
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
            meta = session_meta(path)
        except (OSError, ValueError):
            pass
        if len(_CODEX_META) > 2000:
            _CODEX_META.clear()
        if meta:  # a newly created rollout may still be empty or have an incomplete first line
            _CODEX_META[path] = meta
        return meta
    return _CODEX_META[path]


def codex_subagent_files(rollout, thread_id):
    """Descendant rollouts across session days, including branches created before a root resume."""
    if not rollout or not thread_id:
        return []
    day = os.path.dirname(rollout)
    folders = [day]
    try:  # A resumed conversation and its descendants can span many dates.
        y, m, d = (int(x) for x in day.replace("\\", "/").rstrip("/").split("/")[-3:])
        datetime.date(y, m, d)
        sessions = os.path.dirname(os.path.dirname(os.path.dirname(day)))
        folders = sorted(set(folders + glob.glob(os.path.join(sessions, "*", "*", "*"))))
    except (ValueError, TypeError):
        pass
    candidates = []
    for folder in folders:
        for path in glob.glob(os.path.join(folder, "rollout-*.jsonl")):
            meta = _codex_meta(path)
            if not meta.get("parent_thread_id"):
                continue
            if internal_thread(meta):
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
