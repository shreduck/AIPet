"""The optional Spaces probe uses owner modules without needing a native desktop."""
import ctypes
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet_selftest
from aipetlib.runtime import paths
from aipetlib.ui import pet_style


class SpacesProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "probe"
        self.output.mkdir()
        self.pending = []
        root = MagicMock()
        root.after.side_effect = lambda delay, callback: self.pending.append((delay, callback))
        root.winfo_pointerxy.return_value = (100, 200)
        canvas = MagicMock()
        canvas.winfo_rootx.return_value = 30
        canvas.winfo_rooty.return_value = 40
        canvas.winfo_width.return_value = 100
        canvas.winfo_height.return_value = 80
        pet = SimpleNamespace(pointer=SimpleNamespace(over_hit=lambda *args: False), cfg={},
                              _mouse_bridge=SimpleNamespace(received=0, last_target=None),
                              pets={"fixture": SimpleNamespace(canvas=canvas, gutter=5)})
        self.app = SimpleNamespace(root=root, pet=pet, quit=MagicMock())
        self.mac = SimpleNamespace(window_titled=lambda title: 1, send=lambda *args, **kwargs: 1,
                                   cls=lambda name: 1, supports_all_applications=lambda: True,
                                   ALL_APPLICATIONS=1 << 18, ALL_SPACES_CONFLICTS=0,
                                   c_ulong=ctypes.c_ulong, c_long=ctypes.c_long,
                                   c_bool=ctypes.c_bool, c_void_p=ctypes.c_void_p)

    def poll(self, request):
        (self.output / "request.json").write_text(json.dumps(request))
        with patch.dict(sys.modules, mac_statusbar=self.mac):
            aipet_selftest.start_spaces_probe(self.app, str(self.output))
            self.pending.pop(0)[1]()  # initial poll schedules the snapshot
            snapshot = next((callback for delay, callback in self.pending if delay == 1000), None)
            if snapshot:
                snapshot()
        return json.loads((self.output / "response.json").read_text())

    def test_snapshot_uses_pet_style_owner_for_positions(self):
        with patch.object(pet_style, "px", side_effect=lambda value: value * 2):
            result = self.poll({"id": "snapshot"})
        self.assertNotIn("error", result)
        self.assertEqual(result["pet_positions"]["fixture"],
                         [30 + 2 * (pet_style.BADGE_GUTTER + 5 + pet_style.PET_W / 2),
                          40 + 2 * (90 - pet_style.TOP_TRIM)])
        self.assertEqual(result["canvas_sizes"], [[100, 80]])

    def test_single_scenario_only_moves_seeded_sessions_in_owner_directory(self):
        sessions = Path(self.tmp.name) / "sessions"
        sessions.mkdir()
        for name in ("selftest-working.json", "selftest-input.json", "unrelated.json"):
            (sessions / name).write_text("{}")
        disabled = sessions / "selftest-codex.json.disabled"
        disabled.write_text("{}")
        with patch.object(paths, "SESSIONS_DIR", str(sessions)):
            result = self.poll({"id": "single", "scenario": "single"})
        self.assertNotIn("error", result)
        self.assertEqual({p.name for p in sessions.iterdir()},
                         {"selftest-working.json", "selftest-input.json.disabled",
                          "selftest-codex.json.disabled", "unrelated.json"})
        self.assertEqual((sessions / "unrelated.json").read_text(), "{}")


if __name__ == "__main__":
    unittest.main()
