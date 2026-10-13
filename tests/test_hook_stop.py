"""How a waiting PermissionRequest hook ends: every reason is recorded, an agent stopping the hook is noted for the
card and the log, and the pet tells a killed hook (stale .waiting file left behind) from one still waiting."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import aipet_hook as hook
from aipetlib import permissions

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def record(path, rid="r1", **extra):
    req = dict({"id": rid, "tool": "Bash", "t": time.time(), "detail": "ls"}, **extra)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"id": "sess", "state": "needs_input", "requests": [req], "request": req}, f)


class WaitReasonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.path = os.path.join(self.base, "sess.json")
        record(self.path)
        for p in (patch.object(hook, "pet_alive", return_value=True), patch.object(hook, "answers_enabled", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def answer(self, behavior):
        os.makedirs(os.path.join(self.base, "answers"), exist_ok=True)
        with open(os.path.join(self.base, "answers", "r1.json.tmp"), "w") as f:
            json.dump({"behavior": behavior}, f)

    def wait(self, seconds=0.4, click=None):
        if click:  # the click lands just after the hook cleared old answers
            original = hook._await

            def delayed(*args):
                self.answer(click)
                os.replace(os.path.join(self.base, "answers", "r1.json.tmp"), os.path.join(self.base, "answers", "r1.json"))
                return original(*args)
            with patch.object(hook, "_await", side_effect=delayed):
                result = hook.await_answer(self.base, "r1", seconds, self.path, "r1")
        else:
            result = hook.await_answer(self.base, "r1", seconds, self.path, "r1")
        self.assertFalse(os.path.exists(os.path.join(self.base, "answers", "r1.waiting")))  # always cleaned up
        return result, dict(hook.WAIT_END)

    def test_each_way_a_wait_ends_is_named(self):
        self.assertEqual(self.wait(click="allow")[0], "allow")
        self.assertEqual(hook.WAIT_END["reason"], "answered")
        self.assertEqual(self.wait(click="release")[1]["reason"], "released")
        result, end = self.wait(0.3)
        self.assertIsNone(result)
        self.assertEqual(end["reason"], "timed_out")
        self.assertGreaterEqual(end["after"], 0.3)
        with patch.object(hook, "pet_alive", return_value=False):
            self.assertEqual(self.wait()[1]["reason"], "pet_closed")
        with patch.object(hook, "answers_enabled", return_value=False):
            self.assertEqual(self.wait()[1]["reason"], "answers_off")
        record(self.path, rid="someone-else")
        self.assertEqual(self.wait(3)[1]["reason"], "answered_elsewhere")


@unittest.skipIf(os.name == "nt", "Windows has no catchable stop signal for a child process")
class StoppedHookTests(unittest.TestCase):
    SCRIPT = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
import aipet_hook as hook
hook.pet_alive = lambda base: True
hook.answer_flow(sys.argv[2], sys.argv[3], "", "r1")
print("finished", flush=True)
"""

    def test_a_stopped_hook_records_it_cleans_up_and_answers_nothing(self):
        with tempfile.TemporaryDirectory() as base:
            path = os.path.join(base, "sess.json")
            record(path)
            for name, text in (("debug-events", ""), ("answer-wait", "30")):
                with open(os.path.join(base, name), "w") as f:
                    f.write(text)
            proc = subprocess.Popen([sys.executable, "-c", self.SCRIPT, ROOT, base, path], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            waiting = os.path.join(base, "answers", "r1.waiting")
            deadline = time.time() + 10
            while not os.path.exists(waiting) and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(os.path.exists(waiting))
            time.sleep(0.3)
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(out.strip(), "finished")  # no decision was printed
            self.assertFalse(os.path.exists(waiting))
            with open(path) as f:
                req = json.load(f)["requests"][0]
            self.assertFalse(req["answerable"])
            self.assertEqual(req["hook_stopped"]["signal"], "SIGTERM")
            self.assertGreater(req["hook_stopped"]["after"], 0)
            with open(os.path.join(base, "events.log")) as f:
                row = json.loads(f.read().splitlines()[-1])
            self.assertEqual((row["ev"], row["reason"], row["signal"], row["sid"]), ("WaitEnded", "stopped", "SIGTERM", "sess"))


class HookStopDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.answers = os.path.join(self.tmp.name, "answers")
        os.makedirs(self.answers)

    def item(self, **req):
        return {"request": dict({"id": "r1", "answerable": True, "t": time.time() - 60}, **req)}

    def touch(self, age):
        path = os.path.join(self.answers, "r1.waiting")
        with open(path, "w") as f:
            f.write("1")
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))

    def test_waiting_finished_and_killed_hooks(self):
        self.assertIsNone(permissions.hook_stop(self.item(), self.answers))  # no file: ended normally
        self.touch(1)
        self.assertIsNone(permissions.hook_stop(self.item(), self.answers))  # still waiting
        self.touch(50)  # left behind ten seconds after the prompt appeared: killed then
        stop = permissions.hook_stop(self.item(), self.answers)
        self.assertEqual(stop["signal"], "")
        self.assertAlmostEqual(stop["after"], 10, delta=1)
        self.assertIsNone(permissions.hook_stop(self.item(answerable=False), self.answers))

    def test_a_recorded_stop_wins(self):
        item = self.item(answerable=False, hook_stopped={"after": 14.2, "signal": "SIGTERM"})
        stop = permissions.hook_stop(item, self.answers)
        self.assertEqual(stop, {"after": 14.2, "signal": "SIGTERM"})


if __name__ == "__main__":
    unittest.main()
