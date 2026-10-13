"""Plain-text helpers shared by session summaries and the agent network (no Tk)."""


def phrase(text, limit=140):
    """The last sentence-ish bit of a message, on one line."""
    lines = [ln.strip(" #>*-`") for ln in str(text).strip().splitlines() if ln.strip(" #>*-`")]
    last = " ".join((lines[-1] if lines else "").split())
    return last if len(last) <= limit else last[:limit - 1].rstrip() + "…"
