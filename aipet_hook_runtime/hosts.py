"""Host, process/window, Cowork and WSL environment detection."""
import json
import os
import shutil
import subprocess
import sys
from . import config as hook_config

LOCAL_PET_DIR = os.path.join(os.path.expanduser("~"), ".aipet")


WSL_CACHE = os.path.join(LOCAL_PET_DIR, "windows-pet-dir.txt")


def is_wsl():
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def resolve_wsl_windows_dir():
    """Return the Windows .aipet dir as a WSL path, cached after the first lookup."""
    try:
        with open(WSL_CACHE, encoding="utf-8") as f:
            cached = f.read().strip()
        return None if cached == "LOCAL" else cached
    except OSError:
        pass
    result = None
    try:
        win_home = subprocess.run(["cmd.exe", "/c", "echo %USERPROFILE%"], capture_output=True,
                                  text=True, timeout=3, cwd="/mnt/c").stdout.strip()
        if win_home and "%" not in win_home:
            unix_home = subprocess.run(["wslpath", "-u", win_home], capture_output=True,
                                       text=True, timeout=3).stdout.strip()
            if unix_home and os.path.isdir(unix_home):
                result = os.path.join(unix_home, ".aipet")
    except Exception:
        result = None
    try:  # cache success or failure ("LOCAL") so we never pay this cost again
        os.makedirs(LOCAL_PET_DIR, exist_ok=True)
        with open(WSL_CACHE, "w", encoding="utf-8") as f:
            f.write(result or "LOCAL")
    except OSError:
        pass
    return result


def sessions_dir(wsl):
    base = os.environ.get("AIPET_DIR") or os.environ.get("CLAUDE_PET_DIR")  # LEGACY: the old name still works
    if not base and wsl:
        base = resolve_wsl_windows_dir()
    return os.path.join(base or LOCAL_PET_DIR, "sessions")


def detect_ide():
    e = os.environ
    entry = e.get("CLAUDE_CODE_ENTRYPOINT", "").lower()
    if ("vscode" in entry or e.get("TERM_PROGRAM") == "vscode" or e.get("VSCODE_PID")
            or e.get("VSCODE_IPC_HOOK_CLI") or e.get("VSCODE_GIT_IPC_HANDLE")):
        return "vscode"
    return ""


def is_cowork():
    """Cowork (the Claude desktop app's agent mode) runs Claude Code with these set; its cwd is a private
    per-session folder ("host-cwd"), so it says nothing about what the user is working on."""
    e = os.environ
    return (e.get("CLAUDE_CODE_IS_COWORK", "").lower() in ("1", "true", "yes")
            or e.get("CLAUDE_CODE_ENTRYPOINT", "").lower() == "local-agent")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def cowork_folders():
    """Folders the user connected to the Cowork session (host paths), in order. Read from
    CLAUDE_CODE_WORKSPACE_HOST_PATHS, accepting JSON (list or mapping) or a separator-delimited list, and keeping
    only real folders outside the app's own session storage (outputs, uploads, skills)."""
    raw = os.environ.get("CLAUDE_CODE_WORKSPACE_HOST_PATHS", "").strip()
    if not raw:
        return []
    try:
        cands = list(_strings(json.loads(raw)))
    except ValueError:
        cands = [p for chunk in raw.splitlines() for p in chunk.split(os.pathsep)]
    out, seen = [], set()
    for p in cands:
        p = p.strip().strip('"')
        if not p or "local-agent-mode-sessions" in p.replace("\\", "/") or not os.path.isabs(p):
            continue
        k = os.path.normcase(os.path.normpath(p))
        if k not in seen and os.path.isdir(p):
            seen.add(k)
            out.append(os.path.normpath(p))
    return out


def is_home_or_root(path):
    """True for the user's home folder or a filesystem root, where the folder name makes a useless title."""
    norm = os.path.normcase(os.path.normpath(path))
    return norm == os.path.normcase(os.path.normpath(os.path.expanduser("~"))) or os.path.dirname(norm) == norm


CLAUDE_PROCESS_NAMES = ("claude", "claude.exe", "node", "node.exe")


def _read_proc(pid):
    """(name, parent pid) of a process, or None. /proc on Linux (WSL), ps on macOS."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as f:
                stat = f.read()
            name = stat[stat.index("(") + 1:stat.rindex(")")]
            return name, int(stat[stat.rindex(")") + 2:].split()[1])
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True, timeout=2).stdout.strip()
        ppid, _, comm = out.partition(" ")
        return os.path.basename(comm.strip()), int(ppid)
    except Exception:
        return None


def _windows_process_table():
    """{pid: (parent pid, exe name)} from a toolhelp snapshot."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ENTRY)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    table, e = {}, ENTRY()
    e.dwSize = ctypes.sizeof(ENTRY)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        table[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return table


def find_claude_pid():
    """PID of the Claude Code process that runs this session (the nearest claude / node ancestor), else None.
    Inside WSL this is a WSL PID, which the pet labels as such."""
    try:
        if os.name == "nt":
            table, cur, seen = _windows_process_table(), os.getpid(), set()
            while cur in table and cur not in seen:
                seen.add(cur)
                parent = table[cur][0]
                if parent in table and table[parent][1].lower() in CLAUDE_PROCESS_NAMES:
                    return parent  # judge the ANCESTOR by its own name
                cur = parent
            return None
        pid, seen = os.getppid(), set()
        while pid > 1 and pid not in seen:
            seen.add(pid)
            info = _read_proc(pid)
            if not info:
                return None
            if info[0].lower() in CLAUDE_PROCESS_NAMES:
                return pid
            pid = info[1]
    except Exception:
        pass
    return None


def find_host_window(cwd=""):
    """(hwnd, exe name) of the nearest ancestor process that owns a visible top-level window, else (None, "").

    That is the terminal (Windows Terminal, mintty, ...) or the Claude desktop app running this session.
    Windows only; any failure just means "no click-to-focus" for this session.
    """
    try:
        import ctypes
        from ctypes import wintypes
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32

        class ENTRY(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ENTRY)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        snap = k32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
        parents, names = {}, {}
        e = ENTRY()
        e.dwSize = ctypes.sizeof(ENTRY)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            parents[e.th32ProcessID], names[e.th32ProcessID] = e.th32ParentProcessID, e.szExeFile
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        k32.CloseHandle(snap)

        chain, seen, pid = [], set(), os.getpid()
        while pid in parents and pid not in seen:  # ancestors, nearest first (guards against pid-reuse loops)
            seen.add(pid)
            pid = parents[pid]
            chain.append(pid)

        for fn, args in ((u32.IsWindowVisible, [wintypes.HWND]), (u32.GetWindow, [wintypes.HWND, wintypes.UINT]),
                         (u32.GetWindowTextLengthW, [wintypes.HWND]),
                         (u32.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
                         (u32.GetWindowLongW, [wintypes.HWND, ctypes.c_int])):
            fn.argtypes = args
        u32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        wins = {}

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _):
            if (u32.IsWindowVisible(hwnd) and not u32.GetWindow(hwnd, 4)  # GW_OWNER: top-level, un-owned
                    and u32.GetWindowTextLengthW(hwnd) > 0
                    and not (u32.GetWindowLongW(hwnd, -20) & 0x80)):  # not WS_EX_TOOLWINDOW
                owner = wintypes.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                wins.setdefault(owner.value, hwnd)
            return True

        u32.EnumWindows(visit, 0)
        for pid in chain:
            if pid in wins:
                return int(wins[pid]), names.get(pid, "")
        if hook_config.AGENT == "codex":
            return _codex_window(parents, names, wins, cwd)
    except Exception:
        pass
    return None, ""


def _codex_window(parents, names, wins, cwd=""):
    """Codex runs its hooks from a background process, not from the terminal, so the hook's own ancestors have no
    window. Walk up from every running codex process instead; if several lead to different windows, prefer the one
    whose title mentions the project folder."""
    import ctypes
    found = {}
    for pid, name in names.items():
        if "codex" not in name.lower():  # codex.exe, or a platform-named binary such as codex-x86_64-...exe
            continue
        seen, cur = set(), pid
        while cur in parents and cur not in seen:
            seen.add(cur)
            cur = parents[cur]
            if cur in wins:
                found[int(wins[cur])] = names.get(cur, "")
                break
    if len(found) == 1:
        return next(iter(found.items()))
    folder = os.path.basename(str(cwd).rstrip("\\/")).lower()
    if folder:
        u32 = ctypes.windll.user32
        for hwnd, exe in found.items():
            buf = ctypes.create_unicode_buffer(512)
            u32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 512)
            if folder in buf.value.lower():
                return hwnd, exe
    return None, ""


def window_alive(hwnd):
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsWindow(ctypes.c_void_p(int(hwnd))))
    except Exception:
        return False


def _ancestor_names():
    try:
        if os.name != "nt":
            return []
        table, cur, out = _windows_process_table(), os.getpid(), []
        while cur in table and len(out) < 12:
            cur = table[cur][0]
            out.append(f"{cur}:{table[cur][1] if cur in table else '?'}")
        return out
    except Exception:
        return []


def _codex_procs():
    try:
        if os.name != "nt":
            return []
        table = _windows_process_table()
        out = []
        for pid, (parent, name) in table.items():
            if "codex" in name.lower():
                chain, cur = [], pid
                while cur in table and len(chain) < 6:
                    cur = table[cur][0]
                    chain.append(table[cur][1] if cur in table else f"{cur}:?")
                out.append(f"{pid}:{name} <- " + " <- ".join(chain))
        return out[:12]
    except Exception:
        return []


def find_statusline_bash():
    if os.name != "nt":
        return shutil.which("bash")
    candidates = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")]
    git = shutil.which("git")
    if git:
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(git)), "bin", "bash.exe"))
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if root:
            candidates += [os.path.join(root, "Git", "bin", "bash.exe"), os.path.join(root, "Programs", "Git", "bin", "bash.exe")]
    return next((p for p in candidates if p and os.path.isfile(p)), None)
