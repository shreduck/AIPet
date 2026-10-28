"""Shared list controls and atomic user confirmation of timed editor changes."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aipetlib import permissions
from aipetlib.app import answers
from aipetlib.ui import approval_rules
import support


class ApprovalPrecedenceUITests(unittest.TestCase):
    def test_pause_dismisses_target_session_cards_and_preserves_other_sessions(self):
        self.app._last_items = [dict(self.item, key='target'), dict(key='other', session_id='another-session')]
        target, other = MagicMock(), MagicMock()
        self.app.details = dict(target=target, other=other)
        with patch.object(answers.theme, 'themed_dialog', return_value=MagicMock()) as dialog, \
                patch.object(self.controller, 'send') as send:
            self.controller.start_timed(self.item, on_result=MagicMock())
            dialog.call_args.kwargs['on_result']('blacklist')
        target.close.assert_called_once()
        other.close.assert_not_called()
        send.assert_not_called()
        self.assertTrue(permissions.timed_grants()['fixture']['respect_blacklist'])
        self.assertFalse(permissions.timed_grants()['fixture']['reject_blacklist'])

    def test_invalid_line_focus_and_edit_recovery_use_actual_text_line_numbers(self):
        editor = object.__new__(approval_rules.RuleLists)
        editor.white_first = MagicMock()
        editor.white_first.get.return_value = False
        editor.boxes = dict(whitelist=MagicMock(), blacklist=MagicMock())
        editor.boxes['whitelist'].get.return_value = 'git status'
        black = editor.boxes['blacklist']
        black.get.return_value = '\n# keep this comment\n\n*.'
        with self.assertRaisesRegex(ValueError, 'blacklist line 4'):
            editor.read()
        editor.focus_error()
        black.tag_add.assert_called_with('sel', '4.0', '4.end')
        black.mark_set.assert_called_with('insert', '4.0')
        black.focus_force.assert_called_once()
        editor.on_change = MagicMock()
        black.edit_modified.return_value = True
        editor.edited(SimpleNamespace(widget=black))
        editor.on_change.assert_called_once()
        self.assertEqual(editor.last_errors, [])
        black.get.return_value = '.*'
        self.assertEqual(editor.read()['blacklist'], ['.*'])

    def test_async_confirmation_returns_before_choice_and_reuses_pending_session_dialog(self):
        completed, window = MagicMock(), MagicMock()
        with patch.object(answers.theme, 'themed_dialog', return_value=window) as dialog:
            self.assertIs(self.controller.start_timed(self.item, on_result=completed), window)
            completed.assert_not_called()
            self.assertEqual(permissions.timed_grants(), {})
            self.assertIs(self.controller.start_timed(self.item, on_result=completed), window)
            dialog.assert_called_once()
            window.lift.assert_called_once()
            dialog.call_args.kwargs['on_result']('blacklist')
            completed.assert_called_once_with(True)
            self.assertTrue(permissions.timed_grants()['fixture']['respect_blacklist'])
            self.assertNotIn('fixture', self.controller._timed_dialogs)

    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(support.paths(AUTO_APPROVE_PATH=os.path.join(folder, 'auto-approve.json')))
        self.app = SimpleNamespace(root=MagicMock(), cfg={})
        self.controller = answers.Answers(self.app)
        self.item = dict(session_id='fixture', title='Project')
        self.key = permissions.auto_approve_key_for(self.item)

    def test_timed_dialog_contains_editors_and_saves_precedence_with_selected_mode(self):
        for mode in ('blacklist', 'reject_blacklist', True):
            with self.subTest(mode=mode), patch.object(approval_rules, 'RuleLists') as editor, \
                    patch.object(answers.tk, 'Label'), patch.object(answers.theme, 'theme_widget'):
                editor.return_value.read.return_value = dict(whitelist=['git status'], blacklist=['git'], precedence='whitelist')
                def confirm(*args, **kwargs):
                    self.assertFalse(kwargs['enter_confirms'])
                    kwargs['content_builder'](MagicMock())
                    self.assertTrue(kwargs['validate'](mode))
                    return mode
                with patch.object(answers.theme, 'themed_dialog', side_effect=confirm):
                    self.assertTrue(self.controller.start_timed(self.item))
                self.assertTrue(editor.call_args.kwargs['compact'])
                saved = permissions.auto_approve_rules()[self.key]
                self.assertEqual(saved['precedence'], 'whitelist')
                self.assertEqual(saved['whitelist'], ['git status'])
                self.assertEqual(saved['blacklist'], ['git'])
                self.assertFalse(saved['enabled'], 'editing timed rules must not enable normal auto approval')
                grant = permissions.timed_grants()['fixture']
                self.assertEqual(grant['respect_blacklist'], mode is not True)
                self.assertEqual(grant['reject_blacklist'], mode == 'reject_blacklist')

    def test_cancel_never_saves_lists_or_starts_timer(self):
        with patch.object(answers.theme, 'themed_dialog', return_value=False), \
                patch.object(permissions, 'save_auto_approve') as save:
            self.assertFalse(self.controller.start_timed(self.item))
        save.assert_not_called()
        self.assertEqual(permissions.timed_grants(), {})

    def test_invalid_patterns_and_failed_save_keep_confirmation_open(self):
        for error in ('bad pattern', None):
            with self.subTest(error=error), patch.object(approval_rules, 'RuleLists') as editor, \
                    patch.object(answers.tk, 'Label') as label, patch.object(answers.theme, 'theme_widget'), \
                    patch.object(permissions, 'save_auto_approve', return_value=False):
                editor.return_value.read.return_value = dict(whitelist=['safe'], blacklist=['danger'], precedence='whitelist')
                editor.return_value.read.side_effect = ValueError(error) if error else None
                def confirm(*args, **kwargs):
                    kwargs['content_builder'](MagicMock())
                    self.assertFalse(kwargs['validate']('blacklist'))
                    self.assertTrue(label.return_value.configure.call_args.kwargs['text'])
                    return False
                with patch.object(answers.theme, 'themed_dialog', side_effect=confirm):
                    self.assertFalse(self.controller.start_timed(self.item))
                self.assertEqual(permissions.timed_grants(), {})

    def test_shared_editor_reads_multiline_lists_and_priority_and_validates_patterns(self):
        editor = object.__new__(approval_rules.RuleLists)
        editor.white_first = MagicMock()
        editor.white_first.get.return_value = True
        editor.boxes = dict(whitelist=MagicMock(), blacklist=MagicMock())
        editor.boxes['whitelist'].get.return_value = '# comment\n\ngit status\n'
        editor.boxes['blacklist'].get.return_value = 'rm\n'
        self.assertEqual(editor.read(), dict(whitelist=['# comment', 'git status'], blacklist=['rm'], precedence='whitelist'))
        editor.boxes['blacklist'].get.return_value = '['
        with self.assertRaisesRegex(ValueError, 'blacklist line 1'):
            editor.read()


if __name__ == '__main__':
    unittest.main()
