"""Tool calls: readable input/output, merging partial records, and one child node per tool."""
import json
import re

from .records import MAX_LOG, full, short


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


def tool_nodes(agents, now, retention, turn_start=0):
    """One child node and input/output log per unique tool for each calling agent. Its label counts the calls of the
    whole session and of this task (since your latest prompt, turn_start), like the stats node."""
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
            task = sum(1 for c in calls if (c.get("started_t") or c.get("finished_t") or 0) >= turn_start - 1)
            state = "working" if busy else ("error" if latest_call.get("error") else "done")
            nodes.append({"id": "tool:" + json.dumps([agent["id"], name]), "name": name,
                          "tab_name": f"{name} · {agent['name']}", "main": False, "tool": True,
                          "mcp": is_mcp_tool(name),
                          "parent": agent["id"], "depth": agent.get("depth", 0) + 1,
                          "kind": f"{len(calls)} calls · {task} this task · {agent['name']}",
                          "calls": len(calls), "task_calls": task, "path": agent["path"],
                          "state": state, "mtime": latest, "first_t": min(c.get("started_t") or latest for c in calls),
                          "model": "", "effort": "", "ctx": 0, "ctx_max": 0,
                          "lines": sorted(rows, key=lambda r: r[0])[-MAX_LOG:]})
    return nodes


def is_mcp_tool(name):
    return bool(re.search(r"(?:^|[._])mcp(?:[._]|$)", str(name), re.IGNORECASE))
