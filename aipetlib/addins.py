"""Versioned hook observers: trusted built-ins and isolated user commands.

Commands receive one JSON envelope on stdin per invocation. Their output never
participates in permission decisions. Importing/discovering an add-in executes no code.
"""
import json
import copy
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

from aipet_addin import AIPet
from aipet_token_guard import on_event as token_guard
from .addin_messages import MessageStore
from . import addin_settings

from .runtime import diagnostics

BUILTINS = {'hook-journal': {
    'name': 'Hook journal', 'description': 'Save hook events locally in addin-data/hook-journal/events.jsonl.',
    'events': ['*'], 'builtin': True},
    'token-guard': {'name': 'Token guard', 'description': 'Detect likely credentials in hook data and alert with values hidden.',
                    'events': ['*'], 'builtin': True}}


def discover(home):
    """Load data-only manifests. IDs are folder names and cannot shadow built-ins."""
    catalog = {key: dict(value) for key, value in BUILTINS.items()}
    catalog['hook-journal']['settings'] = [dict(key='directory', label='Log directory', type='directory',
        default=str((Path(home) / 'addin-data' / 'hook-journal').resolve()),
        description='Moves the current and previous journal files to this directory.')]
    root = Path(home) / 'addins'
    try:
        folders = sorted(root.iterdir()) if root.is_dir() else []
    except OSError as error:
        diagnostics.log_error(f'add-in discovery: {error}', home=home)
        return catalog
    for folder in folders:
        if not folder.is_dir() or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', folder.name) or folder.name in catalog:
            continue
        manifest = folder / 'addin.json'
        try:
            if manifest.stat().st_size > 16384:
                raise ValueError('manifest exceeds 16 KiB')
            spec = json.loads(manifest.read_text(encoding='utf-8'))
            command, events = spec.get('command'), spec.get('events', ['*'])
            if type(spec.get('api_version')) is not int or spec['api_version'] != 1:
                raise ValueError('api_version must be 1')
            if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
                raise ValueError('command must be a nonempty array of arguments')
            if not isinstance(events, list) or not all(isinstance(x, str) for x in events):
                raise ValueError('events must be an array of event names')
            timeout = float(spec.get('timeout_seconds', 5))
            if not 0.1 <= timeout <= 30:
                raise ValueError('timeout_seconds must be between 0.1 and 30')
            catalog[folder.name] = dict(name=str(spec.get('name') or folder.name)[:80],
                description=str(spec.get('description') or 'User script; runs with your account permissions.')[:500],
                command=command, events=events, timeout=timeout, folder=str(folder.resolve()), builtin=False,
                settings=addin_settings.validate_schema(spec.get('settings', [])))
        except (OSError, ValueError, TypeError, AttributeError) as error:
            diagnostics.log_error(f'add-in {folder.name}: {error}', home=home)
    return catalog


def journal(event, context):
    """First built-in: a bounded, local raw-event journal (one previous file kept)."""
    folder = Path(context.get('settings', {}).get('directory', context['data_dir']))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'events.jsonl'
    if path.exists() and path.stat().st_size >= 1_000_000:
        os.replace(path, folder / 'events.previous.jsonl')
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')


BUILTIN_HANDLERS = {'hook-journal': journal, 'token-guard': token_guard}


class Worker(threading.Thread):
    """One bounded queue per add-in; scripts cannot block dispatch to other workers."""
    def __init__(self, home, key, spec, settings=None, execution_lock=None, location_provider=None, session_provider=None):
        super().__init__(name=f'AIPet add-in {key}', daemon=True)
        self.home, self.key, self.spec = home, key, spec
        # Provision on dispatch's thread before starting concurrent workers.
        (Path(self.home) / 'addin-data' / self.key).mkdir(parents=True, exist_ok=True)
        self.pending = queue.Queue(maxsize=16)
        self.stopped = threading.Event()
        self.lock, self.process = threading.Lock(), None
        self.run_id = uuid.uuid4().hex
        self.execution_lock = execution_lock if execution_lock is not None else threading.RLock()
        self.settings = dict(settings or {})
        self.location_provider = location_provider
        self.session_provider = session_provider

    def context(self, event):
        return {'addin_id': self.key, 'data_dir': str(Path(self.home) / 'addin-data' / self.key),
                'outbox': str(Path(self.home) / 'addin-outbox'), 'run_id': self.run_id,
                'agent': str(event.get('agent') or ''), 'session_id': str(event.get('session_id') or ''),
                'sdk_dir': str(Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))),
                'settings': dict(self.settings), 'settings_properties': self.spec.get('settings', []),
                'pet': self.location_provider(event) if self.location_provider else None,
                'session': self.session_provider(event) if self.session_provider else {}}

    def submit(self, event):
        if self.stopped.is_set() or not ('*' in self.spec['events'] or event['event'] in self.spec['events']):
            return
        try:
            self.pending.put_nowait(event)
        except queue.Full:
            diagnostics.log_error(f'add-in {self.key}: queue full; events dropped', home=self.home)

    def stop(self):
        self.stopped.set()
        with self.lock:
            if self.process is not None:
                try:
                    self.process.kill()
                except OSError:
                    pass

    def execute(self, event):
        with self.execution_lock:
            if not self.stopped.is_set():
                self._execute_locked(event)

    def _execute_locked(self, event):
        context = self.context(event)
        api = AIPet(context)
        api.status('working', _preserve_attention=True)
        try:
            self._execute(event, context)
        finally:
            api.status('stopped', _preserve_attention=True)

    def _execute(self, event, context):
        if self.spec['builtin']:
            BUILTIN_HANDLERS[self.key](event, context)
            return
        command = []
        for argument in self.spec['command']:
            if '{python}' in argument and getattr(sys, 'frozen', False):
                raise ValueError('packaged AIPet requires an explicit Python interpreter in command')
            command.append(argument.replace('{addin_dir}', self.spec['folder']).replace('{python}', sys.executable))
        payload = json.dumps({'event': event, 'context': context}, ensure_ascii=False).encode('utf-8')
        with self.lock:
            if self.stopped.is_set():
                return
            process = self.process = subprocess.Popen(command, cwd=self.spec['folder'], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            process.communicate(payload, timeout=self.spec['timeout'])
            if process.returncode and not self.stopped.is_set():
                raise RuntimeError(f'script exited with code {process.returncode}')
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError('script timed out') from None
        finally:
            with self.lock:
                self.process = None

    def run(self):
        while not self.stopped.is_set():
            try:
                event = self.pending.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if not self.stopped.is_set():
                    self.execute(event)
            except Exception as error:
                diagnostics.log_error(f'add-in {self.key}: {error}', home=self.home)
                AIPet(self.context(event)).error('Add-in failed', str(error))


class AddinManager(threading.Thread):
    """Consumes hook spool files away from Tk. Changes to enablement apply live."""
    def __init__(self, home, enabled=None):
        super().__init__(name='AIPet add-in dispatch', daemon=True)
        self.home = str(home)
        self.lock, self.stopped = threading.RLock(), threading.Event()
        self.catalog, self.workers = {}, {}
        self._catalog_snapshot = []
        self._settings = {}
        self._settings_snapshot = {}
        self._pet_locations = (None, {})
        self._session_context = {}
        self._execution_locks = {}
        self.settings_requests = queue.Queue(maxsize=16)
        self.messages = MessageStore()
        self.config_requests = queue.Queue(maxsize=1)
        self.configure(enabled or {})

    def configure(self, enabled):
        catalog = discover(self.home)
        with self.lock:
            for key, spec in catalog.items():
                if key in self._settings and spec.get('settings', []) == self.catalog.get(key, {}).get('settings', []):
                    continue
                try:
                    self._settings[key] = addin_settings.load(self.home, key, spec.get('settings', []))
                except (OSError, ValueError, TypeError) as error:
                    diagnostics.log_error(f'add-in {key} settings: {error}', home=self.home)
                    self._settings[key] = {p['key']: p['default'] for p in spec.get('settings', [])}
            for key, worker in list(self.workers.items()):
                if not isinstance(enabled, dict) or enabled.get(key) is not True or catalog.get(key) != worker.spec:
                    worker.stop()
                    self.messages.stop_addin(key)
                    del self.workers[key]
            self.catalog = catalog
            self._catalog_snapshot = [(key, dict(spec)) for key, spec in catalog.items()]
            if isinstance(enabled, dict) and not self.stopped.is_set():
                for key, spec in catalog.items():
                    if enabled.get(key) is True and key not in self.workers:
                        execution_lock = self._execution_locks.setdefault(key, threading.RLock())
                        worker = self.workers[key] = Worker(self.home, key, spec, self._settings[key], execution_lock,
                                                           self.pet_location, self.session_context)
                        worker.start()
            self._publish_settings()

    def entries(self):
        # UI reads a published snapshot, never the lock used during process startup/stop.
        return list(self._catalog_snapshot)

    def set_pet_locations(self, default, sessions):
        """Publish plain snapshots from Tk; never query widgets in a worker."""
        self._pet_locations = copy.deepcopy((default, sessions))

    def pet_location(self, event):
        default, sessions = self._pet_locations
        return copy.deepcopy(sessions.get((str(event.get('agent') or ''), str(event.get('session_id') or '')), default))

    def set_session_context(self, sessions):
        self._session_context = copy.deepcopy(sessions)

    def session_context(self, event):
        return copy.deepcopy(self._session_context.get((str(event.get('agent') or ''), str(event.get('session_id') or '')), {}))

    def get_settings(self, key):
        """Return editable property descriptors including current values; no disk IO."""
        return [dict(p) for p in self._settings_snapshot[key]]

    def _publish_settings(self):
        self._settings_snapshot = {key: [dict(p, value=self._settings[key][p['key']])
            for p in spec.get('settings', [])] for key, spec in self.catalog.items()}

    def set_settings(self, key, values):
        """Validate and persist a partial update. Call from a background thread."""
        with self.lock:
            schema = {p['key']: p for p in self.catalog[key].get('settings', [])}
            if not isinstance(values, dict) or values.keys() - schema.keys():
                raise ValueError('Unknown add-in setting')
            updated = dict(self._settings[key])
            updated.update({k: addin_settings.validate_value(schema[k], v) for k, v in values.items()})
            worker = self.workers.get(key)
            with self._execution_locks.setdefault(key, threading.RLock()):
                rollback = lambda: None
                if key == 'hook-journal':
                    rollback = addin_settings.relocate_journal(self._settings[key]['directory'], updated['directory'])
                try:
                    addin_settings.save(self.home, key, updated)
                except Exception:
                    rollback()
                    raise
                self._settings[key] = updated
                if worker:
                    worker.settings = dict(updated)
                self._publish_settings()
        return self.get_settings(key)

    def request_set_settings(self, key, values, callback):
        """Queue a UI edit. callback(error) runs on dispatch's thread; None means success."""
        try:
            self.settings_requests.put_nowait((key, dict(values), callback))
        except queue.Full:
            callback('Too many pending settings changes; try again shortly.')

    def apply_settings_requests(self):
        try:
            key, values, callback = self.settings_requests.get_nowait()
        except queue.Empty:
            return
        try:
            self.set_settings(key, values)
        except Exception as error:
            callback(str(error))
        else:
            callback(None)

    def request_configure(self, enabled):
        """UI entry point: discovery, file IO and stopping workers happen on dispatch's thread."""
        try:
            self.config_requests.get_nowait()
        except queue.Empty:
            pass
        self.config_requests.put_nowait(dict(enabled) if isinstance(enabled, dict) else {})

    def drain_output(self):
        folder = Path(self.home) / 'addin-outbox'
        if not folder.is_dir():
            return
        for path in sorted(folder.glob('*.json'))[:256]:
            try:
                size = path.stat().st_size
                if size > 1_000_000:
                    continue
                message = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(message, dict) or message.get('api_version') != 1:
                    continue
                if size > 16384 and not (message.get('kind') == 'message'
                        and isinstance(message.get('report'), dict) and 'hook_text' in message['report']):
                    continue
                worker = self.workers.get(message.get('addin_id'))
                if worker and not worker.stopped.is_set() and message.get('run_id') == worker.run_id:
                    if message.get('kind') == 'settings':
                        try:
                            self.set_settings(worker.key, message.get('values'))
                        except (OSError, ValueError, TypeError) as error:
                            AIPet(worker.context({})).error('Settings change failed', str(error))
                    else:
                        if isinstance(message.get('report'), dict):
                            report_context = message['report'].setdefault('context', {})
                            if isinstance(report_context, dict):
                                for key, value in self.session_context(message).items():
                                    if not report_context.get(key):
                                        report_context[key] = value
                        self.messages.apply(message, worker.spec['name'])
            except (OSError, ValueError, TypeError):
                pass
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
        for path in folder.glob('*.tmp'):
            try:
                if time.time() - path.stat().st_mtime > 60:
                    path.unlink()
            except OSError:
                pass

    def dispatch(self, event):
        with self.lock:
            for worker in self.workers.values():
                worker.submit(event)

    def drain(self):
        folder = Path(self.home) / 'addin-events'
        if not folder.is_dir():
            return
        for path in sorted(folder.glob('*.json'))[:256]:
            try:
                if path.stat().st_size > 262144:
                    continue
                event = json.loads(path.read_text(encoding='utf-8'))
                if (isinstance(event, dict) and event.get('api_version') == 1
                        and isinstance(event.get('event'), str)
                        and isinstance(event.get('timestamp'), (int, float))
                        and 0 <= time.time() - event['timestamp'] < 60):
                    self.dispatch(event)
            except (OSError, ValueError, TypeError):
                pass
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
        # A killed hook can leave an incomplete temporary file; reclaim its slot.
        for path in folder.glob('*.tmp'):
            try:
                if time.time() - path.stat().st_mtime > 60:
                    path.unlink()
            except OSError:
                pass

    def stop(self):
        self.stopped.set()
        with self.lock:
            for worker in self.workers.values():
                worker.stop()
                self.messages.stop_addin(worker.key)
            self.workers.clear()

    def run(self):
        while not self.stopped.is_set():
            try:
                try:
                    self.configure(self.config_requests.get_nowait())
                except queue.Empty:
                    pass
                self.apply_settings_requests()
                self.drain()
                self.drain_output()
            except Exception as error:
                diagnostics.log_error(f'add-in dispatch: {error}', home=self.home)
            self.stopped.wait(0.25)
