"""Pure provider configuration merge and status checks."""
import base64
import json
import sys
from . import protocol as hook_protocol

IS_MAC = sys.platform == "darwin"


def parse_settings(text):
    text = (text or "").strip()
    if not text:
        return {}
    data = json.loads(text)  # raises -> caller refuses to overwrite
    if not isinstance(data, dict):
        raise ValueError("settings.json is not a JSON object")
    return data


def _strip_ours(groups):
    out = []
    for g in groups:
        if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
            out.append(g)
            continue
        kept = [h for h in g["hooks"] if not (isinstance(h, dict) and hook_protocol.MARKER.search(str(h.get("command", ""))))]
        if kept or not g["hooks"]:
            out.append({**g, "hooks": kept})
    return out


def merge_hooks(settings, command, events=None, timeouts=None):
    events = hook_protocol.HOOK_EVENTS if events is None else events
    timeouts = hook_protocol.HOOK_TIMEOUTS if timeouts is None else timeouts
    settings = dict(settings or {})
    hooks = dict(settings.get("hooks") or {})
    for event, matcher in events:
        groups = _strip_ours(hooks.get(event) or [])
        entry = {"hooks": [{"type": "command", "command": command,
                            **({"timeout": timeouts[event]} if event in timeouts else {})}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        groups.append(entry)
        hooks[event] = groups
    settings["hooks"] = hooks
    return settings


def remove_hooks(settings, previous=None, installed=None):
    settings = dict(settings or {})
    hooks = dict(settings.get("hooks") or {})
    for event in list(hooks):
        if isinstance(hooks[event], list):
            groups = _strip_ours(hooks[event])
            if groups:
                hooks[event] = groups
            else:
                del hooks[event]
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return remove_usage(settings, previous, installed)


def remove_usage(settings, previous=None, installed=None):
    settings = dict(settings)
    status = settings.get("statusLine")
    if isinstance(status, dict) and hook_protocol.MARKER.search(str(status.get("command", ""))) and "--usage" in status.get("command", ""):
        if installed is not None and status != installed:
            return settings  # preserve a status line the user has edited
        if "aipet_previous" in status:
            previous = status["aipet_previous"]  # one-time migration of v0.3.1 settings
        if previous is None:
            settings.pop("statusLine", None)
        else:
            settings["statusLine"] = previous
    return settings


def merge_usage(settings, command, previous=None):
    settings = dict(settings)
    current = settings.get("statusLine")
    if isinstance(current, dict) and hook_protocol.MARKER.search(str(current.get("command", ""))) and "--usage" in current.get("command", ""):
        if "aipet_previous" in current:
            previous = current["aipet_previous"]
    else:
        previous = current
    # Preserve unfamiliar status line types instead of disabling them.
    if previous and (not isinstance(previous, dict) or previous.get("type") != "command"):
        return settings
    forward = (previous or {}).get("command", "")
    encoded = base64.urlsafe_b64encode(forward.encode("utf-8")).decode("ascii")
    settings["statusLine"] = {**(previous or {}), "type": "command", "command": command + " --usage" +
                              (" --usage-forward=" + encoded if forward else "")}
    return settings


def has_hooks(settings):
    return bool(hook_protocol.MARKER.search(json.dumps((settings or {}).get("hooks", {}))))


def dump(settings):
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def hooks_state(settings, command, events=None, timeouts=None):
    """Compare the AIPet hook entries with the command we would install now.

    "missing"   no AIPet hooks at all
    "outdated"  at least one entry uses a different command (e.g. an old location)
    "partial"   right command, but some of the events are not hooked
    "current"   every event uses exactly `command`
    """
    events = hook_protocol.HOOK_EVENTS if events is None else events
    timeouts = hook_protocol.HOOK_TIMEOUTS if timeouts is None else timeouts
    ok = {command} if isinstance(command, str) else set(command)
    ours, bad_timeout = {}, False
    for event, groups in ((settings or {}).get("hooks") or {}).items():
        if not isinstance(groups, list):
            continue
        for g in groups:
            for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                if isinstance(h, dict) and hook_protocol.MARKER.search(str(h.get("command", ""))):
                    ours.setdefault(event, []).append(h.get("command", ""))
                    if event in timeouts and h.get("timeout") != timeouts[event]:
                        bad_timeout = True
    if not ours:
        return "missing"
    if any(c not in ok for cmds in ours.values() for c in cmds):
        return "outdated"
    if bad_timeout or any(event not in ours for event, _ in events):
        return "partial"
    return "current"
