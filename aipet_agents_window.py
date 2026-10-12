"""The agent network window: a live, terminal-looking map of one session's main agent and its subagents.

Top: a graph. Each agent is a node with a small copy of the pet's robot (red when that agent needs you), its name,
model, effort and context. Edges show what is going on: blue with dots flowing out while a subagent works, red while it
needs you, green with a dot flowing back once it is done. Next to each subagent: its latest thought or words.
Bottom: logs. [all] merges every agent's activity in time order, each line in its agent's colour; one tab per agent.
When an agent needs you, a small chip says so; clicking it (or Space) opens the prompt over the logs, with its answers.
The cogwheel (top right) drops down the window's settings: always on top, title bar, see-through background, compact,
one window per session, and sliders for opacity, node opacity and scale.

Keys: Tab / Right next agent, Shift+Tab / Left previous, A shared log, PgUp / PgDn other sessions, Space prompt,
1-9 answer, C compact, Esc fold the prompt / settings (or close).
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
NODE_STEP = 25  # node fill opacity, %: 0 / 25 / 50 / 75 / 100 (drawn with Tk stipples)
SCALE_MIN, SCALE_MAX, SCALE_STEP = 50, 150, 5  # the window's text and drawing size, %
STIPPLE = {75: "gray75", 50: "gray50", 25: "gray25"}
OPTIONS_KEY = "agent_window"  # config.json
DEFAULT_OPTIONS = {"on_top": False, "alpha": 100, "clear_bg": False, "node_alpha": 100, "frameless": False,
                   "compact": False, "separate": False, "scale": 100}
HINT = "tab/←→ agent · a all · pgup/pgdn session · space prompt · c compact · esc close"


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
    opts["node_alpha"] = _snap(opts["node_alpha"], 0, 100, NODE_STEP)
    opts["scale"] = _snap(opts["scale"], SCALE_MIN, SCALE_MAX, SCALE_STEP)
    return opts


def key_color(hex_color):
    """A colour one notch off hex_color: made fully see-through (Windows -transparentcolor) without keying out text
    or lines drawn in the theme's own colours."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{r:02x}{g:02x}{b ^ 1:02x}"


def needed_size(n_subs, scale, line, small, compact=False):
    """(width, height) in pixels the window needs to show everything at this scale (s = 1.5 * scale), given the
    line heights of its main and small fonts at that scale."""
    s = 1.5 * scale
    cols = 1 if n_subs <= 4 else 2
    rows = n_subs if cols == 1 else math.ceil(n_subs / 2)
    graph_w = 10 + 150 + (70 + cols * 180 + (110 if cols == 1 else 0) if n_subs else 10)
    width = max(380, graph_w) * s
    main_h = line * 4 + 10 * s
    sub_h = line * (3 if cols == 1 else 4) + 14 * s
    graph_h = max(main_h, rows * sub_h) + 20 * s
    head = line + 10 * s
    height = head + graph_h
    if not compact:
        height += (small + 6 * s) + (line * 4 + 10 * s) + (small + 8 * s)  # tabs, 4 log lines, key bar
    return int(width), int(height)


PALETTES = {
    "light": {"bg": "#fbfaf7", "bar": "#f1efe8", "node": "#ffffff", "line": "#d3d1c7", "tx": "#2c2c2a",
              "mu": "#888780", "hi": "#2c2c2a", "blue": "#185fa5", "amb": "#9a6410", "grn": "#3b6d11",
              "red": "#c0362c", "ambbg": "#faeeda", "redbg": "#fcebeb", "sel": "#e6e4dc",
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


def prompt_agent(net, item):
    """Index of the agent the session's prompt belongs to (0 = main), or None when nothing is asked."""
    if not item or item.get("state") != "needs_input" or not item.get("request"):
        return None
    aid = str((item.get("request") or {}).get("agent") or item.get("wait_agent") or "")
    for i, a in enumerate(net["agents"]):
        if aid and a["id"] == aid:
            return i
    return 0


class Slider:
    """A terminal-looking slider: [██████░░░░] 70%. Click or drag to set, scroll to step."""

    def __init__(self, win, parent, lo, hi, step, value, on_change, fmt=lambda v: f"{v}%"):
        self.win, self.lo, self.hi, self.step, self.value = win, lo, hi, step, value
        self.on_change, self.fmt = on_change, fmt
        self.c = tk.Canvas(parent, highlightthickness=0, bd=0, cursor="hand2")
        for seq, fn in (("<Button-1>", self._click), ("<B1-Motion>", self._click),
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
        return "break"

    def set(self, value, notify=True):
        value = _snap(value, self.lo, self.hi, self.step)
        if value != self.value:
            self.value = value
            self.draw()
            if notify:
                self.on_change(value)


class AgentNetworkWindow:
    def __init__(self, root, pet, key=None, pinned=False, on_options=None, cascade=0):
        """pinned: this window belongs to one session (one window per session): no switching sessions.
        on_options(opts, window, changed): called after a setting changed, so the app can apply it to its other
        agent network windows (and switch between one window and one per session)."""
        self.root, self.pet = root, pet
        self.pinned, self.on_options, self.cascade = pinned, on_options, cascade
        self.key = None
        self.net = {"agents": [], "log": []}
        self.item = None
        self.sel = -1  # -1: the shared log ("all")
        self.overlay_open = self.menu_open = False
        self.option = 0
        self._after = self._anim = self._save_job = None
        self._robots = {}
        self._log_sig = self._graph_sig = self._prompt_sig = self._tabs_sig = None
        self.edges, self._dots = [], []
        self.t0 = time.time()
        self.theme = None
        self.opts = load_options(getattr(pet, "cfg", {}))
        self.u = self.opts["scale"] / 100
        self.s = 1.5 * self.u
        self._applied = {}  # window attributes last set (only touched when they change)
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
        self._tick()
        self._animate()

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
        return needed_size(n, u, self.f_main.metrics("linespace") * r, self.f_small.metrics("linespace") * r,
                           self.opts["compact"])

    def _place(self):
        w, h = self.needed()
        w, h = max(w, int((420 if self.opts["compact"] else 620) * self.s)), max(h, 0 if self.opts["compact"]
                                                                                  else int(480 * self.s))
        try:
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            w, h = min(w, sw - 40), min(h, sh - 80)
            off = self.px(28) * (self.cascade % 8)  # one window per session: fanned out
            self.win.geometry(f"{w}x{h}+{(sw - w) // 2 + off}+{(sh - h) // 3 + off}")
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
        toggles = {"frame": "frameless", "pin": "on_top", "bg": "clear_bg", "view": "compact", "each": "separate"}
        if name in toggles:
            o[toggles[name]] = not o[toggles[name]]
        elif name == "alpha":
            o["alpha"] = _snap(o["alpha"] + d * ALPHA_STEP, ALPHA_MIN, 100, ALPHA_STEP)
        elif name == "nodes":
            o["node_alpha"] = _snap(o["node_alpha"] + d * NODE_STEP, 0, 100, NODE_STEP)
        self._spin_until = time.time() + 0.6  # the cog turns a little on every change
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
        if self.opts["scale"] != old.get("scale"):
            self.set_scale(self.opts["scale"] / 100, save=False)
        self._apply_options()

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

    def _on_window_resize(self, e):
        """Shrinking the window below what the current scale needs makes the scale follow it (down to 50%)."""
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
        want = {"topmost": bool(o["on_top"]), "alpha": o["alpha"] / 100,
                "clear": (key_color(P["bg"]) if not IS_MAC else "mac") if o["clear_bg"] else ""}
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
        if o["clear_bg"]:
            bg = "systemTransparent" if IS_MAC else want["clear"] or P["bg"]
        if changed in (None, "frame") and not IS_MAC:
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
        elif geo and changed in ("alpha", "bg", "pin", "nodes"):
            try:  # keep the size, whatever the window manager did
                w.after_idle(lambda g=geo: w.geometry(g) if w.geometry().split("+")[0] != g.split("+")[0] else None)
            except tk.TclError:
                pass
        try:
            self.canvas.configure(bg=bg)  # only the graph goes see-through: the logs stay readable
        except tk.TclError:
            pass
        frameless = o["frameless"] and not IS_MAC
        if frameless:
            self.close_btn.pack(side="right", before=self.cog)
        else:
            self.close_btn.pack_forget()
        if changed in (None, "view", "frame"):
            self._apply_view()
        if frameless:
            self.grip.place(relx=1.0, rely=1.0, anchor="se")
            self.grip.lift()
        else:
            self.grip.place_forget()
        self._update_minsize()
        self._graph_sig = self._tabs_sig = None
        self._draw_graph(force=True)
        self._draw_tabs()

    def _apply_view(self):
        """Full: graph, tabs, logs and the key bar. Compact: only the graph (and the prompt chip), in a window sized
        to it; the full size comes back when switching back."""
        compact = self.opts["compact"]
        parts = (self.sep, self.tabs, self.log)
        try:
            shown = self.log.winfo_manager() == "pack"
            if compact and shown:
                self._full_size = (self.win.winfo_width(), self.win.winfo_height())
                for wdg in parts:
                    wdg.pack_forget()
                self.footbar.pack_forget()
                self._update_minsize()
                w, h = self.needed()
                self._fitting = True
                self.win.geometry(f"{max(w, int(self.win.winfo_width() * 0.75))}x{h}")
                self.win.after(200, lambda: setattr(self, "_fitting", False))
            elif not compact and not shown:
                self.footbar.pack(side="bottom", fill="x", before=self.body)
                for wdg in parts:
                    wdg.pack(fill="both" if wdg is self.log else "x", padx=self.px(6) if wdg is self.tabs else 0)
                self._update_minsize()
                need = self.needed()
                w, h = getattr(self, "_full_size", (int(620 * self.s), int(480 * self.s)))
                self._fitting = True
                self.win.geometry(f"{max(w, need[0])}x{max(h, need[1])}")
                self.win.after(200, lambda: setattr(self, "_fitting", False))
        except tk.TclError:
            pass

    def _on_resize(self, e=None):
        self._draw_graph(force=True)
        if self.overlay_open:
            self._place_overlay()
        if self.menu_open:
            self._place_menu()

    def _drag_start(self, e):
        self._drag = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y(), self.win.winfo_width(),
                      self.win.winfo_height())

    def _drag_move(self, e):
        if self._drag:
            x0, y0, wx, wy, _, _ = self._drag
            self.win.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _resize_move(self, e):
        if self._drag:
            x0, y0, _, _, ww, wh = self._drag
            mw, mh = self.needed(SCALE_MIN / 100)
            self.win.geometry(f"{max(mw, ww + e.x_root - x0)}x{max(mh, wh + e.y_root - y0)}")

    # ---- building
    def _build(self):
        s = self.s
        self.f_main = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(5, int(round(8 * s))))
        self.f_bold = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(5, int(round(8 * s))), weight="bold")
        self.f_small = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(5, int(round(7 * s))))
        w = self.win
        self.head = tk.Frame(w)
        self.head.pack(fill="x")
        self.prompt_mark = tk.Label(self.head, text="▍", font=self.f_bold)
        self.prompt_mark.pack(side="left")
        self.title = tk.Label(self.head, font=self.f_main, anchor="w")
        self.title.pack(side="left", fill="x", expand=True, pady=self.px(3))
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
        for wdg in (self.head, self.title, self.prompt_mark):  # drag the bar to move (needed without a title bar)
            wdg.bind("<Button-1>", self._drag_start)
            wdg.bind("<B1-Motion>", self._drag_move)
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
        self.body = tk.Frame(w)
        self.body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.body, highlightthickness=0, bd=0, height=self.px(150))
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.sep = tk.Frame(self.body, height=1)
        self.sep.pack(fill="x")
        self.tabs = tk.Frame(self.body)
        self.tabs.pack(fill="x", padx=self.px(6))
        self.log = tk.Text(self.body, height=6, wrap="word", bd=0, highlightthickness=0, font=self.f_main,
                           padx=self.px(8), pady=self.px(4), cursor="arrow", takefocus=0)
        self.log.pack(fill="both", expand=False)
        self.win.bind("<Configure>", self._on_window_resize, add="+")

        self.chip = tk.Label(w, font=self.f_main, cursor="hand2", padx=self.px(6), pady=self.px(1), bd=1,
                             relief="solid")
        self.chip.bind("<Button-1>", lambda e: self.open_overlay(True))
        self.overlay = tk.Frame(w, bd=1, relief="solid")
        self.menu = tk.Frame(w, bd=0, highlightthickness=1)

    def _bind_keys(self):
        """Keys work wherever the focus is in the window, except while typing your own answer."""
        w = self.win
        w.bind("<Button-1>", self._click_anywhere, add="+")

        def key(fn, typing_too=False):
            def handler(e):
                if self._typing() and not typing_too:
                    return None  # the entry gets the key
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
        for k in ("c", "C"):
            w.bind(k, key(lambda: self.tool("view")))
        w.bind("<Prior>", key(lambda: self.step_session(-1), True))
        w.bind("<Next>", key(lambda: self.step_session(1), True))
        w.bind("<space>", key(lambda: self.open_overlay(not self.overlay_open)))
        w.bind("<Escape>", key(self._escape, True))
        w.bind("<Return>", key(lambda: self.choose(self.option) if self.overlay_open else None))
        for n in range(1, 10):
            w.bind(str(n), key(lambda n=n: self.choose(n - 1) if self.overlay_open else None))

    def _escape(self):
        if self.menu_open:
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
        if self.opts["frameless"] and not self._typing():
            try:
                self.win.focus_force()
            except tk.TclError:
                pass

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
        tk.Label(m, text="── settings ──", font=self.f_small, bg=P["bar"], fg=P["mu"], anchor="w").pack(
            fill="x", padx=pad, pady=(pad // 2, 0))
        o = self.opts
        rows = [("pin", "on_top", "always on top")]
        if not IS_MAC:
            rows.append(("frame", "frameless", "no title bar"))
        if IS_MAC or sys.platform == "win32":
            rows.append(("bg", "clear_bg", "see-through background"))
        rows += [("view", "compact", "compact (c)"), ("each", "separate", "one window per session")]
        for name, opt, text in rows:
            lab = tk.Label(m, text=f"[{'x' if o[opt] else ' '}] {text}", font=self.f_main, bg=P["bar"],
                           fg=P["blue"] if o[opt] else P["tx"], anchor="w", cursor="hand2")
            lab.pack(fill="x", padx=pad)
            lab.bind("<Button-1>", lambda e, n=name: (self.tool(n), "break")[1])
        width = self.px(150)
        line = self.f_small.metrics("linespace")
        self._sliders = []

        def slider(text, lo, hi, step, value, on_change):
            row = tk.Frame(m, bg=P["bar"])
            row.pack(fill="x", padx=pad, pady=(self.px(2), 0))
            tk.Label(row, text=text.ljust(8), font=self.f_small, bg=P["bar"], fg=P["tx"]).pack(side="left")
            sl = Slider(self, row, lo, hi, step, value, on_change)
            sl.c.pack(side="left", fill="x", expand=True)
            sl.size(width, line + self.px(4))
            self._sliders.append(sl)
            return sl
        slider("opacity", ALPHA_MIN, 100, ALPHA_STEP, o["alpha"], lambda v: self._slide("alpha", v))
        slider("nodes", 0, 100, NODE_STEP, o["node_alpha"], lambda v: self._slide("node_alpha", v))
        self.scale_slider = slider("scale", SCALE_MIN, SCALE_MAX, SCALE_STEP, o["scale"],
                                   lambda v: self.set_scale(v / 100))
        tk.Label(m, text="esc fold · click outside", font=self.f_small, bg=P["bar"], fg=P["mu"], anchor="w").pack(
            fill="x", padx=pad, pady=(self.px(3), pad // 2))
        self._place_menu()

    def _slide(self, key, value):
        self.opts[key] = value
        self._spin_until = time.time() + 0.4
        self._apply_options(changed="alpha" if key == "alpha" else "nodes")
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
        self.log.configure(bg=P["bg"], fg=P["tx"], insertbackground=P["bg"], selectbackground=P["sel"],
                           selectforeground=P["hi"])
        self.chip.configure(bg=P["redbg"], fg=P["red"], highlightbackground=P["red"])
        self.overlay.configure(bg=P["node"], highlightbackground=P["red"], highlightthickness=1, bd=0)
        try:
            core.titlebar_theme(self.win)
        except Exception:
            pass
        self._robots.clear()
        self._log_sig = self._graph_sig = self._prompt_sig = None
        self._applied.pop("clear", None)  # the see-through key colour follows the theme
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
        try:
            self._refresh()
        except Exception as e:  # never let one bad transcript kill the window
            core.log_error(f"agent network: {e!r}")
        self._after = self.win.after(REFRESH_MS, self._tick)

    def _refresh(self):
        self._apply_theme()
        items = getattr(self.pet, "_last_items", [])
        self.item = next((i for i in candidate_sessions(items) if i.get("key") == self.key), None)
        if self.item is None and self.key is None and not self.pinned:
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

    def _layout(self, W, H):
        """Node boxes: main on the left, subagents in one column (two when there are many)."""
        n = len(self.net["agents"]) - 1
        lh = self.f_main.metrics("linespace")
        pad = self.px(10)
        main_w, main_h = self.px(150), lh * 4 + pad
        boxes = [(pad, max(pad, (H - main_h) // 2), main_w, main_h)]
        if n <= 0:
            return boxes, False
        cols = 1 if n <= 4 else 2
        sub_w = self.px(140)
        sub_h = lh * (3 if cols == 1 else 4) + self.px(8)
        per_col = math.ceil(n / cols)
        x0 = pad + main_w + self.px(70)
        gap_x = sub_w + self.px(40)
        for k in range(n):
            col, row = divmod(k, per_col)
            rows = min(per_col, n - col * per_col)
            step = max(sub_h + self.px(6), (H - 2 * pad) / max(1, rows))
            top = pad + (H - 2 * pad - step * rows) / 2 + row * step + (step - sub_h) / 2
            boxes.append((x0 + col * gap_x, int(max(pad // 2, top)), sub_w, sub_h))
        return boxes, cols == 1

    def _draw_graph(self, force=False):
        c, P = self.canvas, self.P
        W, H = max(50, c.winfo_width()), max(50, c.winfo_height())
        sig = (W, H, self.sel, self.theme, self.s, self.opts["node_alpha"], self.in_window,
               tuple((a["name"], a["state"], a["model_label"], a["effort"], a["ctx"], a["thought"])
                     for a in self.net["agents"]))
        if sig == self._graph_sig and not force:
            return
        self._graph_sig = sig
        c.delete("all")
        self.edges = []
        ags = self.net["agents"]
        if not ags:
            c.create_text(W // 2, H // 2, text="$ waiting for a session in claude code or codex_",
                          fill=P["mu"], font=self.f_main)
            self._dots = []
            return
        boxes, beside = self._layout(W, H)
        lh = self.f_main.metrics("linespace")
        mx, my, mw, mh = boxes[0]
        for i, (a, (x, y, w, h)) in enumerate(zip(ags[1:], boxes[1:]), 1):  # edges first, under the nodes
            p0, p3 = (mx + mw, my + mh / 2), (x, y + h / 2)
            mid = (p0[0] + p3[0]) / 2
            pts = (p0, (mid, p0[1]), (mid, p3[1]), p3)
            col = {"working": P["blue"], "needs_input": P["red"], "done": P["grn"]}.get(a["state"], P["line"])
            dash = () if a["state"] == "done" else (3, 4)
            line = [coord for t in range(0, 21) for coord in bezier(*pts, t / 20)]
            c.create_line(*line, fill=col, width=max(1, self.px(1)), dash=dash, smooth=True)
            self.edges.append((pts, a["state"], col))
        for i, (a, (x, y, w, h)) in enumerate(zip(ags, boxes)):
            status_col, status = STATUS.get(a["state"], STATUS["idle"])
            border = P[status_col] if a["state"] != "idle" else P["line"]
            if a["main"] and a["state"] == "waiting":
                border = P["amb"]
            fill = P["sel"] if i == self.sel else P["node"]
            na = self.opts["node_alpha"]
            tag = f"node{i}"
            c.create_rectangle(x, y, x + w, y + h, fill=fill if na else "", stipple=STIPPLE.get(na, ""),
                               outline=border, width=max(1, self.px(1.3 if i == self.sel else 1)), tags=tag)
            img = self.robot(a["state"], max(6, int(lh * 1.15)))
            c.create_image(x + self.px(6), y + self.px(5), image=img, anchor="nw", tags=tag)
            tx = x + self.px(6) + img.width() + self.px(5)
            inner = x + w - tx - self.px(4)
            c.create_text(tx, y + self.px(5), text=self._fit(a["name"], inner, self.f_bold), anchor="nw",
                          fill=self.color(i), font=self.f_bold, tags=tag)
            info = " · ".join(v for v in (a["model_label"], a["effort"]) if v)
            ctx = agents.fmt_tokens(a["ctx"])
            ctx_line = (f"ctx {ctx}" + (f"/{agents.fmt_tokens(a['ctx_max'])}" if a["ctx_max"] else "")) if ctx else ""
            rows = [info, ctx_line] if a["main"] else [" · ".join(v for v in (info, ctx_line) if v)]
            if not a["main"] and not beside:
                rows.append(a["thought"])
            ly = y + self.px(5) + lh
            for row in rows:
                if row:
                    c.create_text(x + self.px(8), ly, text=self._fit(row, w - self.px(14), self.f_small), anchor="nw",
                                  fill=P["mu"], font=self.f_small, tags=tag)
                ly += lh
            label = status
            if i == self.in_window:
                label = "! answer in the window"
            if a["main"] and a["state"] == "waiting" and a["thought"]:
                label = "… " + a["thought"]
            c.create_text(x + self.px(8), y + h - self.px(4), text=self._fit(label, w - self.px(14), self.f_small),
                          anchor="sw", fill=P[status_col], font=self.f_small, tags=tag)
            if not a["main"] and beside and a["thought"]:
                room = W - (x + w) - self.px(18)
                if room > self.px(40):
                    c.create_text(x + w + self.px(10), y + h / 2, text=self._fit(f"› {a['thought']}", room,
                                                                                 self.f_small),
                                  anchor="w", fill=P[status_col] if a["state"] != "done" else P["grn"],
                                  font=self.f_small, tags=tag)
            c.tag_bind(tag, "<Button-1>", lambda e, i=i: self.select(i))
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
        self._dots = [c.create_oval(0, 0, 0, 0, fill=col, outline="", state="hidden")
                      for pts, st, col in self.edges for _ in range(2)]

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
        try:
            t = time.time() - self.t0
            r = max(2, self.px(2.2))
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
        for k, name in enumerate(names):
            i = k - 1
            on = i == self.sel
            col = P["hi"] if i < 0 else self.color(i)
            lab = tk.Label(self.tabs, text=f"[{name}]", font=self.f_bold if on else self.f_small, cursor="hand2",
                           bg=col if on else P["bg"], fg=P["bg"] if on else col, padx=self.px(3), pady=self.px(1))
            lab.pack(side="left", padx=(0, self.px(3)), pady=self.px(2))
            lab.bind("<Button-1>", lambda e, i=i: self.select(i))

    def _draw_log(self):
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
        self._log_sig = sig
        at_end = log.yview()[1] > 0.98
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
        for t, i, kind, text in rows:
            log.insert("end", time.strftime("%H:%M:%S ", time.localtime(t)) if t else " " * 9, "mu")
            if self.sel < 0:
                log.insert("end", ags[i]["name"].ljust(width)[:width], f"a{i}")
                log.insert("end", " │ ", "mu")
            mark = KIND_MARK.get(kind, "")
            if mark:
                log.insert("end", mark + " ", "mu" if kind in ("tool", "say") else kind)
            log.insert("end", text + "\n", kind if kind in ("think", "you", "error", "spawn") else "")
        log.configure(state="disabled")
        if at_end or switched:
            log.see("end")

    # ---- the prompt
    def _draw_prompt(self):
        item = self.item
        who = prompt_agent(self.net, item) if self.net["agents"] else None
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
            self.chip.place(relx=1.0, x=-self.px(10), y=self.body.winfo_y() + self.canvas.winfo_height()
                            - self.px(4), anchor="se")
        sig = (req.get("id"), self.theme, self.s, core.hook_waiting(item))
        if sig != self._prompt_sig:
            self._prompt_sig = sig
            self._build_overlay(name, req, item)

    def open_overlay(self, show):
        self.overlay_open = bool(show) and self.chip.winfo_ismapped()
        if self.overlay_open:
            if self.menu_open:
                self.toggle_menu(False)
            self.chip.place_forget()
            self.option = 0
            self._mark_option()
            self._place_overlay()
        else:
            self.overlay.place_forget()
            grown, self._grown = getattr(self, "_grown", None), None
            if grown and self.opts["compact"]:
                try:
                    self.win.geometry(f"{self.win.winfo_width()}x{grown}")
                except tk.TclError:
                    pass
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
        compact = self.opts["compact"]
        bottom = (self.win.winfo_height() if compact else self.footbar.winfo_y()) - self.px(6)
        self._flow_options(width - self.px(20))
        highest = self.body.winfo_y() + self.px(6)
        top = self.body.winfo_y() + int(self.canvas.winfo_height() * 0.72)
        try:
            self.overlay.update_idletasks()
            need = self.overlay.winfo_reqheight() + self.px(4)
        except tk.TclError:
            need = 0
        if compact and bottom - highest < need and not getattr(self, "_grown", None):
            # the small window can't hold the prompt: grow it while the prompt is open
            self._grown = self.win.winfo_height()
            self._fitting = True
            self.win.geometry(f"{self.win.winfo_width()}x{self._grown + need - (bottom - highest)}")
            self.win.update_idletasks()
            self.win.after(200, lambda: setattr(self, "_fitting", False))
            bottom = self.win.winfo_height() - self.px(6)
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
            self.options = [("allow once", lambda: self._send("allow")),
                            ("always (aipet whitelist)", self._always),
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

    def _send(self, behavior, answers=None):
        ok = self.pet.send_answer(self.key, behavior, answers=answers)
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
