"""Normalized agent record shapes, limits and small text helpers.

A parsed transcript ("info", see _empty) is a dict:
    model, effort        str
    model_hint           str: the model Claude Code was asked for (requestedModel), e.g. with a "[1m]" suffix
    ctx, ctx_max         int tokens (ctx_max 0 when unknown)
    lines                [(t, kind, text)]  kind: you / say / think / tool / spawn / error
    final_lines          [(t, text)]        replies known to be final results
    queries              [(t, text)]        your prompts
    tool_calls           [{"id", "name", "input", "output", "started_t", "finished_t", "error"}]
    last_prompt_t, first_t, closed_t   epoch seconds (0 when unknown)
    done                 True / False / None (unknown)
History adds "mtime" and "cut" (the read started inside the file).
"""
import datetime
import json
import re
MAX_LOG = 240
RECENT = 4.0  # a subagent file written this recently counts as working
SKIP_TEXT = ("<", "[Request interrupted", "[Image", "Caveat:")


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


def _empty():
    return {"model": "", "model_hint": "", "effort": "", "ctx": 0, "ctx_max": 0, "lines": [], "last_prompt_t": 0.0, "done": None,
            "first_t": 0.0, "tool_calls": [], "closed_t": 0.0, "final_lines": [], "queries": []}
