"""The agent network window: a live, CLI-looking map of one session's main agent and its subagents.

Top: a graph. Each agent is a node with a small copy of the pet's robot (red when that agent needs you), its name,
model, effort and context. Edges show what is going on: blue with dots flowing out while a subagent works, red while it
needs you, green with a dot flowing back once it is done. Next to each subagent: its latest thought or words.
Bottom: logs. "All" merges every agent's activity in time order, each line in its agent's colour; one tab per agent.
When an agent needs you, a small chip says so; clicking it (or Space) opens the prompt over the logs, with its answers.

Keys: Tab / Right next agent, Shift+Tab / Left previous, A shared log, PgUp / PgDn other sessions, Space prompt,
1 / 2 / 3 answer, Esc fold the prompt (or close).
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
ALPHAS = (100, 90, 80, 70, 60, 50, 40, 30)  # window opacity steps, %
NODE_ALPHAS = (100, 75, 50, 25, 0)  # node fill opacity steps, % (drawn with Tk stipples)
STIPPLE = {75: "gray75", 50: "gray50", 25: "gray25"}
OPTIONS_KEY = "agent_window"  # config.json: {"on_top", "alpha", "clear_bg", "node_alpha", "frameless"}
DEFAULT_OPTIONS = {"on_top": False, "alpha": 100, "clear_bg": False, "node_alpha": 100, "frameless": False,
                   "compact": False, "separate": False}


def load_options(cfg):
    opts = dict(DEFAULT_OPTIONS)
    saved = (cfg or {}).get(OPTIONS_KEY)
    if isinstance(saved, dict):
        for k, v in saved.items():
            if k in opts and isinstance(v, type(opts[k])):
                opts[k] = v
    opts["alpha"] = min(ALPHAS, key=lambda a: abs(a - opts["alpha"]))
    opts["node_alpha"] = min(NODE_ALPHAS, key=lambda a: abs(a - opts["node_alpha"]))
    return opts


def next_step(steps, value, d):
    """The next value of a cycle (wrapping), from the nearest step."""
    i = min(range(len(steps)), key=lambda k: abs(steps[k] - value))
    return steps[(i + d) % len(steps)]


def key_color(hex_color):
    """A colour one notch off hex_color: made fully see-through (Windows -transparentcolor) without keying out text
    or lines drawn in the theme's own colours."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{r:02x}{g:02x}{b ^ 1:02x}"

PALETTES = {
    "light": {"bg": "#fbfaf7", "bar": "#f1efe8", "node": "#ffffff", "line": "#d3d1c7", "tx": "#2c2c2a",
              "mu": "#888780", "hi": "#2c2c2a", "blue": "#185fa5", "amb": "#9a6410", "grn": "#3b6d11",
              "red": "#c0362c", "ambbg": "#faeeda", "redbg": "#fcebeb", "sel": "#efeee8",
              "agents": ["#444441", "#534ab7", "#993c1d", "#0f6e56", "#993556", "#185fa5", "#854f0b", "#3b6d11",
                         "#5f5e5a"]},
    "dark": {"bg": "#15171c", "bar": "#1d2027", "node": "#1d2027", "line": "#3a3f49", "tx": "#c9ccd3",
             "mu": "#7a7f89", "hi": "#e8e9ec", "blue": "#7fb4f0", "amb": "#e6c48a", "grn": "#8fcf7a",
             "red": "#f08a80", "ambbg": "#2a2418", "redbg": "#331d1d", "sel": "#252932",
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
    who = "Codex" if item.get("agent") == "codex" else ("Cowork" if item.get("app") == "cowork" else "Claude Code")
    name = item.get("title") or "session"
    if item.get("conv"):
        name += f" · {item['conv']}"
    return f"{name}  ({who})"


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


class AgentNetworkWindow:
    HINT = "Tab / ←→ agent   A all logs   PgUp/PgDn session   Space prompt   Esc close"

    def __init__(self, root, pet, key=None, pinned=False, on_options=None, cascade=0):
        """pinned: this window belongs to one session (one window per session): no switching sessions.
        on_options(opts, window, changed): called after a toolbar option changed, so the app can apply it to its
        other agent network windows (and switch between one window and one per session)."""
        self.root, self.pet = root, pet
        self.pinned, self.on_options, self.cascade = pinned, on_options, cascade
        self.key = None
        self.net = {"agents": [], "log": []}
        self.item = None
        self.sel = -1  # -1: the shared log ("All")
        self.overlay_open = False
        self.option = 0
        self._after = self._anim = None
        self._robots = {}
        self._log_sig = self._graph_sig = self._prompt_sig = None
        self.edges = []  # (bezier points, state)
        self.t0 = time.time()
        try:
            zoom = float(pet.cfg.get("settings_zoom", 1.0))
        except (TypeError, ValueError, AttributeError):
            zoom = 1.0
        self.s = 1.5 * min(1.5, max(0.5, zoom))
        self.theme = None
        self.opts = load_options(getattr(pet, "cfg", {}))
        self._drag = None
        self._released = set()  # prompts handed back to Codex (Answer in Codex): answer them in its window
        self.in_window = None  # index of the agent whose prompt can only be answered in the session's window
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.title("AIPet · agent network")
        w.minsize(int(560 * self.s), int(420 * self.s))
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
        for job in (self._after, self._anim):
            if job:
                try:
                    self.win.after_cancel(job)
                except tk.TclError:
                    pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _place(self):
        w, h = int(620 * self.s), int(480 * self.s)
        if self.opts["compact"]:
            w, h = int(420 * self.s), self._compact_height()
        try:
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            w, h = min(w, sw - 40), min(h, sh - 80)
            off = self.px(28) * (self.cascade % 8)  # one window per session: fanned out
            self.win.geometry(f"{w}x{h}+{(sw - w) // 2 + off}+{(sh - h) // 3 + off}")
        except tk.TclError:
            pass

    def px(self, n):
        return int(round(n * self.s))

    # ---- window options (header toolbar)
    def tool(self, name, d):
        o = self.opts
        if name == "close":
            self.close()
            return "break"
        if name == "frame":
            o["frameless"] = not o["frameless"]
        elif name == "pin":
            o["on_top"] = not o["on_top"]
        elif name == "alpha":
            o["alpha"] = next_step(ALPHAS, o["alpha"], d)
        elif name == "bg":
            o["clear_bg"] = not o["clear_bg"]
        elif name == "view":
            o["compact"] = not o["compact"]
        elif name == "each":
            o["separate"] = not o["separate"]
        elif name == "nodes":
            o["node_alpha"] = next_step(NODE_ALPHAS, o["node_alpha"], d)
        if name != "each":
            self._apply_options(changed=name)
        try:
            self.pet.cfg[OPTIONS_KEY] = dict(o)
            core.save_setting(OPTIONS_KEY, dict(o))
        except Exception as e:
            core.log_error(f"agent network options: {e!r}")
        if self.on_options:
            self.on_options(dict(o), self, name)
        return "break"

    def set_options(self, opts):
        """The same options as another agent network window (changed there)."""
        self.opts = dict(opts)
        self._apply_options()

    def _apply_options(self, changed=None):
        o, P, w = self.opts, self.P, self.win
        try:
            w.attributes("-topmost", bool(o["on_top"]))
            w.attributes("-alpha", o["alpha"] / 100)
        except tk.TclError:
            pass
        if changed in (None, "frame") and not IS_MAC:
            try:
                if bool(w.overrideredirect()) != o["frameless"]:
                    geo = w.geometry()
                    w.withdraw()
                    w.overrideredirect(o["frameless"])
                    w.geometry(geo)
                    w.deiconify()
                    w.focus_force()
            except tk.TclError:
                pass
        if o["frameless"] and not IS_MAC:
            self.grip.pack(side="right")
        else:
            self.grip.pack_forget()
        bg = P["bg"]
        if o["clear_bg"]:
            try:
                if IS_MAC:
                    w.attributes("-transparent", True)
                    bg = "systemTransparent"
                else:
                    bg = key_color(P["bg"])
                    w.attributes("-transparentcolor", bg)
            except tk.TclError:
                bg = P["bg"]
        else:
            try:
                if IS_MAC:
                    w.attributes("-transparent", False)
                elif sys.platform == "win32":
                    w.attributes("-transparentcolor", "")
            except tk.TclError:
                pass
        try:
            self.canvas.configure(bg=bg)  # only the graph: the logs stay readable
        except tk.TclError:
            pass
        self._tabs_sig = None
        on, off = P["blue"], P["mu"]
        marks = {"close": ("✕", off), "frame": ("▭" if o["frameless"] else "▣", on if o["frameless"] else off),
                 "pin": ("pin", on if o["on_top"] else off), "alpha": (f"α{o['alpha']}%", on if o["alpha"] < 100 else off),
                 "bg": ("bg", on if o["clear_bg"] else off),
                 "nodes": (f"node{o['node_alpha']}%", on if o["node_alpha"] < 100 else off),
                 "view": ("compact", on if o["compact"] else off),
                 "each": ("each", on if o["separate"] else off)}
        for name, lab in self.tools.items():
            text, fg = marks[name]
            lab.configure(text=text, fg=fg)
        if changed in (None, "view"):
            self._apply_view()
        close, rest = self.tools["close"], [t for n, t in self.tools.items() if n != "close"]
        if o["frameless"]:
            close.pack(side="right", before=rest[0])  # without a title bar: a close button of its own
        else:
            close.pack_forget()
        self._graph_sig = None
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
                if self.opts["frameless"] and not IS_MAC:
                    self.grip.place(relx=1.0, rely=1.0, anchor="se")
                self.win.minsize(self.px(260), self.px(120))
                self.win.geometry(f"{max(self.px(300), int(self.win.winfo_width() * 0.75))}x{self._compact_height()}")
            elif not compact and not shown:
                self.grip.place_forget()
                self.footbar.pack(side="bottom", fill="x", before=self.body)
                for wdg in parts:
                    wdg.pack(fill="both" if wdg is self.log else "x", padx=self.px(6) if wdg is self.tabs else 0)
                self.win.minsize(int(560 * self.s), int(420 * self.s))
                w, h = getattr(self, "_full_size", (int(620 * self.s), int(480 * self.s)))
                self.win.geometry(f"{max(w, int(560 * self.s))}x{max(h, int(420 * self.s))}")
        except tk.TclError:
            pass

    def _compact_height(self):
        """Header plus the graph: enough rows for the subagents."""
        n = max(0, len(self.net["agents"]) - 1)
        lh = self.f_main.metrics("linespace")
        rows = n if n <= 4 else math.ceil(n / 2)
        graph = max(lh * 4 + self.px(30), rows * (lh * 3 + self.px(14)) + self.px(20))
        return self.head.winfo_reqheight() + graph

    def _on_resize(self, e=None):
        self._draw_graph(force=True)
        if self.overlay_open:
            self._place_overlay()

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
            self.win.geometry(f"{max(self.px(300), ww + e.x_root - x0)}x{max(self.px(240), wh + e.y_root - y0)}")

    # ---- building
    def _build(self):
        s = self.s
        self.f_main = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(7, int(round(8 * s))))
        self.f_bold = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(7, int(round(8 * s))), weight="bold")
        self.f_small = tkfont.Font(root=self.win, family=core.UI_FONT, size=max(6, int(round(7 * s))))
        w = self.win
        self.head = tk.Frame(w)
        self.head.pack(fill="x")
        self.prev_btn = tk.Label(self.head, text="‹", font=self.f_bold, cursor="hand2", padx=self.px(6))
        self.prev_btn.pack(side="left")
        self.title = tk.Label(self.head, font=self.f_main, anchor="w")
        self.title.pack(side="left", fill="x", expand=True, pady=self.px(4))
        # window options, right-most: frameless, always on top, opacity, see-through background, node opacity
        self.tools = {}
        tips = {"each": "One window per session, or one window for all (PgUp / PgDn switch)", "view": "Compact: just the graph, in a small window (C)", "close": "Close (Esc)", "frame": "Title bar on / off (drag this bar to move, the corner to resize)",
                "pin": "Always on top", "alpha": "Window opacity (click, or scroll)",
                "bg": "See-through graph background", "nodes": "Node opacity (click, or scroll)"}
        for name in ("close", "frame", "pin", "alpha", "bg", "nodes", "view", "each"):
            if name == "frame" and IS_MAC:
                continue  # a borderless window can't take the keyboard on macOS
            if name == "bg" and not (IS_MAC or sys.platform == "win32"):
                continue
            lab = tk.Label(self.head, font=self.f_small, cursor="hand2", padx=self.px(4))
            lab.bind("<Button-1>", lambda e, n=name: self.tool(n, 1))
            lab.bind("<Button-3>", lambda e, n=name: self.tool(n, -1))
            lab.bind("<MouseWheel>", lambda e, n=name: self.tool(n, 1 if e.delta < 0 else -1))
            lab.bind("<Button-4>", lambda e, n=name: self.tool(n, -1))
            lab.bind("<Button-5>", lambda e, n=name: self.tool(n, 1))
            lab.tip = tips[name]
            lab.bind("<Enter>", lambda e, l=lab: self.foot.configure(text=l.tip))
            lab.bind("<Leave>", lambda e: self.foot.configure(text=self.HINT))
            self.tools[name] = lab
        for name in ("close", "frame", "pin", "alpha", "bg", "nodes", "view", "each"):
            if name in self.tools:
                self.tools[name].pack(side="right")
        self.tool_sep = tk.Label(self.head, text="│", font=self.f_small)
        self.tool_sep.pack(side="right", padx=(self.px(10), self.px(4)))
        self.next_btn = tk.Label(self.head, text="›", font=self.f_bold, cursor="hand2", padx=self.px(6))
        self.next_btn.pack(side="right")
        self.count = tk.Label(self.head, font=self.f_small)
        self.count.pack(side="right")
        for wdg in (self.head, self.title):  # drag the bar to move the window (needed without a title bar)
            wdg.bind("<Button-1>", self._drag_start)
            wdg.bind("<B1-Motion>", self._drag_move)
        self.prev_btn.bind("<Button-1>", lambda e: self.step_session(-1))
        self.next_btn.bind("<Button-1>", lambda e: self.step_session(1))

        self.footbar = tk.Frame(w)
        self.footbar.pack(side="bottom", fill="x")
        self.foot = tk.Label(self.footbar, font=self.f_small, anchor="w", padx=self.px(8), pady=self.px(3),
                             text=self.HINT)
        self.foot.pack(side="left", fill="x", expand=True)
        self.grip = tk.Label(self.footbar, text="◢", font=self.f_small, cursor="size_nw_se", padx=self.px(3))
        self.grip.bind("<Button-1>", self._drag_start)
        self.grip.bind("<B1-Motion>", self._resize_move)
        self.body = tk.Frame(w)
        self.body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.body, highlightthickness=0, bd=0, height=self.px(200))
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.sep = tk.Frame(self.body, height=1)
        self.sep.pack(fill="x")
        self.tabs = tk.Frame(self.body)
        self.tabs.pack(fill="x", padx=self.px(6))
        self.log = tk.Text(self.body, height=9, wrap="word", bd=0, highlightthickness=0, font=self.f_main,
                           padx=self.px(8), pady=self.px(4), cursor="arrow")
        self.log.pack(fill="both", expand=False)
        self.log.configure(takefocus=0)  # read-only (disabled); keys go to the window's own bindings

        self.chip = tk.Label(w, font=self.f_main, cursor="hand2", padx=self.px(8), pady=self.px(2), bd=1,
                             relief="solid")
        self.chip.bind("<Button-1>", lambda e: self.open_overlay(True))
        self.overlay = tk.Frame(w, bd=1, relief="solid")

    def _bind_keys(self):
        """Keys work wherever the focus is in the window, except while typing your own answer."""
        w = self.win
        # a borderless window doesn't get the keyboard by itself on Windows: take it on any click
        w.bind("<Button-1>", lambda e: w.focus_force() if self.opts["frameless"] and not self._typing() else None,
               add="+")

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
            w.bind(k, key(lambda: self.tool("view", 1)))
        w.bind("<Prior>", key(lambda: self.step_session(-1), True))
        w.bind("<Next>", key(lambda: self.step_session(1), True))
        w.bind("<space>", key(lambda: self.open_overlay(not self.overlay_open)))
        w.bind("<Escape>", key(lambda: self.open_overlay(False) if self.overlay_open else self.close(), True))
        w.bind("<Return>", key(lambda: self.choose(self.option) if self.overlay_open else None))
        for n in range(1, 10):
            w.bind(str(n), key(lambda n=n: self.choose(n - 1) if self.overlay_open else None))

    def _typing(self):
        try:
            return isinstance(self.win.focus_get(), tk.Entry)
        except (tk.TclError, KeyError):
            return False

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
                    self.grip, self.tool_sep, *self.tools.values()):
            wdg.configure(bg=P["bar"])
        for wdg in (self.title, self.prev_btn, self.next_btn):
            wdg.configure(fg=P["hi"])
        for wdg in (self.count, self.foot, self.grip, self.tool_sep):
            wdg.configure(fg=P["mu"])
        self.canvas.configure(bg=P["bg"])
        self._apply_options()
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
        self.count.configure(text=f"{[i['key'] for i in sessions].index(self.key) + 1}/{len(sessions)} "
                             if self.key in [i['key'] for i in sessions] else "")
        for wdg in (self.prev_btn, self.next_btn, self.count):  # nothing to switch in a session's own window
            if self.pinned and wdg.winfo_manager():
                wdg.pack_forget()
        if not self.item:
            self.title.configure(text="This session has ended" if self.pinned and self.key else "No sessions right now")
            self.net = {"agents": [], "log": []}
        else:
            self.title.configure(text=session_label(self.item))
            try:
                self.win.title(f"AIPet · {self.item.get('title') or 'agent network'}" if self.pinned
                               else "AIPet · agent network")
            except tk.TclError:
                pass
            self.net = agents.read_network(self.item)
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
            i = self.sel + 1 + d  # -1 (All) .. n-1 -> 0 .. n
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
                r, g, b, a = im.split()
                red = Image.new("RGBA", im.size, (214, 48, 40, 255))
                tinted = Image.blend(im, red, 0.55)
                tinted.putalpha(a)
                im = tinted
            width = max(1, round(im.width * height / im.height))
            im = im.resize((width, height), Image.LANCZOS)
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
        sig = (W, H, self.sel, self.theme, self.opts["node_alpha"], self.in_window, tuple((a["name"], a["state"], a["model_label"], a["effort"], a["ctx"],
                                                  a["thought"]) for a in self.net["agents"]))
        if sig == self._graph_sig and not force:
            return
        self._graph_sig = sig
        c.delete("all")
        self.edges = []
        ags = self.net["agents"]
        if not ags:
            c.create_text(W // 2, H // 2, text="Nothing to show yet: start a session in Claude Code or Codex.",
                          fill=P["mu"], font=self.f_main)
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
                               outline=border, width=self.px(1.3 if i == self.sel else 1), tags=tag)
            img = self.robot(a["state"], int(lh * 1.15))
            c.create_image(x + self.px(6), y + self.px(5), image=img, anchor="nw", tags=tag)
            tx = x + self.px(6) + img.width() + self.px(5)
            inner = x + w - tx - self.px(4)
            c.create_text(tx, y + self.px(5), text=self._fit(a["name"], inner, self.f_bold), anchor="nw",
                          fill=self.color(i), font=self.f_bold, tags=tag)
            info = " · ".join(v for v in (a["model_label"], a["effort"]) if v)
            ctx = agents.fmt_tokens(a["ctx"])
            ctx_line = (f"ctx {ctx}" + (f" / {agents.fmt_tokens(a['ctx_max'])}" if a["ctx_max"] else "")) if ctx else ""
            rows = [info, ctx_line] if a["main"] else [" · ".join(v for v in (info, ctx_line and ctx_line[4:]) if v)]
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
                    c.create_text(x + w + self.px(10), y + h / 2, text=self._fit(f"“{a['thought']}”", room, self.f_small),
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
            dots = getattr(self, "_dots", [])
            for k, (pts, state, col) in enumerate(self.edges):
                for j in range(2):
                    if 2 * k + j >= len(dots):
                        break
                    dot = dots[2 * k + j]
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
        except tk.TclError:
            pass
        self._anim = self.win.after(FRAME_MS, self._animate)

    # ---- tabs and log
    def _draw_tabs(self):
        P = self.P
        names = ["All"] + [a["name"] for a in self.net["agents"]]
        sig = (tuple(names), self.sel, self.theme)
        if getattr(self, "_tabs_sig", None) == sig:
            return
        self._tabs_sig = sig
        for wdg in self.tabs.winfo_children():
            wdg.destroy()
        for k, name in enumerate(names):
            i = k - 1
            on = i == self.sel
            lab = tk.Label(self.tabs, text=name, font=self.f_bold if on else self.f_small, cursor="hand2",
                           bg=P["sel"] if on else P["bg"], fg=P["hi"] if i < 0 else self.color(i),
                           padx=self.px(6), pady=self.px(2))
            lab.pack(side="left")
            lab.bind("<Button-1>", lambda e, i=i: self.select(i))

    def _draw_log(self):
        P, log = self.P, self.log
        ags = self.net["agents"]
        if self.sel < 0:
            rows = self.net["log"][-160:]
        else:
            a = ags[self.sel]
            rows = [(t, self.sel, kind, text) for t, kind, text in a["lines"][-160:]]
        sig = (self.sel, self.theme, len(rows), rows[-1] if rows else None, len(ags))
        if sig == self._log_sig:
            return
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
        log.tag_configure("head", foreground=P["mu"])
        if self.sel >= 0 and ags:
            a = ags[self.sel]
            info = " · ".join(v for v in (a["model_label"], a["effort"],
                                          f"ctx {agents.fmt_tokens(a['ctx'])}" if a["ctx"] else "") if v)
            log.insert("end", f"<{a['name']}> ", f"a{self.sel}")
            log.insert("end", (info + (f" · {a['kind']}" if a["kind"] and not a["main"] else "")) + "\n", "head")
        if not rows:
            log.insert("end", "Nothing logged yet.", "mu")
        width = max((len(a["name"]) for a in ags), default=4)
        for t, i, kind, text in rows:
            if self.sel < 0:
                log.insert("end", ags[i]["name"].ljust(width)[:width], f"a{i}")
                log.insert("end", " │ ", "mu")
            mark = KIND_MARK.get(kind, "")
            if mark:
                log.insert("end", mark + " ", "mu" if kind in ("tool", "say") else kind)
            log.insert("end", text + "\n", kind if kind in ("think", "you", "error", "spawn") else "")
        log.configure(state="disabled")
        if at_end or self._log_sig[0] != sig[0]:
            log.see("end")

    # ---- the prompt
    def _draw_prompt(self):
        item, P = self.item, self.P
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
        self.chip.configure(text=f"▲ {name} needs your input — " + ("open" if answerable else "answer it in the window"))
        in_window = None if answerable else who
        if in_window != self.in_window:
            self.in_window = in_window
            self._draw_graph()
        if not self.overlay_open:
            self.chip.place(relx=1.0, x=-self.px(10), y=self.body.winfo_y() + self.canvas.winfo_height()
                            - self.px(4), anchor="se")
        sig = (req.get("id"), self.theme, core.hook_waiting(item))
        if sig != self._prompt_sig:
            self._prompt_sig = sig
            self._build_overlay(name, req, item)

    def open_overlay(self, show):
        self.overlay_open = bool(show) and self.chip.winfo_ismapped()
        if self.overlay_open:
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
            self.win.geometry(f"{self.win.winfo_width()}x{self._grown + need - (bottom - highest)}")
            self.win.update_idletasks()
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
        kind = "asks you" if req.get("kind") == "question" or questions else f"wants to use {req.get('tool') or 'a tool'}"
        top = tk.Frame(ov, bg=P["node"])
        top.pack(fill="x", padx=pad, pady=(pad, 0))
        tk.Label(top, text=f"? {name} {kind}", font=self.f_bold, bg=P["node"], fg=P["red"], anchor="w").pack(
            side="left")
        fold = tk.Label(top, text="[Esc] fold", font=self.f_small, bg=P["node"], fg=P["mu"], cursor="hand2")
        fold.pack(side="right")
        fold.bind("<Button-1>", lambda e: self.open_overlay(False))
        detail = (req.get("detail") or "").strip()
        if req.get("description"):
            lab = tk.Label(ov, text=req["description"], font=self.f_small, bg=P["node"], fg=P["mu"], anchor="w",
                           justify="left", wraplength=self.px(560))
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
                lab = tk.Label(ov, text="Several questions or picks: answer them on the full card.", font=self.f_small,
                               bg=P["node"], fg=P["mu"], anchor="w", justify="left")
                lab.pack(fill="x", padx=pad)
                self._wraps.append(lab)
            if answerable and simple:
                q, self.radio = questions[0], True
                for opt in q["options"][:8]:
                    label = str(opt.get("label") if isinstance(opt, dict) else opt)
                    self.options.append((label, lambda label=label, q=q: self._send(
                        "allow", {q["question"]: label})))
                self.options.append(("Your own answer:", self._send_own))
        elif answerable:
            self.options = [("Allow once", lambda: self._send("allow")),
                            ("Always (add to auto approve)", self._always),
                            ("Deny", lambda: self._send("deny"))]
        n_answers = len(self.options)
        self.options.append(("Open the full card", lambda: self.pet.open_detail(self.key)))
        codex = (item or {}).get("agent") == "codex"
        self.options.append(("Answer in Codex" if codex and answerable else "Go to window", self._go))
        self._opt_row = tk.Frame(ov, bg=P["node"])
        self._opt_row.pack(fill="x", padx=pad, pady=self.px(6))
        self._opt_labels, self._opt_flow = [], None
        for n, (label, _) in enumerate(self.options):
            if self.radio and label == "Your own answer:":
                own_row = tk.Frame(self._opt_row, bg=P["node"])
                lab = tk.Label(own_row, font=self.f_main, cursor="hand2", padx=self.px(6), pady=self.px(1),
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
                lab = tk.Label(self._opt_row, font=self.f_main, cursor="hand2", padx=self.px(6), pady=self.px(1),
                               bg=P["node"], fg=P["tx"], anchor="w", justify="left")
                lab.bind("<Button-1>", lambda e, n=n: self.choose(n))
                lab._row = None
            lab._text = label
            lab._answer = n < n_answers
            self._opt_labels.append(lab)
        self._flow_options(self.px(560))
        if answerable:
            hint = ("1-9 or ↑↓ and Enter to answer · type your own and press Enter · Esc fold" if self.radio
                    else "1-9 or ←→ and Enter to answer · Esc fold")
        elif req.get("id") in self._released:
            hint = "Handed back to Codex: answer it in the Codex window now (Go to window takes you there)."
        else:
            hint = "This prompt can only be answered in the session window (or turn on answering in Permissions)."
        self.note = tk.Label(ov, text=hint, font=self.f_small, bg=P["node"], fg=P["mu"], anchor="w")
        self.note.pack(fill="x", padx=pad, pady=(0, pad))
        self.note.configure(justify="left")
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
        for n, lab in enumerate(labels):
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
                lab.configure(text=f"{n + 1} {mark}{lab._text}", bg=P["redbg"] if on else P["node"],
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
        self.note.configure(text="Sent." if ok else "Couldn't send it: the prompt may have been answered already.")
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
            self.note.configure(text=added)
