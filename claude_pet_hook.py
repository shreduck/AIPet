#!/usr/bin/env python3
"""
Claude Pet - Claude Code hook (Windows, WSL and VS Code).

Claude Code pipes a JSON payload to this script on stdin for each hook event.
We translate it into a pet state and write
    <Windows home>\\.claude-pet\\sessions\\<session_id>.json
which claude_pet.py (running on Windows) watches.

    UserPromptSubmit / PostToolUse -> working
    Notification (permission etc.) -> needs_input
    Stop                           -> done
    SessionStart                   -> idle
    SessionEnd                     -> (file removed)

Environment detection:
    * WSL     -> writes into the *Windows* profile (/mnt/c/Users/<you>/.claude-pet),
                 resolved once via cmd.exe + wslpath and cached.
    * VS Code -> the Claude Code extension or VS Code's integrated terminal.
Override the target folder with CLAUDE_PET_DIR (path to the .claude-pet folder).

Never prints and always exits 0, so it can't block Claude Code.
"""
import json
import os
import re
import subprocess
import sys
import time

WORKING_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse"}
LOCAL_PET_DIR = os.path.join(os.path.expanduser("~"), ".claude-pet")
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
    """Return the Windows .claude-pet dir as a WSL path, cached after the first lookup."""
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
                result = os.path.join(unix_home, ".claude-pet")
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
    base = os.environ.get("CLAUDE_PET_DIR")
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


def is_home_or_root(path):
    """True for the user's home folder or a filesystem root, where the folder name makes a useless title."""
    norm = os.path.normcase(os.path.normpath(path))
    return norm == os.path.normcase(os.path.normpath(os.path.expanduser("~"))) or os.path.dirname(norm) == norm


def find_host_window():
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
    except Exception:
        pass
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
            "env": {"entry": e.get("CLAUDE_CODE_ENTRYPOINT", ""), "term": e.get("TERM_PROGRAM", ""),
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
            "detail": detail[:1500], "id": str(data.get("tool_use_id") or ""), "t": time.time()}


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
        decision["message"] = "Denied from Claude Pet"
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}})


def await_answer(base, key, seconds):
    """Wait for the pet to drop <pet dir>/answers/<key>.json ({"behavior": "allow"|"deny"}). None = no answer."""
    path = os.path.join(base, "answers", key + ".json")
    try:
        os.remove(path)  # an old answer must never apply to a new prompt
    except OSError:
        pass
    deadline = time.time() + seconds
    while time.time() < deadline:
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
    """Seconds the user allows for answering from the pet (0 = off). Set with the pet's "Answer timeout" slider,
    which mirrors it into <pet dir>/answer-wait; 3 minutes if it was never set."""
    try:
        raw = os.environ.get("CLAUDE_PET_ANSWER_WAIT")
        if not raw:
            with open(os.path.join(base, "answer-wait"), encoding="utf-8") as f:
                raw = f.read().strip()
        value = float(raw)
    except (OSError, ValueError):
        value = 180.0
    return max(0.0, min(300.0, value))


def answer_flow(base, path, aid):
    """After the request is recorded: give the user a window to answer from the pet, then print the decision.
    Prints nothing (normal prompt) if the pet isn't running, answering is switched off, or nobody clicks in time."""
    record = read_json(path)
    req = record.get("request") or {}
    if not req or not pet_alive(base) or os.path.exists(os.path.join(base, "no-answers")):
        return
    seconds = configured_wait(base)
    if seconds <= 0:
        return  # answering from the pet is switched off: observe only
    key = safe_name(req.get("id") or record.get("id") or "request")
    req["answerable"] = True
    record["request"] = req
    write_atomic(path, record)
    decision = await_answer(base, key, seconds)
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
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f)
    os.replace(tmp, path)


def main():
    try:
        data = json.loads(read_stdin() or "{}")
    except Exception:
        data = {}

    event = data.get("hook_event_name") or (sys.argv[1] if len(sys.argv) > 1 else "")
    session_id = data.get("session_id") or "unknown"
    wsl = is_wsl()
    target = sessions_dir(wsl)
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, safe_name(session_id) + ".json")

    if event == "SessionEnd":
        debug_log(os.path.dirname(target), event, data, read_json(path).get("state"), "(removed)")
        try:
            os.remove(path)
        except OSError:
            pass
        return

    prev = read_json(path)
    state = prev.get("state", "idle")
    message = prev.get("message", "")
    # Subagents: their hook payloads (probably) carry an agent_id. Track the recently active ones so the pet can
    # show a stack, and so a subagent's tool call can't hide a permission prompt that belongs to someone else.
    aid = str(data.get("agent_id") or data.get("subagent_id") or "")
    t_now = time.time()
    agents = {k: v for k, v in (prev.get("agents") or {}).items() if t_now - v < 120}
    if event == "SubagentStop":
        agents.pop(aid, None)
    elif aid:
        agents[aid] = t_now
    agents = dict(sorted(agents.items(), key=lambda kv: kv[1])[-8:])
    wait_agent = prev.get("wait_agent", "")
    request = prev.get("request") or {}

    if event in WORKING_EVENTS:
        keep = (aid and event != "UserPromptSubmit" and prev.get("state") == "needs_input"
                and (not wait_agent or wait_agent != aid))
        if not keep:  # otherwise another agent is working while this prompt is still waiting for the user
            state, message = "working", ""
    elif event == "PermissionRequest":  # observe only: print nothing, so the normal prompt is untouched
        request = build_request(data)
        state, message = "needs_input", f"Claude needs your permission to use {request['tool'] or 'a tool'}"
        wait_agent = aid
    elif event == "Notification":
        msg = data.get("message", "") or ""
        ntype = data.get("notification_type", "") or ""
        is_idle_reminder = ntype == "idle_prompt" or "waiting for your input" in msg.lower()
        # An "idle" reminder after Claude already finished is not a new question: stay "done".
        if not (is_idle_reminder and prev.get("state") in ("done", "idle")):
            state, message = "needs_input", msg
            wait_agent = aid
            if request and (request.get("source") == "transcript" or time.time() - request.get("t", 0) > 5):
                request = {}  # a request from an earlier prompt, not this one (transcript-derived ones are recomputed)
            if not request and (ntype == "permission_prompt" or "permission" in msg.lower()):
                request = request_from_transcript(data.get("transcript_path"))  # sessions without PermissionRequest
    elif event == "Stop":
        state, message = "done", ""
        agents = {}
    elif event == "SessionStart":
        state = "idle"
    elif event == "SubagentStop":
        pass  # only the agent count changes
    else:
        debug_log(os.path.dirname(target), event, data, prev.get("state"), "(ignored)")
        return

    now = time.time()
    # Prefer the stable project root: payload cwd follows the shell, so it changes on every `cd`.
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or prev.get("cwd") or data.get("cwd") or os.getcwd()
    hwnd, host = prev.get("hwnd"), prev.get("host", "")
    if os.name == "nt" and not wsl and not (hwnd and window_alive(hwnd)):
        hwnd, host = find_host_window()  # for click-to-focus; re-found if the window was closed
    title = os.path.basename(cwd.rstrip("\\/")) or cwd
    topic = prev.get("topic", "")
    if is_home_or_root(cwd):  # no project folder to name it after: use the first words of the first prompt
        if not topic and event == "UserPromptSubmit":
            topic = " ".join(str(data.get("prompt") or "").split())[:40]
        title = topic or "~"
    record = {
        "id": session_id,
        "source": "claude-code",
        "env": "wsl" if wsl else ("windows" if os.name == "nt" else sys.platform),
        "distro": os.environ.get("WSL_DISTRO_NAME", "") if wsl else "",
        "ide": detect_ide() or prev.get("ide", ""),
        "title": title,
        "topic": topic,
        "hwnd": hwnd,
        "host": host,
        "agents": agents,
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
    if event == "PermissionRequest":
        answer_flow(os.path.dirname(target), path, aid)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
