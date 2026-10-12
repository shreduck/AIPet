"""Small shared helpers for session records."""

STATE_ORDER = ("needs_input", "error", "done", "working")


def first(d, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None
