"""TEMPORARY compatibility facade for the agent network backend, which now lives in aipetlib.agents.

New code imports the owning module directly (see aipetlib/agents/__init__.py). Patch names where they are used,
not here: patching this facade does not affect the moved code. Remove this file once no external tool needs it
(restructure.md, stage 11).
"""
from aipetlib.agents.claude import claude_subagent_files, parse_claude
from aipetlib.agents.codex import _CODEX_META, _codex_meta, codex_subagent_files, parse_codex
from aipetlib.agents.history import MAIN_TAIL, SUB_TAIL, _CACHE_LOCK, _TAILS, _cached, _merge_info, _read_updates
from aipetlib.agents.log_filters import _primary_results, available_log_tools, default_log_filters, filtered_log_rows
from aipetlib.agents.network import read_network
from aipetlib.agents.records import MAX_LOG, RECENT, SKIP_TEXT, _empty, fmt_tokens, full, model_name, short
from aipetlib.agents.stats import network_stats
from aipetlib.agents.tools import _merge_tool_calls, _tool_failed, _tool_text, is_mcp_tool, tool_nodes
from aipetlib.agents.tracker import NetworkTracker

__all__ = [
    "claude_subagent_files", "parse_claude", "_CODEX_META", "_codex_meta", "codex_subagent_files", "parse_codex",
    "MAIN_TAIL", "SUB_TAIL", "_CACHE_LOCK", "_TAILS", "_cached", "_merge_info", "_read_updates",
    "_primary_results", "available_log_tools", "default_log_filters", "filtered_log_rows", "read_network",
    "MAX_LOG", "RECENT", "SKIP_TEXT", "_empty", "fmt_tokens", "full", "model_name", "short", "network_stats",
    "_merge_tool_calls", "_tool_failed", "_tool_text", "is_mcp_tool", "tool_nodes", "NetworkTracker",
]
