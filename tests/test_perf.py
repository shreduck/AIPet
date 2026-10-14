"""UI-thread timings to ~/.aipet/perf.log while debugging: slow callbacks and stalls, nothing when off."""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import support
from aipetlib.runtime import perf


class PerfLogTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.flag = os.path.join(self.home.name, "debug-events")
        p = support.paths(HOME_DIR=self.home.name, DEBUG_FLAG=self.flag)
        p.start()
        self.addCleanup(p.stop)
        perf._FLAG.update(on=False, checked=float("inf"))
        self.log = os.path.join(self.home.name, "perf.log")

    def lines(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                return f.read().splitlines()
        except OSError:
            return []

    def debugging(self, on):
        if on:
            open(self.flag, "w").close()
        elif os.path.exists(self.flag):
            os.remove(self.flag)
        perf._FLAG["checked"] = -1e9
        self.assertEqual(perf.enabled(now=0.0), on)  # looked at the switch now
        perf._FLAG["checked"] = float("inf")  # and not again while the test steps the clock

    def test_slow_blocks_are_logged_only_while_debugging(self):
        clock = iter([0.0, 0.050, 1.0, 1.050, 2.0, 2.005])
        with patch.object(perf.time, "perf_counter", side_effect=lambda: next(clock)):
            with perf.timed("agent window: graph"):  # 50 ms, switch off
                pass
            self.debugging(True)
            with perf.timed("agent window: graph"):  # 50 ms, switch on
                pass
            with perf.timed("pet: frame"):  # 5 ms: not slow
                pass
        self.assertEqual(len(self.lines()), 1)
        self.assertTrue(self.lines()[0].endswith("50.0 ms  agent window: graph"))

    def test_as_a_decorator(self):
        self.debugging(True)

        @perf.timed("pet: poll", slow_ms=0)
        def poll():
            return "polled"
        self.assertEqual(poll(), "polled")
        self.assertIn("pet: poll", self.lines()[0])

    def test_heartbeat_notes_stalls(self):
        self.debugging(True)
        root, beats = MagicMock(), []
        root.after.side_effect = lambda ms, fn: beats.append(fn)
        clock = iter([0.0, 0.1, 0.35])  # started; first beat on time; the second 150 ms late
        with patch.object(perf.time, "perf_counter", side_effect=lambda: next(clock)):
            perf.watch(root)
            beats[-1]()
            beats[-1]()
        self.assertEqual(len(self.lines()), 1)
        self.assertIn("ui stall", self.lines()[0])
        self.assertIn("150.0 ms", self.lines()[0])

    def test_a_big_log_starts_again(self):
        self.debugging(True)
        with open(self.log, "w") as f:
            f.write("x" * (perf.MAX_BYTES + 1))
        perf.note("pet: frame", 40)
        self.assertTrue(os.path.exists(self.log + ".old"))
        self.assertEqual(len(self.lines()), 1)


if __name__ == "__main__":
    unittest.main()
