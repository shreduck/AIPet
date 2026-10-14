"""Hook stdin/stdout, atomic JSON writes and session locking."""
import json
import os
import re
import sys
import time

def write_stdout(text):
    """Print even from the windowed (no-console) exe, where sys.stdout is None: write to the inherited handle."""
    try:
        if sys.stdout is not None:
            sys.stdout.write(text)
            sys.stdout.flush()
            return
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            k32.GetStdHandle.restype = wintypes.HANDLE
            handle = k32.GetStdHandle(-11)
            data = text.encode("utf-8")
            written = wintypes.DWORD()
            k32.WriteFile(handle, data, len(data), ctypes.byref(written), None)
        except Exception:
            pass


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:120] or "unknown"


def read_stdin():
    """Read the hook payload. Works for python/pythonw and the no-console hook exe."""
    stream = sys.stdin
    if stream is not None:
        try:
            raw = stream.buffer.read() if hasattr(stream, "buffer") else stream.read().encode("utf-8")
            return raw.decode("utf-8", "replace")
        except Exception:
            pass
    try:
        with open(0, "rb", closefd=False) as f:
            return f.read().decode("utf-8", "replace")
    except Exception:
        pass
    if os.name == "nt":  # windowed exe: read the inherited pipe handle directly
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            k32.GetStdHandle.restype = wintypes.HANDLE
            handle = k32.GetStdHandle(-10)
            if handle and handle != wintypes.HANDLE(-1).value:
                buf, n, chunks = ctypes.create_string_buffer(65536), wintypes.DWORD(), []
                while k32.ReadFile(handle, buf, 65536, ctypes.byref(n), None) and n.value:
                    chunks.append(buf.raw[: n.value])
                return b"".join(chunks).decode("utf-8", "replace")
        except Exception:
            pass
    return ""


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_atomic(path, record):
    tmp = "%s.%d.tmp" % (path, os.getpid())  # unique per process: two hooks writing at once must not share a file
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f)
    os.replace(tmp, path)


class SessionLock:
    """Serialises read-modify-write of one session file. Claude Code starts several hooks for one moment (for example
    Notification and PermissionRequest) as separate processes; without this the last writer wins with stale data."""

    def __init__(self, path):
        self.path = path + ".lock"
        self.held = False

    def __enter__(self):
        deadline = time.time() + 2.0
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 3:  # left behind by a crashed hook
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    return self  # never block Claude Code: carry on unlocked
                time.sleep(0.01)
            except OSError:
                return self

    def __exit__(self, *exc):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
