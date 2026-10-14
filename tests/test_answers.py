import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import aipet
import aipetlib.app.answers as answers_module
from aipetlib.app.answers import Answers
import support
import aipet_hook as hook


class AnswerToggleTests(unittest.TestCase):
    def test_permission_suggestions_reach_only_the_selected_waiting_hook(self):
        app = object.__new__(aipet.PetApp)
        app.cfg = {'claude_answers': True}
        app._last_items = [{'key': 'selected', 'agent': 'claude',
                            'request': {'id': 'selected-hook', 'answerable': True}},
                           {'key': 'other', 'agent': 'claude',
                            'request': {'id': 'other-hook', 'answerable': True}}]
        app.answers = Answers(app)
        suggestions = [{'type': 'addRules', 'rules': [{'toolName': 'Bash', 'ruleContent': 'echo fixture'}],
                        'behavior': 'allow', 'destination': 'session'}]
        with tempfile.TemporaryDirectory() as folder, support.paths(HOME_DIR=folder):
            answers = Path(folder, 'answers')
            answers.mkdir()
            Path(answers, 'selected-hook.waiting').touch()
            Path(answers, 'other-hook.waiting').touch()
            self.assertTrue(app.answers.send('selected', 'allow', permissions=suggestions))
            payload = json.loads(Path(answers, 'selected-hook.json').read_text())
            self.assertEqual(payload['behavior'], 'allow')
            self.assertEqual(payload['permissions'], suggestions)
            self.assertFalse(Path(answers, 'other-hook.json').exists())
            self.assertFalse(Path(answers, 'selected-hook.json.tmp').exists())
            Path(answers, 'selected-hook.waiting').unlink()
            self.assertFalse(app.answers.send('selected', 'deny', permissions=suggestions))
            self.assertEqual(json.loads(Path(answers, 'selected-hook.json').read_text()), payload)

    def test_disabled_manual_answering_still_records_requests_and_preserves_auto_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'no-claude-answers').touch()
            data = {'hook_event_name': 'PermissionRequest', 'session_id': 'test'}
            for auto in (None, 'whitelist'):
                with self.subTest(auto=auto), patch.object(hook, 'AGENT', 'claude'), \
                        patch.object(hook, 'read_stdin', return_value=json.dumps(data)), \
                        patch.object(hook, 'is_wsl', return_value=False), \
                        patch.object(hook, 'sessions_dir', return_value=str(Path(folder, 'sessions'))), \
                        patch.object(hook, 'usage', None), patch.object(hook, 'claude_usage', None), \
                        patch.object(hook, 'auto_decision', return_value=auto), \
                        patch.object(hook, '_update_session', return_value='test') as update, \
                        patch.object(hook, 'answer_flow') as answer, patch.object(hook, 'write_stdout') as output:
                    hook.main()
                    update.assert_called_once()
                    answer.assert_not_called()
                    if auto:
                        output.assert_called_once_with(hook.decision_output('allow'))
                    else:
                        output.assert_not_called()

    def test_claude_setting_persists_and_does_not_change_codex(self):
        app = object.__new__(aipet.PetApp)
        app.cfg = dict(aipet.DEFAULT_CONFIG)
        app.claude_answer_var = MagicMock()
        app.sync_bubbles, app._last_items = MagicMock(), []
        app.answers = Answers(app)
        with tempfile.TemporaryDirectory() as folder, support.paths(HOME_DIR=folder), \
                patch.object(answers_module.runtime_settings, 'save_setting') as save:
            codex_flag = Path(folder, 'codex-answers')
            codex_flag.touch()
            for enabled in (False, True):
                app.answers.set_claude_answers(enabled)
                save.assert_called_with('claude_answers', enabled)
                self.assertEqual(app.answers.enabled({'agent': 'claude'}), enabled)
                self.assertEqual(Path(folder, 'no-claude-answers').exists(), not enabled)
                self.assertTrue(app.answers.enabled({'agent': 'codex'}))
                self.assertTrue(codex_flag.exists())
                app.sync_bubbles.assert_called_with([])

    def test_hooks_respect_independent_toggles_and_legacy_global_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            for claude_on in (False, True):
                flag = Path(folder, 'no-claude-answers')
                flag.unlink(missing_ok=True) if claude_on else flag.touch()
                for codex_on in (False, True):
                    flag = Path(folder, 'codex-answers')
                    flag.touch() if codex_on else flag.unlink(missing_ok=True)
                    for agent, expected in [('claude', claude_on), ('codex', codex_on)]:
                        with self.subTest(agent=agent, claude=claude_on, codex=codex_on), \
                                patch.object(hook, 'AGENT', agent):
                            self.assertEqual(hook.answers_enabled(folder), expected)
                            with patch.object(hook, 'pet_alive', return_value=True), \
                                    patch.object(hook, 'await_answer') as wait:
                                if not expected:
                                    hook.answer_flow(folder, str(Path(folder, 'unused.json')), '')
                                    wait.assert_not_called()
            Path(folder, 'no-answers').touch()
            for agent in ('claude', 'codex'):
                with patch.object(hook, 'AGENT', agent):
                    self.assertFalse(hook.answers_enabled(folder))

    def test_disabling_claude_releases_hook_already_waiting(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(hook, 'AGENT', 'claude'), \
                patch.object(hook, 'pet_alive', return_value=True), \
                patch.object(hook.time, 'sleep', side_effect=lambda _: Path(folder, 'no-claude-answers').touch()) as sleep:
            result = hook._await(str(Path(folder, 'answer.json')), str(Path(folder, 'waiting')), folder, 0, None, None)
            self.assertIsNone(result)
            sleep.assert_called_once()

    def test_release_lets_go_so_codex_shows_its_own_prompt(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(hook, 'AGENT', 'codex'), \
                patch.object(hook, 'pet_alive', return_value=True):
            Path(folder, 'codex-answers').touch()
            answer = Path(folder, 'answer.json')
            answer.write_text('{"behavior": "release"}')
            self.assertIsNone(hook._await(str(answer), str(Path(folder, 'waiting')), folder, 5, None, None))
            self.assertFalse(answer.exists())

    def test_go_to_window_releases_only_codex_prompts(self):
        app = object.__new__(aipet.PetApp)
        app.answers = Answers(app)
        app.answers.send, app.focus_key = MagicMock(), MagicMock()
        app._last_items = [{'key': 'cx', 'agent': 'codex', 'state': 'needs_input'},
                           {'key': 'cc', 'agent': 'claude', 'state': 'needs_input'}]
        app.answers.go_to_prompt('cx')
        app.answers.send.assert_called_once_with('cx', 'release')
        app.answers.go_to_prompt('cc')
        app.answers.send.assert_called_once()  # Claude Code shows its own prompt alongside: nothing to let go
        self.assertEqual([c.args[0] for c in app.focus_key.call_args_list], ['cx', 'cc'])

    def test_disabled_claude_cannot_submit_an_answer_to_a_stale_dialog(self):
        app = object.__new__(aipet.PetApp)
        app.cfg = {'claude_answers': False, 'codex_answers': True}
        app._last_items = [{'key': 'test', 'agent': 'claude', 'request': {'id': 'test', 'answerable': True}}]
        app.answers = Answers(app)
        with patch.object(answers_module.permission_rules, 'hook_waiting', return_value=True), \
                patch.object(answers_module.os, 'replace') as write:
            self.assertFalse(app.answers.send('test', 'allow'))
            write.assert_not_called()
