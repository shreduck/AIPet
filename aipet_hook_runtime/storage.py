"""Shared approval-file location, including a user-selected writable folder.

The locator sits beside .aipet, so a read-only .aipet folder can be recovered
without changing hook commands. WSL readers translate Windows drive paths.
"""
import json
import os
import re
import tempfile


def locator_path(base):
    return os.path.abspath(base) + "-approval-storage.json"


def approval_path(base, default=None):
    try:
        with open(locator_path(base), encoding="utf-8") as f:
            location = json.load(f)
    except FileNotFoundError:
        return default or os.path.join(base, "auto-approve.json")
    if not isinstance(location, dict):
        raise ValueError("Invalid approval storage location")
    path = location.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("Invalid approval storage location")
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", path):
        path = "/mnt/" + path[0].lower() + "/" + path[3:].replace("\\", "/")
    if not os.path.isabs(path):
        raise ValueError("Approval storage location must be an absolute path")
    return path


def write_json(path, data):
    """Atomic, private writes; a failed replace leaves the previous file intact."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=parent,
                                         prefix=".aipet-", suffix=".tmp", delete=False) as f:
            tmp = f.name
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
