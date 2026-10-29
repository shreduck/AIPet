"""Callable, standard-library-only AIPet API for add-in scripts (API v1).

Construct AIPet(message['context']). Calls atomically enqueue small JSON messages;
they never call Tk, wait for the user, or participate in permission decisions.
"""
import copy
import json
import os
import re
from datetime import datetime
from pathlib import Path
import time
import uuid


def format_markdown_report(report, timestamp=None, *, include_source=True):
    """Expand a declarative Markdown report into text plus masked character ranges.

    include_source=False produces a safe notification body with source/value placeholders.
    No templates are evaluated as code, and substitutions are never recursively expanded.
    """
    context = report.get('context') or {}
    template = report.get('markdown_template') or ''
    sensitive = {report.get('value'), report.get('decoded')}
    hook = report.get('hook_text', report.get('value', ''))
    hook_spans = report.get('hook_spans', [[0, len(hook)]] if hook else [])
    sensitive.update(hook[start:end] for start, end in hook_spans)
    parts, spans, length = [], [], 0

    def append(value, ranges=()):
        nonlocal length
        parts.append(value)
        spans.extend([length+start, length+end] for start, end in ranges)
        length += len(value)

    def escape(value):
        return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', ' '.join(str(value).splitlines()))

    def metadata(key):
        if key == 'timestamp':
            for value in (context.get('event_time'), timestamp):
                try:
                    return datetime.fromtimestamp(float(value)).astimezone().isoformat(timespec='seconds')
                except (TypeError, ValueError, OverflowError, OSError):
                    pass
            return 'Not available'
        value = context.get(key[8:]) if key.startswith('context.') else report.get(key)
        value = str(value) if value not in (None, '') else 'Not available'
        if key.startswith('context.'):
            for secret in sensitive:
                if secret:
                    value = value.replace(secret, '[censored]')
        return escape(value)

    cursor = 0
    for match in re.finditer(r'\{\{([a-z_]+(?:\.[a-z_]+)?)\}\}', template):
        append(template[cursor:match.start()])
        key = match.group(1)
        if key in ('hook_text', 'value', 'decoded'):
            if include_source:
                value = hook if key == 'hook_text' else report.get(key, '')
                append(value, hook_spans if key == 'hook_text' else [[0, len(value)]] if value else [])
            else:
                append('[Censored — reveal in AIPet]')
        else:
            append(metadata(key))
        cursor = match.end()
    append(template[cursor:])
    return ''.join(parts), spans


class AIPet:
    def __init__(self, context):
        self.context = context

    def get_settings(self):
        """Property descriptors and values captured when this delivery started."""
        values = self.context.get('settings', {})
        return copy.deepcopy([dict(p, value=values.get(p['key'], p.get('default')))
                for p in self.context.get('settings_properties', [])])

    def get_pet_position(self):
        """Delivery-time x/y, width/height, screen_id and screen bounds, or None before UI is ready."""
        return copy.deepcopy(self.context.get('pet'))

    def get_pet_screen(self):
        """Delivery-time screen id, bounds and usable work_area, or None."""
        position = self.get_pet_position()
        return position.get('screen') if position else None

    def set_settings(self, values):
        """Queue a partial update for this add-in. True means queued, not yet saved.

        AIPet validates/persists it; subsequent deliveries receive the saved values.
        Failures appear as add-in error messages.
        """
        if not isinstance(values, dict):
            raise ValueError('settings must be a dictionary')
        return self._send(kind='settings', values=values)

    def _send(self, **fields):
        """Best effort: True if queued, False on a full queue or unavailable app."""
        try:
            folder = Path(self.context['outbox'])
            folder.mkdir(parents=True, exist_ok=True)
            limit = 224 if fields.get('kind') == 'status' else 256  # leave room for user-facing alerts
            with os.scandir(folder) as entries:
                if sum(1 for entry in entries if entry.name.endswith(('.json', '.tmp'))) >= limit:
                    return False
            message = {key: self.context[key] for key in ('addin_id', 'run_id', 'agent', 'session_id')}
            message.update(api_version=1, timestamp=time.time(), **fields)
            payload = json.dumps(message, ensure_ascii=False).encode('utf-8')
            limit_bytes = 1_000_000 if isinstance(fields.get('report'), dict) and 'hook_text' in fields['report'] else 16384
            if len(payload) > limit_bytes:
                return False
            path = folder / f'{time.time_ns()}-{uuid.uuid4().hex}.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(payload)
            temporary.replace(path)
            return True
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def status(self, state, detail='', *, _preserve_attention=False):
        """Report working, stopped, or needs_attention, without opening a window."""
        if state not in ('working', 'stopped', 'needs_attention'):
            raise ValueError('state must be working, stopped, or needs_attention')
        return self._send(kind='status', state=state, detail=str(detail)[:500],
                          preserve_attention=bool(_preserve_attention))

    def _notify(self, level, title, content='', key=None, window=None, report=None):
        if window not in (None, 'main', 'pet'):
            raise ValueError('window must be main, pet, or None')
        return self._send(kind='message', level=level, title=str(title)[:160],
                          content=str(content).encode('utf-8')[:8000].decode('utf-8', errors='ignore'),
                          dedup_key=str(key or '')[:128], window=window, report=report)

    def info(self, title, content='', *, key=None, window=None):
        return self._notify('info', title, content, key, window)

    def warning(self, title, content='', *, key=None, window=None, report=None):
        return self._notify('warning', title, content, key, window, report)

    def error(self, title, content='', *, key=None, window=None, report=None):
        return self._notify('error', title, content, key, window, report)
