"""Independent hook deployment and atomic file synchronization."""
import os
import shutil
import sys
import time
import glob
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


def _invalidate_bytecode(source):
    """Timestamp caches cannot distinguish rapid, same-size source updates."""
    if not source.endswith(".py"):
        return
    folder, name = os.path.dirname(source), os.path.basename(source)[:-3]
    caches = glob.glob(os.path.join(folder, "__pycache__", name + ".*.pyc"))
    for path in caches + [source + "c", source + "o"]:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _sync_file(src, dst):
    """Copy src over dst only if different, via a temp file + atomic replace, so a hook that
    is executing right now never sees a half-written or missing file."""
    if os.path.exists(dst) and _same_file(src, dst):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".new"
    shutil.copyfile(src, tmp)
    shutil.copymode(src, tmp)  # keep the executable bit (macOS hook binary)
    _invalidate_bytecode(dst)
    try:
        os.replace(tmp, dst)
        _invalidate_bytecode(dst)
        return
    except OSError:
        pass
    # Windows: a running hook's exe / DLLs can't be overwritten, but they can be renamed. Move the busy copy aside
    # (the running hook keeps using it) and put the new one in its place; the aside copy is deleted on a later sync.
    aside = f"{dst}.old-{os.getpid()}-{int(time.time())}"
    try:
        os.replace(dst, aside)
        os.replace(tmp, dst)
        _invalidate_bytecode(dst)
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


def _deploy_hook_runtime():
    """Synchronize source dependencies before switching the entry script; never ship cache/test files."""
    source = hook_locations.resource_path("aipet_hook_runtime")
    target = os.path.join(hook_locations.INSTALL_DIR, "aipet_hook_runtime")
    if not all(os.path.isfile(os.path.join(source, name)) for name in ("__init__.py", "cli.py")):
        raise RuntimeError("Bundled hook runtime source is missing - rebuild AIPet.")
    keep = set()
    for root, dirs, files in os.walk(source):
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(root, name), source)
            src, dst = os.path.join(source, rel), os.path.join(target, rel)
            _sync_file(src, dst)
            if not _same_file(src, dst):
                raise RuntimeError(f"Hook runtime update could not replace {rel}; the entry script was kept.")
            keep.add(os.path.normcase(os.path.normpath(rel)))
    # Only this generated package's obsolete Python files are removed, with their caches.
    for root, dirs, files in os.walk(target):
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.normcase(os.path.relpath(path, target))
            if name.endswith(".py") and rel not in keep:
                _invalidate_bytecode(path)
                os.remove(path)


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
    _deploy_hook_runtime()
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
    # Dependencies are current before an existing installation starts importing the new package.
    _sync_file(hook_locations.resource_path("aipet_hook.py"), os.path.join(hook_locations.INSTALL_DIR, "aipet_hook.py"))
