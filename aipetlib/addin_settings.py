"""Typed add-in properties and transactional journal relocation; no UI dependencies."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import uuid


def validate_schema(properties):
    if not isinstance(properties, list) or len(properties) > 32:
        raise ValueError('settings must be a list of at most 32 properties')
    result, keys = [], set()
    for prop in properties:
        if not isinstance(prop, dict):
            raise ValueError('invalid settings property')
        key = prop.get('key')
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', key) or key in keys:
            raise ValueError('settings keys must be unique lowercase identifiers')
        kind = prop.get('type', 'string')
        if kind not in ('string', 'directory', 'boolean', 'integer', 'number', 'integer-slider', 'number-slider'):
            raise ValueError(f'unsupported setting type: {kind}')
        item = dict(key=key, label=str(prop.get('label') or key)[:160], type=kind,
                    description=str(prop.get('description') or '')[:1000], default=prop.get('default'))
        if kind.endswith('-slider'):
            integer = kind == 'integer-slider'
            item.update(min=prop.get('min'), max=prop.get('max'), step=prop.get('step', 1 if integer else 0.1))
            for field in ('min', 'max', 'step'):
                value = item[field]
                if (type(value) not in ((int,) if integer else (int, float))
                        or not math.isfinite(value)):
                    raise ValueError(f'{key}: {field} must be a finite ' + ('integer' if integer else 'number'))
            if item['min'] >= item['max'] or item['step'] <= 0:
                raise ValueError(f'{key}: min must be less than max and step must be positive')
        validate_value(item, item['default'])
        result.append(item)
        keys.add(key)
    return result


def validate_value(prop, value):
    kind = prop['type']
    valid = ((kind in ('string', 'directory') and isinstance(value, str) and len(value) <= 4096
              and '\x00' not in value)
             or (kind == 'boolean' and type(value) is bool)
             or (kind in ('integer', 'integer-slider') and type(value) is int)
             or (kind in ('number', 'number-slider') and type(value) in (int, float) and math.isfinite(value)))
    if not valid:
        raise ValueError(f"Invalid value for {prop['label']} ({kind})")
    if kind.endswith('-slider') and not prop['min'] <= value <= prop['max']:
        raise ValueError(f"{prop['label']} must be between {prop['min']} and {prop['max']}")
    if kind == 'directory':
        path = Path(value).expanduser()
        if not value.strip() or not path.is_absolute():
            raise ValueError(f"{prop['label']} must be an absolute directory path")
        return str(path.resolve())
    return value


def load(home, key, schema):
    path = Path(home) / 'addin-settings' / (key + '.json')
    values = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(values, dict):
        raise ValueError('saved add-in settings must be an object')
    return {p['key']: validate_value(p, values.get(p['key'], p['default'])) for p in schema}


def save(home, key, values):
    folder = Path(home) / 'addin-settings'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (key + '.json')
    temporary = folder / (key + '-' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def relocate_journal(old, new):
    """Move both generations without overwriting. Return a rollback for a failed settings save."""
    old, new = Path(old).resolve(), Path(new).resolve()
    if old == new:
        return lambda: None
    new.mkdir(parents=True, exist_ok=True)
    names = ('events.jsonl', 'events.previous.jsonl')
    for name in names:
        if (new / name).exists():
            raise ValueError(f'Destination already contains {name}; choose another directory')
    moved = []

    def rollback():
        for name in reversed(moved):
            shutil.move(str(new / name), str(old / name))

    try:
        for name in names:
            if (old / name).exists():
                # Exclusive reservation protects against another writer creating the destination.
                with (new / name).open('xb'):
                    pass
                try:
                    shutil.move(str(old / name), str(new / name))
                except Exception:
                    (new / name).unlink(missing_ok=True)
                    raise
                moved.append(name)
    except Exception:
        rollback()
        raise
    return rollback
