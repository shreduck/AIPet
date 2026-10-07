import base64
import ast
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import aipet
import aipet_hook
import aipet_update
import aipet_usage as usage
import hooks_installer as installer


class UsageTests(unittest.TestCase):
    def test_reported_limits_only(self):
        self.assertEqual(usage.normalize({"info": {"total_tokens": 100000}}), [])
        self.assertEqual(usage.normalize({"primary": {"used_percent": None}}), [])
        self.assertEqual(usage.normalize({"primary": {"used_percent": float("nan")}}), [])
        self.assertEqual(usage.normalize({"primary": {"used_percent": True}}), [])

    def test_durations_and_clamping(self):
        rows = usage.normalize({"primary": {"used_percent": 49.8, "window_minutes": 300},
                                "secondary": {"used_percent": 120, "window_minutes": 10080}})
        self.assertEqual([(r["window"], r["percent"]) for r in rows], [("5h", 50), ("7d", 100)])

    def test_expired_and_stale_readings_hidden(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "sessions"
            target.mkdir()
            usage.save(str(target), "test", {"five_hour": {"used_percentage": 50, "resets_at": time.time() - 1},
                                             "seven_day": {"used_percentage": 60, "resets_at": time.time() + 60}})
            self.assertEqual([r["percent"] for r in usage.load(str(target / "test.json"))], [60])
            path = Path(folder) / "usage" / "test.json"
            data = json.loads(path.read_text())
            data["updated"] = time.time() - 90000
            path.write_text(json.dumps(data))
            self.assertEqual(usage.load(str(target / "test.json")), [])

    def test_equal_values_merge_per_ai(self):
        entries = [{"agent": "claude", "entry": name, "usage": [{"window": "5h", "percent": 50}]} for name in ("cli", "vscode", "app")]
        entries.append({"agent": "codex", "usage": [{"window": "5h", "percent": 50}]})
        self.assertEqual(usage.badge_rows(entries), [("claude", "5h", 50), ("codex", "5h", 50)])

    def test_same_harness_uses_latest_quota_not_latest_hook(self):
        entries = [{"agent": "claude", "entry": "cli", "updated": 100,
                    "usage": [{"window": "5h", "percent": 40, "updated": 10}]},
                   {"agent": "claude", "entry": "cli", "updated": 50,
                    "usage": [{"window": "5h", "percent": 50, "updated": 20}]}]
        self.assertEqual(usage.badge_rows(entries), [("claude", "5h", 50)])

    def test_different_harness_values_remain_visible(self):
        entries = [{"agent": "claude", "entry": name, "usage": [{"window": "5h", "percent": pct}]} for name, pct in (("cli", 50), ("app", 60))]
        self.assertEqual(len(usage.badge_rows(entries)), 2)

    def test_all_usage_rows_are_drawn(self):
        pet = aipet.Pet.__new__(aipet.Pet)
        pet.canvas = MagicMock()
        pet.data = {"everyone": [{"agent": "claude", "entry": str(i), "usage": [{"window": "5h", "percent": i}]} for i in range(10)]}
        pet._char_w = lambda font: 3
        self.assertGreater(pet._draw_usage(), 0)
        self.assertEqual(pet.canvas.create_text.call_count, 10)
        self.assertEqual(pet.canvas.create_rectangle.call_count, 10)

    def test_usage_does_not_change_idle_canvas_width(self):
        pet = aipet.Pet.__new__(aipet.Pet)
        pet.canvas = MagicMock()
        pet._char_w = lambda font: 3
        pet.data = {"state": "idle"}
        idle_width = pet._draw_usage()
        pet.data = {"agent": "claude", "usage": [{"window": "5h", "percent": 100}]}
        self.assertEqual(pet._draw_usage(), idle_width)
        self.assertEqual(idle_width, aipet.USAGE_GUTTER)

    def test_usage_badge_sits_beside_lower_body(self):
        pet = aipet.Pet.__new__(aipet.Pet)
        pet.canvas = MagicMock()
        pet._char_w = lambda font: 3
        pet.data = {"agent": "codex", "usage": [{"window": "7d", "percent": 2}]}
        pet._draw_usage()
        rectangle = pet.canvas.create_rectangle.call_args
        self.assertEqual(rectangle.args[1:4:2], (72, 80))
        self.assertIn("usage", rectangle.kwargs["tags"])

    def test_tooltip_includes_merged_sources_and_reset(self):
        entries = [{"agent": "codex", "entry": entry, "where": where,
                    "usage": [{"window": "7d", "percent": 2, "resets_at": 2000000000, "updated": 1900000000}]}
                   for entry, where in (("cli", "Codex CLI"), ("app", "Codex app"))]
        details = usage.badge_details(entries)
        self.assertEqual(len(details), 1)
        text = usage.tooltip(details[0])
        for expected in ("Codex", "7-day account usage limit", "2% used", "98% remaining", "Codex CLI", "Codex app", "Resets:", "Last reported:"):
            self.assertIn(expected, text)

    def test_tooltip_does_not_invent_reset_time(self):
        text = usage.tooltip({"agent": "claude", "window": "5h", "percent": 50,
                              "readings": [{"where": "Claude Code", "updated": 0}]})
        self.assertIn("Resets: not reported", text)

    def test_statusline_preserved_and_restored(self):
        before = {"statusLine": {"type": "command", "command": "echo original", "padding": 3}, "other": "keep"}
        installed = installer.merge_usage(installer.merge_hooks(before, "aipet-hook"), "aipet-hook")
        encoded = installed["statusLine"]["command"].split("--usage-forward=", 1)[1]
        self.assertEqual(base64.urlsafe_b64decode(encoded).decode(), "echo original")
        self.assertEqual(installed["statusLine"]["padding"], 3)
        self.assertEqual(installer.remove_hooks(installed), before)
        self.assertEqual(installer.merge_usage(installed, "aipet-hook"), installed)

    def test_statusline_removal_when_previously_absent(self):
        installed = installer.merge_usage(installer.merge_hooks({}, "aipet-hook"), "aipet-hook")
        self.assertEqual(installer.remove_hooks(installed), {})

    def test_user_statusline_change_is_kept_on_uninstall(self):
        installed = installer.merge_usage(installer.merge_hooks({}, "aipet-hook"), "aipet-hook")
        installed["statusLine"] = {"type": "command", "command": "echo changed"}
        self.assertEqual(installer.remove_hooks(installed)["statusLine"], installed["statusLine"])

    def test_existing_install_is_marked_for_quota_upgrade(self):
        settings = installer.merge_hooks({}, "aipet-hook")
        self.assertEqual(installer.hooks_state(settings, "aipet-hook"), "partial")
        settings = installer.merge_usage(settings, "aipet-hook")
        self.assertEqual(installer.hooks_state(settings, "aipet-hook"), "current")
        codex = installer.merge_hooks({}, "aipet-hook --codex", installer.CODEX_HOOK_EVENTS, installer.CODEX_HOOK_TIMEOUTS)
        self.assertEqual(installer.hooks_state(codex, "aipet-hook --codex", installer.CODEX_HOOK_EVENTS, installer.CODEX_HOOK_TIMEOUTS), "current")

    def test_claude_collector_subprocess(self):
        with tempfile.TemporaryDirectory() as folder:
            data = {"session_id": "test", "rate_limits": {"five_hour": {"used_percentage": 50}}}
            result = subprocess.run([sys.executable, str(Path(aipet_hook.__file__)), "--usage"],
                                    input=json.dumps(data), text=True, capture_output=True,
                                    env={**os.environ, "AIPET_DIR": folder}, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "5h 50% used")
            self.assertEqual(usage.load(str(Path(folder) / "sessions" / "test.json"))[0]["percent"], 50)
            self.assertFalse((Path(folder) / "sessions" / "test.json").exists())  # quota updates do not change state

    def test_codex_rollout_ignores_null_snapshots(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "sessions"
            target.mkdir()
            rollout = Path(folder) / "rollout.jsonl"
            rollout.write_text("\n".join(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": limits}})
                                         for limits in ({"primary": {"used_percent": 42, "window_minutes": 300}}, None)), encoding="utf-8")
            aipet_hook.capture_codex_usage(str(target), "test", {"transcript_path": str(rollout)}, {})
            self.assertEqual(usage.load(str(target / "test.json"))[0]["percent"], 42)

    def test_tls_supplements_default_trust(self):
        context = MagicMock()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"tag_name":"v1.0.0"}'
        certifi = MagicMock()
        certifi.where.return_value = "trusted-ca.pem"
        with patch.dict(sys.modules, {"certifi": certifi}), patch.object(aipet_update.ssl, "create_default_context", return_value=context), patch.object(aipet_update.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertEqual(aipet_update.latest_release()["tag"], "v1.0.0")
            context.load_verify_locations.assert_called_once_with(cafile="trusted-ca.pem")
            self.assertIs(urlopen.call_args.kwargs["context"], context)

    def test_mac_mutually_exclusive_behaviors_are_cleared(self):
        # Execute the native flag policy without loading macOS's ObjC runtime.
        tree = ast.parse((Path(aipet.__file__).parent / "mac_statusbar.py").read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if
                 (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("ALL_SPACES", "ALL_SPACES_CONFLICTS") for t in node.targets)) or
                 (isinstance(node, ast.FunctionDef) and node.name == "set_all_spaces")]
        sent = []
        def send(window, selector, *args, **kwargs):
            if selector == "collectionBehavior":
                return (1 << 1) | (1 << 2) | (1 << 3) | (1 << 5) | (1 << 7) | (1 << 9) | (1 << 11)
            sent.append(args[0])
        scope = {"send": send, "window_titled": lambda title: 1, "c_ulong": ctypes.c_ulong}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "mac-behavior", "exec"), scope)
        self.assertTrue(scope["set_all_spaces"]("AIPet", True))
        self.assertEqual(sent[0] & scope["ALL_SPACES_CONFLICTS"], 0)
        self.assertEqual(sent[0] & scope["ALL_SPACES"], scope["ALL_SPACES"])
        self.assertTrue(sent[0] & (1 << 11))  # unrelated flags survive


if __name__ == "__main__":
    unittest.main()
