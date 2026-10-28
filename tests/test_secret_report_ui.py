"""Masked reveal widgets, animation cleanup, and readable structured report rendering."""
import unittest
from unittest.mock import MagicMock, patch

from aipetlib.ui import secret_value, warning_beacon, addin_messages


class ReportUITests(unittest.TestCase):
    def test_warning_timestamp_uses_event_time_then_message_time_and_includes_timezone(self):
        from datetime import datetime
        expected = datetime.fromtimestamp(1234567890).astimezone().isoformat(timespec='seconds')
        self.assertEqual(addin_messages.warning_timestamp(dict(timestamp=1234567890)), expected)
        self.assertEqual(addin_messages.warning_timestamp(dict(timestamp=999,
            report=dict(context=dict(event_time='1234567890')))), expected)
        self.assertEqual(addin_messages.warning_timestamp(dict(timestamp=1234567890,
            report=dict(context=dict(event_time='invalid')))), expected)
        self.assertEqual(addin_messages.warning_timestamp({}), 'Not available')

    def test_secret_widget_contains_only_mask_until_eye_is_explicitly_toggled(self):
        secret = 'synthetic-secret-for-reveal'
        text = MagicMock()
        with patch.object(secret_value.tk, 'Canvas') as eye:
            control = secret_value.inline(text, secret)
            self.assertNotIn(secret, repr(text.mock_calls))
            toggle = next(call.args[1] for call in eye.return_value.bind.call_args_list if call.args[0] == '<Button-1>')
            toggle()
            self.assertEqual(text.insert.call_args.args[1], secret)
            text.reset_mock()
            control.set_content_scale(1.5)
            text.insert.assert_not_called()
            text.delete.assert_not_called()
            eye.return_value.configure.assert_any_call(bg=secret_value.theme.T['btn_bg'],
                highlightbackground=secret_value.theme.T['border'], width=36, height=30)
            toggle()
            self.assertEqual(text.insert.call_args.args[1], '••••••••••••')
            self.assertNotIn(secret, repr(text.mock_calls))

    def test_each_eye_control_has_independent_reveal_state(self):
        text = MagicMock()
        with patch.object(secret_value.tk, 'Canvas') as eye:
            secret_value.inline(text, 'encoded-test')
            first = next(call.args[1] for call in eye.return_value.bind.call_args_list if call.args[0] == '<Button-1>')
            eye.return_value.bind.reset_mock()
            secret_value.inline(text, 'decoded-test')
            second = next(call.args[1] for call in eye.return_value.bind.call_args_list if call.args[0] == '<Button-1>')
            first()
            self.assertEqual(text.insert.call_args.args[1], 'encoded-test')
            second()
            self.assertEqual(text.insert.call_args.args[1], 'decoded-test')
            first()
            self.assertEqual(text.insert.call_args.args[1], '••••••••••••')

    def test_beacon_rotates_in_brand_cyan_and_cancels_timer(self):
        with patch.object(warning_beacon.tk, 'Canvas') as canvas:
            beacon = warning_beacon.WarningBeacon(MagicMock())
            first_phase = beacon.phase
            self.assertTrue(beacon.robot, 'the alert should use the packaged AIPet robot body')
            beacon.set_level('error')
            beacon.phase = 1.4  # Ensure the rotating beam is visible as well as the dome.
            beacon.tick()
            self.assertGreater(beacon.phase, first_phase)
            self.assertTrue(any(call.kwargs.get('fill') == beacon.robot['meta']['cyan']
                                for call in canvas.return_value.create_rectangle.call_args_list))
            canvas.return_value.after.assert_called_with(70, beacon.tick)
            canvas.return_value.move.assert_called_with('alarm', 14, 34)
            canvas.return_value.scale.assert_called_with('alarm', 0, 0, 40/76, 36/58)
            timer = beacon.timer
            beacon.stop()
            canvas.return_value.after_cancel.assert_called_once_with(timer)
            self.assertIsNone(beacon.timer)

    def test_report_displays_rule_explanation_session_and_masked_value_widgets(self):
        report = dict(rule='base64.credentials', name='Encoded credentials', warning_level='high',
            explanation='Decoded data resembles a credential pair.', fingerprint='fixture-fingerprint',
            value='encoded-fixture', decoded='decoded-fixture', context=dict(conversation_title='Test conversation',
                harness='Codex', session_id='session-fixture', hook_event='UserPromptSubmit', cwd='/example'))
        report.update(hook_text='prefix encoded-fixture suffix', hook_field='prompt', hook_spans=[[7, 22]])
        rows = [dict(name='Empty session', messages=[], level='info'), dict(name='Token guard', level='error',
            messages=[dict(level='error', title='High warning', content='Summary', report=report)])]
        parent = MagicMock()
        parent.winfo_viewable.return_value = False
        with patch.object(addin_messages.tk, 'Toplevel') as top, patch.object(addin_messages.tk, 'Frame'), \
                patch.object(addin_messages.tk, 'Label'), patch.object(addin_messages.tk, 'Button'), \
                patch.object(addin_messages, 'WarningBeacon'), patch.object(addin_messages, 'ToggleSwitch'), patch.object(addin_messages, 'inline_secret') as field, \
                patch.object(addin_messages, 'ScrolledText') as text, patch.object(addin_messages.theme, 'theme_window'), \
                patch.object(addin_messages.theme, 'theme_widget'):
            top.return_value.winfo_reqwidth.return_value = 600
            top.return_value.winfo_reqheight.return_value = 400
            text.return_value.yview.return_value = (0.0, 1.0)
            addin_messages.show(parent, rows, screen=dict(work_area=[0, 0, 1920, 1080]))
            rendered = ''.join(call.args[1] for call in text.return_value.insert.call_args_list)
            for expected in ('WARNING LEVEL: HIGH', 'base64.credentials', report['explanation'],
                             'Test conversation', 'Codex', 'session-fixture', 'UserPromptSubmit', '/example'):
                self.assertIn(expected, rendered)
            self.assertNotIn('No messages', rendered)
            self.assertNotIn('Empty session', rendered)
            self.assertNotIn('encoded-fixture', rendered)
            self.assertNotIn('decoded-fixture', rendered)
            self.assertIn('prefix ', rendered)
            self.assertIn(' suffix', rendered)
            self.assertEqual([call.args[1] for call in field.call_args_list], ['encoded-fixture', 'decoded-fixture'])

    def test_markdown_toggle_rerenders_surroundings_and_keeps_inline_secrets_masked(self):
        source = 'Before **synthetic-secret** after'
        start = source.index('synthetic-secret')
        report = dict(rule='fixture', name='Fixture rule', warning_level='standard', explanation='Test explanation',
            fingerprint='fixture', value='synthetic-secret', hook_text=source, hook_field='prompt',
            hook_spans=[[start, start+len('synthetic-secret')]],
            markdown_template='# Token guard report\n\n## Finding\n\n{{explanation}}\n\n## Hook text\n\n{{hook_text}}')
        rows = [dict(name='Token guard', level='warning', messages=[dict(title='Test warning', level='warning', report=report)])]
        parent = MagicMock()
        parent.winfo_viewable.return_value = False
        with patch.object(addin_messages.tk, 'Toplevel'), patch.object(addin_messages.tk, 'Frame'), \
                patch.object(addin_messages.tk, 'Label'), patch.object(addin_messages.tk, 'Button'), \
                patch.object(addin_messages.tk, 'BooleanVar') as preview, patch.object(addin_messages.tk, 'Checkbutton') as check, \
                patch.object(addin_messages, 'WarningBeacon'), patch.object(addin_messages, 'ToggleSwitch'), patch.object(addin_messages, 'inline_secret') as field, \
                patch.object(addin_messages, 'ScrolledText') as text, patch.object(addin_messages.theme, 'theme_window'):
            preview.return_value.get.return_value = False
            text.return_value.yview.return_value = (0.0, 1.0)
            addin_messages.show(parent, rows, screen=dict(work_area=[0, 0, 1920, 1080]))
            self.assertEqual(check.call_args.kwargs['text'], 'Markdown preview')
            self.assertIs(preview.call_args.kwargs['value'], True)
            text.return_value.insert.reset_mock()
            field.reset_mock()
            preview.return_value.get.return_value = True
            check.call_args.kwargs['command']()
            rendered = ''.join(call.args[1] for call in text.return_value.insert.call_args_list)
            self.assertIn('Before ', rendered)
            self.assertIn('Token guard report', rendered)
            self.assertIn('Test explanation', rendered)
            self.assertTrue(any('md-h1' in call.args[2] for call in text.return_value.insert.call_args_list if len(call.args) > 2))
            self.assertIn(' after', rendered)
            self.assertNotIn('**', rendered)
            self.assertNotIn('synthetic-secret', rendered)
            field.assert_called_once_with(text.return_value, 'synthetic-secret')


if __name__ == '__main__':
    unittest.main()
