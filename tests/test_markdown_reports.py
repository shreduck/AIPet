"""Add-in Markdown templates keep source spans revealable and metadata literal."""
import unittest
from unittest.mock import MagicMock

from aipet_addin import format_markdown_report
from aipet_token_guard import report_markdown
from aipetlib.addin_messages import report_fields
from aipetlib.ui.markdown_preview import render


class MarkdownReportTests(unittest.TestCase):
    def report(self):
        source = 'Before café 🔒 synthetic-value after {{decoded}}'
        start = source.index('synthetic-value')
        report = dict(rule='fixture.rule_name', name='Fixture rule', warning_level='high',
            explanation='A synthetic test finding.', fingerprint='fixture', value='synthetic-value',
            decoded='decoded-value', hook_text=source, hook_spans=[[start, start+15]], hook_field='prompt',
            context=dict(conversation_title='**literal title**', harness='Test harness', session_id='fixture'))
        report['markdown_template'] = report_markdown(report)
        return report

    def test_transported_template_renders_headings_and_preserves_unicode_secret_spans(self):
        report = report_fields(self.report())
        self.assertIsNotNone(report)
        document, spans = format_markdown_report(report, 1234567890)
        self.assertEqual([document[start:end] for start, end in spans], ['synthetic-value', 'decoded-value'])
        self.assertIn('after {{decoded}}', document)  # Source is never reinterpreted as a template.
        text, reveal = MagicMock(), MagicMock()
        render(text, document, spans, reveal)
        displayed = ''.join(call.args[1] for call in text.insert.call_args_list)
        for expected in ('Token guard report', 'Conversation', 'Finding', 'Hook text', 'Decoded content',
                         'fixture.rule_name', '**literal title**', 'Before café 🔒 '):
            self.assertIn(expected, displayed)
        self.assertNotIn('synthetic-value', displayed)
        self.assertNotIn('decoded-value', displayed)
        self.assertEqual([call.args[0] for call in reveal.call_args_list], ['synthetic-value', 'decoded-value'])

    def test_notification_content_excludes_sources_and_redacts_context(self):
        report = self.report()
        report['context']['conversation_title'] = 'Test synthetic-value decoded-value'
        document, spans = format_markdown_report(report, include_source=False)
        self.assertTrue(document.startswith('# Token guard report\n'))
        self.assertIn('## Finding', document)
        self.assertNotIn('synthetic-value', document)
        self.assertNotIn('decoded-value', document)
        self.assertNotIn('Before café', document)
        self.assertEqual(spans, [])

    def test_template_uses_latest_session_context_without_embedding_it_in_payload(self):
        report = self.report()
        self.assertNotIn('literal title', report['markdown_template'])
        report['context']['conversation_title'] = 'Updated title'
        document, _ = format_markdown_report(report)
        self.assertIn('Updated title', document)

    def test_oversized_template_falls_back_to_legacy_report(self):
        report = self.report()
        report['markdown_template'] = 'x' * 8193
        self.assertNotIn('markdown_template', report_fields(report))
