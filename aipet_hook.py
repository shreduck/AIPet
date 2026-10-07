#!/usr/bin/env python3
"""
AIPet - Claude Code hook (Windows, WSL and VS Code).

Claude Code pipes a JSON payload to this script on stdin for each hook event.
We translate it into a pet state and write
    <Windows home>\\.aipet\\sessions\\<session_id>.json
which aipet.py (running on Windows) watches.

    UserPromptSubmit / PostToolUse -> working
    Notification (permission etc.) -> needs_input
    Stop                           -> done
    SessionStart                   -> idle
    SessionEnd                     -> (file removed; Cowork: "done", cleared after the done timeout)
    Interrupt (Codex)              -> done

Codex (OpenAI) sends the same events and fields; its hooks call this script with --codex. Codex runs PermissionRequest
hooks before showing its own approval prompt, so for Codex the pet only waits for a click when the user opted in
(<pet dir>/codex-answers); then Codex's own prompt appears only if nobody answers on the pet in time.

Environment detection:
    * WSL     -> writes into the *Windows* profile (/mnt/c/Users/<you>/.aipet),
                 resolved once via cmd.exe + wslpath and cached.
    * VS Code -> the Claude Code extension or VS Code's integrated terminal.
Override the target folder with AIPET_DIR (path to the .aipet folder).

Never prints and always exits 0, so it can't block Claude Code.
"""
import json
import os
import re
import subprocess
import sys
import time

AGENT = "codex" if "--codex" in sys.argv[1:] else "claude"
WORKING_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart"}
# Notification kinds that ask the user to act. Everything else (idle reminders, auth_success, agent_completed, elicitation_complete,
# quota_auto_resume_fired, ...) is informational and must not raise a "needs you".
ACTIONABLE_NOTIFICATIONS = {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input",
                            "quota_auto_resume_stale"}
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
        if AGENT == "codex":
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


def debug_log(base_dir, event, data, prev_state, new_state):
    """Opt-in diagnostics: if <pet dir>/debug-events exists, append one metadata line per hook event to
    events.log. Field NAMES only - never prompts, tool inputs or other values - so it is safe to share."""
    try:
        if not os.path.exists(os.path.join(base_dir, "debug-events")):
            return
        e = os.environ
        row = {
            "t": time.strftime("%H:%M:%S"), "ev": event, "sid": str(data.get("session_id", ""))[:8],
            "tool": data.get("tool_name", ""), "ntype": data.get("notification_type", ""),
            "msg": str(data.get("message", ""))[:80], "keys": sorted(data.keys()),
            "agent": {k: str(data[k])[:12] for k in ("agent_id", "agent_type", "parent_session_id") if k in data},
            "prev": prev_state, "new": new_state,
            # shapes only (type, length, key names): never the values
            "shape": {k: [type(data[k]).__name__, len(data[k]) if hasattr(data[k], "__len__") else data[k],
                          sorted(data[k][0].keys()) if isinstance(data[k], list) and data[k] and isinstance(data[k][0], dict) else None]
                      for k in ("background_tasks", "session_crons", "agents", "subagents") if k in data},
            "env": {"entry": e.get("CLAUDE_CODE_ENTRYPOINT", ""), "term": e.get("TERM_PROGRAM", ""),
                    "cowork": is_cowork(), "cowork_folders": len(cowork_folders()),  # a count, not the paths
                    "vscode_vars": [k for k in ("VSCODE_PID", "VSCODE_IPC_HOOK_CLI", "VSCODE_GIT_IPC_HANDLE") if e.get(k)]},
        }
        path = os.path.join(base_dir, "events.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if os.path.getsize(path) > 400_000:  # keep the newest half
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[len(lines) // 2:])
    except Exception:
        pass


def build_request(data):
    """What the permission prompt is about: tool, description and the command/path/url. Kept short on purpose;
    it only lives in the session file while the prompt is pending."""
    inp = data.get("tool_input")
    if not isinstance(inp, dict):
        inp = {"input": inp} if inp else {}
    detail = ""
    for key in ("command", "file_path", "path", "url", "pattern", "query", "prompt"):
        if inp.get(key):
            detail = str(inp[key])
            break
    if not detail and inp:
        detail = "\n".join(f"{k}: {str(v)[:200]}" for k, v in list(inp.items())[:6])
    return {"tool": str(data.get("tool_name") or ""), "description": str(inp.get("description") or "")[:300],
            "detail": detail[:1500], "id": str(data.get("tool_use_id") or data.get("turn_id") or ""), "t": time.time()}


def pet_alive(base):
    """The pet touches <pet dir>/alive every couple of seconds while it runs."""
    try:
        return time.time() - os.path.getmtime(os.path.join(base, "alive")) < 8
    except OSError:
        return False


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


def decision_output(behavior):
    decision = {"behavior": behavior}
    if behavior == "deny":
        decision["message"] = "Denied from AIPet"
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}})


MAX_WAIT = 1800.0  # the pet's slider: 0 (no limit) to 30 minutes


def await_answer(base, key, seconds, session_path=None, request_id=None):
    """Wait for the pet to drop <pet dir>/answers/<key>.json ({"behavior": "allow"|"deny"}). None = no answer.
    seconds <= 0 waits with no limit. Also gives up once the session has moved past this prompt (answered in
    Claude Code itself), so a waiting hook never outlives its prompt."""
    path = os.path.join(base, "answers", key + ".json")
    waiting = os.path.join(base, "answers", key + ".waiting")  # touched while we wait: the pet only offers its
    try:                                                         # buttons while this is fresh
        os.remove(path)  # an old answer must never apply to a new prompt
    except OSError:
        pass
    try:
        return _await(path, waiting, base, seconds, session_path, request_id)
    finally:
        try:
            os.remove(waiting)
        except OSError:
            pass


def _await(path, waiting, base, seconds, session_path, request_id):
    deadline = time.time() + seconds if seconds > 0 else None
    checked, touched = time.time(), 0.0
    while deadline is None or time.time() < deadline:
        if time.time() - touched > 1:
            touched = time.time()
            try:
                os.makedirs(os.path.dirname(waiting), exist_ok=True)
                with open(waiting, "w") as f:
                    f.write(str(os.getpid()))
            except OSError:
                pass
        if session_path and request_id and time.time() - checked > 2:
            checked = time.time()
            if ((read_json(session_path).get("request") or {}).get("id")) != request_id:
                return None  # answered elsewhere: the record no longer holds this prompt
        if os.path.exists(path):
            time.sleep(0.05)
            data = read_json(path)
            try:
                os.remove(path)
            except OSError:
                pass
            if data.get("behavior") in ("allow", "deny"):
                return data["behavior"]
        if not pet_alive(base):
            return None  # the pet was closed: fall back to the normal prompt
        time.sleep(0.2)
    return None


def configured_wait(base):
    """Seconds the user allows for answering from the pet (0 = no limit). Set with the pet's "Answer timeout" slider,
    which mirrors it into <pet dir>/answer-wait; 3 minutes if it was never set."""
    try:
        raw = os.environ.get("AIPET_ANSWER_WAIT") or os.environ.get("CLAUDE_PET_ANSWER_WAIT")  # LEGACY
        if not raw:
            with open(os.path.join(base, "answer-wait"), encoding="utf-8") as f:
                raw = f.read().strip()
        value = float(raw)
    except (OSError, ValueError):
        value = 180.0
    return max(0.0, min(MAX_WAIT, value))


def answer_flow(base, path, aid):
    """After the request is recorded: give the user a window to answer from the pet, then print the decision.
    Prints nothing (normal prompt) if the pet isn't running, answering is switched off, or nobody clicks in time."""
    seconds = configured_wait(base)  # 0 = wait until answered
    if not pet_alive(base) or os.path.exists(os.path.join(base, "no-answers")):
        return  # observe only
    with SessionLock(path):
        record = read_json(path)
        req = record.get("request") or {}
        if not req:
            return
        key = safe_name(req.get("id") or record.get("id") or "request")
        req["answerable"] = True
        record["request"] = req
        write_atomic(path, record)
    decision = await_answer(base, key, seconds, path, req.get("id"))  # no lock held while waiting
    with SessionLock(path):
        cur = read_json(path)
        if (cur.get("request") or {}).get("id") == req.get("id"):  # still the same prompt: settle the record
            if decision:
                cur.update(state="working", message="", request={}, wait_agent="", changed=time.time(), updated=time.time())
            else:
                cur["request"]["answerable"] = False
            write_atomic(path, cur)
    if decision:
        write_stdout(decision_output(decision))

def request_from_transcript(path, max_bytes=300000):
    """Fallback for sessions that only send a Notification (the VS Code extension): the tool call that has no result
    yet in the transcript is the one asking for permission. Reads just the tail of the file."""
    try:
        if not path or not os.path.isfile(path):
            return {}
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # drop the partial first line
            lines = f.read().decode("utf-8", "replace").splitlines()
        uses, done = {}, set()
        for line in lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            content = (obj.get("message") or {}).get("content") if isinstance(obj, dict) else None
            for blk in content if isinstance(content, list) else []:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "tool_use":
                    uses[blk.get("id")] = blk
                elif blk.get("type") == "tool_result":
                    done.add(blk.get("tool_use_id"))
        pending = [b for i, b in uses.items() if i not in done]
        if not pending:
            return {}
        blk = pending[-1]
        req = build_request({"tool_name": blk.get("name"), "tool_input": blk.get("input"), "tool_use_id": blk.get("id")})
        req["source"] = "transcript"  # not answerable: no PermissionRequest hook is waiting for a click
        if len(pending) > 1:
            req["description"] = (req["description"] + " " if req["description"] else "") + "(%d tool calls pending; showing the latest)" % len(pending)
        return req
    except Exception:
        return {}


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


def _update_session(path, target, event, data, wsl):
    """Apply one hook event to the session file (call under SessionLock). Returns the agent id, if any."""
    session_id = data.get("session_id") or "unknown"
    prev = read_json(path)
    state = prev.get("state", "idle")
    message = prev.get("message", "")
    # Subagents: their hook payloads (probably) carry an agent_id. Track the recently active ones so the pet can
    # show a stack, and so a subagent's tool call can't hide a permission prompt that belongs to someone else.
    aid = str(data.get("agent_id") or data.get("subagent_id") or "")
    t_now = time.time()
    agents = {k: v for k, v in (prev.get("agents") or {}).items() if t_now - v < 900}  # until SubagentStop (15 min safety)
    if event == "SubagentStop":
        agents.pop(aid, None)
    elif aid:
        agents[aid] = t_now
    agents = dict(sorted(agents.items(), key=lambda kv: kv[1])[-8:])
    wait_agent = prev.get("wait_agent", "")
    request = prev.get("request") or {}
    main_stopped = bool(prev.get("main_stopped"))  # the main agent finished its turn while subagents kept running

    if event in WORKING_EVENTS:
        keep = (aid and event != "UserPromptSubmit" and prev.get("state") == "needs_input"
                and (not wait_agent or wait_agent != aid))
        if not keep:  # otherwise another agent is working while this prompt is still waiting for the user
            state, message = "working", ""
        if not aid:
            main_stopped = False  # the main agent itself is active again
    elif event == "PermissionRequest":  # observe only: print nothing, so the normal prompt is untouched
        request = build_request(data)
        state, message = "needs_input", f"{'Codex' if AGENT == 'codex' else 'Claude'} needs your permission to use {request['tool'] or 'a tool'}"
        wait_agent = aid
    elif event == "Notification":
        msg = data.get("message", "") or ""
        ntype = data.get("notification_type", "") or ""
        is_idle_reminder = ntype == "idle_prompt" or "waiting for your input" in msg.lower()
        # Only notifications that ask the user to act count. An idle reminder ("Claude is waiting for your input", which also
        # fires while background agents run) and informational kinds never become "needs you". Older versions send no type:
        # then a permission message counts.
        actionable = ntype in ACTIONABLE_NOTIFICATIONS or (not ntype and not is_idle_reminder and "permission" in msg.lower())
        if actionable:
            state, message = "needs_input", msg
            wait_agent = aid
            if request and request.get("source") == "transcript":
                request = {}  # a transcript guess is recomputed; a real PermissionRequest one is kept whatever its age
            if not request and (ntype == "permission_prompt" or "permission" in msg.lower()):
                request = request_from_transcript(data.get("transcript_path"))  # sessions without PermissionRequest
    elif event == "Interrupt":  # Codex: the user interrupted the turn
        state, message, main_stopped = "done", "", False
        request, wait_agent = {}, ""
    elif event == "Stop":
        if agents:  # the turn is over but background subagents are still running: not done yet
            if prev.get("state") != "needs_input":
                state, message = "working", ""
            main_stopped = True
        else:
            state, message, main_stopped = "done", "", False
    elif event == "SessionStart":
        state = "idle"
    elif event == "SubagentStop":
        if not agents and main_stopped and prev.get("state") == "working":
            state, message, main_stopped = "done", "", False  # the last background agent finished and the main one is idle
    else:
        debug_log(os.path.dirname(target), event, data, prev.get("state"), "(ignored)")
        return

    now = time.time()
    # Prefer the stable project root: payload cwd follows the shell, so it changes on every `cd`.
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or prev.get("cwd") or data.get("cwd") or os.getcwd()
    hwnd, host = prev.get("hwnd"), prev.get("host", "")
    if os.name == "nt" and not wsl and not (hwnd and window_alive(hwnd)):
        hwnd, host = find_host_window(data.get("cwd") or cwd)  # for click-to-focus; re-found if the window was closed
    cowork = is_cowork() or prev.get("app") == "cowork"
    folders = cowork_folders() if cowork else []
    if folders:  # name a Cowork session after the folder it works in, not its private "host-cwd"
        cwd = folders[0]
    title = os.path.basename(cwd.rstrip("\\/")) or cwd
    if len(folders) > 1:
        title += f" +{len(folders) - 1}"
    pid = prev.get("pid") or find_claude_pid()  # looked up once per session
    topic = prev.get("topic", "")
    if is_home_or_root(cwd) or (cowork and not folders):  # no folder to name it after: use the first prompt
        if not topic and event == "UserPromptSubmit":
            topic = " ".join(str(data.get("prompt") or "").split())[:40]
        title = topic or ("Cowork" if cowork else "~")
    record = {
        "id": session_id,
        "source": "claude-code",
        "agent": AGENT,
        "app": "cowork" if cowork else "",
        "env": "wsl" if wsl else ("windows" if os.name == "nt" else sys.platform),
        "distro": os.environ.get("WSL_DISTRO_NAME", "") if wsl else "",
        "ide": detect_ide() or prev.get("ide", ""),
        "entry": os.environ.get("CLAUDE_CODE_ENTRYPOINT", "") or prev.get("entry", ""),  # e.g. cli, claude-vscode
        "title": title,
        "topic": topic,
        "hwnd": hwnd,
        "host": host,
        "pid": pid,
        "agents": agents,
        "main_stopped": main_stopped,
        "wait_agent": wait_agent if state == "needs_input" else "",
        "request": request if state == "needs_input" else {},
        "cwd": cwd,
        "state": state,
        "message": message,
        "updated": now,
        "changed": now if state != prev.get("state") else prev.get("changed", now),
    }
    debug_log(os.path.dirname(target), event, data, prev.get("state"), state)
    write_atomic(path, record)
    return aid


# TEMP (Codex testing): log EVERYTHING a Codex hook receives to <pet dir>/codex-raw.log - the full payload (prompts,
# commands, tool output included), argv, platform, cwd and the names of CODEX*/OPENAI* environment variables (names
# only: values can hold API keys). Remove this block, and its call in main(), once Codex support is verified.
CODEX_RAW_LOG = True


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


def codex_raw_log(raw, base):
    if not (CODEX_RAW_LOG and AGENT == "codex"):
        return
    try:
        import platform
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "codex-raw.log")
        if os.path.exists(path) and os.path.getsize(path) > 5_000_000:  # keep the newest half
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            with open(path, "w", encoding="utf-8") as f:
                f.write(text[len(text) // 2:])
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "argv": sys.argv, "os": platform.platform(),
               "wsl": is_wsl(), "cwd": os.getcwd(), "pid": os.getpid(), "ppid": os.getppid(),
               "env_names": sorted(k for k in os.environ if k.upper().startswith(("CODEX", "OPENAI"))),
               "ancestors": _ancestor_names(),
               "codex_procs": _codex_procs(),
               "payload_raw": raw}
        try:
            row["payload"] = json.loads(raw) if raw else None
            del row["payload_raw"]
        except ValueError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    raw = read_stdin()
    try:
        data = json.loads(raw or "{}")
    except Exception:
        data = {}

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    event = data.get("hook_event_name") or (args[0] if args else "")
    session_id = data.get("session_id") or "unknown"
    wsl = is_wsl()
    target = sessions_dir(wsl)
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, safe_name(session_id) + ".json")
    codex_raw_log(raw, os.path.dirname(target))  # TEMP (Codex testing)

    if event == "SessionEnd" and (is_cowork() or read_json(path).get("app") == "cowork"):
        # Cowork ends its Claude Code session after every turn (and when the app closes), so removing the pet here
        # would make it vanish as soon as it answers. Mark it done instead: the pet then hides it after
        # the "Clear finished after" timeout, like any finished session, and the next message brings it back.
        with SessionLock(path):
            rec = read_json(path)
            prev_state = rec.get("state")
            if rec:
                now = time.time()
                rec.update({"state": "done", "message": "", "agents": {}, "main_stopped": False, "wait_agent": "",
                            "request": {}, "updated": now,
                            "changed": now if prev_state != "done" else rec.get("changed", now)})
                write_atomic(path, rec)
            debug_log(os.path.dirname(target), event, data, prev_state, "done (cowork: kept until timeout)")
        return

    if event == "SessionEnd":
        debug_log(os.path.dirname(target), event, data, read_json(path).get("state"), "(removed)")
        try:
            os.remove(path)
        except OSError:
            pass
        return

    with SessionLock(path):
        aid = _update_session(path, target, event, data, wsl)
    # Codex asks its hooks BEFORE showing its own prompt, so the pet only waits for a click if the user opted in
    # (Answer Codex prompts from the pet -> <pet dir>/codex-answers); otherwise Codex's prompt appears right away.
    if event == "PermissionRequest" and (AGENT != "codex" or os.path.exists(os.path.join(os.path.dirname(target),
                                                                                          "codex-answers"))):
        answer_flow(os.path.dirname(target), path, aid or "")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
