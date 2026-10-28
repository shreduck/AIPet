"""Shared list matching and precedence for the hook and desktop prompt UI."""
import re


def patterns(lines):
    return [line for line in (str(value).strip() for value in (lines or [])) if line and not line.startswith('#')]


def whitelist_matches(white, subjects):
    for pattern in patterns(white):
        try:
            if any(re.fullmatch(pattern, str(subject)) for subject in subjects):
                return True
        except re.error:
            continue
    return False


def blacklist_blocks(black, subjects):
    for pattern in patterns(['.*'] if black is None else black):
        try:
            if any(re.search(pattern, str(subject)) for subject in subjects):
                return True
        except re.error:
            return True
    return False


def lists_block(rules, subjects):
    rules = rules if isinstance(rules, dict) else {}
    if rules.get('precedence') == 'whitelist' and whitelist_matches(rules.get('whitelist'), subjects):
        return False
    return blacklist_blocks(rules.get('blacklist'), subjects)
