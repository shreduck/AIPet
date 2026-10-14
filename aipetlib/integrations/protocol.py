"""Provider hook events, timeouts and recognition."""
import re
from .targets import is_codex
import sys

IS_MAC = sys.platform == "darwin"


HOOK_EVENTS = [
    ("SessionStart", None),
    ("UserPromptSubmit", None),
    ("PostToolUse", "*"),
    ("Notification", None),
    ("Stop", None),
    ("SessionEnd", None),
    ("SubagentStart", None),  # lets the pet see a subagent the moment it starts (even one running a single long command)
    ("SubagentStop", None),  # ... and drop it when it finishes
    ("PermissionRequest", None),  # gives the pet the command/description behind a permission prompt (observe only)
    ("PostToolUseFailure", "*"),  # an approved tool that failed: its prompt is over too (there is no PostToolUse)
    ("PermissionDenied", "*"),  # a prompt denied without the user (auto mode): no longer waiting
    ("StopFailure", None),  # the turn ended on an API error: usage limit, overloaded, billing... shown on the pet
]


HOOK_TIMEOUTS = {"PermissionRequest": 86400}  # seconds; the answer timeout can be "no limit" (the hook


CODEX_HOOK_EVENTS = [
    ("SessionStart", None),
    ("UserPromptSubmit", None),
    ("PostToolUse", None),
    ("PermissionRequest", None),
    ("Stop", None),
    ("Interrupt", None),
    ("SessionEnd", None),
    ("SubagentStart", None),
    ("SubagentStop", None),
]


CODEX_HOOK_TIMEOUTS = {e: 10 for e, _ in CODEX_HOOK_EVENTS}


CODEX_HOOK_TIMEOUTS.update({"SessionEnd": 3, "Interrupt": 3})  # Codex caps these two at 3 s


CODEX_HOOK_TIMEOUTS["PermissionRequest"] = 86400  # long enough to answer from the pet when that is switched on


CODEX_FLAG = "--codex"  # appended to the hook command so the hook knows the agent


MARKER = re.compile(r"(aipet|claude[-_]pet)[-_]hook", re.I)  # LEGACY: claude-pet = the old name (see legacy.py)


STALE = "installed · update needed"


def events_for(key):
    return (CODEX_HOOK_EVENTS, CODEX_HOOK_TIMEOUTS) if is_codex(key) else (HOOK_EVENTS, HOOK_TIMEOUTS)
