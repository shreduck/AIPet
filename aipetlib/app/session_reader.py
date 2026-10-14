"""Reading the sessions off the UI thread: every session file (WSL folders too), their usage readings, transcript
tails and Codex questions are read here in the background; the pet's poll takes the latest list without waiting.
A slow folder (a WSL distro starting) or a long transcript used to stall the pet for as long as it took."""
import threading

from ..runtime.diagnostics import log_error


class SessionReader(threading.Thread):
    """Calls read() (the pet app's read_items) every interval seconds and keeps the newest result."""

    def __init__(self, read, interval):
        super().__init__(name="AIPet session reader", daemon=True)
        self.read, self.interval = read, interval
        self._lock = threading.Lock()
        self._items = None  # None until the first read finished
        self._wake, self._stop_event = threading.Event(), threading.Event()

    def latest(self):
        """The newest session list (shared: copy what you change), or None before the first read."""
        with self._lock:
            return self._items

    def wake(self):
        """Read again now (an answer was just sent, a prompt was cleared...)."""
        self._wake.set()

    def stop(self):
        self._stop_event.set()
        self._wake.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                items = self.read()
                with self._lock:
                    self._items = items
            except Exception as e:
                log_error(f"session reader: {e!r}")
            self._wake.wait(self.interval)
            self._wake.clear()
