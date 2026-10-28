"""Timed grants and auto-approval rule matching."""
import json
import os
import sys
import time
from . import config as hook_config
from . import hosts as hook_hosts
from . import requests as hook_requests
from . import waiting as hook_waiting
from .storage import approval_path
from .rules import blacklist_blocks, whitelist_matches, lists_block

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


def timed_decision(base, session_id, now=None, *, data=None, key=None):
    """'timed' to approve, 'blocked' to ask without falling back to global rules, or None without a live grant.
    Blacklist mode reads this target's current blacklist regardless of its enabled/allow_all flags.
    Like the other rules, grants only apply while the pet runs."""
    try:
        with open(approval_path(base), encoding="utf-8") as f:
            config = json.load(f)
            grant = (config.get("timed") or {}).get(str(session_id))
    except Exception:
        return None
    until = grant.get("until") if isinstance(grant, dict) else None
    if not isinstance(until, (int, float)) or (now or time.time()) >= until or not hook_waiting.pet_alive(base):
        return None
    if grant.get("respect_blacklist"):
        targets = config.get("targets")
        rules = targets.get(key) if isinstance(targets, dict) else None
        if data is None or lists_block(rules, hook_requests.request_subjects(data)):
            # A blocked timed grant must not fall through to global Allow all.
            return "blacklist" if grant.get("reject_blacklist") else "blocked"
    return "timed"


def auto_decision(rules, data):
    """'all', 'whitelist', or None (ask), applying the target's configured list precedence.
    Blacklists match anywhere (re.search); an invalid one counts as a match. Whitelists must match a whole subject, so
    'git status' doesn't also approve 'git status; rm -rf ~'."""
    if not rules:
        return None
    if rules.get("allow_all"):
        return "all"
    subjects = hook_requests.request_subjects(data)
    if lists_block(rules, subjects):
        return None
    return "whitelist" if whitelist_matches(rules.get('whitelist'), subjects) else None
