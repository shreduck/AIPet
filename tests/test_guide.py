import unittest
import aipetlib.config as config
import aipet_guide


class GuideTests(unittest.TestCase):
    def test_compact_addin_example_can_emit_a_warning_without_opening_ui(self):
        import json
        from pathlib import Path
        import re
        import subprocess
        import sys
        import tempfile
        guide = aipet_guide.build_addin_guide('/test-home')
        source = re.search(r'```python\n(.*?)```', guide, re.S).group(1)
        self.assertLess(len(guide.splitlines()), 85)
        with tempfile.TemporaryDirectory() as folder:
            context = dict(addin_id='my-addin', run_id='fixture', agent='codex', session_id='fixture',
                outbox=folder, sdk_dir=str(Path(__file__).resolve().parents[1]), settings={}, settings_properties=[])
            event = dict(id='test-event', data=dict(prompt='aipet demo warning'))
            result = subprocess.run([sys.executable, '-c', source], input=json.dumps(dict(event=event, context=context)),
                text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            message, = [json.loads(path.read_text(encoding='utf-8')) for path in Path(folder).glob('*.json')]
            self.assertEqual((message['kind'], message['level'], message['window']), ('message', 'warning', 'pet'))

    def test_every_setting_is_explained(self):
        missing = [k for k in config.DEFAULT_CONFIG if k not in aipet_guide.CONFIG_KEYS]
        self.assertEqual(missing, [])

    def test_guide_names_the_folder_and_current_values(self):
        cfg = dict(config.DEFAULT_CONFIG, theme="dark", size=1.5)
        text = aipet_guide.build_guide(cfg, r"C:\Users\me\.aipet", "v0.4.0")
        self.assertIn(r"C:\Users\me\.aipet", text)
        self.assertIn('| `theme` | `"dark"` |', text)
        self.assertIn("| `size` | `1.5` |", text)
        self.assertIn("auto-approve.json", text)
        self.assertIn('`precedence`: `"blacklist"` (default) or `"whitelist"`', text)

    def test_addin_guide_includes_interface_and_current_properties(self):
        text = aipet_guide.build_guide({}, '/example', addins=[dict(id='hook-journal',
            enabled=True, settings=[dict(key='directory', value='/custom/logs')])])
        for expected in ('### Add-ins:', 'pet.get_settings()', 'pet.set_settings(',
                         'settings_properties', '"default": false', '/custom/logs',
                         'events.previous.jsonl', 'rather than editing the saved settings JSON'):
            self.assertIn(expected, text)
        for expected in ('#### Add-in UI and AIPet theming', 'theme.theme_window(win)', 'theme.T',
                         'Consolas on Windows', '<<AIPetThemeChanged>>', 'SDK error severity'):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
