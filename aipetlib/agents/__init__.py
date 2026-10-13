"""The agent network backend: transcript adapters, incremental history, tool calls, network assembly, log filters,
stats and the background tracker. Everything here is read-only, imports no Tk and never creates windows.

Claude Code writes each subagent's transcript next to the session's own one:
    <session transcript without .jsonl>/subagents/agent-<id>.jsonl   (+ agent-<id>.meta.json: agentType, description)
Its assistant lines carry the model (message.model), the context used (message.usage) and the effort ("effort").

Codex writes each spawned agent its own rollout file; its first line (session_meta) names the parent thread
(parent_thread_id), a nickname and the task path. Model and effort come from turn_context lines, the context from
token_count events (with the model's context window).

Module ownership:
    records      record shapes, limits and text helpers
    tools        tool-call input/output merging and tool child nodes
    claude       Claude Code transcript adapter and subagent discovery
    codex        Codex rollout adapter, rollout metadata cache and descendant discovery
    history      incremental per-file history cache (the one owner of _TAILS and its lock)
    network      one session's agent hierarchy, states and retention
    log_filters  log row selection and primary results
    stats        the stats node's numbers
    tracker      the always-on background tracker thread
"""
