"""Windows native window discovery and deliberate session focusing (no-ops elsewhere).

Imports no Tk and no application entry point; errors go to runtime.diagnostics.
"""
import os
import time

from .runtime import diagnostics


def _user32():
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.IsWindow.argtypes = u.IsIconic.argtypes = u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    return u


def _go_to_desktop_of(hwnd):
    """Windows 10/11 virtual desktops: switch to the desktop the window is on. SetForegroundWindow alone often
    leaves you where you are when the window lives on another desktop (the pet is on every desktop, so this happens)."""
    try:
        from pyvda import AppView, VirtualDesktop
        view = AppView(hwnd=int(hwnd))
        if view.is_pinned():
            return
        target = view.desktop
        if target.number != VirtualDesktop.current().number:
            target.go()
            time.sleep(0.15)  # let the switch animation hand over before activating the window
    except Exception:
        pass  # pyvda missing or this Windows build unsupported: focusing still works on the current desktop


def focus_hwnd(hwnd):
    """Bring a window to the front, on whatever virtual desktop it is. True if it really came to the front."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u, k32 = _user32(), ctypes.windll.kernel32
        h = int(hwnd)
        if not u.IsWindow(h):
            return False
        _go_to_desktop_of(h)
        if u.IsIconic(h):
            u.ShowWindow(h, 9)  # SW_RESTORE
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        u.BringWindowToTop.argtypes = [wintypes.HWND]

        def front():
            return u.GetForegroundWindow() == h

        u.SetForegroundWindow(h)
        if not front():
            # Windows only lets the foreground app hand the foreground on. Borrow it: attach to the thread that
            # owns the current foreground window for the call, then detach again.
            mine = k32.GetCurrentThreadId()
            theirs = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
            attached = bool(theirs and theirs != mine and u.AttachThreadInput(mine, theirs, True))
            try:
                u.BringWindowToTop(h)
                u.SetForegroundWindow(h)
            finally:
                if attached:
                    u.AttachThreadInput(mine, theirs, False)
        if not front():  # last resort: a tap of ALT unlocks SetForegroundWindow
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x12, 0, 2, 0)
            u.SetForegroundWindow(h)
        if not front():
            diagnostics.log_error(f"go to window: couldn't bring {h:#x} to the front")
        return front()
    except Exception as e:
        diagnostics.log_error(f"go to window: {e!r}")
        return False


def terminal_windows():
    """Visible Windows Terminal windows as [(hwnd, title)], frontmost first."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    u, out = _user32(), []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if u.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(128)
            u.GetClassNameW(hwnd, cls, 128)
            if cls.value == "CASCADIA_HOSTING_WINDOW_CLASS":
                title = ctypes.create_unicode_buffer(512)
                u.GetWindowTextW(hwnd, title, 512)
                out.append((int(hwnd), title.value))
        return True

    u.EnumWindows(visit, 0)
    return out


def claude_app_windows(exe_name="claude.exe"):
    """Visible top-level windows of a desktop app (the Claude app: claude.exe; the Codex app: codex.exe), frontmost
    first. The CLIs of the same names own no window (their terminal does), so they never show up here."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    u, k32, out = _user32(), ctypes.windll.kernel32, []
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    names = {}

    def exe(pid):
        if pid not in names:
            names[pid] = ""
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                buf, n = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                    names[pid] = os.path.basename(buf.value).lower()
                k32.CloseHandle(h)
        return names[pid]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if u.IsWindowVisible(hwnd) and not u.GetWindow(hwnd, 4) and u.GetWindowTextLengthW(hwnd) > 0:
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if exe(pid.value) == exe_name:
                out.append(int(hwnd))
        return True

    u.EnumWindows(visit, 0)
    return out


def _folder(d):
    return os.path.basename(str(d.get("cwd") or "").replace("\\", "/").rstrip("/")).lower()


def terminal_agent(title):
    """Which agent a terminal title looks like: "claude" (Claude Code puts ✳ or a braille spinner in front of its
    title), "codex", or "" when it doesn't say."""
    t = (title or "").lstrip()
    low = t.lower()
    if "codex" in low:
        return "codex"
    if "claude" in low or (t and (t[0] in "\u2733\u2736\u273b\u273d\u2722" or 0x2800 <= ord(t[0]) <= 0x28ff)):
        return "claude"
    return ""


def focus_wsl_terminal(d, others=()):
    """Best effort for sessions without a recorded window (WSL hooks can't see Windows process ids). Never picks a
    window another session recorded as its own (a Codex terminal, say), and keeps Claude and Codex apart: a terminal
    whose title says it runs the other agent is skipped, one that says it runs this agent is preferred. In order:
    a terminal whose title mentions the distro or project folder; for sessions run by the desktop app, that app's
    window; a terminal of this agent, else one that doesn't say; the agent's desktop app."""
    agent = "codex" if d.get("agent") == "codex" else "claude"
    if agent == "claude" and ("desktop" in (d.get("entry") or "").lower() or d.get("app") == "cowork"):
        # Run by the Claude desktop app (its Code tab, here in WSL): that app's window, always. Not a terminal that
        # happens to mention the distro or folder, and not "claimed": one app window hosts all of its sessions, so
        # a Cowork session having recorded it doesn't make it someone else's.
        app = claude_app_windows("claude.exe")
        if app:
            return focus_hwnd(app[0])
    claimed = {int(o["hwnd"]) for o in others if o.get("hwnd")}
    folder = _folder(d)
    needles = [n.lower() for n in (d.get("distro"), folder, d.get("raw_title")) if n]
    foreign = {f for f in (_folder(o) for o in others) if f and f != folder}
    wins = [(h, t, terminal_agent(t)) for h, t in terminal_windows() if h not in claimed]
    wins = [w for w in wins if w[2] in ("", agent)]  # never the other agent's terminal
    named = [w for w in wins if any(n in w[1].lower() for n in needles)]
    if named:  # this agent's own terminal first, then one that doesn't say
        return focus_hwnd(sorted(named, key=lambda w: w[2] != agent)[0][0])
    wins = [w for w in wins if not any(f in w[1].lower() for f in foreign)]
    app = [h for h in claude_app_windows(agent + ".exe") if h not in claimed]
    if app and (agent == "claude" and "desktop" in (d.get("entry") or "").lower() or not wins):
        return focus_hwnd(app[0])
    if wins:
        return focus_hwnd(sorted(wins, key=lambda w: w[2] != agent)[0][0])
    return bool(app) and focus_hwnd(app[0])
