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
OPTIONS_KEY = "agent_window"  # config.json
DEFAULT_OPTIONS = {"on_top": False, "alpha": 100, "bg_alpha": 100, "node_alpha": 100, "frameless": False,
                   "separate": False, "tabbed": True, "tidy": False, "scale": 100, "log_lines": 6,
                   "log_cols": 60, "log_side": "bottom", "log_tail": True}
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
    opts["log_lines"] = _snap(opts["log_lines"], 3, 80, 1)
    opts["log_cols"] = _snap(opts["log_cols"], 20, 400, 1)
    if opts["log_side"] not in LOG_SIDES:
        opts["log_side"] = "bottom"
    return opts


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
KIND_MARK = {"you": "you ›", "say": "", "think": "thinking:", "tool": "→", "spawn": "⇢", "error": "✕"}


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
    return [i for i in items or [] if i.get("source") == "CC" and i.get("session_id")]


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
        """Never smaller than everything needs at the smallest scale; between that and the current scale's needs,
        shrinking the window shrinks the scale with it (_on_window_resize)."""
        try:
            self.win.minsize(*self.needed(SCALE_MIN / 100))
        except tk.TclError:
            pass

    # ---- settings (the cogwheel's drop-down)
    def tool(self, name, d=1):
        o = self.opts
        if name == "close":
            self.close()
            return "break"
        toggles = {"frame": "frameless", "pin": "on_top", "each": "separate", "tabs": "tabbed", "tidy": "tidy",
                   "tail": "log_tail"}
        if name in toggles:
            o[toggles[name]] = not o[toggles[name]]
            if name == "tail":
                self._update_tailbtn()
                if o["log_tail"]:
                    self.log.see("end")
        elif name == "alpha":
            o["alpha"] = _snap(o["alpha"] + d * ALPHA_STEP, ALPHA_MIN, 100, ALPHA_STEP)
        elif name == "logs":
            o["log_side"] = LOG_SIDES[(LOG_SIDES.index(o.get("log_side", "bottom")) + d) % len(LOG_SIDES)]
            self._arrange()
            self._center_next = True
        elif name == "nodes":
            o["node_alpha"] = _snap(o["node_alpha"] + d * OPACITY_STEP, ALPHA_MIN, 100, OPACITY_STEP)
        self._spin_until = time.time() + 0.6  # the cog turns a little on every change
        if name == "tabs":
            self._center_next = True
            self._refresh()
        if name != "each":
            self._apply_options(changed=name)
            if self.menu_open:
                self._build_menu()
        self._save(name)
        return "break"

    def _save(self, name, delay=0):
        def save():
            self._save_job = None
            try:
                self.pet.cfg[OPTIONS_KEY] = dict(self.opts)
                core.save_setting(OPTIONS_KEY, dict(self.opts))
            except Exception as e:
                core.log_error(f"agent network options: {e!r}")
            if self.on_options:
                self.on_options(dict(self.opts), self, name)
        if self._save_job:
            try:
                self.win.after_cancel(self._save_job)
            except tk.TclError:
                pass
            self._save_job = None
        if delay:
            self._save_job = self.win.after(delay, save)
        else:
            save()

    def set_options(self, opts):
        """The same settings as another agent network window (changed there)."""
        old = self.opts
        self.opts = dict(opts)
        if self.opts.get("tabbed") != old.get("tabbed"):
            self._center_next = True
        if self.opts["scale"] != old.get("scale"):
            self.set_scale(self.opts["scale"] / 100, save=False)
        if any(self.opts.get(k) != old.get(k) for k in ("log_side", "log_lines", "log_cols")):
            self._arrange()
        self._apply_options()

    def reset_appearance(self, opts, cascade=0):
        """Default look again, centred on the main screen (the AIPet menu's Reset agent network appearance)."""
        self.toggle_menu(False)
        self.opts = dict(opts)
        self._arrange()
        self.set_scale(self.opts["scale"] / 100, save=False, grow=False)
        self._apply_options()
        self.cascade, self.at = cascade, None
        self._place()
        self.raise_()
        self._user_moved = False
        self.win.after(150, self._settle)  # and the graph at 100%, centred

    def set_scale(self, u, save=True, grow=True):
        """Resize every font and drawing. grow: make the window big enough for the new scale."""
        u = _snap(u * 100, SCALE_MIN, SCALE_MAX, SCALE_STEP) / 100
        if abs(u - self.u) < 1e-6:
            return
        self.u, self.s = u, 1.5 * u
        self.opts["scale"] = int(round(u * 100))
        self.f_main.configure(size=max(5, int(round(8 * self.s))))
        self.f_bold.configure(size=max(5, int(round(8 * self.s))))
        self.f_small.configure(size=max(5, int(round(7 * self.s))))
        self._robots.clear()
        self._graph_sig = self._tabs_sig = self._prompt_sig = self._log_sig = None
        self._update_minsize()
        if grow:
            try:
                w, h = self.needed()
                cw, ch = self.win.winfo_width(), self.win.winfo_height()
                if cw < w or ch < h:
                    self._fitting = True
                    self.win.geometry(f"{max(cw, w)}x{max(ch, h)}")
                    self.win.after(200, lambda: setattr(self, "_fitting", False))
            except tk.TclError:
                pass
        self._size_cog()
        if self.menu_open:  # resize the drop-down in place (rebuilding it would drop a slider being dragged)
            for sl in getattr(self, "_sliders", []):
                sl.size(self.px(150), self.f_small.metrics("linespace") + self.px(4))
            if getattr(self, "scale_slider", None):
                self.scale_slider.set(self.opts["scale"], notify=False)
            self._place_menu()
        self._refresh()
        if save:
            self._save("scale", delay=500)

    # ---- the backdrop behind a see-through graph background
    def _sync_backdrop(self):
        """Show / move / restyle the backdrop window: exactly under the graph, right below this window."""
        want = bool(self._applied.get("clear"))
        b = getattr(self, "backdrop", None)
        try:
            if not want:
                if b is not None:
                    b.destroy()
                self.backdrop = None
                return
            if b is None or not b.winfo_exists():
                b = self.backdrop = tk.Toplevel(self.win)
                b.withdraw()
                b.overrideredirect(True)
                for seq in ("<Button-1>", "<Button-3>"):
                    b.bind(seq, lambda e: self._restack(focus=True))
            P, o = self.P, self.opts
            b.configure(bg=P["bg"])
            b.attributes("-alpha", max(0.05, o["alpha"] * o["bg_alpha"] / 10000))
            b.attributes("-topmost", bool(o["on_top"]))
            c = self.canvas
            x, y, w, h = c.winfo_rootx(), c.winfo_rooty(), c.winfo_width(), c.winfo_height()
            if w > 1 and h > 1 and self.win.winfo_viewable():
                b.geometry(f"{w}x{h}+{x}+{y}")
                if b.state() != "normal":
                    b.deiconify()
                self._restack()
            elif b.state() == "normal":
                b.withdraw()
        except tk.TclError:
            pass

    def _restack(self, focus=False):
        """The backdrop right below this window (after this window was brought to the front, for instance)."""
        b = getattr(self, "backdrop", None)
        try:
            if b is not None and b.winfo_exists() and b.state() == "normal":
                b.lower(self.win)
            if focus:
                self.win.lift()
                self.win.focus_force()
        except tk.TclError:
            pass

    def _window_event(self, e):
        if e.widget is self.win:
            self.win.after_idle(self._sync_backdrop)

    def _on_window_resize(self, e):
        """Shrinking the window below what the current scale needs makes the scale follow it (down to 50%)."""
        if e.widget is self.win:
            self._moving_until = time.time() + 0.3  # moved or resized: animations pause until it settles
        if e.widget is self.win and getattr(self, "backdrop", None) is not None:
            self.win.after_idle(self._sync_backdrop)  # moved or resized: the backdrop follows
        if e.widget is not self.win or self._fitting or self.u <= SCALE_MIN / 100 + 1e-6:
            return
        nw, nh = self.needed()
        if e.width >= nw - 2 and e.height >= nh - 2:
            return
        fit = self.u * min(e.width / max(1, nw), e.height / max(1, nh))
        self.set_scale(max(SCALE_MIN / 100, math.floor(fit * 20) / 20), grow=False)

    def _apply_options(self, changed=None):
        o, P, w = self.opts, self.P, self.win
        # Window attributes only when they change: re-setting -alpha / -transparentcolor on Windows makes Tk redo
        # the layered window, which could resize it
        geo = None
        try:
            geo = w.geometry()
        except tk.TclError:
            pass
        bg = P["bg"]
        # Background opacity: the graph's background colour is made see-through, and a backdrop window of our own
        # sits right under it at the chosen opacity. Through the hole you see the backdrop (and, faintly, what is
        # behind it), and the backdrop takes the clicks, so nothing behind the window can be clicked by accident.
        see_through = BG_OPACITY and o["bg_alpha"] < 100
        want = {"topmost": bool(o["on_top"]), "alpha": o["alpha"] / 100,
                "clear": (key_color(P["bg"]) if not IS_MAC else "mac") if see_through else ""}
        try:
            if self._applied.get("topmost") != want["topmost"]:
                w.attributes("-topmost", want["topmost"])
            if self._applied.get("alpha") != want["alpha"]:
                w.attributes("-alpha", want["alpha"])
            if self._applied.get("clear") != want["clear"]:
                if IS_MAC:
                    w.attributes("-transparent", bool(want["clear"]))
                elif sys.platform == "win32":
                    w.attributes("-transparentcolor", want["clear"])
            self._applied = want
        except tk.TclError:
            pass
        if see_through:
            bg = "systemTransparent" if IS_MAC else want["clear"] or P["bg"]
        if sys.platform == "win32":
            self._win_caption(not o["frameless"])
        elif changed in (None, "frame") and not IS_MAC:
            try:
                if bool(w.overrideredirect()) != o["frameless"]:
                    w.withdraw()
                    w.overrideredirect(o["frameless"])
                    if geo:
                        w.geometry(geo)
                    w.deiconify()
                    w.focus_force()
            except tk.TclError:
                pass
        elif geo and changed in ("alpha", "bg_alpha", "pin", "nodes"):
            try:  # keep the size, whatever the window manager did
                w.after_idle(lambda g=geo: w.geometry(g) if w.geometry().split("+")[0] != g.split("+")[0] else None)
            except tk.TclError:
                pass
        try:
            self.canvas.configure(bg=bg)  # only the graph goes see-through: the logs stay readable
        except tk.TclError:
            pass
        self._sync_backdrop()
        frameless = o["frameless"] and not IS_MAC
        if frameless:
            self.close_btn.pack(side="right", before=self.cog)
        else:
            self.close_btn.pack_forget()
        if frameless:  # (Windows' own edges are only a few pixels wide: these give more to grab)
            # no title bar, so no window frame to drag: thin handles along the edges, and the corner grip
            t = max(6, self.px(4))  # a frame wide enough to grab
            for side, h in self.handles.items():
                spot = {"e": dict(relx=1.0, y=0, anchor="ne", width=t, relheight=1.0),
                        "w": dict(x=0, y=0, anchor="nw", width=t, relheight=1.0),
                        "s": dict(x=0, rely=1.0, anchor="sw", height=t, relwidth=1.0),
                        "sw": dict(x=0, rely=1.0, anchor="sw", width=3 * t, height=3 * t)}[side]
                h.configure(bg=self.P["bar"], highlightthickness=0)
                h.place(**spot)
                h.lift()
            self.grip.place(relx=1.0, rely=1.0, anchor="se")
            self.grip.lift()
        else:
            self.grip.place_forget()
            for h in self.handles.values():
                h.place_forget()
        self._update_minsize()
        self._graph_sig = self._tabs_sig = None
        self._draw_graph(force=True)
        self._draw_tabs()

    def _win_caption(self, show):
        set_caption(self.win, show)

    def toggle_maximize(self, e=None):
        """Double-click the top bar: maximise (leaving the taskbar / Dock alone), or back."""
        return toggle_maximize(self.win, self)

    def _on_resize(self, e=None):
        """The graph is redrawn once the size has settled (redrawing on every step of a resize drag flickers)."""
        if getattr(self, "_resize_job", None):
            try:
                self.win.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self._resize_job = self.win.after(70, self._resized)

    def _resized(self):
        self._resize_job = None
        if not getattr(self, "_user_moved", False):
            self._center_next = True  # until you pan or zoom, the graph stays centred as the window opens / resizes
        self._draw_graph(force=True)
        if getattr(self, "backdrop", None) is not None:
            self.win.after_idle(self._sync_backdrop)
        if self.overlay_open:
            self._place_overlay()
        if self.menu_open:
            self._place_menu()

    def _drag_start(self, e):
        self._drag = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y(), self.win.winfo_width(),
                      self.win.winfo_height())

    def _drag_move(self, e):
        try:
            if self.win.state() == "zoomed" or getattr(self, "_restore_geo", None):
                return  # maximised: stays put (double-click the bar to restore)
        except tk.TclError:
            pass
        if self._drag:
            x0, y0, wx, wy, _, _ = self._drag
            self.win.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _resize_move(self, e, side="se"):
        """Resize a window without a title bar from its edges (the left ones move it too)."""
        if self._drag:
            x0, y0, wx, wy, ww, wh = self._drag
            mw, mh = self.needed(SCALE_MIN / 100)
            dx, dy = e.x_root - x0, e.y_root - y0
            w = ww + dx if "e" in side else (ww - dx if "w" in side else ww)
            h = wh + dy if "s" in side else wh
            w, h = max(mw, w), max(mh, h)
            x = wx + (ww - w) if "w" in side else wx
            self.win.geometry(f"{w}x{h}+{x}+{wy}")

    # ---- building
    def _build(self):
        s = self.s
        self.f_main = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(5, int(round(8 * s))))
        self.f_bold = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(5, int(round(8 * s))), weight="bold")
        self.f_small = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(5, int(round(7 * s))))
        self.g_main = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(5, int(round(8 * s))))
        self.g_bold = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(5, int(round(8 * s))), weight="bold")
        self.g_small = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(5, int(round(7 * s))))
        w = self.win
        self.head = tk.Frame(w)
        self.head.pack(fill="x")
        # Packed right to left before the title, and the title asks for no width of its own: whatever the scale,
        # the cogwheel and the session arrows keep their place and the title gets cut instead
        self.cog = tk.Canvas(self.head, highlightthickness=0, bd=0, cursor="hand2")
        self.cog.pack(side="right", padx=(self.px(6), self.px(6)))
        self.cog.bind("<Button-1>", lambda e: self.toggle_menu())
        self.cog.bind("<Enter>", lambda e: setattr(self, "_cog_hover", True))
        self.cog.bind("<Leave>", lambda e: setattr(self, "_cog_hover", False))
        self.close_btn = tk.Label(self.head, text="✕", font=self.f_small, cursor="hand2", padx=self.px(4))
        self.close_btn.bind("<Button-1>", lambda e: self.close())
        self.next_btn = tk.Label(self.head, text="›", font=self.f_bold, cursor="hand2", padx=self.px(4))
        self.next_btn.pack(side="right")
        self.count = tk.Label(self.head, font=self.f_small)
        self.count.pack(side="right")
        self.prev_btn = tk.Label(self.head, text="‹", font=self.f_bold, cursor="hand2", padx=self.px(4))
        self.prev_btn.pack(side="right")
        self.prompt_mark = tk.Label(self.head, text="▍", font=self.f_bold)
        self.prompt_mark.pack(side="left")
        self.title = tk.Label(self.head, font=self.f_main, anchor="w", width=1)
        self.title.pack(side="left", fill="x", expand=True, pady=self.px(3))
        for wdg in (self.head, self.title, self.prompt_mark):  # drag the bar to move (needed without a title bar)
            wdg.bind("<Button-1>", self._drag_start)
            wdg.bind("<B1-Motion>", self._drag_move)
            wdg.bind("<Double-Button-1>", self.toggle_maximize)
        self.prev_btn.bind("<Button-1>", lambda e: self.step_session(-1))
        self.next_btn.bind("<Button-1>", lambda e: self.step_session(1))
        self._size_cog()

        self.footbar = tk.Frame(w)
        self.footbar.pack(side="bottom", fill="x")
        self.foot = tk.Label(self.footbar, font=self.f_small, anchor="w", padx=self.px(8), pady=self.px(3), text=HINT)
        self.foot.pack(side="left", fill="x", expand=True)
        self.grip = tk.Label(w, text="◢", font=self.f_small, cursor="size_nw_se", padx=self.px(2), pady=0)
        self.grip.bind("<Button-1>", self._drag_start)
        self.grip.bind("<B1-Motion>", self._resize_move)
        cursors = {"e": "sb_h_double_arrow", "w": "sb_h_double_arrow", "s": "sb_v_double_arrow",
                   "sw": "size_ne_sw" if sys.platform == "win32" else "bottom_left_corner"}
        self.handles = {}
        for side, cur in cursors.items():
            try:
                h = tk.Frame(w, cursor=cur, bd=0, highlightthickness=0)
            except tk.TclError:
                h = tk.Frame(w, bd=0, highlightthickness=0)
            h.bind("<Button-1>", self._drag_start)
            h.bind("<B1-Motion>", lambda e, side=side: self._resize_move(e, side))
            self.handles[side] = h
        self.body = tk.Frame(w)
        self.body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.body, highlightthickness=0, bd=0, height=self.px(150), width=self.px(200))
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._pan_end, add="+")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(seq, self._wheel)
        # zoom controls (top left of the graph) and the stats node (bottom right): fixed, outside the zoom
        self.zoombar = tk.Frame(self.canvas, bd=0, highlightthickness=1)
        self.zoom_btns = []
        for text, fn in (("[-]", lambda: self.zoom(1 / 1.25)), (None, None), ("[+]", lambda: self.zoom(1.25)),
                         ("[reset]", lambda: self.zoom(reset=True)), ("[center]", lambda: self.center_view(by_user=True))):
            lab = tk.Label(self.zoombar, text=text or "100%", font=self.f_small, cursor="" if fn is None else "hand2",
                           padx=self.px(2))
            lab.pack(side="left")
            if fn:
                lab.bind("<Button-1>", lambda e, fn=fn: fn())
            else:
                self.zoom_label = lab
            self.zoom_btns.append(lab)
        self.zoombar.place(x=self.px(4), y=self.px(4), anchor="nw")
        self.statc = tk.Canvas(self.canvas, bd=0, highlightthickness=1)
        self.statc.place(relx=1.0, rely=1.0, x=-self.px(6), y=-self.px(6), anchor="se")
        self.tip = tk.Label(w, font=self.f_small, justify="left", anchor="w", padx=self.px(6), pady=self.px(3))
        # the split between graph and logs: drag it (the logs get up to 80% of the window)
        self.sep = tk.Frame(self.body, height=max(3, self.px(3)), width=max(3, self.px(3)))
        self.sep.bind("<Button-1>", self._split_start)
        self.sep.bind("<B1-Motion>", self._split_move)
        self.sep.bind("<ButtonRelease-1>", lambda e: self._save("log_lines", delay=300))
        self.logbox = tk.Frame(self.body)
        self.tabs = tk.Frame(self.logbox)
        self.tabs.pack(fill="x", padx=self.px(6))
        self.log = tk.Text(self.logbox, height=6, width=40, wrap="word", bd=0, highlightthickness=0,
                           font=self.f_main, padx=self.px(8), pady=self.px(4), cursor="xterm", takefocus=0,
                           exportselection=True)
        self.log.pack(fill="both", expand=True)
        self._arrange()
        self.log.bind("<Control-c>", lambda e: self._copy(self.log))
        self.log.bind("<Control-a>", lambda e: (self.log.tag_add("sel", "1.0", "end"), "break")[1])
        self.log.bind("<Button-1>", lambda e: self.log.focus_set(), add="+")  # so Ctrl+C reaches it
        self.win.bind("<Configure>", self._on_window_resize, add="+")
        for seq in ("<FocusIn>", "<Map>", "<Unmap>"):  # brought to the front, minimised or restored
            self.win.bind(seq, self._window_event, add="+")

        self.chip = tk.Label(w, font=self.f_main, cursor="hand2", padx=self.px(6), pady=self.px(1), bd=1,
                             relief="solid")
        self.chip.bind("<Button-1>", lambda e: self.open_overlay(True))
        self.overlay = tk.Frame(w, bd=1, relief="solid")
        self.menu = tk.Frame(w, bd=0, highlightthickness=1)
        self.statdrop = tk.Frame(w, bd=0, highlightthickness=1)
        self.stathead = tk.Label(self.statdrop, font=self.f_small, anchor="w", cursor="hand2")
        self.stathead.pack(fill="x", padx=self.px(6), pady=(self.px(3), 0))
        self.stathead.bind("<Button-1>", lambda e: self.toggle_stat(self.stat_open))
        self.stattext = tk.Text(self.statdrop, font=self.f_small, bd=0, highlightthickness=0, wrap="none",
                                takefocus=0, cursor="xterm", padx=self.px(6), pady=self.px(3))
        self.stattext.bind("<Control-c>", lambda e: self._copy(self.stattext))
        self.stattext.bind("<Button-1>", lambda e: self.stattext.focus_set(), add="+")
        self.stattext.pack(fill="both", expand=True)
        self.stat_open = None
        self.stats = agents.network_stats({"agents": []})

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
        """The logs below, above, left or right of the graph (settings: logs position), split by a draggable line."""
        side = self.opts.get("log_side", "bottom")
        across = side in ("left", "right")
        for wdg in (self.canvas, self.sep, self.logbox):
            wdg.pack_forget()
        self.logbox.pack(side=side, fill="y" if across else "x")
        self.sep.pack(side=side, fill="y" if across else "x")
        self.sep.configure(cursor="sb_h_double_arrow" if across else "sb_v_double_arrow")
        self.canvas.pack(side=side, fill="both", expand=True)
        self._size_log()

    def _size_log(self):
        """The logs' size from the settings: lines (above / below) or a width in characters (left / right)."""
        if self.opts.get("log_side", "bottom") in ("left", "right"):
            self.log.configure(width=self.opts.get("log_cols", 60), height=1)
        else:
            self.log.configure(height=self.opts.get("log_lines", 6), width=1)

    def _split_start(self, e):
        self._split = (e.x_root, e.y_root, self.log.winfo_width(), self.log.winfo_height())

    def _split_move(self, e):
        """Drag the split: the logs from a few lines (or 20 characters) up to 80% of the window."""
        side = self.opts.get("log_side", "bottom")
        x0, y0, w0, h0 = getattr(self, "_split", (e.x_root, e.y_root, self.log.winfo_width(), self.log.winfo_height()))
        if side in ("left", "right"):
            col = max(1, self.f_main.measure("0"))
            want = w0 + ((e.x_root - x0) if side == "left" else (x0 - e.x_root))
            most = int(self.win.winfo_width() * 0.8)
            cols = int(max(20, min(most // col, want // col)))
            if cols != self.opts.get("log_cols"):
                self.opts["log_cols"] = cols
                self._size_log()
            return
        line = max(1, self.f_main.metrics("linespace"))
        want = h0 + ((e.y_root - y0) if side == "top" else (y0 - e.y_root))
        most = int(self.win.winfo_height() * 0.8) - self.tabs.winfo_height() - self.px(8)
        lines = int(max(3, min(most // line, want // line)))
        if lines != self.opts.get("log_lines"):
            self.opts["log_lines"] = lines
            self._size_log()

    def _typing(self):
        try:
            return isinstance(self.win.focus_get(), tk.Entry)
        except (tk.TclError, KeyError):
            return False

    # ---- the cogwheel and its drop-down
    def _size_cog(self):
        d = self.f_bold.metrics("linespace")
        self.cog.configure(width=d, height=d)
        self._draw_cog()

    def _draw_cog(self):
        c, P = self.cog, self.P
        try:
            d = int(c.cget("width"))
            c.delete("all")
            c.configure(bg=P["bar"])
            cx = cy = d / 2
            r_out, r_in, r_hole = d * 0.46, d * 0.33, d * 0.13
            teeth = 8
            pts = []
            for k in range(teeth * 4):
                a = self._spin + k * math.pi / (teeth * 2)
                r = r_out if (k % 4) in (1, 2) else r_in
                pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
            col = P["blue"] if self.menu_open or getattr(self, "_cog_hover", False) else P["mu"]
            c.create_polygon(*pts, fill=col, outline="")
            c.create_oval(cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole, fill=P["bar"], outline="")
        except (tk.TclError, ValueError):
            pass

    def toggle_menu(self, show=None):
        self.menu_open = (not self.menu_open) if show is None else bool(show)
        if self.menu_open:
            if self.overlay_open:
                self.open_overlay(False)
            self._build_menu()
        else:
            self.menu.place_forget()
        self._draw_cog()
        return "break"

    def _build_menu(self):
        P, m = self.P, self.menu
        for wdg in m.winfo_children():
            wdg.destroy()
        m.configure(bg=P["bar"], highlightbackground=P["line"], highlightcolor=P["line"])
        pad = self.px(8)
        o = self.opts
        width = self.px(150)
        line = self.f_small.metrics("linespace")
        self._sliders = []

        def section(title, first=False):
            tk.Label(m, text=f"── {title} " + "─" * 24, font=self.f_small, bg=P["bar"], fg=P["mu"], anchor="w").pack(
                fill="x", padx=pad, pady=(pad // 2 if first else self.px(6), 0))

        def toggle(name, opt, text, inverted=False):
            on = bool(o[opt]) != inverted
            lab = tk.Label(m, text=f"{'[on ]' if on else '[off]'} {text}", font=self.f_main, bg=P["bar"],
                           fg=P["blue"] if on else P["tx"], anchor="w", cursor="hand2")
            lab.pack(fill="x", padx=pad)
            lab.bind("<Button-1>", lambda e, n=name: (self.tool(n), "break")[1])

        def slider(text, lo, hi, step, value, on_change, live=True):
            row = tk.Frame(m, bg=P["bar"])
            row.pack(fill="x", padx=pad, pady=(self.px(2), 0))
            tk.Label(row, text=text.ljust(8), font=self.f_small, bg=P["bar"], fg=P["tx"]).pack(side="left")
            sl = Slider(self, row, lo, hi, step, value, on_change, live=live)
            sl.c.pack(side="left", fill="x", expand=True)
            sl.size(width, line + self.px(4))
            self._sliders.append(sl)
            return sl

        section("window", first=True)
        toggle("pin", "on_top", "always on top")
        if not IS_MAC:
            toggle("frame", "frameless", "title bar", inverted=True)
        toggle("each", "separate", "compact (one window for every session)", inverted=True)
        if not o["separate"] and not self.pinned:
            toggle("tabs", "tabbed", "  tabbed (one session at a time)")
        slider("opacity", ALPHA_MIN, 100, ALPHA_STEP, o["alpha"], lambda v: self._slide("alpha", v))
        self.scale_slider = slider("scale", SCALE_MIN, SCALE_MAX, SCALE_STEP, o["scale"],
                                   lambda v: self.set_scale(v / 100), live=False)

        section("graph")
        if not o["separate"] and not self.pinned and not o["tabbed"]:
            toggle("tidy", "tidy", "tidy (each session a titled group)")
        if BG_OPACITY:
            slider("backgrnd", ALPHA_MIN, 100, OPACITY_STEP, o["bg_alpha"], lambda v: self._slide("bg_alpha", v))
        slider("nodes", ALPHA_MIN, 100, OPACITY_STEP, o["node_alpha"], lambda v: self._slide("node_alpha", v))

        section("logs")
        side = tk.Label(m, text=f"[{o['log_side']:^6}] position (click to move)", font=self.f_main, bg=P["bar"],
                        fg=P["tx"], anchor="w", cursor="hand2")
        side.pack(fill="x", padx=pad)
        side.bind("<Button-1>", lambda e: (self.tool("logs"), "break")[1])
        toggle("tail", "log_tail", "tail (follow the newest line)")
        tk.Label(m, text="esc fold · click outside", font=self.f_small, bg=P["bar"], fg=P["mu"], anchor="w").pack(
            fill="x", padx=pad, pady=(self.px(6), pad // 2))
        self._place_menu()

    def _slide(self, key, value):
        self.opts[key] = value
        self._spin_until = time.time() + 0.4
        self._apply_options(changed=key)
        self._save(key, delay=400)

    def _place_menu(self):
        try:
            self.menu.place(relx=1.0, x=-self.px(4), y=self.head.winfo_height(), anchor="ne")
            self.menu.lift()
        except tk.TclError:
            pass

    # ---- theme
    @property
    def P(self):
        return PALETTES.get(core.T.get("name"), PALETTES["light"])

    def _apply_theme(self):
        name = core.T.get("name", "light")
        if name == self.theme:
            return
        self.theme = name
        P = self.P
        for wdg in (self.win, self.body, self.tabs):
            wdg.configure(bg=P["bg"])
        for wdg in (self.head, self.title, self.prev_btn, self.next_btn, self.count, self.foot, self.footbar,
                    self.grip, self.close_btn, self.prompt_mark):
            wdg.configure(bg=P["bar"])
        for wdg in (self.title, self.prev_btn, self.next_btn, self.close_btn):
            wdg.configure(fg=P["hi"])
        self.prompt_mark.configure(fg=P["blue"])
        for wdg in (self.count, self.foot, self.grip):
            wdg.configure(fg=P["mu"])
        self.canvas.configure(bg=P["bg"])
        self.sep.configure(bg=P["line"])
        self.log.configure(bg=P["bg"], fg=P["tx"], insertbackground=P["bg"], selectbackground=mix(P["blue"], P["bg"], 0.65),
                           selectforeground=P["hi"], inactiveselectbackground=mix(P["blue"], P["bg"], 0.75))
        self.chip.configure(bg=P["redbg"], fg=P["red"], highlightbackground=P["red"])
        self.zoombar.configure(bg=P["bar"], highlightbackground=P["line"])
        for lab in self.zoom_btns:
            lab.configure(bg=P["bar"], fg=P["mu"] if lab is self.zoom_label else P["tx"])
        self._statc_sig = None
        self.overlay.configure(bg=P["node"], highlightbackground=P["red"], highlightthickness=1, bd=0)
        try:
            core.titlebar_theme(self.win)
        except Exception:
            pass
        self._robots.clear()
        self._log_sig = self._graph_sig = self._prompt_sig = None
        if self._applied.get("clear"):
            self._applied["clear"] = None  # the see-through key colour follows the theme
        self._apply_options()
        self._draw_cog()

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
            self.net = agents.read_network(self.item)
        if len(self.net["agents"]) != n_before:
            self._update_minsize()
        self.blocks = [(self.key, self.item, self.net)] if self.item else []
        if self._stacked_mode():
            # compact, not tabbed: every session's graph, one under the other
            self.blocks = [(i["key"], i, self.net if i["key"] == self.key else agents.read_network(i))
                           for i in sessions]
        self.stats = agents.network_stats(self.net)
        if self.stat_open:
            self._fill_stat()
        if self.sel >= len(self.net["agents"]):
            self.sel = -1
        self._draw_graph()
        self._draw_tabs()
        self._draw_log()
        self._draw_prompt()

    # ---- selection
    def select(self, i):
        self.sel = i
        self._graph_sig = self._log_sig = None
        self._draw_graph()
        self._draw_tabs()
        self._draw_log()
        return "break"

    def step_agent(self, d):
        n = len(self.net["agents"])
        if n:
            i = self.sel + 1 + d  # -1 (all) .. n-1 -> 0 .. n
            self.select((i % (n + 1)) - 1)
        return "break"

    def color(self, i):
        cols = self.P["agents"]
        return cols[i % len(cols)]

    # ---- graph
    def robot(self, state, height):
        key = (state, height, self.theme)
        if key not in self._robots:
            from PIL import Image
            im = core.state_image(ROBOT_STATE.get(state, "idle")).convert("RGBA")
            if state == "needs_input":  # red when that agent needs you
                a = im.split()[3]
                tinted = Image.blend(im, Image.new("RGBA", im.size, (214, 48, 40, 255)), 0.55)
                tinted.putalpha(a)
                im = tinted
            width = max(1, round(im.width * height / im.height))
            im = im.resize((width, max(1, height)), Image.LANCZOS)
            self._robots[key] = core._to_photo(im)
        return self._robots[key]

    # The graph is drawn at its own zoom (self.gz) on top of the window's scale: gpx() for sizes, g_* fonts.
    # Panning moves the canvas view over it (scan_mark / scan_dragto); everything else stays put.
    def gpx(self, n):
        return int(round(n * self.s * self.gz))

    def _graph_fonts(self):
        size = max(4, int(round(8 * self.s * self.gz)))
        small = max(4, int(round(7 * self.s * self.gz)))
        for f, sz in ((self.g_main, size), (self.g_bold, size), (self.g_small, small)):
            if f.cget("size") != sz:
                f.configure(size=sz)

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
        """Height one session's graph needs (tabbed off: the sessions are stacked)."""
        cols, beside = self._columns(net)
        rows = max((len(c) for c in cols), default=0)
        lh = self.g_main.metrics("linespace")
        main_h = lh * 4 + self.gpx(10)
        sub_h = lh * 4 + self.gpx(8)
        return int(max(main_h, rows * (sub_h + self.gpx(6))) + 2 * self.gpx(10) + lh * 1.6)

    def _layout(self, net, W, H, reserve=None):
        """Node boxes (one per agent, in net["agents"] order): main on the left, its subagents in a column to its
        right, agents started by those further right. reserve: (x, y) of a box at the bottom right that the columns
        should stay above. -> (boxes, beside)"""
        lh = self.g_main.metrics("linespace")
        pad = self.gpx(10)
        main_w, main_h = self.gpx(150), lh * 4 + pad
        boxes = [None] * len(net["agents"])
        boxes[0] = (pad, max(pad, (H - main_h) // 2), main_w, main_h)
        cols, beside = self._columns(net)
        if not cols:
            return boxes, False
        sub_w = self.gpx(140)
        sub_h = lh * 4 + self.gpx(8)
        x0 = pad + main_w + self.gpx(70)
        gap_x = sub_w + self.gpx(60)
        if reserve and x0 + (len(cols) - 1) * gap_x + sub_w > reserve[0] - pad:
            H = max(sub_h + 2 * pad, reserve[1])
        for c, col in enumerate(cols):
            rows = len(col)
            step = max(sub_h + self.gpx(6), (H - 2 * pad) / max(1, rows))
            for row, i in enumerate(col):
                top = pad + (H - 2 * pad - step * rows) / 2 + row * step + (step - sub_h) / 2
                boxes[i] = (x0 + c * gap_x, int(max(pad // 2, top)), sub_w, sub_h)
        return boxes, beside

    def _draw_graph(self, force=False):
        c, P = self.canvas, self.P
        W, H = max(50, c.winfo_width()), max(50, c.winfo_height())
        blocks = self.blocks
        sig = (W, H, self.gz, self.sel, self.key, self.theme, self.s, self.opts["node_alpha"], self.in_window,
               tuple((k, tuple((a["id"], a.get("parent", ""), a.get("depth", 0),
                                a["name"], a["state"], a["model_label"], a["effort"], a["ctx"], a["thought"] if a["main"] else "")
                               for a in net["agents"])) for k, _, net in blocks))
        if sig == self._graph_sig and not force:
            self._draw_stats()
            return
        self._graph_sig = sig
        self._graph_fonts()
        # keep the view where it was (as a fraction of the drawing) unless a centre was asked for
        old = getattr(self, "_content", None)
        view = (c.canvasx(0), c.canvasy(0)) if old else None
        c.delete("all")
        self.edges, self._node_rects = [], []
        fading = self._stacked_mode() and (self._leaving or any(
            b.get("net", {}).get("agents") for b in self._bands.values()))  # the last session still fading out
        if (not blocks or not any(net["agents"] for _, _, net in blocks)) and not fading:
            c.configure(scrollregion=(0, 0, W, H))
            c.xview_moveto(0)
            c.yview_moveto(0)
            c.create_text(W // 2, H // 2, text="$ waiting for a session in claude code or codex_",
                          fill=P["mu"], font=self.f_main)
            self._dots, self._content = [], None
            self._draw_stats()
            return
        VW = max(W * self.gz, self.gpx(420))
        stacked = self._stacked_mode() or bool(self._leaving)
        sbox = self._stats_size()
        now = time.time()
        self._titles = []
        animating = False
        if stacked:
            y1 = self._plan_bands(blocks, H, now)
            tidy = self.opts.get("tidy")
            lh = self.g_main.metrics("linespace")
            head = int(lh * 1.6)
            order = [(k, None) for k in self._leaving] + [(k, b) for b, (k, _, _) in enumerate(blocks)]
            for key, b in order:
                leaving = key in self._leaving
                band = self._leaving[key] if leaving else self._bands[key]
                fade = min(1.0, (now - band["t0"]) / self.FADE_S) if leaving else 0.0
                y = band["y"] if leaving else self._band_y(band, now)
                animating = animating or leaving or now < band["t"] + self.SLIDE_S
                self._draw_band(b if b is not None else -1, key, band["item"], band["net"], y, band["h"], VW, head,
                                tidy, fade)
        else:
            self._bands, y1 = {}, 0
            key, item, net = blocks[0]
            bh = max(H * self.gz, self._block_height(net, H))
            self._bands[key] = {"from": 0, "to": 0, "t": 0, "y": 0, "item": item, "net": net, "h": bh}
            reserve = (VW - sbox[0] - self.gpx(8), bh - sbox[1] - self.gpx(8))
            self._draw_block(0, key, net, 0, 0, VW, bh, reserve)
            y1 = bh
        self._animating = animating
        x1 = VW
        content_old = self._content
        self._content = (x1, y1)
        # scroll region: the drawing plus a view's worth around it, so it can be panned anywhere
        c.configure(scrollregion=(-W, -H, x1 + W, y1 + H))
        layout = ("stacked",) if stacked else ("single", blocks[0][0])
        if layout != getattr(self, "_last_layout", None):
            self._last_layout, self._center_next, self._user_moved = layout, True, False
        if self._center_next or not old:
            if W > 80 and H > 80 and c.winfo_ismapped():
                self._center_next = False  # centred for real (not on the 1-pixel canvas of a window still opening)
            self.center_view()
        elif view and (animating or getattr(self, "_was_animating", False)):
            self._scroll_to(*view)  # sessions sliding: the view stays exactly where it is
        elif view:
            fx, fy = (view[0] + W / 2) / max(1, old[0]), (view[1] + H / 2) / max(1, old[1])
            self._scroll_to(fx * x1 - W / 2, fy * y1 - H / 2)
        self._was_animating = animating
        if not self._center_next and not self._shift_into_view(margin=0.0, check_only=True):
            self.center_view()  # nothing of the graph in sight (say, its only session moved): show it again
        self._dots = [c.create_oval(0, 0, 0, 0, fill=col, outline="", state="hidden")
                      for pts, st, col in self.edges for _ in range(2)]
        self._draw_stats()

    # ---- stacked sessions (compact, not tabbed): bands that fade out and slide up
    def _stacked_mode(self):
        return not self.pinned and not self.opts["separate"] and not self.opts["tabbed"]

    FADE_S, SLIDE_S = 0.45, 0.35

    def _plan_bands(self, blocks, H, now):
        """Where each session's band goes. A session that left fades where it was; the others then slide to their
        new place. Returns the drawing's height."""
        gap = self.gpx(14) if self.opts.get("tidy") else 0
        head = int(self.g_main.metrics("linespace") * 1.6)
        targets, heights, y = {}, {}, 0
        for key, item, net in blocks:
            heights[key] = self._block_height(net, H) + (self.gpx(6) if self.opts.get("tidy") else 0)
            targets[key] = y
            y += heights[key] + gap
        prev = getattr(self, "_bands", {}) or {}
        for key, band in prev.items():
            if key not in targets and key not in self._leaving and band.get("net", {}).get("agents"):
                self._leaving[key] = dict(band, y=self._band_y(band, now), t0=now)
        self._leaving = {k: v for k, v in self._leaving.items() if now - v["t0"] < self.FADE_S and k not in targets}
        delay = max([self.FADE_S - (now - v["t0"]) for v in self._leaving.values()] or [0])
        bands = {}
        for key, item, net in blocks:
            band = prev.get(key)
            if band is None:
                band = {"from": targets[key], "to": targets[key], "t": now - self.SLIDE_S}
            elif abs(band["to"] - targets[key]) > 0.5:  # its place changed: slide there (after any fade)
                band = {"from": self._band_y(band, now), "to": targets[key], "t": now + delay}
            bands[key] = dict(band, item=item, net=net, h=heights[key])
        self._bands = bands
        end = max([b["to"] + b["h"] for b in bands.values()] + [v["y"] + v["h"] for v in self._leaving.values()]
                  + [head])
        return end

    def _band_y(self, band, now):
        p = min(1.0, max(0.0, (now - band["t"]) / self.SLIDE_S))
        ease = 1 - (1 - p) ** 3
        return band["from"] + (band["to"] - band["from"]) * ease

    def _draw_band(self, b, key, item, net, y, bh, VW, head, tidy, fade):
        c, P = self.canvas, self.P
        F = (lambda col: mix(col, P["bg"], fade)) if fade else (lambda col: col)
        on = key == self.key and not fade
        tag = f"band{b}" if b >= 0 else f"gone{abs(hash(key)) % 10000}"
        title = session_label(item).replace("agent-network ▸ ", "")
        tbar = f"tbar{b}" if b >= 0 else tag  # the session's title bar: a click opens its [all] log
        pad = self.gpx(8)
        if tidy:  # a group: a title bar on top of the session's nodes, its name sliding when it doesn't fit
            x0, x1 = pad, VW - pad
            c.create_rectangle(x0, y + head, x1, y + bh, fill="", outline=F(P["blue"] if on else P["line"]),
                               dash=() if on else (2, 3))
            c.create_rectangle(x0, y, x1, y + head, fill=F(P["sel"] if on else P["bar"]),
                               outline=F(P["blue"] if on else P["line"]), tags=(tag, tbar))
            font = self.g_bold if on else self.g_main
            entry = (f"ttl{b}_{abs(hash(key)) % 10000}", ("● " if on else "○ ") + title, font,
                     F(P["hi"] if on else P["tx"]), x0 + pad, x1 - x0 - 2 * pad, y + head / 2, (tag, tbar))
            self._titles.append(entry)
            self._draw_title(entry, time.time())
        else:  # bands: a line between sessions; the name is a title bar on the session's main node
            far = 50000  # bands run on forever sideways, however far you pan
            c.create_rectangle(-far, y, VW + far, y + bh, fill=P["sel"] if on else P["bg"], outline="",
                               stipple="gray25" if on else "", tags=tag)
            c.create_line(-far, y, VW + far, y, fill=F(P["line"]))
        if b >= 0:
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=key: self._pick_session(k))
        self._draw_block(b, key, net, pad if tidy else 0, y + head, VW - (2 * pad if tidy else 0), bh - head, None,
                         fade=fade, title=None if tidy else (title, on, (tag, tbar)))
        if b >= 0:
            c.tag_bind(tbar, "<ButtonRelease-1>", lambda e, k=key: self._title_click(k))
            c.tag_bind(tbar, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tbar, "<Leave>", lambda e: c.configure(cursor=""))

    def _draw_title(self, entry, now):
        """A group's title: as is when it fits, else scrolling round like the pet's name tags (characters drawn one
        by one, only while wholly inside the bar: a Tk canvas can't clip text)."""
        tag, text, font, fill, left, room, ymid, click = entry
        c = self.canvas
        c.delete(tag)
        if font.measure(text) <= room:
            c.create_text(left, ymid, text=text, anchor="w", fill=fill, font=font, tags=(tag,) + tuple(click))
            return False
        loop_text = text + "    "
        loop = font.measure(loop_text)
        offset = (now * 30 * self.s * self.gz) % loop
        right = left + room
        for k in (0, 1):
            cx = left - offset + k * loop
            for ch in loop_text:
                cw = font.measure(ch)
                if cx >= right:
                    break
                if cx >= left and cx + cw <= right and not ch.isspace():
                    c.create_text(cx, ymid, text=ch, anchor="w", fill=fill, font=font, tags=(tag,) + tuple(click))
                cx += cw
        return True

    def _draw_block(self, b, key, net, ox, oy, W, H, reserve, fade=0.0, title=None):
        """One session's graph, at (ox, oy) in a W x H area. fade: 0 as is .. 1 gone (a session being cleared)."""
        c, P0 = self.canvas, self.P
        P = {k: (mix(v, P0["bg"], fade) if fade and isinstance(v, str) and v.startswith("#") else v)
             for k, v in P0.items()}
        ags = net["agents"]
        if not ags:
            return
        current = key == self.key
        boxes, beside = self._layout(net, W, H, reserve)
        boxes = [(x + ox, y + oy, w, h) for x, y, w, h in boxes]
        lh = self.g_main.metrics("linespace")
        index = {a["id"]: k for k, a in enumerate(ags)}
        for i, (a, (x, y, w, h)) in enumerate(zip(ags[1:], boxes[1:]), 1):  # edges first, under the nodes
            mx, my, mw, mh = boxes[index.get(a.get("parent", ""), 0)]  # from whoever started it
            p0, p3 = (mx + mw, my + mh / 2), (x, y + h / 2)
            mid = (p0[0] + p3[0]) / 2
            pts = (p0, (mid, p0[1]), (mid, p3[1]), p3)
            col = {"working": P["blue"], "needs_input": P["red"], "done": P["grn"]}.get(a["state"], P["line"])
            dash = () if a["state"] == "done" else (3, 4)
            line = [coord for t in range(0, 21) for coord in bezier(*pts, t / 20)]
            c.create_line(*line, fill=col, width=max(1, self.gpx(1)), dash=dash, smooth=True)
            if not fade:
                self.edges.append((pts, a["state"], col))
        for i, (a, (x, y, w, h)) in enumerate(zip(ags, boxes)):
            status_col, status = STATUS.get(a["state"], STATUS["idle"])
            border = P[status_col] if a["state"] != "idle" else P["line"]
            if a["main"] and a["state"] == "waiting":
                border = P["amb"]
            picked = current and i == self.sel
            fill = P["sel"] if picked else P["node"]
            tag = f"node{b}_{i}" if b >= 0 else "gone"
            if b >= 0:
                self._node_rects.append((x, y, x + w, y + h))
            c.create_rectangle(x, y, x + w, y + h, fill=fill, stipple=dither(self.opts["node_alpha"]),
                               outline=border, width=max(1, self.gpx(1.3 if picked else 1)), tags=tag)
            if a["main"] and title:  # the session's name as a title bar on top of its main node
                text, on, click = title
                th = int(lh * 1.35)
                c.create_rectangle(x, y - th, x + w, y, fill=P["sel"] if on else P["bar"],
                                   outline=P["blue"] if on else border, tags=click)
                entry = (f"mttl{b}_{abs(hash(key)) % 10000}", ("● " if on else "○ ") + text,
                         self.g_bold if on else self.g_small, P["hi"] if on else P["tx"], x + self.gpx(5),
                         w - self.gpx(10), y - th / 2, click)
                self._titles.append(entry)
                self._draw_title(entry, time.time())
            img = self.robot(a["state"], max(6, int(lh * 1.15)))
            if fade < 0.35:  # an image can't fade: it goes early
                c.create_image(x + self.gpx(6), y + self.gpx(5), image=img, anchor="nw", tags=tag)
            tx = x + self.gpx(6) + img.width() + self.gpx(5)
            inner = x + w - tx - self.gpx(4)
            c.create_text(tx, y + self.gpx(5), text=self._fit(a["name"], inner, self.g_bold), anchor="nw",
                          fill=mix(self.color(i), P0["bg"], fade) if fade else self.color(i), font=self.g_bold,
                          tags=tag)
            # a subagent's type (general-purpose, Explore...) when its name is its description, then model and effort
            info = " · ".join(v for v in ((a.get("kind") if not a["main"] else ""), a["model_label"], a["effort"]) if v)
            ctx = agents.fmt_tokens(a["ctx"])
            ctx_line = (f"ctx {ctx}" + (f"/{agents.fmt_tokens(a['ctx_max'])}" if a["ctx_max"] else "")) if ctx else ""
            rows = [info, ctx_line]  # the context always on a line of its own (what it says: in the logs)
            ly = y + self.gpx(5) + lh
            for row in rows:
                if row:
                    c.create_text(x + self.gpx(8), ly, text=self._fit(row, w - self.gpx(14), self.g_small),
                                  anchor="nw", fill=P["mu"], font=self.g_small, tags=tag)
                ly += lh
            label = status
            if current and i == self.in_window:
                label = "! answer in the window"
            if a["main"] and a["state"] == "waiting" and a["thought"]:
                label = "… " + a["thought"]
            c.create_text(x + self.gpx(8), y + h - self.gpx(4), text=self._fit(label, w - self.gpx(14), self.g_small),
                          anchor="sw", fill=P[status_col], font=self.g_small, tags=tag)
            if b >= 0:
                c.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=key, i=i: self._click_node(e, k, i))
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))

    def _title_click(self, key):
        """A click (not a drag) on a session's title bar: that session, with its [all] log."""
        if self._panned:
            return
        if key != self.key:
            self._pick_session(key)
        self.select(-1)

    def _click_node(self, e, key, i):
        if self._panned:
            return  # that was a drag
        if key != self.key:
            self._pick_session(key)
        self.select(i)
        if prompt_agent(self.net, self.item) == i:  # the agent that is asking: open its prompt
            self._draw_prompt()
            self.open_overlay(True)

    def _pick_session(self, key):
        if self._panned or key == self.key:
            return
        self.key, self.sel = key, -1
        self.open_overlay(False)
        self._log_sig = self._graph_sig = self._prompt_sig = self._tabs_sig = None
        self._refresh()

    # ---- zoom and pan
    def zoom(self, factor=None, at=None, reset=False):
        """Zoom the graph (0.4x - 3x) around a point of the view (default: its middle)."""
        c = self.canvas
        old = self.gz
        new = 1.0 if reset else min(3.0, max(0.4, round(old * factor, 2)))
        if abs(new - old) < 1e-3 and not reset:
            return "break"
        self._user_moved = not reset
        W, H = c.winfo_width(), c.winfo_height()
        mx, my = at if at else (W / 2, H / 2)
        content = getattr(self, "_content", None)
        frac = None
        if content:
            frac = (c.canvasx(mx) / max(1, content[0]), c.canvasy(my) / max(1, content[1]))
        self.gz = new
        self._robots.clear()
        self._center_next = reset
        self._draw_graph(force=True)
        if frac and not reset and self._content:
            self._scroll_to(frac[0] * self._content[0] - mx, frac[1] * self._content[1] - my)
            self._shift_into_view()
        self._update_zoombar()
        return "break"

    def center_view(self, by_user=False):
        """The drawing in the middle of the view (its top first when it's taller than the view)."""
        if by_user:
            self._user_moved = False
        c = self.canvas
        W, H = c.winfo_width(), c.winfo_height()
        x1, y1 = getattr(self, "_content", None) or (W, H)
        self._scroll_to((x1 - W) / 2, (y1 - H) / 2 if y1 <= H else 0)
        return "break"

    def _scroll_to(self, left, top):
        c = self.canvas
        try:
            x0, y0, x1, y1 = (float(v) for v in str(c.cget("scrollregion")).split())
        except ValueError:
            return
        c.xview_moveto((left - x0) / max(1, x1 - x0))
        c.yview_moveto((top - y0) / max(1, y1 - y0))

    def _pan_start(self, e):
        self._panned = False
        self._pan_from = (e.x, e.y)
        self.canvas.scan_mark(e.x, e.y)

    def _pan_move(self, e):
        fx, fy = getattr(self, "_pan_from", (e.x, e.y))
        if self._panned or abs(e.x - fx) + abs(e.y - fy) > 4:
            self._panned = self._user_moved = True
            self.canvas.configure(cursor="fleur")
            self.canvas.scan_dragto(e.x, e.y, gain=1)
            if self._shift_into_view():  # held at the edge: re-anchor, so the drag goes on from here
                self.canvas.scan_mark(e.x, e.y)

    def _shift_into_view(self, margin=0.1, check_only=False):
        """Keep at least one node at least partly inside the view (less a 10% margin all round): panning, scrolling or
        zooming stops there. Returns True when the view had to be moved (check_only: whether a node is in sight)."""
        c = self.canvas
        rects = getattr(self, "_node_rects", [])
        if not rects:
            return True if check_only else False
        W, H = c.winfo_width(), c.winfo_height()
        vx0, vy0 = c.canvasx(0) + W * margin, c.canvasy(0) + H * margin
        vx1, vy1 = c.canvasx(0) + W * (1 - margin), c.canvasy(0) + H * (1 - margin)
        best = None
        for x0, y0, x1, y1 in rects:
            dx = (x1 - vx0 - 1) if x1 < vx0 else ((x0 - vx1 + 1) if x0 > vx1 else 0)
            dy = (y1 - vy0 - 1) if y1 < vy0 else ((y0 - vy1 + 1) if y0 > vy1 else 0)
            if not dx and not dy:
                return True if check_only else False
            if best is None or abs(dx) + abs(dy) < abs(best[0]) + abs(best[1]):
                best = (dx, dy)
        if check_only:
            return False
        self._scroll_to(c.canvasx(0) + best[0], c.canvasy(0) + best[1])
        return True

    def _pan_end(self, e):
        if self._panned:
            self.canvas.configure(cursor="")
            self.win.after_idle(lambda: setattr(self, "_panned", False))

    def _wheel(self, e):
        """Ctrl + wheel zooms at the pointer; the wheel alone scrolls (shift: sideways)."""
        up = (getattr(e, "delta", 0) > 0) or getattr(e, "num", 0) == 4
        if e.state & 0x4 or (IS_MAC and e.state & 0x8):
            return self.zoom(1.15 if up else 1 / 1.15, at=(e.x, e.y))
        self._user_moved = True
        if e.state & 0x1:
            self.canvas.xview_scroll(-2 if up else 2, "units")
        else:
            self.canvas.yview_scroll(-2 if up else 2, "units")
        self._shift_into_view()
        return "break"

    def _update_zoombar(self):
        try:
            self.zoom_label.configure(text=f"{int(round(self.gz * 100))}%")
        except (tk.TclError, AttributeError):
            pass

    # ---- the stats node and its drop-downs (fixed at the bottom right of the graph, outside the zoom)
    def _stats_size(self):
        sl = self.f_small.metrics("linespace")
        return self.px(150), sl * 4 + self.px(10)

    def _draw_stats(self):
        sc, P, st = self.statc, self.P, self.stats
        w, h = self._stats_size()
        sig = (w, h, self.theme, repr(st), self.stat_open, self.opts["node_alpha"])
        if sig == getattr(self, "_statc_sig", None):
            return
        self._statc_sig = sig
        sl = self.f_small.metrics("linespace")
        try:
            sc.configure(width=w, height=h, bg=P["node"], highlightbackground=P["line"], highlightthickness=1)
            sc.delete("all")
        except tk.TclError:
            return
        sc.create_text(self.px(6), self.px(4), text="≡ this task", anchor="nw", fill=P["hi"], font=self.f_bold)
        if st.get("missing"):  # the task began before the part of a transcript that was read
            sc.create_text(w - self.px(6), self.px(4), text="ⓘ", anchor="ne", fill=P["mu"], font=self.f_bold,
                           tags="warn")
            sc.tag_bind("warn", "<Enter>", lambda e: self._show_tip(
                "For your info: this task started a long way back. AIPet reads only the last part of each transcript (about 256 KB "
                "for the main agent, 128 KB for a subagent), so older subagents, tool calls and skills may be "
                "missing from these lists.", sc, w))
            sc.tag_bind("warn", "<Leave>", lambda e: self._hide_tip())
        n_calls = sum(n for _, n in st["subagents"])
        tool_calls = sum(n for _, n in st["tools"])
        rows = (("subs", f"subagents {n_calls} ▾"), ("ctx", f"context {agents.fmt_tokens(st['ctx_total']) or 0} ▾"),
                ("tools", f"tools {len(st['tools'])} · {tool_calls} calls ▾"))
        for k, (kind, text) in enumerate(rows):
            tag = f"stat_{kind}"
            on = self.stat_open == kind
            sc.create_text(self.px(8), self.px(6) + sl * (k + 1), text=self._fit(text, w - self.px(12), self.f_small),
                           anchor="nw", fill=P["blue"] if on else P["tx"], font=self.f_small, tags=tag)
            sc.tag_bind(tag, "<Button-1>", lambda e, k=kind: self.toggle_stat(k))
            sc.tag_bind(tag, "<Enter>", lambda e: sc.configure(cursor="hand2"))
            sc.tag_bind(tag, "<Leave>", lambda e: sc.configure(cursor=""))

    def _show_tip(self, text, near, width):
        P = self.P
        try:
            self.tip.configure(text=text, bg=P["bar"], fg=P["tx"], wraplength=self.px(260),
                               highlightbackground=P["line"], highlightthickness=1)
            self.tip.place(in_=near, x=width, y=0, anchor="se")
            self.tip.lift()
        except tk.TclError:
            pass

    def _hide_tip(self):
        try:
            self.tip.place_forget()
        except tk.TclError:
            pass

    def toggle_stat(self, kind):
        """Open (or fold) a read-only drop-down of the stats node: subagents, every context, tools and skills."""
        if self.stat_open == kind:
            kind = None
        self.stat_open = kind
        if kind is None:
            self.statdrop.place_forget()
        else:
            if self.menu_open:
                self.toggle_menu(False)
            self._fill_stat()
        self._draw_stats()
        return "break"

    def _stat_lines(self, kind):
        st = self.stats
        if kind == "subs":
            if not st["subagents"]:
                return [("# no subagents in this task", "mu")]
            return [(f"{name:<22} ×{n}", "") for name, n in st["subagents"]]
        if kind == "ctx":
            lines = [(f"{'#':<3}{'agent':<18}{'model':<14}{'context':>8}", "mu")]
            for k, (name, model, ctx) in enumerate(st["calls"], 1):
                lines.append((f"{k:<3}{name[:17]:<18}{model[:13]:<14}{agents.fmt_tokens(ctx) or '-':>8}", ""))
            lines.append((f"{'':<3}{'total':<32}{agents.fmt_tokens(st['ctx_total']) or '0':>8}", "mu"))
            return lines
        lines = [(f"{name:<26} ×{n}", "") for name, n in st["tools"]] or [("# no tool calls in this task", "mu")]
        lines.append(("── skills ──", "mu"))
        lines += [(f"{name:<26} ×{n}", "") for name, n in st["skills"]] or [("# no skills used", "mu")]
        if st.get("missing"):
            lines.append(("ⓘ older entries may be missing (long task)", "mu"))
        return lines

    def _fill_stat(self):
        P, t = self.P, self.stattext
        lines = self._stat_lines(self.stat_open)
        sig = (self.stat_open, tuple(lines), self.theme, self.s)
        if sig == getattr(self, "_stat_sig", None) and self.statdrop.winfo_ismapped():
            return
        self._stat_sig = sig
        title = {"subs": "subagents used", "ctx": "context per agent call", "tools": "tools and skills"}[self.stat_open]
        self.stathead.configure(text=f"── {title} ── [esc]", bg=P["bar"], fg=P["mu"])
        self.statdrop.configure(bg=P["bar"], highlightbackground=P["line"], highlightcolor=P["line"])
        t.configure(state="normal", bg=P["bar"], fg=P["tx"], width=max(len(x) for x, _ in lines) + 1,
                    height=min(14, len(lines)))
        t.delete("1.0", "end")
        t.tag_configure("mu", foreground=P["mu"])
        t.tag_configure("warn", foreground=P["amb"])
        for text, tag in lines:
            t.insert("end", text + "\n", tag)
        t.configure(state="disabled")
        self.statdrop.place(in_=self.statc, relx=1.0, x=0, y=-self.px(4), anchor="se")
        self.statdrop.lift()

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
        if not self.alive():
            return
        if time.time() < self._moving_until:  # being dragged / resized: drawing now leaves trails on Windows
            self._anim = self.win.after(FRAME_MS, self._animate)
            return
        try:
            t = time.time() - self.t0
            r = max(2, self.gpx(2.2))
            for k, (pts, state, col) in enumerate(self.edges):
                for j in range(2):
                    if 2 * k + j >= len(self._dots):
                        break
                    dot = self._dots[2 * k + j]
                    if state == "working":
                        u = (t / 1.6 + j / 2) % 1.0  # two dots flowing out
                    elif state == "done" and j == 0:
                        u = 1 - (t / 2.4) % 1.0  # one flowing back with the result
                    else:
                        self.canvas.itemconfigure(dot, state="hidden")
                        continue
                    x, y = bezier(*pts, u)
                    self.canvas.coords(dot, x - r, y - r, x + r, y + r)
                    self.canvas.itemconfigure(dot, state="normal")
            if self._animating or self._leaving:  # sessions fading out / sliding into place
                self._draw_graph(force=True)
            else:
                now = time.time()
                for entry in self._titles:  # long group titles scroll like the pet's name tags
                    if entry[2].measure(entry[1]) > entry[5]:
                        self._draw_title(entry, now)
            # the cogwheel turns while hovered, while its drop-down is open, and for a moment after a change
            if self.menu_open or getattr(self, "_cog_hover", False) or time.time() < self._spin_until:
                self._spin = (self._spin + 0.12) % (2 * math.pi)
                self._draw_cog()
        except tk.TclError:
            pass
        self._anim = self.win.after(FRAME_MS, self._animate)

    # ---- tabs and log
    def _draw_tabs(self):
        P = self.P
        names = ["all"] + [a["name"] for a in self.net["agents"]]
        sig = (tuple(names), self.sel, self.theme, self.s)
        if self._tabs_sig == sig:
            return
        self._tabs_sig = sig
        for wdg in self.tabs.winfo_children():
            wdg.destroy()
        self.tailbtn = tk.Label(self.tabs, font=self.f_small, cursor="hand2", padx=self.px(3))
        self.tailbtn.pack(side="right")
        self.tailbtn.bind("<Button-1>", lambda e: self.tool("tail"))
        self._update_tailbtn()
        for k, name in enumerate(names):
            i = k - 1
            on = i == self.sel
            col = P["hi"] if i < 0 else self.color(i)
            lab = tk.Label(self.tabs, text=f"[{name}]", font=self.f_bold if on else self.f_small, cursor="hand2",
                           bg=col if on else P["bg"], fg=P["bg"] if on else col, padx=self.px(3), pady=self.px(1))
            lab.pack(side="left", padx=(0, self.px(3)), pady=self.px(2))
            lab.bind("<Button-1>", lambda e, i=i: self.select(i))

    def _update_tailbtn(self):
        P = self.P
        try:
            on = self.opts.get("log_tail", True)
            self.tailbtn.configure(text="[tail ▼]" if on else "[tail —]", bg=P["bg"], fg=P["blue"] if on else P["mu"])
        except (tk.TclError, AttributeError):
            pass

    def _draw_log(self, keep=None):
        """keep: the scroll position to stay at (folding an entry), instead of following the newest line."""
        P, log = self.P, self.log
        ags = self.net["agents"]
        if self.sel < 0:
            rows = self.net["log"][-160:]
        else:
            a = ags[self.sel]
            rows = [(t, self.sel, kind, text) for t, kind, text in a["lines"][-160:]]
        sig = (self.sel, self.theme, self.s, len(rows), rows[-1] if rows else None, len(ags))
        if sig == self._log_sig:
            return
        switched = (self._log_sig or (None,))[0] != sig[0]
        try:
            if not switched and log.tag_ranges("sel"):
                return  # you are selecting text: new lines wait until the selection is gone
        except tk.TclError:
            pass
        self._log_sig = sig
        top = log.yview()[0]
        log.configure(state="normal")
        log.delete("1.0", "end")
        for i in range(len(ags)):
            log.tag_configure(f"a{i}", foreground=self.color(i), font=self.f_bold)
        log.tag_configure("mu", foreground=P["mu"])
        log.tag_configure("think", foreground=P["blue"])
        log.tag_configure("you", foreground=P["hi"], font=self.f_bold)
        log.tag_configure("error", foreground=P["red"])
        log.tag_configure("spawn", foreground=P["amb"])
        if self.sel >= 0 and ags:
            a = ags[self.sel]
            info = " · ".join(v for v in (a["model_label"], a["effort"],
                                          f"ctx {agents.fmt_tokens(a['ctx'])}" if a["ctx"] else "") if v)
            log.insert("end", f"$ tail {a['name']} ", f"a{self.sel}")
            log.insert("end", "# " + info + (f" · {a['kind']}" if a["kind"] and not a["main"] else "") + "\n", "mu")
        if not rows:
            log.insert("end", "(nothing logged yet)", "mu")
        width = max((len(a["name"]) for a in ags), default=4)
        # long and multi-line entries (a full reply) wrap under their text, not under the time stamp
        hang = 9 + ((width + 3) if self.sel < 0 else 0)
        log.tag_configure("hang", lmargin2=self.f_main.measure(" " * (hang + 2)))
        log.tag_configure("fold", foreground=P["blue"])
        log.tag_configure("odd", background=mix(P["bar"], P["bg"], 0.35))  # every other entry shaded
        # an agent's final reply (its result): a light green tint, and a green mark before its time stamp
        log.tag_configure("final", background=mix(P["grn"], P["bg"], 0.9))
        log.tag_configure("finalmark", foreground=P["grn"])
        finals = set()
        for i, a in enumerate(ags):
            if a["state"] not in ("working", "needs_input", "waiting"):
                last = next((k for k in range(len(rows) - 1, -1, -1) if rows[k][1] == i and rows[k][2] == "say"), None)
                if last is not None:
                    finals.add(last)
        self._entries = []
        for n, (t, i, kind, text) in enumerate(rows):
            # long entries show their first line with ›: click (or right-click) to unfold them, ‹ to fold again
            key = (t, ags[i]["name"], kind, hash(text))
            big = "\n" in text.strip() or len(text) > 220
            first = text.strip().split("\n", 1)[0]
            shown = text if not big or key in self._expanded else (first[:220].rstrip() + "…" if len(first) > 220
                                                                    else first)
            self._entries.append((key, text, ags[i]["name"], big))
            start = log.index("end-1c")
            if n in finals:
                log.insert("end", "▍", "finalmark")
            log.insert("end", time.strftime("%H:%M:%S ", time.localtime(t)) if t else " " * 9, "mu")
            if self.sel < 0:
                log.insert("end", ags[i]["name"].ljust(width)[:width], f"a{i}")
                log.insert("end", " │ ", "mu")
            mark = KIND_MARK.get(kind, "")
            if mark:
                log.insert("end", mark + " ", "mu" if kind in ("tool", "say") else kind)
            log.insert("end", shown, (kind if kind in ("think", "you", "error", "spawn") else "say", "hang"))
            if big:
                log.insert("end", "  ‹" if key in self._expanded else "  ›", ("fold", "hang"))
            log.insert("end", "\n")
            log.tag_add(f"entry{n}", start, "end-1c")
            if n % 2:
                log.tag_add("odd", start, "end-1c")
            if n in finals:
                log.tag_add("final", start, "end-1c")
            log.tag_bind(f"entry{n}", "<ButtonRelease-1>", lambda e, n=n: self._entry_click(n))
            for b in ("<Button-3>",) + (("<Button-2>", "<Control-Button-1>") if IS_MAC else ()):
                log.tag_bind(f"entry{n}", b, lambda e, n=n: self._entry_menu(e, n))
        log.configure(state="disabled")
        if keep is not None:
            log.yview_moveto(keep)
        elif self.opts.get("log_tail", True) or switched:
            log.see("end")  # tail: follow the newest line
        else:
            log.yview_moveto(top)  # stays where you are reading

    # ---- log entries: fold / unfold, copy, open in a window
    def _entry_click(self, n):
        try:
            if self.log.tag_ranges("sel"):
                return  # that was a selection, not a click
        except tk.TclError:
            pass
        self._toggle_entry(n)

    def _toggle_entry(self, n):
        if n >= len(self._entries) or not self._entries[n][3]:
            return
        key = self._entries[n][0]
        try:
            self.log.tag_remove("sel", "1.0", "end")
        except tk.TclError:
            pass
        if key in self._expanded:
            self._expanded.discard(key)
        else:
            self._expanded.add(key)
            if self.opts.get("log_tail", True):  # unfolding to read: stop following the tail
                self.opts["log_tail"] = False
                self._save("tail")
                self._update_tailbtn()
                if self.menu_open:
                    self._build_menu()
        self._log_sig = None
        self._draw_log(keep=self.log.yview()[0])
        try:
            self.log.see(f"entry{n}.first")  # its first line stays in sight (unfolding near the end scrolls)
        except tk.TclError:
            pass

    def _entry_menu(self, e, n):
        if n >= len(self._entries):
            return "break"
        key, text, who, big = self._entries[n]
        P = self.P
        menu = tk.Menu(self.win, tearoff=0, bg=P["bar"], fg=P["tx"], activebackground=P["sel"],
                       activeforeground=P["hi"], font=self.f_small)
        if big:
            menu.add_command(label="collapse" if key in self._expanded else "expand",
                             command=lambda: self._toggle_entry(n))
        menu.add_command(label="copy text", command=lambda: (self.win.clipboard_clear(),
                                                             self.win.clipboard_append(text)))
        first = text.strip().split("\n", 1)[0]
        title = f"{who} · {first[:70]}" + ("…" if len(first) > 70 else "")
        menu.add_command(label="open in a window", command=lambda: TextPopup(self, title, text))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
        return "break"

    # ---- the prompt
    def _draw_prompt(self):
        item = self.item
        who = prompt_agent(self.net, item) if self.net["agents"] else None
        self._has_prompt = who is not None
        if who is None:
            if self.in_window is not None:
                self.in_window = None
                self._draw_graph()
            self.chip.place_forget()
            if self.overlay_open:
                self.open_overlay(False)
            self._prompt_sig = None
            return
        req = item.get("request") or {}
        name = self.net["agents"][who]["name"]
        answerable = core.hook_waiting(item) and self.pet.answers_enabled(item)
        self.chip.configure(text=f"[!] {name} needs your input · " + ("space to open" if answerable
                                                                       else "answer it in the window"))
        in_window = None if answerable else who
        if in_window != self.in_window:
            self.in_window = in_window
            self._draw_graph()
        if not self.overlay_open:
            self.chip.place(in_=self.canvas, relx=1.0, rely=1.0, x=-self.px(170), y=-self.px(6), anchor="se")
        sig = (req.get("id"), self.theme, self.s, core.hook_waiting(item))
        if sig != self._prompt_sig:
            self._prompt_sig = sig
            self._build_overlay(name, req, item)

    def open_overlay(self, show):
        self.overlay_open = bool(show) and self._has_prompt
        if self.overlay_open:
            if self.menu_open:
                self.toggle_menu(False)
            self.chip.place_forget()
            self.option = 0
            self._mark_option()
            self._place_overlay()
        else:
            self.overlay.place_forget()
            self._prompt_sig = None  # the chip comes back on the next refresh
        return "break"

    def _place_overlay(self):
        """Over the logs, and up over the graph as far as the prompt needs (never under the bars)."""
        width = max(self.px(200), self.win.winfo_width() - self.px(20))
        for lab in getattr(self, "_wraps", []):
            try:
                lab.configure(wraplength=width - self.px(36))
            except tk.TclError:
                pass
        bottom = self.footbar.winfo_y() - self.px(6)
        self._flow_options(width - self.px(20))
        highest = self.body.winfo_y() + self.px(6)
        top = self.body.winfo_y() + self.canvas.winfo_y() + int(self.canvas.winfo_height() * 0.72)
        try:
            self.overlay.update_idletasks()
            need = self.overlay.winfo_reqheight() + self.px(4)
        except tk.TclError:
            need = 0
        top = max(highest, min(top, bottom - need))
        self.overlay.place(x=self.px(10), y=top, width=width, height=max(self.px(60), bottom - top))
        self.overlay.lift()

    def _build_overlay(self, name, req, item):
        P, ov = self.P, self.overlay
        for wdg in ov.winfo_children():
            wdg.destroy()
        self.options, self._wraps = [], []
        pad = self.px(10)
        questions = req.get("questions") or []
        kind = "asks you" if req.get("kind") == "question" or questions else f"wants to run {req.get('tool') or 'a tool'}"
        top = tk.Frame(ov, bg=P["node"])
        top.pack(fill="x", padx=pad, pady=(pad, 0))
        tk.Label(top, text=f"? {name} {kind}", font=self.f_bold, bg=P["node"], fg=P["red"], anchor="w").pack(
            side="left")
        fold = tk.Label(top, text="[esc] fold", font=self.f_small, bg=P["node"], fg=P["mu"], cursor="hand2")
        fold.pack(side="right")
        fold.bind("<Button-1>", lambda e: self.open_overlay(False))
        detail = (req.get("detail") or "").strip()
        if req.get("description"):
            lab = tk.Label(ov, text="# " + req["description"], font=self.f_small, bg=P["node"], fg=P["mu"],
                           anchor="w", justify="left", wraplength=self.px(560))
            lab.pack(fill="x", padx=pad)
            self._wraps.append(lab)
        if detail and not questions:
            lines = detail.splitlines()
            shown = "\n".join(lines[:6]) + ("\n…" if len(lines) > 6 else "")
            lab = tk.Label(ov, text=shown, font=self.f_main, bg=P["bar"], fg=P["hi"], anchor="w", justify="left",
                           wraplength=self.px(560), padx=self.px(6), pady=self.px(3))
            lab.pack(fill="x", padx=pad, pady=self.px(4))
            self._wraps.append(lab)
        answerable = core.hook_waiting(item) and self.pet.answers_enabled(item)
        self.own = None  # (question, entry) when a typed answer can be sent
        self.radio = False  # the options are a pick-one list (a question)
        if questions:
            simple = len(questions) == 1 and not questions[0].get("multiSelect") and questions[0].get("options")
            for q in questions[:1]:
                lab = tk.Label(ov, text=q.get("question", ""), font=self.f_main, bg=P["node"], fg=P["hi"], anchor="w",
                               justify="left", wraplength=self.px(560))
                lab.pack(fill="x", padx=pad, pady=(self.px(4), 0))
                self._wraps.append(lab)
            if len(questions) > 1 or (questions and questions[0].get("multiSelect")):
                lab = tk.Label(ov, text="# several questions or picks: answer them on the full card", font=self.f_small,
                               bg=P["node"], fg=P["mu"], anchor="w", justify="left")
                lab.pack(fill="x", padx=pad)
                self._wraps.append(lab)
            if answerable and simple:
                q, self.radio = questions[0], True
                for opt in q["options"][:8]:
                    label = str(opt.get("label") if isinstance(opt, dict) else opt)
                    self.options.append((label, lambda label=label, q=q: self._send(
                        "allow", {q["question"]: label})))
                self.options.append(("your own answer:", self._send_own))
        elif answerable:
            self.options = [("allow once", lambda: self._send("allow"))]
            for sug in (req.get("suggestions") or [])[:3]:  # the AI's own "don't ask again" choices
                agent_name = "codex" if (item or {}).get("agent") == "codex" else "claude"
                self.options.append((f"{agent_name}: {suggestion_label(sug)}",
                                     lambda sug=sug: self._send("allow", permissions=[sug])))
            self.options += [("aipet: always (whitelist)", self._always),
                             ("deny", lambda: self._send("deny"))]
        n_answers = len(self.options)
        self.options.append(("full card", lambda: self.pet.open_detail(self.key)))
        codex = (item or {}).get("agent") == "codex"
        self.options.append(("answer in codex" if codex and answerable else "go to window", self._go))
        self._opt_row = tk.Frame(ov, bg=P["node"])
        self._opt_row.pack(fill="x", padx=pad, pady=self.px(6))
        self._opt_labels, self._opt_flow = [], None
        for n, (label, _) in enumerate(self.options):
            if self.radio and label == "your own answer:":
                own_row = tk.Frame(self._opt_row, bg=P["node"])
                lab = tk.Label(own_row, font=self.f_main, cursor="hand2", padx=self.px(4), pady=self.px(1),
                               bg=P["node"], fg=P["tx"], anchor="w")
                lab.pack(side="left")
                entry = tk.Entry(own_row, font=self.f_main, bg=P["bar"], fg=P["hi"], insertbackground=P["hi"],
                                 relief="flat", highlightthickness=1, highlightbackground=P["line"],
                                 highlightcolor=P["red"])
                entry.pack(side="left", fill="x", expand=True, padx=(self.px(4), 0))
                entry.bind("<KeyRelease>", lambda e, n=n: self._own_typed(n))
                entry.bind("<FocusIn>", lambda e, n=n: self._pick(n))
                entry.bind("<Return>", lambda e: (self._send_own(), "break")[1])
                entry.bind("<Escape>", lambda e: (self.win.focus_set(), "break")[1])
                self.own = (questions[0], entry)
                lab.bind("<Button-1>", lambda e, n=n: self.choose(n))
                lab._row = own_row
            else:
                lab = tk.Label(self._opt_row, font=self.f_main, cursor="hand2", padx=self.px(4), pady=self.px(1),
                               bg=P["node"], fg=P["tx"], anchor="w", justify="left")
                lab.bind("<Button-1>", lambda e, n=n: self.choose(n))
                lab._row = None
            lab._text = label
            lab._answer = n < n_answers
            self._opt_labels.append(lab)
        self._flow_options(self.px(560))
        if answerable:
            hint = ("# 1-9 or ↑↓ + enter to answer · type your own + enter · esc fold" if self.radio
                    else "# 1-9 or ←→ + enter to answer · esc fold")
        elif req.get("id") in self._released:
            hint = "# handed back to codex: answer it in the codex window now (go to window takes you there)"
        else:
            hint = "# this prompt can only be answered in the session window (or turn on answering in permissions)"
        self.note = tk.Label(ov, text=hint, font=self.f_small, bg=P["node"], fg=P["mu"], anchor="w", justify="left")
        self.note.pack(fill="x", padx=pad, pady=(0, pad))
        self._wraps.append(self.note)
        self._mark_option()
        if self.overlay_open:
            self._place_overlay()

    def _flow_options(self, width):
        """The answers side by side when they fit, else one per line (wrapping long ones); a question's answers are
        always one per line, like radio buttons."""
        labels = getattr(self, "_opt_labels", [])
        if not labels:
            return
        for lab in labels:
            lab.configure(wraplength=0)
        total = sum(lab.winfo_reqwidth() + self.px(4) for lab in labels)
        flow = "row" if not self.radio and total <= width else "column"
        if flow == self._opt_flow and flow == "row":
            return
        self._opt_flow = flow
        for lab in labels:
            (lab._row or lab).pack_forget()
        for lab in labels:
            widget = lab._row or lab
            if flow == "row":
                widget.pack(side="left", padx=(0, self.px(4)))
            else:
                widget.pack(side="top", fill="x", anchor="w", pady=(0, self.px(1)))
                if widget is lab:
                    lab.configure(wraplength=max(self.px(120), width - self.px(30)))

    def _mark_option(self):
        P = self.P
        for n, lab in enumerate(getattr(self, "_opt_labels", [])):
            try:
                on = n == self.option
                mark = ("(•) " if on else "( ) ") if self.radio and lab._answer else ""
                lab.configure(text=f"[{n + 1}] {mark}{lab._text}", bg=P["redbg"] if on else P["node"],
                              fg=P["red"] if on else P["tx"])
            except (tk.TclError, AttributeError):
                pass

    def _pick(self, n):
        self.option = n
        self._mark_option()

    def _own_typed(self, n):
        if self.own and self.own[1].get().strip():
            self._pick(n)

    def _send_own(self):
        """Send what was typed as the answer; with nothing typed yet, go to the box."""
        if not self.own:
            return
        q, entry = self.own
        text = entry.get().strip()
        if not text:
            entry.focus_set()
            return
        self.win.focus_set()
        self._send("allow", {q["question"]: text})

    def step_option(self, d):
        if getattr(self, "options", None):
            self.option = (self.option + d) % len(self.options)
            self._mark_option()
        return "break"

    def choose(self, n):
        if self.overlay_open and 0 <= n < len(getattr(self, "options", [])):
            self.option = n
            self._mark_option()
            self.options[n][1]()
        return "break"

    def _send(self, behavior, answers=None, permissions=None):
        ok = (self.pet.send_answer(self.key, behavior, answers=answers, permissions=permissions) if permissions
              else self.pet.send_answer(self.key, behavior, answers=answers))
        self.note.configure(text="# sent" if ok else "# couldn't send it: the prompt may have been answered already")
        if ok:
            self.win.after(600, lambda: self.open_overlay(False))

    def _go(self):
        """Go to the session's window. Codex: AIPet lets go of the prompt, so Codex shows its own one there (with
        choices the pet doesn't have, like always allowing that tool)."""
        req = (self.item or {}).get("request") or {}
        if (self.item or {}).get("agent") == "codex" and req.get("id"):
            self._released.add(req["id"])
        self.pet.go_to_prompt(self.key)
        self._prompt_sig = None  # rebuilt as "answer it in the window"
        self.open_overlay(False)

    def _always(self):
        added = self.pet.whitelist_request(self.item)
        self._send("allow")
        if added:
            self.note.configure(text="# " + added)
