"""Bounded, thread-safe add-in presentation state. No UI or disk operations."""
import copy
import threading
import time
import uuid

LEVELS = {'info': ('i', '#2563eb', '#eff6ff'),
          'warning': ('!', '#b45309', '#fffbeb'),
          'error': ('×', '#b91c1c', '#fef2f2')}
RANK = {'info': 0, 'warning': 1, 'error': 2}


def report_fields(value):
    """Validate bounded structured data; ordinary content never contains the revealable values."""
    if not isinstance(value, dict) or value.get('warning_level') not in ('low', 'standard', 'high'):
        return None
    limits = dict(rule=100, name=160, explanation=1200, fingerprint=64, value=4096, decoded=4096)
    result = dict(warning_level=value['warning_level'], truncated=value.get('truncated') is True)
    for key, limit in limits.items():
        if key == 'decoded' and key not in value:
            continue
        field = value.get(key)
        if not isinstance(field, str) or len(field.encode('utf-8')) > limit:
            return None
        result[key] = field
    template = value.get('markdown_template')
    if isinstance(template, str) and len(template.encode('utf-8')) <= 8192:
        result['markdown_template'] = template
    source = value.get('hook_text')
    spans = value.get('hook_spans')
    if isinstance(source, str) and len(source.encode('utf-8')) <= 262144 and isinstance(spans, list) and len(spans) <= 16384:
        previous = 0
        valid = True
        for span in spans:
            if (not isinstance(span, (list, tuple)) or len(span) != 2
                    or any(type(index) is not int for index in span)
                    or not previous <= span[0] < span[1] <= len(source)):
                valid = False
                break
            previous = span[1]
        if valid and spans:
            result.update(hook_text=source, hook_spans=copy.deepcopy(spans), hook_field=str(value.get('hook_field') or '$')[:512])
    context = value.get('context')
    if isinstance(context, dict):
        keys = ('conversation_title', 'project', 'harness', 'agent', 'session_id', 'cwd', 'environment',
                'source', 'entry', 'model', 'hook_event', 'event_id', 'event_time', 'tool', 'agent_id', 'agent_name')
        result['context'] = {key: str(context[key])[:512] for key in keys if context.get(key)}
        secrets = {result['value'], result.get('decoded')}
        if 'hook_text' in result:
            secrets.update(result['hook_text'][start:end] for start, end in result['hook_spans'])
        for key, text in result['context'].items():
            for secret in secrets:
                if secret:
                    text = text.replace(secret, '[censored]')
            result['context'][key] = text
    return result


class MessageStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}

    def apply(self, message, name):
        kind = message.get('kind')
        if kind == 'message' and message.get('level') not in LEVELS:
            return
        if kind == 'status' and message.get('state') not in ('working', 'stopped', 'needs_attention'):
            return
        if kind not in ('message', 'status'):
            return
        identity = (str(message.get('addin_id', '')), str(message.get('agent', '')), str(message.get('session_id', '')))
        with self.lock:
            if identity not in self.rows:
                if len(self.rows) >= 128:
                    self.rows.pop(next(iter(self.rows)))
                self.rows[identity] = dict(id=uuid.uuid4().hex, addin_id=identity[0], agent=identity[1],
                    session_id=identity[2], name=str(name)[:80], state='stopped', level='info', messages=[], detail='')
            row = self.rows[identity]
            if kind == 'status':
                if message.get('preserve_attention') and row['state'] == 'needs_attention':
                    return
                row.update(state=message['state'], detail=str(message.get('detail') or '')[:500])
            else:
                dedup = str(message.get('dedup_key') or '')[:128]
                if dedup and any(m['dedup_key'] == dedup for m in row['messages']):
                    return
                row['messages'].append(dict(id=uuid.uuid4().hex, level=message['level'],
                    title=str(message.get('title') or '')[:160], content=str(message.get('content') or '')[:8000],
                    timestamp=time.time(), unread=True, dedup_key=dedup))
                row['messages'][-1]['window'] = message.get('window') if message.get('window') in ('main', 'pet') else None
                report = report_fields(message.get('report'))
                if report is not None:
                    row['messages'][-1]['report'] = report
                row['messages'] = row['messages'][-20:]
                level = max((m['level'] for m in row['messages'] if m['unread']), key=RANK.get)
                row.update(state='needs_attention', level=level)

    def stop_addin(self, key):
        with self.lock:
            for row in self.rows.values():
                if row['addin_id'] == key:
                    row['state'] = 'stopped'

    def acknowledge(self, ids):
        with self.lock:
            for row in self.rows.values():
                for message in row['messages']:
                    if message['id'] in ids:
                        message['unread'] = False

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(list(self.rows.values()))


def for_items(rows, items):
    """Associate interjections with the right agent/session, including compact pets."""
    identities = {('codex' if i.get('agent') == 'codex' else 'claude', str(i.get('session_id') or '')) for i in items}
    return [row for row in rows if (row['agent'], row['session_id']) in identities]


def unread_summary(rows):
    messages = [message for row in rows for message in row['messages'] if message['unread']]
    if not messages:
        return None
    return max((m['level'] for m in messages), key=RANK.get), len(messages)


class AlertCarousel:
    """An ordered queue that preserves the current alert as new messages arrive."""
    def __init__(self):
        self.entries = []
        self.seen = set()
        self.index = 0

    def add(self, rows):
        for row in rows:
            for message in row['messages']:
                key = message.get('id') or id(message)
                if key not in self.seen:
                    self.seen.add(key)
                    self.entries.append(dict(row, level=message['level'], messages=[message]))

    def current(self):
        return self.entries[self.index:self.index+1]

    def step(self, delta):
        if self.entries:
            self.index = (self.index + delta) % len(self.entries)

    def dismiss(self, rows):
        keys = {message.get('id') or id(message) for row in rows for message in row['messages']}
        current = self.current()
        self.entries[:] = [row for row in self.entries
                           if (row['messages'][0].get('id') or id(row['messages'][0])) not in keys]
        if current and current[0] in self.entries:
            self.index = self.entries.index(current[0])
        else:
            self.index = min(self.index, max(0, len(self.entries)-1))
