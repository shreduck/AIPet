import os
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet


class ChimeTests(unittest.TestCase):
    def test_chimes_are_written_once_as_clean_wav_files(self):
        with tempfile.TemporaryDirectory() as home, patch.object(aipet, 'HOME_DIR', home):
            for kind, seconds in (("ting", 0.26 + 0.22 + 0.22 * 6), ("knock", 0.26 + 0.24 + 0.17 * 6)):
                path = aipet.chime_path(kind)
                with wave.open(path) as f:
                    self.assertEqual((f.getnchannels(), f.getsampwidth(), f.getframerate()), (1, 2, 44100))
                    self.assertAlmostEqual(f.getnframes() / 44100, seconds, places=2)
                stamp = os.path.getmtime(path)
                self.assertEqual(aipet.chime_path(kind), path)
                self.assertEqual(os.path.getmtime(path), stamp)  # not rewritten
            samples = aipet.chime_samples("knock")
            self.assertLessEqual(max(abs(v) for v in samples), 0.5 + 1e-9)
            self.assertLess(abs(samples[0]), 0.01)
            self.assertLess(abs(samples[-1]), 0.01)

    def test_windows_plays_chimes_unless_system_sounds_are_chosen(self):
        app = object.__new__(aipet.PetApp)
        app.muted = MagicMock(get=MagicMock(return_value=False))
        ws = SimpleNamespace(PlaySound=MagicMock(), MessageBeep=MagicMock(), SND_FILENAME=1, SND_ASYNC=2,
                             SND_NODEFAULT=4, MB_ICONEXCLAMATION=48, MB_ICONASTERISK=64, MB_ICONHAND=16)
        with patch.object(aipet, 'winsound', ws), patch.object(aipet, 'chime_path', side_effect=lambda k: k + '.wav'):
            app.cfg = {"sound_style": "chimes"}
            app.beep(True)
            ws.PlaySound.assert_called_once_with('knock.wav', 7)
            app.beep(True, kind="error")
            ws.PlaySound.assert_called_with('thud.wav', 7)  # errors have their own sound
            ws.PlaySound.reset_mock()
            app.cfg = {"sound_style": "system"}
            app.beep(False)
            ws.MessageBeep.assert_called_once_with(64)
            ws.MB_ICONHAND = 16
            app.beep(True, kind="error")
            ws.MessageBeep.assert_called_with(16)
            app.muted.get.return_value = True
            app.beep(True)
            ws.PlaySound.assert_not_called()  # muted


if __name__ == "__main__":
    unittest.main()
