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
    if hook_config.SSH_HOOK:
        return "codex:ssh" if hook_config.AGENT == "codex" else "ssh"
    if hook_hosts.is_cowork() and hook_config.AGENT != "codex":
        return "cowork"
    if wsl:
        base = "wsl:" + os.environ.get("WSL_DISTRO_NAME", "")
    else:
        base = "windows" if os.name == "nt" else ("mac" if sys.platform == "darwin" else sys.platform)
    return ("codex:" + base) if hook_config.AGENT == "codex" else base


def _approval_config(base):
    if not hook_waiting.pet_alive(base):
        return {}
    ssh = hook_config.SSH_HOOK
    try:
        path = os.path.join(base, hook_config.SSH_APPROVAL_FILE) if ssh else approval_path(base)
        with open(path, encoding="utf-8") as f:
            if ssh and not 0 <= time.time() - os.fstat(f.fileno()).st_mtime < hook_config.SSH_APPROVAL_LEASE:
                return {}
            config = json.load(f)
            return config if isinstance(config, dict) else {}
    except (OSError, ValueError):
        return {}


def _rules_for(config, key, session_id):
    targets = config.get("targets")
    if isinstance(targets, list):
        return {"enabled": True, "allow_all": True} if key in targets else None
    if isinstance(targets, dict):
        override = targets.get(f"{key}:{session_id}") if hook_config.SSH_HOOK else None
        rules = override if isinstance(override, dict) else targets.get(key)
        return rules if isinstance(rules, dict) else None
    return None


def permission_decision(base, session_id, *, key, data):
    """Evaluate timed and standing rules from the same snapshot."""
    config = _approval_config(base)
    timed = _timed_decision(config, session_id, time.time(), data, key)
    if timed:
        return timed
    rules = _rules_for(config, key, session_id)
    return auto_decision(rules, data) if rules and rules.get("enabled") else None


def auto_approve_rules(base, key, session_id=None):
    """The auto-approve rules of this hook config, or None when it is off - the default: no file, an unreadable file,
    a missing or disabled config all mean off. Only honoured while the pet is running, so closing the pet stops it.
    A plain list of keys (the first version of the setting) means "allow all" for those keys."""
    rules = _rules_for(_approval_config(base), key, session_id)
    return rules if rules and rules.get("enabled") else None


def timed_decision(base, session_id, now=None, *, data=None, key=None):
    """'timed' to approve, 'blocked' to ask without falling back to global rules, or None without a live grant.
    Blacklist mode reads this target's current blacklist regardless of its enabled/allow_all flags.
    Like the other rules, grants only apply while the pet runs."""
    return _timed_decision(_approval_config(base), session_id, now or time.time(), data, key)


def _timed_decision(config, session_id, now, data, key):
    grant_key = f"{key}:{session_id}" if hook_config.SSH_HOOK else str(session_id)
    grants = config.get("timed")
    grant = grants.get(grant_key) if isinstance(grants, dict) else None
    until = grant.get("until") if isinstance(grant, dict) else None
    if not isinstance(until, (int, float)) or not now < until < float("inf"):
        return None
    if grant.get("respect_blacklist"):
        rules = _rules_for(config, key, session_id)
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
