"""HistoryStore: one owner for the transcripts read so far, each read incremental and safe from any thread."""
import os
import tempfile
import threading
import unittest

from aipetlib.agents.claude import parse_claude
from aipetlib.agents.history import STORE, HistoryStore
from test_agents import claude_user, write_lines


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def transcript(self, name, n):
        path = os.path.join(self.dir.name, name)
        write_lines(path, [claude_user(100 + k, f"q{k}") for k in range(n)])
        return path

    def test_separate_stores_do_not_share_their_files(self):
        path = self.transcript("a.jsonl", 2)
        one, two = HistoryStore(), HistoryStore()
        self.assertEqual(len(one.read(path, 10000, parse_claude)["queries"]), 2)
        self.assertEqual(len(two._tails), 0)
        one.clear()
        self.assertEqual(len(one._tails), 0)

    def test_oldest_file_goes_first_when_full(self):
        store = HistoryStore(capacity=2)
        paths = [self.transcript(f"{k}.jsonl", 1) for k in range(3)]
        for p in paths:
            store.read(p, 10000, parse_claude)
        self.assertEqual(len(store._tails), 2)
        self.assertNotIn(os.path.normcase(os.path.abspath(paths[0])) if os.name == "nt" else paths[0], store._tails)

    def test_reads_from_two_threads_keep_one_consistent_entry(self):
        store = HistoryStore()
        path = self.transcript("t.jsonl", 50)
        results = []
        threads = [threading.Thread(target=lambda: results.append(store.read(path, 10000, parse_claude)))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(len(store._tails), 1)
        self.assertTrue(all(len(r["queries"]) == 50 for r in results))

    def test_the_app_uses_one_store(self):
        from aipetlib.agents import network
        self.assertIs(network.history.STORE, STORE)


if __name__ == "__main__":
    unittest.main()
