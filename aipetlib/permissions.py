"""Permission requests, approval rules and timed grants.

Services are supplied by the compatibility entry point; this module does not import aipet.
"""
import json
import os
import re
import sys
import time


def unconfirmed_prompts(services, rec, now=None):
    """True when a session shows "needs you" only for permission prompts Claude Code never put on screen: none got
    its permission_prompt Notification in time. Only for Claude Code sessions known to send those notifications."""
    if rec.get("state") != "needs_input" or not rec.get("notifies") or rec.get("agent") == "codex":
        return False
    reqs = [r for r in (rec.get("requests") or []) if isinstance(r, dict) and r.get("source") != "transcript"]
    if any(r.get("kind") == "question" for r in reqs):
        return False  # a question is never hidden: it stays until it is answered
    now = time.time() if now is None else now
    return bool(reqs) and all(not r.get("shown") and now - float(r.get("t") or now) > services.PROMPT_CONFIRM_SECONDS
                              for r in reqs)


def answer_name(services, item):
    req = (item or {}).get("request") or {}
    return "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in str(req.get("id") or (item or {}).get("sid") or "request"))[:120]


def hook_waiting(services, item):
    """True while a PermissionRequest hook is really waiting for this prompt: it touches answers/<id>.waiting every
    second. A hook stopped by its agent (e.g. Codex's own hook timeout) leaves 'answerable' set but stops touching."""
    req = (item or {}).get("request") or {}
    if not req.get("answerable"):
        return False
    try:
        return time.time() - os.path.getmtime(os.path.join(services.answers_dir(), services.answer_name(item) + ".waiting")) < 4
    except OSError:
        return False


def answers_dir(services):
    return os.path.join(services.HOME_DIR, "answers")


def write_answer_wait(services, sec):
    """Tell the hooks (including WSL ones, through the shared folder) how long to wait for a click."""
    try:
        os.makedirs(services.HOME_DIR, exist_ok=True)
        with open(os.path.join(services.HOME_DIR, "answer-wait"), "w", encoding="utf-8") as f:
            f.write(str(int(sec)))
    except OSError:
        pass


def auto_approve_key_for(services, item):
    """The hook config a session runs under, named like the hooks' auto_approve_key()."""
    item = item or {}
    if item.get("app") == "cowork" and item.get("agent") != "codex":
        return "cowork"
    if item.get("env") == "wsl":
        base = "wsl:" + (item.get("distro") or "")
    else:
        base = "windows" if os.name == "nt" else ("mac" if services.IS_MAC else sys.platform)
    return ("codex:" + base) if item.get("agent") == "codex" else base


def _search(services, pattern, text):
    try:
        return bool(re.search(pattern, text))
    except re.error:
        return True


def default_auto_rules(services):
    return {"enabled": False, "allow_all": False, "whitelist": [], "blacklist": list(services.DEFAULT_BLACKLIST)}


def auto_approve_rules(services):
    """{hook config key: rules} as saved by the Auto approve windows. A missing or unreadable file means all off.
    The first version stored a plain list of keys: those meant "allow all"."""
    try:
        with open(services.AUTO_APPROVE_PATH, encoding="utf-8") as f:
            targets = json.load(f).get("targets")
    except Exception:
        return {}
    if isinstance(targets, list):
        return {k: dict(services.default_auto_rules(), enabled=True, allow_all=True) for k in targets if isinstance(k, str)}
    if not isinstance(targets, dict):
        return {}
    out = {}
    for k, r in targets.items():
        if isinstance(r, dict):
            rules = services.default_auto_rules()
            rules.update({x: r[x] for x in ("enabled", "allow_all", "whitelist", "blacklist") if x in r})
            out[k] = rules
    return out


def auto_approve_targets(services):
    """Hook configs with auto-approval switched on (empty = all off, the default)."""
    return sorted(k for k, r in services.auto_approve_rules().items() if r.get("enabled"))


def save_auto_approve(services, key, rules):
    """Save one hook config's rules (key None = switch every config off, keeping their lists). True if saved."""
    targets = services.auto_approve_rules()
    if key is None:
        for r in targets.values():
            r["enabled"] = False
    else:
        targets[key] = rules
    return services._write_auto_approve(targets=targets)


def _write_auto_approve(services, targets=None, timed=None):
    """Write auto-approve.json, keeping whichever section (rules per hook config / timed grants) isn't given."""
    try:
        with open(services.AUTO_APPROVE_PATH, encoding="utf-8") as f:
            current = json.load(f)
        current = current if isinstance(current, dict) else {}
    except Exception:
        current = {}
    data = {"targets": targets if targets is not None else current.get("targets", {}),
            "timed": timed if timed is not None else current.get("timed", {})}
    try:
        os.makedirs(services.HOME_DIR, exist_ok=True)
        tmp = services.AUTO_APPROVE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, services.AUTO_APPROVE_PATH)
        return True
    except OSError as e:
        services.log_error(f"auto-approve: {e!r}")
        return False


def timed_grants(services, now=None):
    """{session id: {"until", "title"}} for the grants still running (the hooks ignore expired ones too)."""
    now = time.time() if now is None else now
    try:
        with open(services.AUTO_APPROVE_PATH, encoding="utf-8") as f:
            timed = json.load(f).get("timed") or {}
    except Exception:
        return {}
    return {sid: g for sid, g in timed.items()
            if isinstance(g, dict) and isinstance(g.get("until"), (int, float)) and g["until"] > now}


def set_timed_grant(services, session_id, minutes, title=""):
    grants = services.timed_grants()
    minutes = max(services.TIMED_MIN, min(services.TIMED_MAX, int(minutes)))
    grants[str(session_id)] = {"until": time.time() + minutes * 60, "title": str(title)[:80]}
    return services._write_auto_approve(timed=grants)


def stop_timed_grant(services, session_id):
    grants = services.timed_grants()
    grants.pop(str(session_id), None)
    return services._write_auto_approve(timed=grants)


def bad_patterns(services, lines):
    """[(line number, pattern, error)] for the regexes that don't compile (blank and # lines are skipped)."""
    import re
    out = []
    for n, ln in enumerate(lines, 1):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        try:
            re.compile(ln)
        except re.error as e:
            out.append((n, ln, str(e)))
    return out


def answers_enabled(services):
    return not os.path.exists(os.path.join(services.HOME_DIR, "no-answers"))
