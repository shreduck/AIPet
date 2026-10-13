"""Which log rows the agent window shows, and which replies count as primary results."""
from .records import MAX_LOG
from .tools import is_mcp_tool


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
