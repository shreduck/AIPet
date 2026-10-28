"""Best-effort hook event delivery. No add-in code runs inside a permission hook."""
import json
import os
import time
import uuid

MAX_EVENTS = 256
MAX_BYTES = 262144


def publish(base, agent, event, data):
    """Drop events on overflow or IO errors; never change the hook's decision."""
    try:
        with open(os.path.join(base, 'config.json'), encoding='utf-8') as stream:
            enabled = json.load(stream).get('addins')
        if not isinstance(enabled, dict) or not any(v is True for v in enabled.values()):
            return
        now = time.time()
        envelope = {'api_version': 1, 'id': uuid.uuid4().hex, 'timestamp': now,
                    'agent': agent, 'event': event, 'session_id': str(data.get('session_id') or 'unknown'),
                    'data': data}
        payload = json.dumps(envelope, ensure_ascii=False).encode('utf-8')
        if len(payload) > MAX_BYTES:
            return
        folder = os.path.join(base, 'addin-events')
        os.makedirs(folder, exist_ok=True)
        with os.scandir(folder) as entries:
            if sum(1 for entry in entries if entry.name.endswith(('.json', '.tmp'))) >= MAX_EVENTS:
                return
        path = os.path.join(folder, f'{time.time_ns()}-{envelope["id"]}.json')
        with open(path + '.tmp', 'wb') as stream:
            stream.write(payload)
        os.replace(path + '.tmp', path)
    except Exception:
        pass
