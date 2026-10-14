"""UI-thread timings for finding stutters: while "Log hook events (debug)" is on, ~/.aipet/perf.log gets every
UI-thread callback that took SLOW_MS or more, and every stall (the UI thread answering a 100 ms heartbeat STALL_MS or
more late, whatever kept it busy). Off, timing a callback costs two clock reads. No Tk here: watch() is given the root.
"""
import contextlib
import os
import time

from . import paths

SLOW_MS = 30  # a callback this slow is logged (a frame is 50 ms; dragging needs the thread free)
STALL_MS = 80  # the heartbeat this late is logged as a stall
BEAT_MS = 100
MAX_BYTES = 1_000_000  # then perf.log becomes perf.log.old and starts again
_FLAG = {"on": False, "checked": 0.0}


def enabled(now=None):
    """Whether the debug switch is on (looked at every 5 s at most, not on every frame)."""
    now = time.monotonic() if now is None else now
    if now - _FLAG["checked"] > 5:
        _FLAG["checked"] = now
        _FLAG["on"] = os.path.exists(paths.DEBUG_FLAG)
    return _FLAG["on"]


def note(name, ms):
    """Log one slow callback or stall (only while debugging)."""
    if not enabled():
        return
    path = os.path.join(paths.HOME_DIR, "perf.log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            os.replace(path, path + ".old")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} {ms:7.1f} ms  {name}\n")
    except OSError:
        pass


class timed(contextlib.ContextDecorator):
    """`with perf.timed("agent window: graph"):` or `@perf.timed("pet: frame")`: logs the block if it is slow."""

    def __init__(self, name, slow_ms=SLOW_MS):
        self.name, self.slow_ms, self._starts = name, slow_ms, []

    def __enter__(self):
        self._starts.append(time.perf_counter())
        return self

    def __exit__(self, *exc):
        ms = (time.perf_counter() - self._starts.pop()) * 1000
        if ms >= self.slow_ms:
            note(self.name, ms)
        return False


def watch(root):
    """Start the heartbeat on the Tk thread: a beat answered late means something held the thread that long."""
    state = {"due": time.perf_counter() + BEAT_MS / 1000}

    def beat():
        now = time.perf_counter()
        late = (now - state["due"]) * 1000
        if late >= STALL_MS:
            note("ui stall (the UI thread was busy)", late)
        state["due"] = now + BEAT_MS / 1000
        try:
            root.after(BEAT_MS, beat)
        except Exception:  # the window is gone
            pass
    root.after(BEAT_MS, beat)
