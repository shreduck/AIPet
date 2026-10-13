"""Values, helpers and small widgets shared by the agent network window and its components (graph, logs, prompt,
controls, shell): option limits and defaults, palettes and state marks, colour helpers, log text helpers, window
helpers (title bar, maximise, work area), the text pop-up and the terminal-looking slider.

Imports no application entry point.
"""
import json
import math
import sys
import tkinter as tk

from .. import sessions
from ..runtime import diagnostics
from . import theme

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
            and sessions.visible_hook_session(item)]


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
        diagnostics.log_error(f"agent network title bar: {e!r}")


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
            theme.titlebar_theme(w)
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
