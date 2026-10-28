"""Add-ins are opt-in observers; failures never enter the approval path."""
import json
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from aipetlib.addins import AddinManager, Worker, discover, journal
from aipet_hook_runtime import addins


class AddinTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)

    def event(self, name='Stop'):
        return dict(api_version=1, event=name, timestamp=time.time(), agent='claude',
                    id='fixture', session_id='test', data={'hook_event_name': name})

    def config(self, enabled):
        (self.home / 'config.json').write_text(json.dumps({'addins': enabled}), encoding='utf-8')

    def script(self, key='example', body='pass', **overrides):
        folder = self.home / 'addins' / key
        folder.mkdir(parents=True)
        (folder / 'main.py').write_text(body, encoding='utf-8')
        spec = dict(api_version=1, command=[sys.executable, '{addin_dir}/main.py'], events=['Stop'])
        spec.update(overrides)
        (folder / 'addin.json').write_text(json.dumps(spec), encoding='utf-8')
        return folder

    def test_publishing_is_opt_in_bounded_and_preserves_raw_payload(self):
        self.config({'hook-journal': False})
        addins.publish(str(self.home), 'codex', 'Stop', {'session_id': 'test'})
        self.assertFalse((self.home / 'addin-events').exists())
        self.config({'hook-journal': True})
        data = {'session_id': 'test', 'tool_input': {'command': 'echo é'}}
        addins.publish(str(self.home), 'codex', 'Stop', data)
        files = list((self.home / 'addin-events').glob('*.json'))
        self.assertEqual(len(files), 1)
        event = json.loads(files[0].read_text(encoding='utf-8'))
        self.assertEqual((event['data'], event['agent'], event['api_version']), (data, 'codex', 1))
        with patch.object(addins, 'MAX_EVENTS', 1):
            addins.publish(str(self.home), 'claude', 'Stop', data)
        addins.publish(str(self.home), 'claude', 'Stop', {'huge': 'x' * addins.MAX_BYTES})
        self.assertEqual(len(list((self.home / 'addin-events').glob('*.json'))), 1)

    def test_discovery_never_executes_scripts_and_rejects_invalid_manifests(self):
        marker = self.home / 'executed'
        self.script(body=f'open({str(marker)!r}, "w").close()')
        self.script(key='bad', api_version=2)
        catalog = discover(str(self.home))
        self.assertEqual(set(catalog), {'hook-journal', 'token-guard', 'example'})
        self.assertFalse(marker.exists())
        manager = AddinManager(self.home, {})
        self.addCleanup(manager.stop)
        self.assertEqual(manager.workers, {})

    def test_example_runs_through_stdin_contract_with_unicode(self):
        source = Path(__file__).resolve().parents[1] / 'examples/addins/event-counter'
        folder = self.home / 'addins/event-counter'
        shutil.copytree(source, folder)
        worker = Worker(str(self.home), 'event-counter', discover(str(self.home))['event-counter'])
        self.addCleanup(worker.stop)
        worker.execute(self.event())
        counts = self.home / 'addin-data/event-counter/counts.json'
        self.assertEqual(json.loads(counts.read_text()), {'claude:Stop': 1})
        worker.execute(self.event())
        self.assertEqual(json.loads(counts.read_text()), {'claude:Stop': 2})

    def test_timeout_and_crash_are_reported_without_leaving_a_process(self):
        self.script(body='import time; time.sleep(10)', timeout_seconds=0.1)
        worker = Worker(str(self.home), 'example', discover(str(self.home))['example'])
        self.addCleanup(worker.stop)
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            worker.execute(self.event())
        self.assertIsNone(worker.process)
        (self.home / 'addins/example/main.py').write_text('raise RuntimeError("fixture")')
        worker.spec['timeout'] = 5
        with self.assertRaisesRegex(RuntimeError, 'exited with code'):
            worker.execute(self.event())

    def test_filter_queue_limit_and_disabled_worker_discard(self):
        self.script()
        worker = Worker(str(self.home), 'example', discover(str(self.home))['example'])
        worker.submit(self.event('SessionStart'))
        self.assertTrue(worker.pending.empty())
        for _ in range(20):
            worker.submit(self.event())
        self.assertEqual(worker.pending.qsize(), 16)
        worker.stop()
        worker.pending = queue.Queue()
        worker.submit(self.event())
        self.assertTrue(worker.pending.empty())

    def test_dispatch_ignores_stale_and_malformed_events_and_cleans_spool(self):
        folder = self.home / 'addin-events'
        folder.mkdir()
        manager = AddinManager(self.home)
        self.addCleanup(manager.stop)
        for name, data in [('fresh', self.event()), ('stale', dict(self.event(), timestamp=time.time()-61)),
                           ('bad-version', dict(self.event(), api_version=2))]:
            (folder / f'{name}.json').write_text(json.dumps(data))
        (folder / 'broken.json').write_text('{')
        with patch.object(manager, 'dispatch') as dispatch:
            manager.drain()
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(list(folder.iterdir()), [])

    def test_live_enable_disable_and_builtin_rotation(self):
        manager = AddinManager(self.home)
        self.addCleanup(manager.stop)
        manager.configure({'hook-journal': True})
        worker = manager.workers['hook-journal']
        manager.configure({'hook-journal': False})
        self.assertTrue(worker.stopped.is_set())
        self.assertEqual(manager.workers, {})
        worker.join(2)
        context = {'data_dir': str(self.home / 'addin-data/hook-journal')}
        journal(self.event(), context)
        path = Path(context['data_dir']) / 'events.jsonl'
        path.write_text('x' * 1_000_000)
        journal(self.event(), context)
        self.assertTrue((path.parent / 'events.previous.jsonl').exists())
        self.assertEqual(json.loads(path.read_text())['event'], 'Stop')

    def test_slow_script_does_not_block_journal_and_disable_kills_script(self):
        self.script(body='import time; time.sleep(20)', timeout_seconds=30)
        manager = AddinManager(self.home, {'example': True, 'hook-journal': True})
        self.addCleanup(manager.stop)
        worker = manager.workers['example']
        journal_worker = manager.workers['hook-journal']
        manager.dispatch(self.event())
        path = self.home / 'addin-data/hook-journal/events.jsonl'
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (worker.process is None or not path.exists()):
            time.sleep(0.01)
        self.assertTrue(path.exists(), 'independent built-in never received the event')
        with worker.lock:
            process = worker.process
        self.assertIsNotNone(process, 'script was not started')
        manager.configure({})
        worker.join(3)
        journal_worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertIsNotNone(process.poll(), 'disabled script was left running')
