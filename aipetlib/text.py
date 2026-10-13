"""Plain-text helpers shared by session summaries, the agent network and the pet's windows (no Tk)."""
import time


def phrase(text, limit=140):
    """The last sentence-ish bit of a message, on one line."""
    lines = [ln.strip(" #>*-`") for ln in str(text).strip().splitlines() if ln.strip(" #>*-`")]
    last = " ".join((lines[-1] if lines else "").split())
    return last if len(last) <= limit else last[:limit - 1].rstrip() + "…"


def ago(ts):
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h {s % 3600 // 60}m ago"


def fmt_wait(sec):
    sec = int(sec)
    if sec <= 0:
        return "no limit"
    m, s = divmod(sec, 60)
    return f"{m} min {s} s" if m and s else (f"{m} min" if m else f"{s} s")


def fmt_left(seconds):
    """Countdown text: 9:41, then 0:05."""
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"
