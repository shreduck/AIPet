import os
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet


class ChimeTests(unittest.TestCase):
    def test_mac_plays_custom_by_default_and_honors_system_choice(self):
        app = object.__new__(aipet.PetApp)
        app.muted = MagicMock(get=MagicMock(return_value=False))
        app.root = MagicMock()
        with patch.object(aipet, 'IS_MAC', True), patch.object(aipet, 'winsound', None), \
                patch.object(aipet, 'chime_path', side_effect=lambda k: k + '.wav'), \
                patch.object(aipet.subprocess, 'Popen') as play:
            for config in ({}, {"sound_style": "chimes"}, {"sound_style": "system"}):
                app.cfg = config
                for urgent, kind, chime, system in ((False, None, 'ting', 'Glass'),
                                                   (True, None, 'knock', 'Funk'),
                                                   (True, 'error', 'thud', 'Basso')):
                    app.beep(urgent, kind=kind)
                    expected = (f'/System/Library/Sounds/{system}.aiff' if config.get('sound_style') == 'system'
                                else chime + '.wav')
                    play.assert_called_with(['afplay', expected])
            play.reset_mock()
            app.muted.get.return_value = True
            app.beep(True)
            play.assert_not_called()
            app.beep(True, force=True)
            play.assert_called_once()

    def test_mac_falls_back_to_system_when_chime_is_unavailable(self):
        app = object.__new__(aipet.PetApp)
        app.cfg, app.root = {}, MagicMock()
        app.muted = MagicMock(get=MagicMock(return_value=False))
        with patch.object(aipet, 'IS_MAC', True), patch.object(aipet, 'winsound', None), \
                patch.object(aipet, 'chime_path', side_effect=OSError('read-only sound directory')), \
                patch.object(aipet.subprocess, 'Popen') as play:
            app.beep(False)
            play.assert_called_once_with(['afplay', '/System/Library/Sounds/Glass.aiff'])

    def test_sound_choice_is_saved_and_previewed(self):
        app = object.__new__(aipet.PetApp)
        app.cfg, app.beep = {}, MagicMock()
        self.assertEqual(aipet.DEFAULT_CONFIG['sound_style'], 'chimes')
        with patch.object(aipet, 'save_setting') as save:
            for style in ('system', 'chimes'):
                app.set_sound_style(style)
                self.assertEqual(app.cfg['sound_style'], style)
                save.assert_called_with('sound_style', style)
                app.beep.assert_called_with(False, force=True)

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
