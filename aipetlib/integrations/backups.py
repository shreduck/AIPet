"""Byte-exact backup creation, retention, restoration and old-location migration."""
import glob
import os
import re
import shutil
import sys
import time
from datetime import datetime
from . import locations as hook_locations
from . import protocol as hook_protocol
from . import settings as hook_settings
from . import targets as hook_targets

IS_MAC = sys.platform == "darwin"


KEEP_BACKUPS = 20  # newest N kept, plus the oldest (original) one


def legacy_dirs():
    """Old install locations that hold backups: the real AppData folder and any MSIX-private copy."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return []
    found = [os.path.join(base, "ClaudePet")]  # pre-rename (Claude Pet) locations
    found += glob.glob(os.path.join(base, "Packages", "*", "LocalCache", "Local", "ClaudePet"))
    return [d for d in found if os.path.isdir(os.path.join(d, "backups"))]


def migrate_legacy_backups():
    """Copy backups from the old locations into ~/.aipet/backups (never deletes or overwrites).

    Returns the number of files copied. Runs until a legacy folder has been seen once.
    """
    marker = os.path.join(hook_locations.BACKUP_ROOT, ".legacy-migrated")
    if os.path.exists(marker):
        return 0
    copied, seen = 0, False
    for legacy in legacy_dirs():
        seen = True
        root = os.path.join(legacy, "backups")
        for target in os.listdir(root):
            src_dir = os.path.join(root, target)
            if not os.path.isdir(src_dir):
                continue
            dst_dir = os.path.join(hook_locations.BACKUP_ROOT, target)
            os.makedirs(dst_dir, exist_ok=True)
            for name in os.listdir(src_dir):
                dst = os.path.join(dst_dir, name)
                if not os.path.exists(dst):
                    shutil.copy2(os.path.join(src_dir, name), dst)
                    copied += 1
    if seen:
        os.makedirs(hook_locations.BACKUP_ROOT, exist_ok=True)
        open(marker, "w").close()
    return copied


def backup_dir(key):
    return os.path.join(hook_locations.BACKUP_ROOT, re.sub(r"[^A-Za-z0-9_.-]", "_", key.replace(":", "-")))


def _save_backup(key, exists, text, reason):
    d = backup_dir(key)
    os.makedirs(d, exist_ok=True)
    now = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"
    slug = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-") or "backup"
    ext = ".json" if exists else ".absent"
    n = 0
    while True:  # never overwrite a backup made in the same millisecond
        path = os.path.join(d, f"{stamp}{'' if n == 0 else f'-{n:02d}'}__{slug}{ext}")
        if not os.path.exists(path):
            break
        n += 1
    with open(path, "xb") as f:
        f.write(text.encode("utf-8") if exists else b"")
    _prune(d)
    return path


def make_backup(key, reason="manual"):
    exists, text = hook_targets.read_raw(key)
    return _save_backup(key, exists, text, reason)


def _backup_files(d):
    try:
        return sorted(f for f in os.listdir(d) if f.endswith((".json", ".absent")))
    except FileNotFoundError:
        return []


def _prune(d):
    files = _backup_files(d)
    if len(files) > KEEP_BACKUPS + 1:
        for f in files[1:-KEEP_BACKUPS]:  # keep files[0] (original) and the newest N
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass


def list_backups(key):
    """Newest first: [{path, when, reason, absent, size, pet_hooks, valid_json, original}]."""
    d = backup_dir(key)
    files = _backup_files(d)
    out = []
    for i, name in enumerate(files):
        path = os.path.join(d, name)
        stem, ext = os.path.splitext(name)
        stamp, _, reason = stem.partition("__")
        try:
            when = datetime.strptime(stamp[:15], "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            when = stamp
        absent = ext == ".absent"
        text = ""
        if not absent:
            try:
                with open(path, "rb") as f:
                    text = f.read().decode("utf-8", "replace")
            except OSError:
                continue
        try:
            hook_settings.parse_settings(text)
            valid = True
        except Exception:
            valid = False
        out.append({
            "path": path, "when": when, "reason": reason.replace("-", " ") or "backup",
            "absent": absent, "size": len(text.encode("utf-8")),
            "pet_hooks": bool(hook_protocol.MARKER.search(text)), "valid_json": valid, "original": i == 0,
        })
    return list(reversed(out))


def restore_backup(key, path):
    absent = path.endswith(".absent")
    text = None
    if not absent:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
    exists, current = hook_targets.read_raw(key)
    if (not absent and exists and current == text) or (absent and not exists):
        return f"{hook_targets.describe(key)} already matches that backup - nothing changed."
    _save_backup(key, exists, current, "before restore")
    hook_targets.write_raw(key, text)
    what = "removed (it didn't exist at that point)" if absent else "restored"
    return f"{hook_targets.describe(key)} {what}.\nThe previous version was saved as a 'before restore' backup."
