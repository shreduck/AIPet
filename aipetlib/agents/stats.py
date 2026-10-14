"""The stats node: what the current task (since your latest prompt) used."""


SPAWN_TOOLS = {"Task": "Agent", "spawn_agent": "Agent"}  # starting a subagent, as the task counts it


def network_stats(net):
    """What the current task (since your latest prompt) used, for the agent network's stats node:
    {"subagents": [(type, times)], "calls": [(name, model, ctx, tokens)] one per agent in start order (main first),
     "ctx_total": sum of their contexts, "tokens_total": sum of the tokens they used (whole session),
     "tools": [(tool, times)], "skills": [(skill, times)] this task, "session_tools": {tool: times} the whole session
     (as far as its transcripts were read; the tool nodes count the same)."""
    start = net.get("turn_start") or 0
    types, tools, skills, session = {}, {}, {}, {}
    calls = []
    for a in net.get("agents", []):
        if a.get("tool"):
            continue  # tool children have no model context and are not subagents
        calls.append((a["name"], a.get("model_label") or "", int(a.get("ctx") or 0),
                      int((a.get("tokens") or {}).get("total") or 0)))
        for call in a.get("tool_calls") or []:
            name = SPAWN_TOOLS.get(call.get("name"), call.get("name"))
            if name and name not in ("wait", "wait_agent"):
                session[name] = session.get(name, 0) + 1
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
            "tokens_total": sum(c[3] for c in calls),
            "tools": order(tools), "skills": order(skills), "session_tools": session,
            "missing": bool(net.get("missing"))}
