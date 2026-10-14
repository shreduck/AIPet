"""Independent hook deployment and atomic file synchronization."""
import os
import shutil
import sys
import time
from . import locations as hook_locations

IS_MAC = sys.platform == "darwin"


def _same_file(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ca, cb = fa.read(1 << 20), fb.read(1 << 20)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


def _sync_file(src, dst):
    """Copy src over dst only if different, via a temp file + atomic replace, so a hook that
    is executing right now never sees a half-written or missing file."""
    if os.path.exists(dst) and _same_file(src, dst):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".new"
    shutil.copyfile(src, tmp)
    shutil.copymode(src, tmp)  # keep the executable bit (macOS hook binary)
    try:
        os.replace(tmp, dst)
        return
    except OSError:
        pass
    # Windows: a running hook's exe / DLLs can't be overwritten, but they can be renamed. Move the busy copy aside
    # (the running hook keeps using it) and put the new one in its place; the aside copy is deleted on a later sync.
    aside = f"{dst}.old-{os.getpid()}-{int(time.time())}"
    try:
        os.replace(dst, aside)
        os.replace(tmp, dst)
    except OSError:  # still busy: keep the working copy, retry on next start
        if not os.path.exists(dst) and os.path.exists(aside):
            try:
                os.replace(aside, dst)  # never leave the hook missing
            except OSError:
                pass
        try:
            os.remove(tmp)
        except OSError:
            pass


def _deploy_shared_hook():
    """Windows folder build: aipet-hook.exe sits next to AIPet.exe and shares its runtime (_internal). Copy it plus
    the runtime files it needs (listed in hook_files.txt at build time) to ~/.aipet/bin/hook, the same layout the
    separately built hook had, so installed hooks keep working wherever the AIPet folder is moved or deleted."""
    manifest = hook_locations.resource_path("hook_files.txt")
    exe = os.path.join(os.path.dirname(sys.executable), "aipet-hook.exe")
    if not (getattr(sys, "frozen", False) and os.path.isfile(manifest) and os.path.isfile(exe)):
        return
    with open(manifest, encoding="utf-8") as f:
        names = [ln.strip() for ln in f if ln.strip()]
    target = os.path.join(hook_locations.INSTALL_DIR, "hook")
    _sync_file(exe, os.path.join(target, "aipet-hook.exe"))
    runtime = hook_locations.resource_path()  # the folder build's _internal
    for name in names:
        src = os.path.normpath(os.path.join(runtime, name))
        if not src.startswith(os.path.normpath(runtime) + os.sep) or not os.path.isfile(src):
            continue  # never outside _internal; a listed file the build didn't keep is skipped
        try:
            _sync_file(src, os.path.normpath(os.path.join(target, "_internal", name)))
        except OSError:
            pass  # leave the existing copy in place (a running hook holds it)
    # Leftovers of earlier builds (another Python version, modules the hook no longer uses) only take space
    for fname in os.listdir(target):
        if fname.startswith("aipet-hook.exe.old-"):  # moved aside while a hook was running (see _sync_file)
            try:
                os.remove(os.path.join(target, fname))
            except OSError:
                pass
    keep = {os.path.normcase(os.path.normpath(n)) for n in names}
    internal = os.path.join(target, "_internal")
    for root, _dirs, files in os.walk(internal, topdown=False):
        for fname in files:
            rel = os.path.relpath(os.path.join(root, fname), internal)
            if os.path.normcase(os.path.normpath(rel)) not in keep:
                try:
                    os.remove(os.path.join(root, fname))
                except OSError:
                    pass  # in use by a hook that's running right now: next time
        if root != internal:
            try:
                os.rmdir(root)  # only succeeds when empty
            except OSError:
                pass


def deploy_files(only_if_deployed=False):
    """Copy the hook (exe + script) to ~/.aipet/bin so settings can point at a stable path.

    With only_if_deployed=True this refreshes an existing deployment and does nothing
    otherwise, so merely starting the app never writes hook files; Install does.
    Unchanged files are left untouched; changed ones are swapped in atomically.
    """
    if only_if_deployed and not os.path.isdir(hook_locations.INSTALL_DIR):
        return
    os.makedirs(hook_locations.INSTALL_DIR, exist_ok=True)
    os.makedirs(os.path.join(hook_locations.PET_DIR, "sessions"), exist_ok=True)
    _sync_file(hook_locations.resource_path("aipet_hook.py"), os.path.join(hook_locations.INSTALL_DIR, "aipet_hook.py"))
    # The source hook fallback needs its toolkit-free event component too.
    for name in ("__init__.py", "hook_events.py"):
        _sync_file(hook_locations.resource_path("aipetlib", name), os.path.join(hook_locations.INSTALL_DIR, "aipetlib", name))
    _sync_file(hook_locations.resource_path("aipet_codex.py"), os.path.join(hook_locations.INSTALL_DIR, "aipet_codex.py"))
    _sync_file(hook_locations.resource_path("aipet_usage.py"), os.path.join(hook_locations.INSTALL_DIR, "aipet_usage.py"))
    _sync_file(hook_locations.resource_path("aipet_claude_usage.py"), os.path.join(hook_locations.INSTALL_DIR, "aipet_claude_usage.py"))
    _deploy_shared_hook()
    src = hook_locations.resource_path("hook")
    if os.path.isdir(src):
        # PyInstaller's Mac bundle uses directory links for frameworks and
        # data. Dereference them so the deployed hook is independent of the app.
        for root, _dirs, files in os.walk(src, followlinks=True):
            rel = os.path.relpath(root, src)
            for name in files:
                try:
                    _sync_file(os.path.join(root, name), os.path.normpath(os.path.join(hook_locations.INSTALL_DIR, "hook", rel, name)))
                except OSError:
                    pass  # leave the existing copy in place
