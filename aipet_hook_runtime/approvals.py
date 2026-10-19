"""Timed grants and auto-approval rule matching."""
import json
import os
import re
import sys
import time
from . import config as hook_config
from . import hosts as hook_hosts
from . import requests as hook_requests
from . import waiting as hook_waiting
from .storage import approval_path

def auto_approve_key(wsl):
    """Which hook config this hook runs under, named like the pet's hook targets: "windows" / "mac" (this machine),
    "wsl:<distro>", "cowork" (the Cowork plugin), and "codex:" + the same for Codex."""
    if hook_hosts.is_cowork() and hook_config.AGENT != "codex":
        return "cowork"
    if wsl:
        base = "wsl:" + os.environ.get("WSL_DISTRO_NAME", "")
    else:
        base = "windows" if os.name == "nt" else ("mac" if sys.platform == "darwin" else sys.platform)
    return ("codex:" + base) if hook_config.AGENT == "codex" else base


def auto_approve_rules(base, key):
    """The auto-approve rules of this hook config, or None when it is off - the default: no file, an unreadable file,
    a missing or disabled config all mean off. Only honoured while the pet is running, so closing the pet stops it.
    A plain list of keys (the first version of the setting) means "allow all" for those keys."""
    try:
        with open(approval_path(base), encoding="utf-8") as f:
            targets = json.load(f).get("targets")
    except Exception:
        return None
    if isinstance(targets, list):
        rules = {"enabled": True, "allow_all": True} if key in targets else None
    elif isinstance(targets, dict) and isinstance(targets.get(key), dict):
        rules = targets[key]
    else:
        rules = None
    if not rules or not rules.get("enabled") or not hook_waiting.pet_alive(base):
        return None
    return rules


def timed_decision(base, session_id, now=None):
    """'timed' while this session has a running "auto approve for N minutes" grant from the pet (auto-approve.json
    "timed": {session id: {"until": epoch seconds}}), else None. Like the other rules, only while the pet runs."""
    try:
        with open(approval_path(base), encoding="utf-8") as f:
            grant = (json.load(f).get("timed") or {}).get(str(session_id))
    except Exception:
        return None
    until = grant.get("until") if isinstance(grant, dict) else None
    if not isinstance(until, (int, float)) or (now or time.time()) >= until or not hook_waiting.pet_alive(base):
        return None
    return "timed"


def _patterns(lines):
    return [ln for ln in (str(x).strip() for x in (lines or [])) if ln and not ln.startswith("#")]


def auto_decision(rules, data):
    """'all' (allow all), 'whitelist' (a whitelist pattern matched and no blacklist pattern did) or None (ask the user).
    Blacklist patterns match anywhere (re.search) and win over the whitelist; an invalid one counts as a match, so a
    typo can only make the pet ask more. Whitelist patterns must match a whole subject (re.fullmatch), so
    'git status' doesn't also approve 'git status; rm -rf ~'."""
    if not rules:
        return None
    if rules.get("allow_all"):
        return "all"
    subjects = hook_requests.request_subjects(data)
    black = rules.get("blacklist")
    for pat in _patterns(hook_config.DEFAULT_BLACKLIST if black is None else black):
        try:
            if any(re.search(pat, sub) for sub in subjects):
                return None
        except re.error:
            return None
    for pat in _patterns(rules.get("whitelist")):
        try:
            if any(re.fullmatch(pat, sub) for sub in subjects):
                return "whitelist"
        except re.error:
            continue
    return None
