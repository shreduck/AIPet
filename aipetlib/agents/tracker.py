"""The always-on background tracker: keeps agent and tool history current while no view is open, and hands the
agent network windows what it read (latest), so they never parse transcripts on the UI thread."""
import threading

from ..runtime.diagnostics import log_error
from . import context
from .network import read_network

_LOCK = threading.Lock()
_NETWORKS = {}  # session key -> its latest network (tool nodes included), as the tracker last read it
_WAKE = threading.Event()  # set to poll now instead of after the interval (a window just opened)
_RUNNING = [0]  # trackers running: without one, windows read networks themselves


def latest(key):
    """The session's network as last read in the background (tool nodes included), or None if not read yet."""
    with _LOCK:
        return _NETWORKS.get(key)


def wake():
    """Read again now (an agent network window wants a session the tracker hasn't read yet)."""
    _WAKE.set()


def running():
    return _RUNNING[0] > 0


class NetworkTracker(threading.Thread):
    """Keep agent and tool history warm for every live Claude/Codex session without any Tk window."""
    def __init__(self, get_items, interval=0.7):
        super().__init__(name="AIPet agent tracker", daemon=True)
        self.get_items, self.interval = get_items, interval
        self._stop_event = threading.Event()

    def poll(self):
        readings, seen = {}, set()
        for item in list(self.get_items()):
            if item.get("source") != "CC" or not item.get("session_id"):
                continue
            try:
                net = read_network(item, include_tools=True, bootstrap=True)
                with _LOCK:  # each session as soon as it is read: a window shows it without waiting for the rest
                    _NETWORKS[item["key"]] = net
                seen.add(item["key"])
                reading = context.reading(item, net["agents"][0] if net.get("agents") else None)
                if reading:
                    readings[item["key"]] = reading
            except Exception as exc:
                log_error(f"agent tracker: {exc!r}")
        with _LOCK:
            for key in [k for k in _NETWORKS if k not in seen]:
                del _NETWORKS[key]  # sessions that are gone
        context.publish(readings)

    def run(self):
        _RUNNING[0] += 1
        try:
            while not self._stop_event.is_set():
                try:
                    self.poll()
                except Exception as exc:
                    log_error(f"agent tracker: {exc!r}")
                _WAKE.wait(self.interval)
                _WAKE.clear()
        finally:
            _RUNNING[0] -= 1

    def stop(self):
        self._stop_event.set()
        _WAKE.set()
