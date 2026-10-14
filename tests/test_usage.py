import base64
import ast
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import time
import unittest
from unittest.mock import MagicMock, patch

import aipet
import aipetlib.app.placement as app_placement
import aipetlib.app.preferences as app_preferences
import aipetlib.config as config_owner
import aipetlib.ui.appearance_controls as appearance_controls
import aipetlib.ui.pet_style as pet_style
import aipetlib.ui.tooltips as tooltips
import aipet_hook
from aipet_hook_runtime import approvals as hook_approvals
from aipet_hook_runtime import capture as hook_capture
from aipet_hook_runtime import cli as hook_cli
from aipet_hook_runtime import config as hook_config
from aipet_hook_runtime import hosts as hook_hosts
from aipet_hook_runtime import io as hook_io
from aipet_hook_runtime import sessions as hook_sessions
from aipet_hook_runtime import waiting as hook_waiting
import aipetlib.update as aipet_update
import aipet_selftest
import aipet_claude_usage as claude_usage
import aipet_usage as usage
from aipetlib.integrations import backups as hook_backups
from aipetlib.integrations import commands as hook_commands
from aipetlib.integrations import deployment as hook_deployment
from aipetlib.integrations import installer as hook_installer
from aipetlib.integrations import locations as hook_locations
from aipetlib.integrations import protocol as hook_protocol
from aipetlib.integrations import settings as hook_settings
from aipetlib.integrations import targets as hook_targets


class UsageTests(unittest.TestCase):
    def setUp(self):
        import support
        self.enterContext(support.installation())

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

    def test_unused_pets_do_not_reserve_a_usage_strip(self):
        pet = aipet.Pet.__new__(aipet.Pet)
        pet.canvas = MagicMock()
        pet._char_w = lambda font: 3
        pet.data = {"state": "idle"}
        idle_width = pet._draw_usage()
        pet.data = {"agent": "claude", "usage": [{"window": "5h", "percent": 100}]}
        self.assertGreater(pet._draw_usage(), idle_width)
        self.assertEqual(idle_width, pet_style.USAGE_GUTTER)
        self.assertEqual(idle_width, 0)

    def test_shared_margin_keeps_rightmost_position_stable(self):
        app = aipet.PetApp.__new__(aipet.PetApp)
        app.appearance = appearance_controls.Appearance(app)
        app.tooltips = tooltips.Tooltips(app)
        app.preferences = app_preferences.PetPreferences(app)
        pet = MagicMock()
        pet.usage_extra = 0
        app.pets, app.order = {"pet": pet}, ["pet"]
        app.frame = MagicMock()
        app.placement = app_placement.Placement(app)
        app.placement.usage_frame_pad = pet_style.px(pet_style.USAGE_EDGE_ROOM)
        pet.usage_extra = 12
        adjustment = app.placement.sync_usage_padding()
        self.assertEqual(adjustment + pet_style.px(12), 0)

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

    def test_tooltip_types_can_be_disabled_independently(self):
        app = aipet.PetApp.__new__(aipet.PetApp)
        app.appearance = appearance_controls.Appearance(app)
        app.tooltips = tooltips.Tooltips(app)
        app.preferences = app_preferences.PetPreferences(app)
        app.cfg = {"session_tooltips": False, "usage_tooltips": True}
        app.tooltips.hide_tip = MagicMock()
        app.tooltips._show_tooltip = MagicMock()
        pet = MagicMock()
        app.tooltips.show_tip(pet)
        app.tooltips._show_tooltip.assert_not_called()
        detail = {"agent": "codex", "window": "7d", "percent": 2, "readings": []}
        app.tooltips.show_usage_tip(pet, detail)
        app.tooltips._show_tooltip.assert_called_once()
        app.tooltips._show_tooltip.reset_mock()
        app.cfg["usage_tooltips"] = False
        app.tooltips.show_usage_tip(pet, detail)
        app.tooltips._show_tooltip.assert_not_called()

    def test_tooltip_settings_are_persisted(self):
        app = aipet.PetApp.__new__(aipet.PetApp)
        app.appearance = appearance_controls.Appearance(app)
        app.tooltips = tooltips.Tooltips(app)
        app.preferences = app_preferences.PetPreferences(app)
        app.cfg, app.pets = {}, {"pet": MagicMock()}
        app.tooltips.hide_tip = MagicMock()
        app.tooltip_vars = {"usage": MagicMock()}
        from aipetlib.runtime import settings
        with patch.object(settings, "save_setting") as save:  # where the tooltip switch saves it
            app.tooltips.set_tooltip("usage", False)
        self.assertFalse(app.cfg["usage_tooltips"])
        save.assert_called_once_with("usage_tooltips", False)
        self.assertIsNone(app.pets["pet"]._usage_hover)

    def test_claude_api_quota_conversion(self):
        result = claude_usage.convert({"five_hour": {"utilization": 51.2, "resets_at": "2030-01-02T03:04:05Z"},
                                       "seven_day": {"utilization": 62, "resets_at": None}})
        self.assertEqual([r["percent"] for r in usage.normalize(result)], [51, 62])
        self.assertGreater(result["five_hour"]["resets_at"], 1800000000)
        self.assertIsNone(result["seven_day"]["resets_at"])

    def test_claude_worker_persists_only_quota_data(self):
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder) / "sessions")
            _, path, lock = claude_usage.paths(target)
            Path(path).parent.mkdir()
            Path(lock).mkdir()
            (Path(folder) / "config.json").write_text('{"claude_oauth_usage": true}')
            with patch.object(claude_usage, "access_token", return_value="test-credential-placeholder"), patch.object(claude_usage, "fetch", return_value={"five_hour": {"used_percentage": 50, "resets_at": time.time() + 500}}):
                claude_usage.worker(target, "test")
            self.assertFalse(Path(lock).exists())
            self.assertNotIn("test-credential-placeholder", Path(path).read_text())
            reading = usage.load(str(Path(target) / "test.json"))[0]
            self.assertEqual(reading["percent"], 50)
            self.assertIn("Claude Code account", reading["provider"])

    def test_claude_cached_reading_keeps_original_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder) / "sessions")
            _, path, _ = claude_usage.paths(target)
            Path(path).parent.mkdir()
            (Path(folder) / "config.json").write_text('{"claude_oauth_usage": true}')
            stamp = time.time() - 40
            Path(path).write_text(json.dumps({"limits": {"five_hour": {"used_percentage": 40}}, "updated": stamp,
                                             "retry_at": time.time() + 200}))
            with patch.object(claude_usage.subprocess, "Popen") as spawn:
                claude_usage.schedule(target, "test", ["hook"])
            spawn.assert_not_called()
            self.assertEqual(usage.load(str(Path(target) / "test.json"))[0]["updated"], stamp)

    def test_claude_cache_does_not_replace_newer_statusline(self):
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder) / "sessions")
            usage.save(target, "test", {"five_hour": {"used_percentage": 60}})
            claude_usage.publish(target, "test", {"limits": {"five_hour": {"used_percentage": 40}}, "updated": time.time() - 100})
            self.assertEqual(usage.load(str(Path(target) / "test.json"))[0]["percent"], 60)

    def test_claude_rate_limit_backs_off_and_keeps_last_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder) / "sessions")
            _, path, lock = claude_usage.paths(target)
            Path(path).parent.mkdir()
            Path(lock).mkdir()
            (Path(folder) / "config.json").write_text('{"claude_oauth_usage": true}')
            stamp = time.time() - 100
            Path(path).write_text(json.dumps({"limits": {"five_hour": {"used_percentage": 40}}, "updated": stamp}))
            error = usage.HTTPStatusError(429)
            with patch.object(claude_usage, "access_token", return_value="placeholder"), patch.object(claude_usage, "fetch", side_effect=error):
                claude_usage.worker(target, "test")
            cache = json.loads(Path(path).read_text())
            self.assertGreater(cache["retry_at"], time.time() + 800)
            self.assertEqual(cache["updated"], stamp)
            self.assertFalse(Path(lock).exists())

    def test_oauth_is_off_without_explicit_consent(self):
        self.assertFalse(config_owner.DEFAULT_CONFIG["claude_oauth_usage"])
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder) / "sessions")
            with patch.object(claude_usage, "access_token") as credentials, patch.object(claude_usage.subprocess, "Popen") as spawn:
                claude_usage.schedule(target, "test", ["hook"])
                claude_usage.worker(target, "test")
            credentials.assert_not_called()
            spawn.assert_not_called()

    def test_queued_oauth_worker_rechecks_opt_out(self):
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder) / "sessions")
            _, path, lock = claude_usage.paths(target)
            Path(path).parent.mkdir()
            Path(lock).mkdir()
            (Path(folder) / "config.json").write_text('{"claude_oauth_usage": false}')
            with patch.object(claude_usage, "access_token") as credentials:
                claude_usage.worker(target, "test")
            credentials.assert_not_called()
            self.assertFalse(Path(lock).exists())

    def test_legacy_statusline_migration_restores_previous(self):
        original = {"type": "command", "command": "echo original", "padding": 2}
        legacy = {"statusLine": {"type": "command", "command": "aipet-hook --usage", "aipet_previous": original}}
        self.assertEqual(hook_settings.remove_usage(legacy)["statusLine"], original)

    def test_usage_restore_preserves_manual_edits(self):
        settings = {"statusLine": {"type": "command", "command": "aipet-hook --usage --manual-edit"}}
        self.assertEqual(hook_settings.remove_usage(settings, previous={"type": "command", "command": "old"},
                                               installed={"type": "command", "command": "aipet-hook --usage"}), settings)

    def test_opt_in_statusline_restore_metadata_lives_outside_settings(self):
        before = hook_settings.merge_hooks({"statusLine": {"type": "command", "command": "printf original", "padding": 2}}, "aipet-hook")
        state = {"text": json.dumps(before)}
        def write(key, text):
            state["text"] = text
        with tempfile.TemporaryDirectory() as folder, patch.object(hook_locations, "PET_DIR", folder), patch.object(hook_targets, "read_raw", side_effect=lambda key: (True, state["text"])), patch.object(hook_targets, "write_raw", side_effect=write), patch.object(hook_backups, "_save_backup") as backup, patch.object(hook_deployment, "deploy_files"), patch.object(hook_commands, "windows_hook_command", return_value="aipet-hook"), patch.object(hook_hosts, "find_statusline_bash", return_value="bash"):
            hook_installer.set_usage("windows", True)
            installed = json.loads(state["text"])
            self.assertNotIn("aipet_previous", installed["statusLine"])
            record = hook_installer.usage_record("windows")
            self.assertEqual(record["previous"], before["statusLine"])
            self.assertIn("statuslines", hook_installer.usage_record_path("windows"))
            hook_installer.set_usage("windows", False)
            self.assertEqual(json.loads(state["text"]), before)
            self.assertEqual(backup.call_count, 2)

    def test_hook_install_keeps_statusline_unchanged(self):
        before = {"statusLine": {"type": "command", "command": "printf original"}}
        state = {"text": json.dumps(before)}
        with patch.object(hook_targets, "read_raw", side_effect=lambda key: (True, state["text"])), patch.object(hook_targets, "write_raw", side_effect=lambda key, text: state.update(text=text)), patch.object(hook_backups, "_save_backup"), patch.object(hook_deployment, "deploy_files"), patch.object(hook_commands, "windows_hook_command", return_value="aipet-hook"):
            hook_installer.install("windows")
        self.assertEqual(json.loads(state["text"])["statusLine"], before["statusLine"])

    def test_usage_failure_does_not_skip_permission_hook(self):
        with tempfile.TemporaryDirectory() as folder:
            data = {"hook_event_name": "PermissionRequest", "session_id": "test", "rate_limits": {"five_hour": {"used_percentage": 40}}}
            with patch.object(hook_io, "read_stdin", return_value=json.dumps(data)), patch.object(hook_hosts, "is_wsl", return_value=False), patch.object(hook_hosts, "sessions_dir", return_value=str(Path(folder) / "sessions")), patch.object(hook_config, "AGENT", "claude"), patch.object(usage, "save", side_effect=PermissionError("usage read-only")), patch.object(hook_approvals, "auto_decision", return_value=None), patch.object(hook_sessions, "_update_session", return_value="approval-id") as update, patch.object(hook_waiting, "answer_flow") as answer:
                hook_cli.main()
            update.assert_called_once()
            answer.assert_called_once()

    def test_hook_operates_without_optional_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "aipet_hook.py"
            shutil.copyfile(aipet_hook.__file__, script)
            # Required source components travel with the hook; usage modules remain optional.
            components = Path(aipet_hook.__file__).parent / "aipet_hook_runtime"
            shutil.copytree(components, Path(folder) / "aipet_hook_runtime", ignore=shutil.ignore_patterns("__pycache__"))
            data = {"hook_event_name": "UserPromptSubmit", "session_id": "isolated", "cwd": folder, "prompt": "test"}
            result = subprocess.run([sys.executable, str(script)], input=json.dumps(data), text=True, capture_output=True,
                                    cwd=folder, env={**os.environ, "PYTHONPATH": "", "AIPET_DIR": folder}, timeout=10)
            self.assertEqual(result.returncode, 0)
            rec = json.loads((Path(folder) / "sessions" / "isolated.json").read_text())
            self.assertEqual(rec["state"], "working")

    def test_unchanged_codex_rollout_is_not_read_again(self):
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder) / "sessions")
            rollout = Path(folder) / "rollout.jsonl"
            rollout.write_text(json.dumps({"payload": {"type": "token_count", "rate_limits": {"primary": {"used_percent": 42}}}}))
            hook_capture.capture_codex_usage(target, "test", {"transcript_path": str(rollout)}, {})
            import builtins
            original = builtins.open
            def guarded(path, *args, **kwargs):
                if str(path) == str(rollout):
                    self.fail("unchanged rollout was opened")
                return original(path, *args, **kwargs)
            with patch("builtins.open", side_effect=guarded):
                hook_capture.capture_codex_usage(target, "test", {"transcript_path": str(rollout)}, {})

    def test_existing_statusline_runs_in_bash(self):
        if not hook_hosts.find_statusline_bash():
            self.skipTest("Bash unavailable")
        with tempfile.TemporaryDirectory() as folder:
            encoded = base64.urlsafe_b64encode(b"printf 'bash status line\\n'").decode()
            result = subprocess.run([sys.executable, aipet_hook.__file__, "--usage", "--usage-forward=" + encoded],
                                    input=json.dumps({"session_id": "test"}), text=True, capture_output=True,
                                    env={**os.environ, "AIPET_DIR": folder}, timeout=10)
            self.assertEqual(result.stdout, "bash status line\n")

    def test_statusline_preserved_and_restored(self):
        before = {"statusLine": {"type": "command", "command": "echo original", "padding": 3}, "other": "keep"}
        installed = hook_settings.merge_usage(hook_settings.merge_hooks(before, "aipet-hook"), "aipet-hook")
        encoded = installed["statusLine"]["command"].split("--usage-forward=", 1)[1]
        self.assertEqual(base64.urlsafe_b64decode(encoded).decode(), "echo original")
        self.assertEqual(installed["statusLine"]["padding"], 3)
        self.assertNotIn("aipet_previous", installed["statusLine"])
        self.assertEqual(hook_settings.remove_hooks(installed, previous=before["statusLine"], installed=installed["statusLine"]), before)
        self.assertEqual(hook_settings.merge_usage(installed, "aipet-hook", previous=before["statusLine"]), installed)

    def test_statusline_removal_when_previously_absent(self):
        installed = hook_settings.merge_usage(hook_settings.merge_hooks({}, "aipet-hook"), "aipet-hook")
        self.assertEqual(hook_settings.remove_hooks(installed), {})

    def test_user_statusline_change_is_kept_on_uninstall(self):
        installed = hook_settings.merge_usage(hook_settings.merge_hooks({}, "aipet-hook"), "aipet-hook")
        installed["statusLine"] = {"type": "command", "command": "echo changed"}
        self.assertEqual(hook_settings.remove_hooks(installed)["statusLine"], installed["statusLine"])

    def test_hook_status_does_not_require_a_usage_collector(self):
        settings = hook_settings.merge_hooks({}, "aipet-hook")
        self.assertEqual(hook_settings.hooks_state(settings, "aipet-hook"), "current")
        settings = hook_settings.merge_usage(settings, "aipet-hook")
        self.assertEqual(hook_settings.hooks_state(settings, "aipet-hook"), "current")
        codex = hook_settings.merge_hooks({}, "aipet-hook --codex", hook_protocol.CODEX_HOOK_EVENTS, hook_protocol.CODEX_HOOK_TIMEOUTS)
        self.assertEqual(hook_settings.hooks_state(codex, "aipet-hook --codex", hook_protocol.CODEX_HOOK_EVENTS, hook_protocol.CODEX_HOOK_TIMEOUTS), "current")

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
            hook_capture.capture_codex_usage(str(target), "test", {"transcript_path": str(rollout)}, {})
            self.assertEqual(usage.load(str(target / "test.json"))[0]["percent"], 42)

    def test_tls_supplements_default_trust(self):
        context = MagicMock()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"tag_name":"v1.0.0"}'
        certifi = MagicMock()
        certifi.where.return_value = "trusted-ca.pem"
        with patch.dict(sys.modules, {"certifi": certifi}), patch("ssl.create_default_context", return_value=context), \
                patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual(aipet_update.latest_release()["tag"], "v1.0.0")
            context.load_verify_locations.assert_called_once_with(cafile="trusted-ca.pem")
            self.assertIs(urlopen.call_args.kwargs["context"], context)

    def test_source_update_check_without_certifi(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"tag_name":"v1.0.0"}'
        with patch.dict(sys.modules, {"certifi": None}), patch("ssl.create_default_context") as context, \
                patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(aipet_update.latest_release()["tag"], "v1.0.0")
            context.return_value.load_verify_locations.assert_not_called()

    def test_app_builds_without_openssl_use_the_system_curl(self):
        done = MagicMock(returncode=0, stdout=b'{"tag_name":"v2.0.0"}\n200', stderr=b"")
        with patch.dict(sys.modules, {"ssl": None}), patch.object(usage, "find_curl", return_value="curl"), \
                patch.object(usage.subprocess, "run", return_value=done) as run:
            self.assertEqual(aipet_update.latest_release(token="secret-token")["tag"], "v2.0.0")
        argv, config = run.call_args.args[0], run.call_args.kwargs["input"].decode()
        self.assertNotIn("secret-token", " ".join(argv))  # never on the command line
        self.assertIn('header = "Authorization: Bearer secret-token"', config)
        self.assertIn(f'url = "{aipet_update.API_URL}"', config)
        self.assertIn("=https", argv)
        done.stdout = b'{"message":"no"}\n429'
        with patch.dict(sys.modules, {"ssl": None}), patch.object(usage, "find_curl", return_value="curl"), \
                patch.object(usage.subprocess, "run", return_value=done):
            with self.assertRaises(usage.HTTPStatusError) as raised:
                usage.https_get_json("https://example.invalid/x", {})
        self.assertEqual(raised.exception.code, 429)

    def test_ci_rate_limit_response_confirms_tls(self):
        error = usage.HTTPStatusError(403, {"X-RateLimit-Remaining": "0"})
        with patch.object(aipet_update, "latest_release", side_effect=error):
            result = aipet_selftest.check_update()
        self.assertTrue(result["tls_verified"])
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["rate_limit_remaining"], "0")
        self.assertNotIn("release", result)

    def test_ci_certificate_failure_remains_failure(self):
        with patch.object(aipet_update, "latest_release", side_effect=OSError("bad certificate")):
            result = aipet_selftest.check_update()
        self.assertFalse(result["tls_verified"])
        self.assertNotIn("http_status", result)

    def test_ci_token_used_only_when_explicitly_supplied(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"tag_name":"v1.0.0"}'
        with patch.dict(sys.modules, {"certifi": MagicMock()}), patch("ssl.create_default_context"), \
                patch("urllib.request.urlopen", return_value=response) as urlopen:
            aipet_update.latest_release(token="ci-test-placeholder")
            self.assertEqual(urlopen.call_args.args[0].get_header("Authorization"), "Bearer ci-test-placeholder")
            aipet_update.latest_release()
            self.assertIsNone(urlopen.call_args.args[0].get_header("Authorization"))

    def test_mac_fullscreen_behavior_on_off_and_older_systems(self):
        # Execute the native flag policy without loading macOS's ObjC runtime.
        tree = ast.parse((Path(aipet.__file__).parent / "mac_statusbar.py").read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if
                 (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("ALL_SPACES", "ALL_SPACES_CONFLICTS", "ALL_APPLICATIONS") for t in node.targets)) or
                 (isinstance(node, ast.FunctionDef) and node.name == "set_all_spaces")]
        current = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 5) | (1 << 7) | (1 << 9) | (1 << 11) | (1 << 16) | (1 << 17)
        def send(window, selector, *args, **kwargs):
            nonlocal current
            if selector == "collectionBehavior":
                return current
            if selector == "setCollectionBehavior:":
                current = args[0]
        scope = {"send": send, "window_titled": lambda title: 1, "c_ulong": ctypes.c_ulong, "c_long": ctypes.c_long,
                 "supports_all_applications": lambda: True}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "mac-behavior", "exec"), scope)
        self.assertTrue(scope["set_all_spaces"]("AIPet", True))
        self.assertEqual(current & scope["ALL_SPACES_CONFLICTS"], 0)
        self.assertEqual(current & scope["ALL_SPACES"], scope["ALL_SPACES"])
        self.assertTrue(current & (1 << 18))  # join other apps, not just ordinary desktops
        self.assertTrue(scope["set_all_spaces"]("AIPet", False))
        self.assertEqual(current, 1 << 11)  # unrelated flags survive; all sharing is removed
        scope["supports_all_applications"] = lambda: False
        self.assertTrue(scope["set_all_spaces"]("AIPet", True))
        self.assertEqual(current, scope["ALL_SPACES"] | (1 << 11))
        scope["window_titled"] = lambda title: None
        self.assertFalse(scope["set_all_spaces"]("missing", True))


    def test_usage_files_follow_their_sessions(self):
        with tempfile.TemporaryDirectory() as home:
            folder, sessions = os.path.join(home, "usage"), os.path.join(home, "sessions")
            os.makedirs(folder)
            os.makedirs(sessions)
            names = ["live.json", "live.rollout.json", "gone.json", "gone.rollout.json", "_claude-abc.json",
                     "fresh.json", "x.json.1.tmp"]
            for name in names:
                Path(folder, name).write_text("{}")
            Path(sessions, "live.json").write_text("{}")
            old = time.time() - 2 * 86400
            for name in names:
                if name != "fresh.json":
                    os.utime(os.path.join(folder, name), (old, old))
            self.assertEqual(usage.sweep(home), 3)  # gone.json, gone.rollout.json and the stray temp file
            self.assertEqual(sorted(os.listdir(folder)),
                             ["_claude-abc.json", "fresh.json", "live.json", "live.rollout.json"])
            usage.remove_for(os.path.join(sessions, "live.json"))
            self.assertEqual(sorted(os.listdir(folder)), ["_claude-abc.json", "fresh.json"])


    def test_windows_statusline_command_runs_in_bash_and_powershell(self):
        import ntpath
        with patch.object(hook_commands.os, "path", ntpath), \
                patch.object(hook_commands, "_short_path", lambda p: p.replace("John Smith", "JOHNSM~1")):
            self.assertEqual(hook_commands._shell_neutral(r"C:\Users\rafae\.aipet\bin\hook\aipet-hook.exe"),
                             "C:/Users/rafae/.aipet/bin/hook/aipet-hook.exe")
            short = hook_commands._shell_neutral(r"C:\Users\John Smith\.aipet\bin\hook\aipet-hook.exe")
            self.assertEqual(short, "C:/Users/JOHNSM~1/.aipet/bin/hook/aipet-hook.exe")
            self.assertTrue(hook_protocol.MARKER.search(short))  # still recognised as AIPet's command
        with patch.object(hook_commands.os, "path", ntpath), patch.object(hook_commands, "_short_path", lambda p: p):
            self.assertIsNone(hook_commands._shell_neutral(r"C:\Users\John Smith\.aipet\bin\hook\aipet-hook.exe"))


if __name__ == "__main__":
    unittest.main()
