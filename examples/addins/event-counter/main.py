"""Copy this folder to ~/.aipet/addins/event-counter, reload, then enable in Settings.

For packaged AIPet, replace {python} in addin.json with your Python executable path.
"""
import json
from pathlib import Path
import sys


def on_event(event, context):
    """Called once per delivery, in a fresh process. Persist state in data_dir."""
    path = Path(context['data_dir']) / 'counts.json'
    counts = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    name = f"{event['agent']}:{event['event']}"
    counts[name] = counts.get(name, 0) + 1
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(counts, indent=2), encoding='utf-8')
    temporary.replace(path)
    sys.path.insert(0, context['sdk_dir'])
    from aipet_addin import AIPet
    pet = AIPet(context)
    settings = {p['key']: p['value'] for p in pet.get_settings()}
    if settings.get('report_counts'):
        pet.info('Event counted', f'{name}: {counts[name]}')


if __name__ == '__main__':
    message = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    on_event(message['event'], message['context'])
