"""Work kept off the UI thread: the session reader feeding the pet's poll, and the tracker running light while no
agent network window is open."""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import aipet
import aipetlib.agents.tracker as agent_tracker
from aipetlib.app.session_reader import SessionReader


class SessionReaderTests(unittest.TestCase):
    def test_keeps_the_newest_read_and_reads_again_when_woken(self):
        reads = []
        done = threading.Event()

        def read():
            reads.append(len(reads))
            if len(reads) >= 2:
                done.set()
            return [{"key": f"cc:{len(reads)}"}]
        reader = SessionReader(read, interval=60)
        self.assertIsNone(reader.latest())  # nothing read yet
        reader.start()
        self.addCleanup(reader.stop)
        for _ in range(100):
            if reader.latest():
                break
            time.sleep(0.01)
        self.assertEqual(reader.latest(), [{"key": "cc:1"}])
        reader.wake()  # not 60 s later: now
        self.assertTrue(done.wait(2))
        reader.stop()
        reader.join(2)
        self.assertFalse(reader.is_alive())

    def test_a_failing_read_keeps_the_last_list(self):
        reader = None

        def read():
            if reader.latest() is None:
                return [{"key": "a"}]
            reader._stop_event.set()  # the second read fails, then the loop ends
            raise RuntimeError("folder gone")
        reader = SessionReader(read, interval=0)
        with patch("aipetlib.app.session_reader.log_error") as log:
            reader.run()  # the loop itself, on this thread
        self.assertEqual(reader.latest(), [{"key": "a"}])
        log.assert_called_once()


class PollUsesTheReaderTests(unittest.TestCase):
    def app(self, items):
        app = object.__new__(aipet.PetApp)
        app.cfg = dict(aipet.DEFAULT_CONFIG)
        app.reader = MagicMock(latest=MagicMock(return_value=items))
        app.read_items = MagicMock(side_effect=AssertionError("read on the UI thread"))
        app._slots, app._slot_n = {}, 0
        app.answers = aipet.Answers(app)
        return app

    def test_the_poll_takes_the_readers_list_and_never_changes_it(self):
        shared = [{"key": "cc:1", "source": "CC", "title": "esign", "session_id": "1", "state": "working", "changed": 0},
                  {"key": "cc:2", "source": "CC", "title": "esign", "session_id": "2", "state": "working", "changed": 0}]
        app = self.app(shared)
        first = app.collect()
        second = app.collect()  # the same list again (the reader hasn't read since)
        self.assertEqual(sorted(i["title"] for i in second), sorted(i["title"] for i in first))
        self.assertTrue(all(i["title"].count("·") == 1 for i in second))  # disambiguated once, not twice
        self.assertEqual([i["title"] for i in shared], ["esign", "esign"])  # the reader's list untouched

    def test_without_a_reader_list_yet_the_poll_reads_once(self):
        app = self.app(None)
        app.read_items = MagicMock(return_value=[])
        self.assertEqual(app.collect(), [])
        app.read_items.assert_called_once()

    def test_an_answer_makes_the_reader_look_now(self):
        app = self.app([])
        app.answers.read_again()
        app.reader.wake.assert_called_once()


class TrackerModeTests(unittest.TestCase):
    def setUp(self):
        agent_tracker._ACTIVE[0] = False
        agent_tracker._WAKE.clear()
        self.addCleanup(agent_tracker._ACTIVE.__setitem__, 0, False)

    def poll_bootstrap(self):
        seen = []
        with patch.object(agent_tracker, "read_network", side_effect=lambda item, **kw: seen.append(kw["bootstrap"])
                          or {"agents": []}):
            agent_tracker.NetworkTracker(lambda: [{"source": "CC", "session_id": "s", "key": "cc:s"}]).poll()
        return seen

    def test_light_without_a_window_full_with_one(self):
        self.assertEqual(self.poll_bootstrap(), [False])  # only the end of each transcript
        agent_tracker.set_active(True)
        self.assertTrue(agent_tracker._WAKE.is_set())  # catches up at once for the window
        self.assertEqual(self.poll_bootstrap(), [True])

    def test_the_tracker_follows_the_open_windows(self):
        from aipetlib.app.agent_windows import AgentWindows
        app = MagicMock()
        app._tk_thread = threading.current_thread()
        windows = AgentWindows(app)
        windows.wins = {"*": MagicMock(alive=MagicMock(return_value=True))}
        windows.is_open()
        self.assertTrue(agent_tracker._ACTIVE[0])
        windows.wins = {}
        windows.is_open()
        self.assertFalse(agent_tracker._ACTIVE[0])


if __name__ == "__main__":
    unittest.main()
