"""Each session's context for the pet's banner and tooltips: the tokens in use and the window they fill.

The background tracker publishes one reading per session (its main agent). Codex reports its context window;
Claude transcripts don't, so Claude's window is estimated from the model: 200k, or 1M for a long-context request
("[1m]") or once the context has grown past 200k. "context_windows" in config.json overrides it per model.
"""
import threading
import time

from .records import fmt_tokens, model_name

CLAUDE_WINDOW = 200_000
LONG_WINDOW = 1_000_000

_LOCK = threading.Lock()
_SNAPSHOT = {}  # session key -> reading; replaced whole on every tracker pass


def reading(item, main):
    """The context reading of one session from its main agent (a read_network node), or None if unknown."""
    if not main or not main.get("ctx"):
        return None
    return {"agent": "codex" if item.get("agent") == "codex" else "claude", "used": int(main["ctx"]),
            "reported_max": int(main.get("ctx_max") or 0), "model": main.get("model") or "",
            "hint": main.get("model_hint") or "", "effort": main.get("effort") or "",
            "title": item.get("title") or "", "updated": time.time()}


def publish(readings):
    global _SNAPSHOT
    with _LOCK:
        _SNAPSHOT = dict(readings)


def get(key):
    with _LOCK:
        return _SNAPSHOT.get(key)


def window(r, overrides=None):
    """(window size, exact): reported by the harness, set in config.json, else estimated from the model."""
    if r.get("reported_max"):
        return r["reported_max"], True
    names = f"{r.get('model', '')} {r.get('hint', '')}".lower()
    for name, size in (overrides or {}).items():
        if str(name).lower() in names and isinstance(size, int) and not isinstance(size, bool) and size > 0:
            return size, True
    if "[1m]" in names or r.get("used", 0) > CLAUDE_WINDOW:
        return LONG_WINDOW, False
    return CLAUDE_WINDOW, False


def _row(r, overrides):
    size, exact = window(r, overrides)
    return dict(r, window=size, exact=exact, percent=min(100, round(100 * r["used"] / size)) if size else 0)


def banner_rows(items, overrides=None):
    """One context row per AI for the pet's banner: the fullest session's, with every session's reading kept for
    the tooltip (compact mode shows one pet for all)."""
    by_ai = {}
    for item in items:
        r = item.get("context")
        if r:
            by_ai.setdefault(r["agent"], []).append(_row(r, overrides))
    rows = []
    for ai in ("claude", "codex"):
        sessions = sorted(by_ai.get(ai, []), key=lambda row: -row["used"] / (row["window"] or 1))
        if sessions:
            rows.append(dict(sessions[0], kind="context", sessions=sessions))
    return rows


def label(row):
    return (f"{'CX' if row['agent'] == 'codex' else 'CL'} ctx {fmt_tokens(row['used'])}/"
            f"{'' if row['exact'] else '~'}{fmt_tokens(row['window'])}")


def describe(row):
    """One line for a session tooltip: used / window (percent) and the model."""
    model = " ".join(v for v in (model_name(row.get("model")), row.get("effort")) if v)
    return (f"Context {fmt_tokens(row['used'])} / {'' if row['exact'] else '~'}{fmt_tokens(row['window'])} "
            f"({row['percent']}%)" + (f" · {model}" if model else ""))


def tooltip(row):
    ai = "Codex" if row["agent"] == "codex" else "Claude"
    lines = [f"{ai} · context window",
             f"{row['used']:,} of {row['window']:,} tokens in use ({row['percent']}%)"]
    model = " ".join(v for v in (model_name(row.get("model")), row.get("effort")) if v)
    if model:
        lines.append(f"Model: {model}")
    if row["agent"] == "codex":
        lines.append("Window reported by Codex.")
    elif row["exact"]:
        lines.append("Window set in config.json (context_windows).")
    else:
        lines.append("Window estimated from the model: Claude transcripts don't record it.\n"
                     'Set "context_windows" in config.json if it is wrong, e.g. {"opus": 1000000}.')
    if len(row.get("sessions") or []) > 1:
        lines.append("")
        lines += [f"{s['title'] or 'session'}: {fmt_tokens(s['used'])} / {'' if s['exact'] else '~'}"
                  f"{fmt_tokens(s['window'])} ({s['percent']}%)" for s in row["sessions"][:12]]
    if row.get("updated"):
        lines.append("Last read: " + time.strftime("%H:%M:%S", time.localtime(row["updated"])))
    return "\n".join(lines)


def agent_window(agent, overrides=None):
    """(window size, exact) of one agent-network node: reported (Codex), set in config.json, else estimated."""
    return window({"reported_max": agent.get("ctx_max") or 0, "model": agent.get("model") or "",
                   "hint": agent.get("model_hint") or "", "used": agent.get("ctx") or 0}, overrides)


def node_line(agent, overrides=None):
    """A node's context line: "ctx 84k/~200k 42%", then the tokens the agent used so far ("Σ 1.2M")."""
    parts = []
    if agent.get("ctx"):
        size, exact = agent_window(agent, overrides)
        parts.append(f"ctx {fmt_tokens(agent['ctx'])}/{'' if exact else '~'}{fmt_tokens(size)} "
                     f"{min(100, round(100 * agent['ctx'] / size)) if size else 0}%")
    tokens = agent.get("tokens")
    if tokens:
        parts.append(f"Σ {'≥' if tokens.get('partial') else ''}{fmt_tokens(tokens['total'])}")
    return " · ".join(parts)


def tokens_text(tokens):
    """The tokens an agent used: "Σ 1.2M tokens (in 40k, cached 1.1M, out 45k)"; "" if none."""
    if not tokens:
        return ""
    return (f"Σ {'≥' if tokens.get('partial') else ''}{fmt_tokens(tokens['total'])} tokens "
            f"(in {fmt_tokens(tokens['in']) or 0}, cached {fmt_tokens(tokens['cached']) or 0}, "
            f"out {fmt_tokens(tokens['out']) or 0})")


def line_usage(usage):
    """A log line's request: "in 84k · out 1.2k" (the prompt it sent, the tokens it answered with)."""
    prompt, out = usage
    return f"in {fmt_tokens(prompt) or 0} · out {fmt_tokens(out) or 0}"

