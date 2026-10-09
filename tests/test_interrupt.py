"""Stopping a turn (Esc / stop button) sends no hook event; the pet reads Claude Code's transcript marker instead."""
import json
import os
import tempfile
import time
import unittest

import aipet


def line(kind, text):
    return json.dumps({"type": kind, "message": {"role": kind, "content": [{"type": "text", "text": text}]}})


class InterruptTests(unittest.TestCase):
    def check(self, *lines):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return aipet.turn_interrupted({"transcript_path": path, "updated": time.time() - 5})

    def test_stopped_turn_is_detected(self):
        self.assertTrue(self.check(line("user", "fix it"), line("assistant", "On it"),
                                   line("user", "[Request interrupted by user]"),
                                   json.dumps({"type": "attachment"})))  # bookkeeping after it is ignored
        self.assertTrue(self.check(line("user", "[Request interrupted by user for tool use]")))

    def test_prompt_cancelled_before_any_reply_is_detected(self):
        bookkeeping = [json.dumps({"type": t}) for t in ("attachment", "last-prompt", "mode", "permission-mode")]
        self.assertTrue(self.check(line("assistant", "earlier answer"), line("user", "do the thing"), *bookkeeping))
        # the same prompt while the agent is still starting: no end-of-turn line yet
        self.assertFalse(self.check(line("assistant", "earlier answer"), line("user", "do the thing"),
                                    json.dumps({"type": "attachment"})))
        # a finished normal turn ends with the reply, then last-prompt: not a stop
        self.assertFalse(self.check(line("user", "do the thing"), line("assistant", "done"), *bookkeeping))

    def test_ongoing_or_resumed_turns_are_not(self):
        self.assertFalse(self.check(line("user", "fix it"), line("assistant", "On it")))
        self.assertFalse(self.check(line("user", "[Request interrupted by user]"), line("user", "try again")))
        self.assertFalse(aipet.turn_interrupted({"transcript_path": "", "updated": 0}))

    def test_wsl_transcripts_are_read_through_wsl_localhost(self):
        rec = {"env": "wsl", "distro": "Ubuntu", "transcript_path": "/home/me/.claude/projects/x/s.jsonl"}
        if os.name == "nt":
            self.assertEqual(aipet.transcript_file(rec), r"\\wsl.localhost\Ubuntu\home\me\.claude\projects\x\s.jsonl")
        else:
            self.assertEqual(aipet.transcript_file(rec), rec["transcript_path"])


if __name__ == "__main__":
    unittest.main()
