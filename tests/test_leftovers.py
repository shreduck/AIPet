import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import aipet


class LeftoverTests(unittest.TestCase):
    def test_sweeps_only_old_leftovers(self):
        with tempfile.TemporaryDirectory() as home:
            now = time.time()

            def make(rel, age):
                p = Path(home, rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x")
                os.utime(p, (now - age, now - age))
                return p
            gone = [make("sessions/a.json.123.tmp", 7200), make("sessions/a.json.lock", 7200),
                    make("answers/x.waiting", 7200), make("answers/x.json", 7200), make("config.json.tmp", 7200),
                    make("codex-raw.log", 8 * 86400), make("events.log", 8 * 86400),
                    make("diagnostics.txt", 31 * 86400)]
            kept = [make("sessions/a.json", 7200),  # session files have their own clean-up (stale hours)
                    make("sessions/b.json.9.tmp", 60), make("answers/y.waiting", 2),  # a hook still waiting
                    make("events.log.keep", 8 * 86400), make("codex-raw.log.x", 1),
                    make("config.json", 99 * 86400), make("auto-approve.json", 99 * 86400),
                    make("backups/windows/1__before-install.json", 99 * 86400), make("usage/z.json", 2)]
            Path(home, "events.log").touch()  # still being written: kept
            gone.remove(Path(home, "events.log"))
            kept.append(Path(home, "events.log"))
            removed = aipet.sweep_leftovers(home, now=now)
            self.assertEqual(sorted(map(str, gone)), sorted(removed))
            for p in kept:
                self.assertTrue(p.exists(), p)

    def test_drops_expired_timed_auto_approves(self):
        with tempfile.TemporaryDirectory() as home:
            path = os.path.join(home, "auto-approve.json")
            with open(path, "w") as f:
                json.dump({"targets": {"windows": {"enabled": True}},
                           "timed": {"old": {"until": 1}, "live": {"until": time.time() + 600}}}, f)
            from unittest.mock import patch
            with patch.object(aipet, "HOME_DIR", home), patch.object(aipet, "AUTO_APPROVE_PATH", path):
                aipet.sweep_leftovers(home)
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(list(data["timed"]), ["live"])
            self.assertEqual(data["targets"], {"windows": {"enabled": True}})


if __name__ == "__main__":
    unittest.main()
