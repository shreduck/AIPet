"""Local formatting preserves masked spans and never fetches linked content."""
import unittest
from unittest.mock import MagicMock

from aipetlib.ui import markdown_preview


class MarkdownPreviewTests(unittest.TestCase):
    def test_headings_emphasis_lists_quotes_code_and_links(self):
        text = MagicMock()
        markdown_preview.render(text, '# Heading\n**Bold** and *italic* and `code`\n- item\n> quote\n'
                                '[label](https://example.invalid)\n```python\n**literal**\n```\n')
        rendered = ''.join(call.args[1] for call in text.insert.call_args_list)
        for expected in ('Heading', 'Bold', 'italic', 'code', '• item', 'quote', 'label (https://example.invalid)', '**literal**'):
            self.assertIn(expected, rendered)
        self.assertNotIn('# Heading', rendered)
        self.assertNotIn('```', rendered)
        self.assertTrue(any('md-h1' in call.args[2] for call in text.insert.call_args_list if len(call.args) > 2))

    def test_secret_inside_bold_markdown_is_only_passed_to_reveal_control(self):
        source = 'Before **synthetic-secret** after.'
        start = source.index('synthetic-secret')
        text, reveal = MagicMock(), MagicMock()
        markdown_preview.render(text, source, [(start, start + len('synthetic-secret'))], reveal)
        reveal.assert_called_once_with('synthetic-secret')
        rendered = ''.join(call.args[1] for call in text.insert.call_args_list)
        self.assertNotIn('synthetic-secret', rendered)
        self.assertIn('Before ', rendered)
        self.assertIn(' after.', rendered)
        self.assertNotIn('**', rendered)

    def test_fenced_secret_stays_masked_and_literal_markup_is_preserved(self):
        source = '```\n**secret-value**\n```'
        start = source.index('secret-value')
        text, reveal = MagicMock(), MagicMock()
        markdown_preview.render(text, source, [(start, start+len('secret-value'))], reveal)
        rendered = ''.join(call.args[1] for call in text.insert.call_args_list)
        reveal.assert_called_once_with('secret-value')
        self.assertEqual(rendered, '**' + '**\n')


if __name__ == '__main__':
    unittest.main()
