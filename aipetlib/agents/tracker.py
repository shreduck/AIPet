"""The always-on background tracker: keeps agent and tool history current while no view is open."""
import threading

from ..runtime.diagnostics import log_error
from .network import read_network
class NetworkTracker(threading.Thread):
    """Keep agent and tool history warm for every live Claude/Codex session without any Tk window."""
    def __init__(self, get_items, interval=0.7):
        super().__init__(name="AIPet agent tracker", daemon=True)
        self.get_items, self.interval = get_items, interval
        self._stop_event = threading.Event()

    def poll(self):
        for item in list(self.get_items()):
            if item.get("source") != "CC" or not item.get("session_id"):
                continue
            try:
                read_network(item, include_tools=True, bootstrap=True)
            except Exception as exc:
                log_error(f"agent tracker: {exc!r}")

    def run(self):
        while not self._stop_event.is_set():
            try:
                self.poll()
            except Exception as exc:
                log_error(f"agent tracker: {exc!r}")
            self._stop_event.wait(self.interval)

    def stop(self):
        self._stop_event.set()
