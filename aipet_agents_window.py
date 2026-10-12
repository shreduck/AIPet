"""The agent network window: a live, terminal-looking map of one session's main agent and its subagents.

Top: a graph. Each agent is a node with a small copy of the pet's robot (red when that agent needs you), its name,
model, effort and context. Edges show what is going on: blue with dots flowing out while a subagent works, red while it
needs you, green with a dot flowing back once it is done. Next to each subagent: its latest thought or words.
Bottom: logs. [all] merges every agent's activity in time order, each line in its agent's colour; one tab per agent.
When an agent needs you, a small chip says so; clicking it (or Space) opens the prompt over the logs, with its answers.
The cogwheel (top right) drops down the window's settings: always on top, title bar, compact (one window for every
session, like the pet's compact mode; off: one window per session), and sliders for window, background and node
opacity and for the scale.

Keys: Tab / Right next agent, Shift+Tab / Left previous, A shared log, PgUp / PgDn other sessions, Space prompt,
1-9 answer, Esc fold the prompt / settings (or close).
"""
import aipetlib.ui.agent_shell as agent_shell
import aipetlib.ui.agent_controls as agent_controls
import aipetlib.ui.agent_prompts as agent_prompts
import aipetlib.ui.agent_logs as agent_logs
import aipetlib.ui.agent_graph as agent_graph
import json
import math
import sys
import time
import tkinter as tk
from tkinter import font as tkfont

import aipet as core
import aipet_agents as agents

REFRESH_MS = 900
FRAME_MS = 45
IS_MAC = sys.platform == "darwin"
ALPHA_MIN, ALPHA_STEP = 30, 5  # window opacity, %
OPACITY_STEP = 5  # background and node opacity, % (30-100), drawn as Tk stipple patterns (see dither())
SCALE_MIN, SCALE_MAX, SCALE_STEP = 50, 150, 5  # the window's text and drawing size, %
TEXT_SCALE_MIN, TEXT_SCALE_MAX, TEXT_SCALE_STEP = 50, 200, 5  # text relative to the window scale, %
ZOOM_MIN, ZOOM_MAX = 0.01, 3.0
OPTIONS_KEY = "agent_window"  # config.json
DEFAULT_OPTIONS = {"on_top": False, "alpha": 100, "bg_alpha": 100, "node_alpha": 100, "frameless": False,
                   "separate": False, "tabbed": True, "tidy": False, "scale": 100, "text_scale": 100, "log_lines": 6,
                   "log_cols": 60, "log_side": "bottom", "log_tail": True, "tools": True, "auto_fit": False}
LOG_SIDES = ("bottom", "right", "top", "left")
HINT = "tab/←→ agent · a all · pgup/pgdn session · space prompt · ctrl+wheel/+/- zoom · 0 reset · drag pan · esc close"
BG_OPACITY = IS_MAC or sys.platform == "win32"  # a see-through background needs Windows' or macOS' window support


def _snap(value, lo, hi, step):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = hi
    return int(min(hi, max(lo, round(value / step) * step)))


def load_options(cfg):
    opts = dict(DEFAULT_OPTIONS)
    saved = (cfg or {}).get(OPTIONS_KEY)
    if isinstance(saved, dict):
        for k, v in saved.items():
            if k in opts and isinstance(v, type(opts[k])) and not (isinstance(v, bool) and not isinstance(opts[k], bool)):
                opts[k] = v
    opts["alpha"] = _snap(opts["alpha"], ALPHA_MIN, 100, ALPHA_STEP)
    opts["bg_alpha"] = _snap(opts["bg_alpha"], ALPHA_MIN, 100, OPACITY_STEP)
    opts["node_alpha"] = _snap(opts["node_alpha"], ALPHA_MIN, 100, OPACITY_STEP)
    opts["scale"] = _snap(opts["scale"], SCALE_MIN, SCALE_MAX, SCALE_STEP)
    opts["text_scale"] = _snap(opts["text_scale"], TEXT_SCALE_MIN, TEXT_SCALE_MAX, TEXT_SCALE_STEP)
    opts["log_lines"] = _snap(opts["log_lines"], 3, 80, 1)
    opts["log_cols"] = _snap(opts["log_cols"], 20, 400, 1)
    if opts["log_side"] not in LOG_SIDES:
        opts["log_side"] = "bottom"
    return opts


def log_preview(text, width, font, marker_width):
    """Fit a collapsed entry to one display line, leaving room for its expand marker."""
    lines = text.strip().splitlines()
    first = (lines[0] if lines else "").expandtabs(4)
    big = len(lines) > 1 or font.measure(first) > width
    if not big:
        return first, False
    available = max(0, width - marker_width)
    if font.measure(first) <= available:
        return first, True
    lo, hi = 0, len(first)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(first[:mid] + "…") <= available:
            lo = mid
        else:
            hi = mid - 1
    return (first[:lo] + "…" if font.measure("…") <= available else ""), True


def formatted_log_content(text):
    """Display structured tool content with real line breaks; leave ordinary text and paths intact."""
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return text

    def lines(value, indent=0):
        pad = " " * indent
        if isinstance(value, dict):
            if not value:
                return [pad + "{}"]
            result = []
            for key, item in value.items():
                if isinstance(item, (dict, list)) or isinstance(item, str) and "\n" in item:
                    result.append(pad + str(key) + ":")
                    result.extend(lines(item, indent + 2))
                else:
                    result.append(pad + str(key) + ": " + (item if isinstance(item, str) else json.dumps(item)))
            return result
        if isinstance(value, list):
            if not value:
                return [pad + "[]"]
            result = []
            for item in value:
                nested = lines(item, indent + 2)
                result.append(pad + "- " + nested[0][indent + 2:])
                result.extend(nested[1:])
            return result
        shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return [pad + line for line in shown.split("\n")]

    return "\n".join(lines(value))


def key_color(hex_color):
    """A colour one notch off hex_color: made fully see-through (Windows -transparentcolor) without keying out text
    or lines drawn in the theme's own colours."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{r:02x}{g:02x}{b ^ 1:02x}"


def dither(opacity):
    """Tk can't draw half see-through, only stipple patterns: the pattern closest to this opacity ("" = solid,
    None = nothing drawn)."""
    if opacity >= 88:
        return ""
    if opacity >= 63:
        return "gray75"
    if opacity >= 38:
        return "gray50"
    return "gray25"


def needed_size(n_subs, scale, line, small, compact=False, side="bottom", depth=1):
    """(width, height) in pixels the window needs to show everything at this scale (s = 1.5 * scale), given the
    line heights of its main and small fonts at that scale. depth: how deep agents start agents (columns)."""
    s = 1.5 * scale
    cols = depth if depth > 1 else (1 if n_subs <= 4 else 2)
    rows = n_subs if cols == 1 else math.ceil(n_subs / cols)
    graph_w = 10 + 150 + (70 + cols * 200 if n_subs else 10)
    width = max(380, graph_w) * s
    main_h = line * 4 + 10 * s
    sub_h = line * 4 + 14 * s
    graph_h = max(main_h, rows * sub_h) + 20 * s
    head = line + 10 * s
    height = head + graph_h
    if not compact and side in ("left", "right"):  # the logs beside the graph: wider, not taller
        width += 260 * s
        height += small + 8 * s
    elif not compact:
        height += (small + 6 * s) + (line * 4 + 10 * s) + (small + 8 * s)  # tabs, 4 log lines, key bar
    return int(width), int(height)


PALETTES = {
    "light": {"bg": "#ffffff", "bar": "#f4f5f7", "node": "#ffffff", "line": "#d5d8dd", "tx": "#24262b",
              "mu": "#7c818a", "hi": "#16181c", "blue": "#185fa5", "amb": "#9a6410", "grn": "#3b6d11",
              "red": "#c0362c", "ambbg": "#fdf3e1", "redbg": "#fdeeee", "sel": "#e9edf3",
              "agents": ["#444441", "#534ab7", "#993c1d", "#0f6e56", "#993556", "#185fa5", "#854f0b", "#3b6d11",
                         "#5f5e5a"]},
    "dark": {"bg": "#15171c", "bar": "#1d2027", "node": "#1d2027", "line": "#3a3f49", "tx": "#c9ccd3",
             "mu": "#7a7f89", "hi": "#e8e9ec", "blue": "#7fb4f0", "amb": "#e6c48a", "grn": "#8fcf7a",
             "red": "#f08a80", "ambbg": "#2a2418", "redbg": "#331d1d", "sel": "#2a2e37",
             "agents": ["#e8e9ec", "#b3a6f0", "#f0a08a", "#6fd3b5", "#ed93b1", "#85b7eb", "#fac775", "#97c459",
                        "#b4b2a9"]},
}
STATUS = {"working": ("blue", "● working"), "needs_input": ("red", "! needs you"), "waiting": ("amb", "… waiting"),
          "done": ("grn", "✓ done"), "idle": ("mu", "· idle"), "error": ("red", "✕ error")}
ROBOT_STATE = {"working": "working", "needs_input": "needs_input", "waiting": "working", "done": "done",
               "idle": "idle", "error": "error"}
KIND_MARK = {"you": "you ›", "say": "", "think": "thinking:", "tool": "→", "spawn": "⇢", "error": "✕",
             "input": "input →", "output": "output ←"}


def mix(a, b, t):
    """Colour a blended toward colour b (t = 0: a, 1: b)."""
    try:
        ra, ga, ba = (int(a[i:i + 2], 16) for i in (1, 3, 5))
        rb, gb, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError, IndexError):
        return a
    t = min(1.0, max(0.0, t))
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def session_label(item):
    who = "codex" if item.get("agent") == "codex" else ("cowork" if item.get("app") == "cowork" else "claude code")
    name = item.get("title") or "session"
    if item.get("conv"):
        name += f" · {item['conv']}"
    return f"agent-network ▸ {name} · {who}"


def candidate_sessions(items):
    return [item for item in items or [] if item.get("source") == "CC" and item.get("session_id")
            and core.visible_hook_session(item)]


def pick_session(items, key=None):
    """The session to show: the one asked for, else one that needs you, else a working one, else the first."""
    sessions = candidate_sessions(items)
    for want in ((lambda i: i.get("key") == key) if key else None, lambda i: i.get("state") == "needs_input",
                 lambda i: i.get("state") == "working"):
        if want:
            hit = next((i for i in sessions if want(i)), None)
            if hit:
                return hit
    return sessions[0] if sessions else None


def suggestion_label(sug):
    """Claude Code's own "don't ask again" choice (a permission_suggestions entry), in words."""
    kind = sug.get("type")
    where = {"session": "for this session", "localSettings": "always, in this project (just you)",
             "projectSettings": "always, in this project", "userSettings": "always, everywhere"}.get(
        sug.get("destination"), "")
    if kind in ("addRules", "replaceRules"):
        rules = [r for r in sug.get("rules") or [] if isinstance(r, dict)]
        what = ", ".join(f"{r.get('toolName', '')}({r['ruleContent']})" if r.get("ruleContent") else
                         str(r.get("toolName", "")) for r in rules[:2]) or "this"
        verb = "allow" if sug.get("behavior", "allow") == "allow" else str(sug.get("behavior"))
        return f"{verb} {what} {where}".strip()
    if kind == "setMode":
        return f"switch to {sug.get('mode', '?')} mode {where}".strip()
    if kind == "addDirectories":
        dirs = ", ".join(str(d) for d in (sug.get("directories") or [])[:2])
        return f"allow access to {dirs} {where}".strip()
    return f"{kind or 'apply'} {where}".strip()


def prompt_agent(net, item):
    """Index of the agent the session's prompt belongs to (0 = main), or None when nothing is asked."""
    if not item or item.get("state") != "needs_input" or not item.get("request"):
        return None
    aid = str((item.get("request") or {}).get("agent") or item.get("wait_agent") or "")
    for i, a in enumerate(net["agents"]):
        if aid and a["id"] == aid:
            return i
    return 0


# ---- window helpers shared by the agent network window and its text pop-ups
def parse_geometry(geo):
    """"620x480+100+-20" -> (620, 480, 100, -20); None when there is none."""
    import re
    m = re.match(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$", str(geo or ""))
    if not m:
        return None
    w, h, x, y = m.groups()
    return int(w), int(h), int(x.replace("+-", "-").lstrip("+")), int(y.replace("+-", "-").lstrip("+"))


def set_caption(win, show):
    """Windows: a window's title bar on or off. Only the caption goes: the window keeps its resizable frame and its
    taskbar button (a borderless Tk window, overrideredirect, would lose both)."""
    try:
        import ctypes
        u = ctypes.windll.user32
        hwnd = int(win.wm_frame(), 16)
        GWL_STYLE, WS_CAPTION, WS_THICKFRAME = -16, 0x00C00000, 0x00040000
        style = u.GetWindowLongW(hwnd, GWL_STYLE)
        new = ((style | WS_CAPTION) if show else (style & ~WS_CAPTION)) | WS_THICKFRAME
        if new != style:
            u.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(new & 0xFFFFFFFF).value if new > 0x7FFFFFFF else new)
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)  # keep size / place / order
    except Exception as e:
        core.log_error(f"agent network title bar: {e!r}")


def has_caption(win):
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetWindowLongW(int(win.wm_frame(), 16), -16) & 0x00C00000)
    except Exception:
        return True


def work_area(win):
    """(left, top, right, bottom) of the usable part of the window's monitor: without the taskbar (Windows);
    None elsewhere."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]
        u = ctypes.windll.user32
        u.MonitorFromWindow.restype = wintypes.HANDLE
        mon = u.MonitorFromWindow(wintypes.HWND(int(win.wm_frame(), 16)), 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not u.GetMonitorInfoW(mon, ctypes.byref(info)):
            return None
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def toggle_maximize(win, holder):
    """Maximise a window, or back. Maximised fills the screen but leaves the taskbar (Windows) or the menu bar and
    the Dock (macOS) alone. A window without a title bar is placed over the work area by hand, since Windows would
    otherwise cover the taskbar. holder: an object that remembers the size to go back to."""
    try:
        if sys.platform == "win32" and not has_caption(win):
            if getattr(holder, "_restore_geo", None):
                win.geometry(holder._restore_geo)
                holder._restore_geo = None
                return "break"
            area = work_area(win)
            if area:
                import ctypes
                holder._restore_geo = win.geometry()
                left, top, right, bottom = area
                ctypes.windll.user32.SetWindowPos(int(win.wm_frame(), 16), 0, left, top, right - left,
                                                  bottom - top, 0x0004 | 0x0010)  # NOZORDER | NOACTIVATE
                return "break"
        if sys.platform == "win32" or IS_MAC:
            win.state("normal" if win.state() == "zoomed" else "zoomed")
        elif not win.overrideredirect():
            win.attributes("-zoomed", not win.attributes("-zoomed"))
        elif getattr(holder, "_restore_geo", None):  # a borderless window on Linux: by hand
            win.geometry(holder._restore_geo)
            holder._restore_geo = None
        else:
            holder._restore_geo = win.geometry()
            win.geometry(f"{win.winfo_screenwidth()}x{win.winfo_screenheight()}+0+0")
    except tk.TclError:
        pass
    return "break"


class TextPopup:
    """A reply in a window of its own: selectable text, a copy button at the top right, and its own minimise /
    maximise / close buttons when title bars are off. Its title (and taskbar entry) is the reply's first line."""

    def __init__(self, owner, title, text):
        self.owner, self.text = owner, text
        P, s = owner.P, owner.s
        frameless = owner.opts.get("frameless") and not IS_MAC
        w = self.win = tk.Toplevel(owner.root)
        w.withdraw()
        w.title(title)
        w.configure(bg=P["bg"])
        bar = tk.Frame(w, bg=P["bar"])
        bar.pack(fill="x")
        head = tk.Label(bar, text="▍" + title, font=owner.f_bold, bg=P["bar"], fg=P["hi"], anchor="w", width=1)
        buttons = [("[copy]", self.copy)]
        if frameless:
            buttons += [("–", lambda: w.iconify()), ("□", lambda: toggle_maximize(w, self)), ("✕", self.close)]
        for text_, fn in reversed(buttons):
            lab = tk.Label(bar, text=text_, font=owner.f_small if text_ == "[copy]" else owner.f_bold, bg=P["bar"],
                           fg=P["tx"], cursor="hand2", padx=owner.px(5))
            lab.pack(side="right")
            lab.bind("<Button-1>", lambda e, fn=fn: fn())
            if text_ == "[copy]":
                self.copy_btn = lab
        head.pack(side="left", fill="x", expand=True, padx=owner.px(4), pady=owner.px(3))
        for wdg in (bar, head):
            wdg.bind("<Button-1>", self._drag_start)
            wdg.bind("<B1-Motion>", self._drag_move)
            wdg.bind("<Double-Button-1>", lambda e: toggle_maximize(w, self))
        body = tk.Frame(w, bg=P["bg"])
        body.pack(fill="both", expand=True)
        sb = tk.Scrollbar(body)
        sb.pack(side="right", fill="y")
        t = self.t = tk.Text(body, wrap="word", bd=0, highlightthickness=0, font=owner.f_main, bg=P["bg"],
                             fg=P["tx"], padx=owner.px(10), pady=owner.px(8), cursor="xterm",
                             selectbackground=mix(P["blue"], P["bg"], 0.65), selectforeground=P["hi"],
                             inactiveselectbackground=mix(P["blue"], P["bg"], 0.75), yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.configure(command=t.yview)
        t.insert("1.0", text)
        t.configure(state="disabled")
        t.bind("<Control-c>", lambda e: owner._copy(t))
        t.bind("<Control-a>", lambda e: (t.tag_add("sel", "1.0", "end"), "break")[1])
        t.bind("<Button-1>", lambda e: t.focus_set(), add="+")
        w.bind("<Escape>", lambda e: self.close())
        width, height = owner.px(460), owner.px(320)
        try:
            x = owner.win.winfo_rootx() + owner.px(40)
            y = owner.win.winfo_rooty() + owner.px(40)
            w.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass
        w.deiconify()
        if frameless and sys.platform == "win32":
            set_caption(w, False)
        try:
            core.titlebar_theme(w)
        except Exception:
            pass
        w.lift()
        w.focus_force()

    def copy(self):
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(self.text)
            self.copy_btn.configure(text="[copied]")
            self.win.after(1200, lambda: self.copy_btn.configure(text="[copy]"))
        except tk.TclError:
            pass

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _drag_start(self, e):
        self._drag = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y())

    def _drag_move(self, e):
        if getattr(self, "_restore_geo", None):
            return  # maximised
        x0, y0, wx, wy = getattr(self, "_drag", (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y()))
        self.win.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")


class Slider:
    """A terminal-looking slider: [██████░░░░] 70%. Click or drag to set, scroll to step.
    live=False: the value is only applied when the mouse button is let go (or a moment after scrolling), for
    settings that resize the window under the pointer."""

    def __init__(self, win, parent, lo, hi, step, value, on_change, fmt=lambda v: f"{v}%", live=True):
        self.win, self.lo, self.hi, self.step, self.value = win, lo, hi, step, value
        self.on_change, self.fmt, self.live = on_change, fmt, live
        self._applied_value, self._job = value, None
        self.c = tk.Canvas(parent, highlightthickness=0, bd=0, cursor="hand2")
        for seq, fn in (("<Button-1>", self._click), ("<B1-Motion>", self._click),
                        ("<ButtonRelease-1>", lambda e: self._commit()),
                        ("<MouseWheel>", lambda e: self.nudge(1 if e.delta > 0 else -1)),
                        ("<Button-4>", lambda e: self.nudge(1)), ("<Button-5>", lambda e: self.nudge(-1)),
                        ("<Configure>", lambda e: self.draw())):
            self.c.bind(seq, fn)

    def size(self, w, h):
        self.c.configure(width=w, height=h)
        self.draw()

    def draw(self):
        win, c = self.win, self.c
        P = win.P
        try:
            c.delete("all")
            c.configure(bg=P["bar"])
            w, h = int(c.cget("width")), int(c.cget("height"))
            label_w = win.f_small.measure(" 150%")
            bracket = win.f_small.measure("[")
            x0, x1 = bracket, max(bracket + 12, w - label_w - bracket)
            cells = 12
            cw = (x1 - x0) / cells
            filled = round((self.value - self.lo) / max(1, self.hi - self.lo) * cells)
            c.create_text(0, h / 2, text="[", anchor="w", fill=P["mu"], font=win.f_small)
            for k in range(cells):
                c.create_rectangle(x0 + k * cw + 1, h * 0.25, x0 + (k + 1) * cw - 1, h * 0.75,
                                   fill=P["blue"] if k < filled else P["line"], outline="")
            c.create_text(x1, h / 2, text="]", anchor="w", fill=P["mu"], font=win.f_small)
            c.create_text(w, h / 2, text=self.fmt(self.value), anchor="e", fill=P["hi"], font=win.f_small)
            self._bar = (x0, x1)
        except (tk.TclError, ValueError):
            pass

    def _click(self, e):
        x0, x1 = getattr(self, "_bar", (0, 1))
        frac = min(1.0, max(0.0, (e.x - x0) / max(1, x1 - x0)))
        self.set(self.lo + frac * (self.hi - self.lo))

    def nudge(self, d):
        self.set(self.value + d * self.step)
        if not self.live:  # scrolling: apply once it settles
            try:
                if self._job:
                    self.c.after_cancel(self._job)
                self._job = self.c.after(450, self._commit)
            except tk.TclError:
                pass
        return "break"

    def set(self, value, notify=True):
        value = _snap(value, self.lo, self.hi, self.step)
        if value != self.value:
            self.value = value
            self.draw()
            if notify and self.live:
                self._applied_value = value
                self.on_change(value)
            elif not notify:
                self._applied_value = value

    def _commit(self):
        self._job = None
        if not self.live and self.value != self._applied_value:
            self._applied_value = self.value
            self.on_change(self.value)


class AgentNetworkWindow:
    def __init__(self, root, pet, key=None, pinned=False, on_options=None, cascade=0, at=None):
        """pinned: this window belongs to one session (one window per session): no switching sessions.
        on_options(opts, window, changed): called after a setting changed, so the app can apply it to its other
        agent network windows (and switch between one window and one per session)."""
        self.root, self.pet = root, pet
        self.pinned, self.on_options, self.cascade = pinned, on_options, cascade
        self.at = parse_geometry(at)  # where to open (the window this one replaces), else centred
        self.key = None
        self.net = {"agents": [], "log": []}
        self.item = None
        self.sel = -1  # -1: the shared log ("all")
        self.overlay_open = self.menu_open = self._has_prompt = False
        self.option = 0
        self._after = self._anim = self._save_job = None
        self._robots = {}
        self._log_sig = self._graph_sig = self._prompt_sig = self._tabs_sig = None
        self.edges, self._dots = [], []
        self.t0 = time.time()
        self.theme = None
        self.opts = load_options(getattr(pet, "cfg", {}))
        self.gz = 1.0  # the graph's own zoom (zoom buttons, ctrl + wheel, + / -)
        self.blocks = []  # (key, item, net) of every session the graph shows (several when compact and not tabbed)
        self._content, self._center_next, self._panned = None, True, False
        self._bands, self._leaving, self._titles, self._animating = {}, {}, [], False
        self._entries, self._expanded = [], set()  # log entries shown / unfolded
        self._log_filters = agents.default_log_filters()
        self.u = self.opts["scale"] / 100
        self.s = 1.5 * self.u
        # window attributes as set (only touched when they change). Starts at Tk's own defaults: setting -alpha or
        # -transparentcolor at all makes a Windows window "layered", which Tk then repaints badly while it is
        # dragged or resized, so a window that never needs them never gets them
        self._applied = {"topmost": False, "alpha": 1.0, "clear": ""}
        self._moving_until = 0.0
        self._drag = None
        self._fitting = False
        self._spin, self._spin_until = 0.0, 0.0
        self._released = set()  # prompts handed back to Codex (Answer in Codex): answer them in its window
        self.in_window = None  # index of the agent whose prompt can only be answered in the session's window
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.title("AIPet · agent network")
        w.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._bind_keys()
        self.show_session(key)
        self._place()
        w.deiconify()
        self._apply_options()
        self.raise_()
        self.win.after(250, self._settle)
        self._tick()
        self._animate()

    def _settle(self):
        """Once the window is on screen: default zoom, graph centred."""
        if self.alive() and self.opts.get("auto_fit"):
            self._maybe_auto_fit()
            return
        if self.alive() and not getattr(self, "_user_moved", False):
            self.gz = 1.0
            self._center_next = True
            self._draw_graph(force=True)
            self._update_zoombar()

    # ---- window
    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def raise_(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    def close(self):
        if self._save_job:
            self._save("scale")  # flush a slider change even if the window is closed immediately
        for job in (self._after, self._anim, self._save_job):
            if job:
                try:
                    self.win.after_cancel(job)
                except tk.TclError:
                    pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def px(self, n):
        return int(round(n * self.s))

    def needed(self, u=None):
        """Pixels the window needs at UI scale u (default: the current one)."""
        u = self.u if u is None else u
        r = u / self.u
        n = max(0, len(self.net["agents"]) - 1)
        depth = max([a.get("depth", 1) for a in self.net["agents"][1:]] or [1])
        return needed_size(n, u, self.f_main.metrics("linespace") * r, self.f_small.metrics("linespace") * r,
                           side=self.opts.get("log_side", "bottom"), depth=depth)

    def _place(self):
        w, h = self.needed()
        w, h = max(w, int(620 * self.s)), max(h, int(480 * self.s))
        try:
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            w, h = min(w, sw - 40), min(h, sh - 80)
            off = self.px(28) * (self.cascade % 8)  # one window per session: fanned out
            if self.at:  # where the window it replaces was (switching compact on / off)
                aw, ah, ax, ay = self.at
                self.win.geometry(f"{max(w, aw) if aw else w}x{max(h, ah) if ah else h}+{ax + off}+{ay + off}")
            else:
                self.win.geometry(f"{w}x{h}+{(sw - w) // 2 + off}+{(sh - h) // 2 + off}")
        except tk.TclError:
            pass
        self._update_minsize()

    def _update_minsize(self):
        """Keep controls usable; graph contents can zoom and pan independently."""
        try:
            self.win.minsize(min(self.px(320), self.win.winfo_screenwidth() - 40),
                             min(self.px(220), self.win.winfo_screenheight() - 80))
        except tk.TclError:
            pass

    # ---- settings (the cogwheel's drop-down)
    def tool(self, name, d=1):
        return agent_controls.tool(self, sys.modules[__name__], name, d)

    def _save(self, name, delay=0):
        return agent_controls._save(self, sys.modules[__name__], name, delay)

    def set_options(self, opts):
        return agent_controls.set_options(self, sys.modules[__name__], opts)

    def reset_appearance(self, opts, cascade=0):
        return agent_controls.reset_appearance(self, sys.modules[__name__], opts, cascade)

    def set_scale(self, u, save=True, grow=True):
        return agent_controls.set_scale(self, sys.modules[__name__], u, save, grow)

    def set_text_scale(self, value, save=True):
        return agent_controls.set_text_scale(self, sys.modules[__name__], value, save)

    def _font_scale(self):
        return agent_controls._font_scale(self, sys.modules[__name__])

    def _apply_scale(self, grow=True):
        return agent_controls._apply_scale(self, sys.modules[__name__], grow)

    # ---- the backdrop behind a see-through graph background
    def _sync_backdrop(self):
        return agent_controls._sync_backdrop(self, sys.modules[__name__])

    def _restack(self, focus=False):
        return agent_controls._restack(self, sys.modules[__name__], focus)

    def _window_event(self, e):
        return agent_controls._window_event(self, sys.modules[__name__], e)

    def _on_window_resize(self, e):
        return agent_controls._on_window_resize(self, sys.modules[__name__], e)

    def _apply_options(self, changed=None):
        return agent_controls._apply_options(self, sys.modules[__name__], changed)

    def _win_caption(self, show):
        return agent_controls._win_caption(self, sys.modules[__name__], show)

    def toggle_maximize(self, e=None):
        return agent_controls.toggle_maximize(self, sys.modules[__name__], e)

    def _on_resize(self, e=None):
        return agent_controls._on_resize(self, sys.modules[__name__], e)

    def _resized(self):
        return agent_controls._resized(self, sys.modules[__name__])

    def _drag_start(self, e):
        return agent_controls._drag_start(self, sys.modules[__name__], e)

    def _drag_move(self, e):
        return agent_controls._drag_move(self, sys.modules[__name__], e)

    def _resize_move(self, e, side="se"):
        return agent_controls._resize_move(self, sys.modules[__name__], e, side)

    # ---- building
    def _build(self):
        return agent_shell._build(self, sys.modules[__name__])

    def _bind_keys(self):
        """Keys work wherever the focus is in the window, except while typing your own answer."""
        w = self.win
        w.bind("<Button-1>", self._click_anywhere, add="+")

        def key(fn, typing_too=False):
            def handler(e):
                if self._typing() and not typing_too:
                    return None  # the entry gets the key
                if e.state & 0x4 and getattr(e, "keysym", "") not in ("plus", "minus", "equal", "KP_Add",
                                                                     "KP_Subtract", "0"):
                    return None  # Ctrl + a key: copying, selecting... not a shortcut of this window
                fn()
                return "break"
            return handler
        w.bind("<Tab>", key(lambda: self.step_agent(1), True))
        for seq in ("<Shift-Tab>", "<ISO_Left_Tab>"):
            try:
                w.bind(seq, key(lambda: self.step_agent(-1), True))
            except tk.TclError:
                pass
        w.bind("<Right>", key(lambda: self.step_option(1) if self.overlay_open else self.step_agent(1)))
        w.bind("<Left>", key(lambda: self.step_option(-1) if self.overlay_open else self.step_agent(-1)))
        w.bind("<Down>", key(lambda: self.step_option(1) if self.overlay_open else self.log.yview_scroll(1, "units")))
        w.bind("<Up>", key(lambda: self.step_option(-1) if self.overlay_open else self.log.yview_scroll(-1, "units")))
        for k in ("a", "A"):
            w.bind(k, key(lambda: self.select(-1)))
        for k in ("plus", "equal", "KP_Add"):
            w.bind(f"<{k}>", key(lambda: self.zoom(1.25)))
        for k in ("minus", "KP_Subtract"):
            w.bind(f"<{k}>", key(lambda: self.zoom(1 / 1.25)))
        w.bind("0", key(lambda: self.zoom(reset=True) if not self.overlay_open else None))
        w.bind("<Prior>", key(lambda: self.step_session(-1), True))
        w.bind("<Next>", key(lambda: self.step_session(1), True))
        w.bind("<space>", key(lambda: self.open_overlay(not self.overlay_open)))
        w.bind("<Escape>", key(self._escape, True))
        w.bind("<Return>", key(lambda: self.choose(self.option) if self.overlay_open else None))
        for n in range(1, 10):
            w.bind(str(n), key(lambda n=n: self.choose(n - 1) if self.overlay_open else None))

    def _escape(self):
        if self.stat_open:
            self.toggle_stat(None)
        elif self.menu_open:
            self.toggle_menu(False)
        elif self.overlay_open:
            self.open_overlay(False)
        else:
            self.close()

    def _click_anywhere(self, e):
        """A click outside the settings drop-down folds it; a borderless window takes the keyboard on any click."""
        try:
            inside = str(e.widget).startswith(str(self.menu)) or e.widget is self.cog
        except (tk.TclError, AttributeError):
            inside = True
        if self.menu_open and not inside:
            self.toggle_menu(False)
        try:
            in_stats = str(e.widget).startswith(str(self.statdrop)) or str(e.widget).startswith(str(self.statc))
        except (tk.TclError, AttributeError):
            in_stats = True
        if self.stat_open and not in_stats:
            self.toggle_stat(None)
        if self.opts["frameless"] and not self._typing():
            try:
                self.win.focus_force()
            except tk.TclError:
                pass

    def _copy(self, widget):
        try:
            text = widget.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        return "break"

    def _arrange(self):
        return agent_logs._arrange(self, sys.modules[__name__])

    def _size_log(self):
        return agent_logs._size_log(self, sys.modules[__name__])

    def _split_start(self, e):
        return agent_logs._split_start(self, sys.modules[__name__], e)

    def _split_move(self, e):
        return agent_logs._split_move(self, sys.modules[__name__], e)

    def _typing(self):
        try:
            return isinstance(self.win.focus_get(), tk.Entry)
        except (tk.TclError, KeyError):
            return False

    # ---- the cogwheel and its drop-down
    def _size_cog(self):
        return agent_controls._size_cog(self, sys.modules[__name__])

    def _draw_cog(self):
        return agent_controls._draw_cog(self, sys.modules[__name__])

    def toggle_menu(self, show=None):
        return agent_controls.toggle_menu(self, sys.modules[__name__], show)

    def _build_menu(self):
        return agent_controls._build_menu(self, sys.modules[__name__])

    def _slide(self, key, value):
        return agent_controls._slide(self, sys.modules[__name__], key, value)

    def _place_menu(self):
        return agent_controls._place_menu(self, sys.modules[__name__])

    # ---- theme
    @property
    def P(self):
        return PALETTES.get(core.T.get("name"), PALETTES["light"])

    def _apply_theme(self):
        return agent_controls._apply_theme(self, sys.modules[__name__])

    # ---- data
    def show_session(self, key=None):
        item = pick_session(getattr(self.pet, "_last_items", []), key)
        new_key = item.get("key") if item else None
        if self.pinned and key:
            new_key = key  # its own session, even after it ends
        if new_key != self.key:
            self.key, self.sel = new_key, -1
            self.open_overlay(False)
            self._log_sig = self._graph_sig = self._prompt_sig = None
        self._refresh()

    def step_session(self, d):
        if self.pinned:
            return "break"  # one window per session: this one stays on its session
        sessions = candidate_sessions(getattr(self.pet, "_last_items", []))
        if not sessions:
            return "break"
        keys = [i["key"] for i in sessions]
        i = keys.index(self.key) if self.key in keys else 0
        self.show_session(keys[(i + d) % len(keys)])
        return "break"

    def _tick(self):
        if not self.alive():
            return
        if time.time() < self._moving_until:  # being dragged / resized: catch up once it settles
            self._after = self.win.after(150, self._tick)
            return
        try:
            self._refresh()
        except Exception as e:  # never let one bad transcript kill the window
            core.log_error(f"agent network: {e!r}")
        self._after = self.win.after(REFRESH_MS, self._tick)

    def _refresh(self):
        self._apply_theme()
        items = getattr(self.pet, "_last_items", [])
        self.item = next((i for i in candidate_sessions(items) if i.get("key") == self.key), None)
        if self.item is None and not self.pinned:  # nothing picked yet, or the picked session is gone
            self.item = pick_session(items)
            self.key = self.item.get("key") if self.item else None
        sessions = candidate_sessions(items)
        keys = [i["key"] for i in sessions]
        self.count.configure(text=f"{keys.index(self.key) + 1}/{len(keys)}" if self.key in keys else "")
        for wdg in (self.prev_btn, self.next_btn, self.count):  # nothing to switch in a session's own window
            if self.pinned and wdg.winfo_manager():
                wdg.pack_forget()
        n_before = len(self.net["agents"])
        selected_id = self.net["agents"][self.sel]["id"] if 0 <= self.sel < n_before else None
        if not self.item:
            self.title.configure(text="agent-network ▸ " + ("session ended" if self.pinned and self.key
                                                            else "no sessions right now"))
            self.net = {"agents": [], "log": []}
        else:
            self.title.configure(text=session_label(self.item))
            try:
                self.win.title(f"AIPet · {self.item.get('title') or 'agent network'}" if self.pinned
                               else "AIPet · agent network")
            except tk.TclError:
                pass
            self.net = agents.read_network(self.item, include_tools=self.opts.get("tools", True))
        if len(self.net["agents"]) != n_before:
            self._update_minsize()
        self.blocks = [(self.key, self.item, self.net)] if self.item else []
        if self._stacked_mode():
            # compact, not tabbed: every session's graph, one under the other
            self.blocks = [(i["key"], i, self.net if i["key"] == self.key else
                            agents.read_network(i, include_tools=self.opts.get("tools", True)))
                           for i in sessions]
        self.stats = agents.network_stats(self.net)
        if self.stat_open:
            self._fill_stat()
        if selected_id is not None:
            self.sel = next((i for i, a in enumerate(self.net["agents"]) if a["id"] == selected_id), -1)
        elif self.sel >= len(self.net["agents"]):
            self.sel = -1
        self._draw_graph()
        self._draw_tabs()
        self._update_log_filters()
        self._draw_log()
        self._draw_prompt()
        self._maybe_auto_fit()

    # ---- selection
    def select(self, i, clear_filters=False):
        if clear_filters:
            self._log_filters = agents.default_log_filters()
        if 0 <= i < len(self.net["agents"]) and self.net["agents"][i].get("tool"):
            tool = self.net["agents"][i]
            self._log_filters.update(show_tools=True, primary=False, only_tool=tool["name"],
                                     tools=set() if agents.is_mcp_tool(tool["name"]) else {tool["name"]},
                                     mcp={tool["name"]} if agents.is_mcp_tool(tool["name"]) else set())
            i = next((j for j, a in enumerate(self.net["agents"]) if a["id"] == tool["parent"]), -1)
        self.sel = i
        self._graph_sig = self._log_sig = None
        self._draw_graph()
        self._draw_tabs()
        self._update_log_filters()
        self._draw_log()
        return "break"

    def step_agent(self, d):
        choices = [-1] + [i for i, a in enumerate(self.net["agents"]) if not a.get("tool")]
        if len(choices) > 1:
            at = choices.index(self.sel) if self.sel in choices else 0
            self.select(choices[(at + d) % len(choices)])
        return "break"

    def color(self, i):
        cols = self.P["agents"]
        return cols[i % len(cols)]

    # ---- graph
    def robot(self, state, height):
        return agent_graph.robot(self, sys.modules[__name__], state, height)

    # The graph is drawn at its own zoom (self.gz) on top of the window's scale: gpx() for sizes, g_* fonts.
    # Panning moves the canvas view over it (scan_mark / scan_dragto); everything else stays put.
    def gpx(self, n):
        return agent_graph.gpx(self, sys.modules[__name__], n)

    def _graph_fonts(self):
        return agent_graph._graph_fonts(self, sys.modules[__name__])

    def _graph_line_height(self):
        return agent_graph._graph_line_height(self, sys.modules[__name__])

    @staticmethod
    def _columns(net):
        """Subagents (indexes into net["agents"]) per column: one column per depth (agents of agents go further
        right, each kept with its parent's); only depth 1, and many of them: two columns. -> (columns, beside):
        beside = thoughts go to the right of the nodes (a single column of up to 4)."""
        ags = net["agents"]
        subs = list(range(1, len(ags)))
        if not subs:
            return [], False
        deepest = max(ags[i].get("depth", 1) for i in subs)
        if deepest <= 1:
            if len(subs) <= 4:
                return [subs], True
            half = math.ceil(len(subs) / 2)
            return [subs[:half], subs[half:]], False
        order = {"": 0}
        cols = []
        for d in range(1, deepest + 1):  # each depth after the one before, children under their parent's place
            col = [i for i in subs if ags[i].get("depth", 1) == d]
            col.sort(key=lambda i: (order.get(ags[i].get("parent", ""), 0), ags[i].get("first_t", 0)))
            for k, i in enumerate(col):
                order[ags[i]["id"]] = k
            cols.append(col)
        return cols, False

    def _block_height(self, net, H):
        return agent_graph._block_height(self, sys.modules[__name__], net, H)

    def _layout(self, net, W, H, reserve=None):
        return agent_graph._layout(self, sys.modules[__name__], net, W, H, reserve)

    def _draw_graph(self, force=False):
        return agent_graph._draw_graph(self, sys.modules[__name__], force)

    # ---- stacked sessions (compact, not tabbed): bands that fade out and slide up
    def _stacked_mode(self):
        return agent_graph._stacked_mode(self, sys.modules[__name__])

    FADE_S, SLIDE_S = 0.45, 0.35

    def _plan_bands(self, blocks, H, now):
        return agent_graph._plan_bands(self, sys.modules[__name__], blocks, H, now)

    def _band_y(self, band, now):
        return agent_graph._band_y(self, sys.modules[__name__], band, now)

    def _draw_band(self, b, key, item, net, y, bh, VW, head, tidy, fade):
        return agent_graph._draw_band(self, sys.modules[__name__], b, key, item, net, y, bh, VW, head, tidy, fade)

    def _draw_title(self, entry, now):
        return agent_graph._draw_title(self, sys.modules[__name__], entry, now)

    def _draw_block(self, b, key, net, ox, oy, W, H, reserve, fade=0.0, title=None):
        return agent_graph._draw_block(self, sys.modules[__name__], b, key, net, ox, oy, W, H, reserve, fade, title)

    def _title_click(self, key):
        return agent_graph._title_click(self, sys.modules[__name__], key)

    def _click_node(self, e, key, i):
        return agent_graph._click_node(self, sys.modules[__name__], e, key, i)

    def _pick_session(self, key):
        return agent_graph._pick_session(self, sys.modules[__name__], key)

    # ---- zoom and pan
    def zoom(self, factor=None, at=None, reset=False):
        return agent_graph.zoom(self, sys.modules[__name__], factor, at, reset)

    def center_view(self, by_user=False):
        return agent_graph.center_view(self, sys.modules[__name__], by_user)

    def _graph_bounds(self):
        return agent_graph._graph_bounds(self, sys.modules[__name__])

    def fit_all(self):
        return agent_graph.fit_all(self, sys.modules[__name__])

    def _maybe_auto_fit(self, force=False):
        return agent_graph._maybe_auto_fit(self, sys.modules[__name__], force)

    def _update_auto_fit(self):
        return agent_graph._update_auto_fit(self, sys.modules[__name__])

    def _scroll_to(self, left, top):
        return agent_graph._scroll_to(self, sys.modules[__name__], left, top)

    def _pan_start(self, e):
        return agent_graph._pan_start(self, sys.modules[__name__], e)

    def _pan_move(self, e):
        return agent_graph._pan_move(self, sys.modules[__name__], e)

    def _shift_into_view(self, margin=0.1, check_only=False):
        return agent_graph._shift_into_view(self, sys.modules[__name__], margin, check_only)

    def _pan_end(self, e):
        return agent_graph._pan_end(self, sys.modules[__name__], e)

    def _wheel(self, e):
        return agent_graph._wheel(self, sys.modules[__name__], e)

    def _update_zoombar(self):
        return agent_graph._update_zoombar(self, sys.modules[__name__])

    # ---- the stats node and its drop-downs (fixed at the bottom right of the graph, outside the zoom)
    def _stats_size(self):
        return agent_graph._stats_size(self, sys.modules[__name__])

    def _draw_stats(self):
        return agent_graph._draw_stats(self, sys.modules[__name__])

    def _show_tip(self, text, near, width):
        return agent_graph._show_tip(self, sys.modules[__name__], text, near, width)

    def _hide_tip(self):
        return agent_graph._hide_tip(self, sys.modules[__name__])

    def toggle_stat(self, kind):
        return agent_graph.toggle_stat(self, sys.modules[__name__], kind)

    def _stat_lines(self, kind):
        return agent_graph._stat_lines(self, sys.modules[__name__], kind)

    def _fill_stat(self):
        return agent_graph._fill_stat(self, sys.modules[__name__])

    @staticmethod
    def _fit(text, width, font):
        text = str(text or "")
        if width <= 0:
            return ""
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…" if text else ""

    def _animate(self):
        return agent_graph._animate(self, sys.modules[__name__])

    # ---- tabs and log
    def set_log_filter(self, key, value):
        return agent_logs.set_log_filter(self, sys.modules[__name__], key, value)

    def clear_log_filters(self):
        return agent_logs.clear_log_filters(self, sys.modules[__name__])

    def _update_log_filters(self):
        return agent_logs._update_log_filters(self, sys.modules[__name__])

    def _layout_log_filters(self, event=None):
        return agent_logs._layout_log_filters(self, sys.modules[__name__], event)

    def _choose_tools(self, group, selected):
        return agent_logs._choose_tools(self, sys.modules[__name__], group, selected)

    def _toggle_log_tool(self, name, mcp):
        return agent_logs._toggle_log_tool(self, sys.modules[__name__], name, mcp)

    def open_tool_filters(self, mcp):
        return agent_logs.open_tool_filters(self, sys.modules[__name__], mcp)

    def _draw_tabs(self):
        return agent_logs._draw_tabs(self, sys.modules[__name__])

    def _update_tailbtn(self):
        return agent_logs._update_tailbtn(self, sys.modules[__name__])

    def _draw_log(self, keep=None):
        return agent_logs._draw_log(self, sys.modules[__name__], keep)

    def _position_log_borders(self, *args):
        return agent_logs._position_log_borders(self, sys.modules[__name__], *args)

    # ---- log entries: fold / unfold, copy, open in a window
    def _entry_click(self, n):
        return agent_logs._entry_click(self, sys.modules[__name__], n)

    def _toggle_entry(self, n):
        return agent_logs._toggle_entry(self, sys.modules[__name__], n)

    def _entry_menu(self, e, n):
        return agent_logs._entry_menu(self, sys.modules[__name__], e, n)

    # ---- the prompt
    def _draw_prompt(self):
        return agent_prompts._draw_prompt(self, sys.modules[__name__])

    def open_overlay(self, show):
        return agent_prompts.open_overlay(self, sys.modules[__name__], show)

    def _place_overlay(self):
        return agent_prompts._place_overlay(self, sys.modules[__name__])

    def _build_overlay(self, name, req, item):
        return agent_prompts._build_overlay(self, sys.modules[__name__], name, req, item)

    def _flow_options(self, width):
        return agent_prompts._flow_options(self, sys.modules[__name__], width)

    def _mark_option(self):
        return agent_prompts._mark_option(self, sys.modules[__name__])

    def _pick(self, n):
        return agent_prompts._pick(self, sys.modules[__name__], n)

    def _own_typed(self, n):
        return agent_prompts._own_typed(self, sys.modules[__name__], n)

    def _send_own(self):
        return agent_prompts._send_own(self, sys.modules[__name__])

    def step_option(self, d):
        return agent_prompts.step_option(self, sys.modules[__name__], d)

    def choose(self, n):
        return agent_prompts.choose(self, sys.modules[__name__], n)

    def _send(self, behavior, answers=None, permissions=None):
        return agent_prompts._send(self, sys.modules[__name__], behavior, answers, permissions)

    def _go(self):
        return agent_prompts._go(self, sys.modules[__name__])

    def _always(self):
        return agent_prompts._always(self, sys.modules[__name__])
