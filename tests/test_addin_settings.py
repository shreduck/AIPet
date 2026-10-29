"""Settings validation, SDK delivery, and live journal relocation."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from aipet_addin import AIPet
from aipetlib import addin_settings
from aipetlib.addins import AddinManager, discover


class SettingsTests(unittest.TestCase):
    def test_slider_properties_preserve_bounds_steps_and_persist_typed_values(self):
        self.script([dict(key='opacity', type='number-slider', min=0, max=1, step=0.05, default=0.8),
                     dict(key='count', type='integer-slider', min=1, max=20, default=5),
                     dict(key='ratio', type='number-slider', min=-1, max=1, default=0)])
        manager = self.manager()
        props = manager.get_settings('example')
        self.assertEqual([p['step'] for p in props], [0.05, 1, 0.1])
        self.assertEqual((props[0]['min'], props[0]['max']), (0, 1))
        manager.set_settings('example', {'opacity': 0.35, 'count': 12})
        self.assertEqual([p['value'] for p in self.manager().get_settings('example')], [0.35, 12, 0])
        for values in ({'opacity': -0.1}, {'opacity': float('inf')}, {'opacity': True},
                       {'count': 21}, {'count': 1.5}, {'count': True}):
            with self.assertRaises(ValueError):
                manager.set_settings('example', values)

    def test_slider_manifest_rejects_invalid_bounds_steps_and_defaults(self):
        base = dict(key='slider', type='number-slider', min=0, max=1, default=0.5)
        for changes in (dict(min=None), dict(max=None), dict(min=1), dict(max=-1),
                        dict(step=0), dict(step=-1), dict(step=True), dict(step=float('nan')),
                        dict(max=float('inf')), dict(default=2), dict(type='integer-slider'),
                        dict(type='integer-slider', default=0, step=0.5),
                        dict(type='integer-slider', default=0, min=0.0)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                addin_settings.validate_schema([dict(base, **changes)])

    def test_slider_editor_uses_declared_increment_and_numeric_variable(self):
        from aipetlib.ui import addin_settings as editor
        for kind, variable in (('integer-slider', 'IntVar'), ('number-slider', 'DoubleVar')):
            with self.subTest(kind=kind), patch.object(editor.tk, variable) as var, patch.object(editor.tk, 'Scale') as scale:
                prop = dict(type=kind, min=0, max=10, step=2 if kind == 'integer-slider' else 0.25, value=4)
                result = editor._slider('parent', 'master', prop)
                self.assertIs(result, var.return_value)
                var.assert_called_once_with('master', value=4)
                options = scale.call_args.kwargs
                self.assertEqual((options['from_'], options['to'], options['resolution']), (0, 10, prop['step']))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def manager(self, enabled=None):
        manager = AddinManager(self.home, enabled)
        def stop():
            workers = list(manager.workers.values())
            manager.stop()
            for worker in workers:
                worker.join(3)
        self.addCleanup(stop)
        return manager

    def script(self, schema, body='pass'):
        folder = self.home / 'addins/example'
        folder.mkdir(parents=True)
        (folder / 'main.py').write_text(body, encoding='utf-8')
        (folder / 'addin.json').write_text(json.dumps(dict(api_version=1, name='Example',
            command=[sys.executable, '{addin_dir}/main.py'], events=['*'], settings=schema)), encoding='utf-8')

    def event(self, i=0):
        return dict(api_version=1, event='Stop', agent='claude', session_id='s', id=str(i), timestamp=time.time())

    def test_typed_properties_partial_updates_and_restart(self):
        self.script([dict(key='verbose', type='boolean', default=False),
                     dict(key='limit', type='integer', default=10)])
        manager = self.manager()
        self.assertEqual(manager.get_settings('example')[0]['value'], False)
        props = manager.set_settings('example', {'verbose': True})
        self.assertEqual([p['value'] for p in props], [True, 10])
        props[0]['value'] = False
        self.assertTrue(manager.get_settings('example')[0]['value'])
        restarted = self.manager()
        self.assertEqual([p['value'] for p in restarted.get_settings('example')], [True, 10])
        for values in ({'verbose': 1}, {'limit': True}, {'unknown': 'value'}, []):
            with self.assertRaises(ValueError):
                manager.set_settings('example', values)
        self.assertEqual(manager.workers, {}, 'settings must not enable scripts')

    def test_invalid_manifest_properties_are_not_discovered(self):
        self.script([dict(key='enabled', type='boolean', default='false')])
        self.assertNotIn('example', discover(self.home))
        for schema in ([dict(key='x', type='number', default=float('nan'))],
                       [dict(key='x', type='string', default=''), dict(key='x', type='string', default='')],
                       [dict(key='path', type='directory', default='relative')]):
            with self.assertRaises(ValueError):
                addin_settings.validate_schema(schema)

    def test_journal_moves_both_files_and_continues_at_new_directory(self):
        manager = self.manager({'hook-journal': True})
        worker = manager.workers['hook-journal']
        worker.execute(self.event())
        old = self.home / 'addin-data/hook-journal'
        (old / 'events.previous.jsonl').write_text('previous\n', encoding='utf-8')
        new = self.home / 'custom é logs'
        manager.set_settings('hook-journal', {'directory': str(new)})
        self.assertFalse((old / 'events.jsonl').exists())
        self.assertFalse((old / 'events.previous.jsonl').exists())
        self.assertEqual((new / 'events.previous.jsonl').read_text(), 'previous\n')
        worker.execute(self.event(1))
        self.assertEqual([json.loads(line)['id'] for line in (new / 'events.jsonl').read_text().splitlines()], ['0', '1'])
        manager.configure({'hook-journal': False})
        restarted = self.manager({'hook-journal': True})
        restarted.workers['hook-journal'].execute(self.event(2))
        self.assertEqual(len((new / 'events.jsonl').read_text().splitlines()), 3)
        manager.set_settings('hook-journal', {'directory': str(old)})
        self.assertTrue((old / 'events.jsonl').exists())

    def test_collision_and_save_failure_keep_old_settings_and_logs(self):
        manager = self.manager({'hook-journal': True})
        worker = manager.workers['hook-journal']
        worker.execute(self.event())
        old = self.home / 'addin-data/hook-journal'
        new = self.home / 'occupied'
        new.mkdir()
        (new / 'events.jsonl').write_text('existing')
        with self.assertRaisesRegex(ValueError, 'already contains'):
            manager.set_settings('hook-journal', {'directory': str(new)})
        self.assertEqual((new / 'events.jsonl').read_text(), 'existing')
        with patch.object(addin_settings, 'save', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                manager.set_settings('hook-journal', {'directory': str(self.home / 'empty')})
        self.assertTrue((old / 'events.jsonl').exists())
        self.assertFalse((self.home / 'empty/events.jsonl').exists())
        self.assertEqual(worker.settings['directory'], str(old.resolve()))
        worker.execute(self.event(1))
        self.assertEqual(len((old / 'events.jsonl').read_text().splitlines()), 2)

    def test_second_move_failure_rolls_back_first_file(self):
        manager = self.manager({'hook-journal': True})
        worker = manager.workers['hook-journal']
        worker.execute(self.event())
        old = self.home / 'addin-data/hook-journal'
        (old / 'events.previous.jsonl').write_text('previous')
        move = addin_settings.shutil.move
        def fail_previous(source, destination):
            if Path(source).resolve() == (old / 'events.previous.jsonl').resolve():
                raise OSError('cannot move previous')
            return move(source, destination)
        with patch.object(addin_settings.shutil, 'move', side_effect=fail_previous):
            with self.assertRaises(OSError):
                manager.set_settings('hook-journal', {'directory': str(self.home / 'new')})
        self.assertTrue((old / 'events.jsonl').exists())
        self.assertEqual((old / 'events.previous.jsonl').read_text(), 'previous')
        self.assertEqual(list((self.home / 'new').iterdir()), [])

    def test_active_writes_and_directory_changes_share_lock_even_after_disable(self):
        manager = self.manager({'hook-journal': True})
        worker = manager.workers['hook-journal']
        worker.execute(self.event())
        started, done = threading.Event(), threading.Event()
        errors = []
        def update():
            started.set()
            try:
                manager.set_settings('hook-journal', {'directory': str(self.home / 'new')})
            except Exception as error:
                errors.append(error)
            finally:
                done.set()
        with worker.execution_lock:
            manager.configure({})
            thread = threading.Thread(target=update)
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(done.wait(0.05))
        thread.join(3)
        worker.join(3)
        self.assertTrue(done.is_set())
        self.assertEqual(errors, [])
        self.assertTrue((self.home / 'new/events.jsonl').exists())

    def test_sdk_get_set_and_next_process_receives_saved_values(self):
        self.script([dict(key='verbose', type='boolean', default=False)], '''import json, sys
m = json.loads(sys.stdin.buffer.read().decode('utf-8'))
sys.path.insert(0, m['context']['sdk_dir'])
from aipet_addin import AIPet
p = AIPet(m['context'])
if not p.get_settings()[0]['value']:
    assert p.set_settings({'verbose': True})
else:
    p.info('Saved setting received')
''')
        manager = self.manager({'example': True})
        worker = manager.workers['example']
        worker.execute(self.event())
        manager.drain_output()
        self.assertTrue(manager.get_settings('example')[0]['value'])
        worker.execute(self.event(1))
        manager.drain_output()
        self.assertEqual(manager.messages.snapshot()[0]['messages'][0]['title'], 'Saved setting received')
        api = AIPet(worker.context(self.event()))
        api.set_settings({'verbose': 'invalid'})
        manager.drain_output()
        self.assertTrue(manager.get_settings('example')[0]['value'])
        manager.drain_output()  # error report is delivered on the next pass
        self.assertTrue(any(m['title'] == 'Settings change failed'
                            for row in manager.messages.snapshot() for m in row['messages']))
        api.set_settings({'verbose': False})
        manager.configure({})
        manager.drain_output()
        self.assertTrue(manager.get_settings('example')[0]['value'], 'disabled workers cannot change settings')

    def test_ui_requests_report_failure_without_changing_saved_settings(self):
        manager = self.manager()
        result = []
        manager.request_set_settings('hook-journal', {'directory': 'relative'}, result.append)
        manager.apply_settings_requests()
        self.assertIn('absolute', result[0])
        self.assertFalse((self.home / 'addin-settings/hook-journal.json').exists())


if __name__ == '__main__':
    unittest.main()
