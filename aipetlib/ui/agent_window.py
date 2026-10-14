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
import aipetlib.ui.agent_menu as agent_menu
import aipetlib.ui.agent_prompts as agent_prompts
import aipetlib.ui.agent_logs as agent_logs
import aipetlib.ui.agent_graph as agent_graph
import json
import math
import sys
import time
import tkinter as tk
from tkinter import font as tkfont

from aipetlib.ui import theme
from aipetlib.runtime import diagnostics
from aipetlib.agents import log_filters, network, stats, tracker
from aipetlib.agents.tools import is_mcp_tool
from aipetlib.ui.agent_common import (  # noqa: F401  (the window's values, also read from here)
    REFRESH_MS, FRAME_MS, IS_MAC, ALPHA_MIN, ALPHA_STEP, OPACITY_STEP, SCALE_MIN, SCALE_MAX, SCALE_STEP,
    TEXT_SCALE_MIN, TEXT_SCALE_MAX, TEXT_SCALE_STEP, ZOOM_MIN, ZOOM_MAX, OPTIONS_KEY, DEFAULT_OPTIONS, LOG_SIDES,
    HINT, BG_OPACITY, _snap, load_options, log_preview, formatted_log_content, key_color, dither, needed_size,
    PALETTES, STATUS, ROBOT_STATE, KIND_MARK, mix, bezier, session_label, candidate_sessions, pick_session,
    suggestion_label, prompt_agent, parse_geometry, set_caption, has_caption, work_area, toggle_maximize, TextPopup,
    Slider,
)


class AgentNetworkWindow(agent_controls.WindowControls):
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
        self.settings = agent_menu.SettingsMenu(self)  # the cog and the settings drop-down (owns their state)
        self._after = self._anim = self._save_job = None
        self._gestures = self._gesture_start_job = None
        self.graph = agent_graph.GraphView(self)  # the graph and its viewport (owns its widgets and state)
        self.logs = agent_logs.LogPanel(self)  # the logs: tabs, filters, entries (owns its widgets and state)
        self.prompt = agent_prompts.PromptPanel(self)  # the prompt chip and overlay (owns its widgets and state)
        self.t0 = time.time()
        self.theme = None
        self.opts = load_options(getattr(pet, "cfg", {}))
        self.blocks = []  # (key, item, net) of every session the graph shows (several when compact and not tabbed)
        self.u = self.opts["scale"] / 100
        self.s = 1.5 * self.u
        # window attributes as set (only touched when they change). Starts at Tk's own defaults: setting -alpha or
        # -transparentcolor at all makes a Windows window "layered", which Tk then repaints badly while it is
        # dragged or resized, so a window that never needs them never gets them
        self._applied = {"topmost": False, "alpha": 1.0, "clear": ""}
        self._moving_until = 0.0
        self._drag = None
        self._fitting = False
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.title("AIPet · agent network")
        w.protocol("WM_DELETE_WINDOW", self.close)
        agent_shell.build(self)
        self._bind_keys()
        self.show_session(key)
        self._place()
        w.deiconify()
        self._apply_options()
        self.raise_()
        if IS_MAC:
            self._gesture_start_job = self.win.after_idle(self._start_gestures)
        self.win.after(250, self._settle)
        self._tick()
        self.graph.animate()

    def _settle(self):
        """Once the window is on screen: default zoom, graph centred."""
        if self.alive() and self.opts.get("auto_fit"):
            self.graph.maybe_auto_fit()
            return
        if self.alive() and not getattr(self.graph, "user_moved", False):
            self.graph.gz = 1.0
            self.graph.center_next = True
            self.graph.draw(force=True)
            self.graph.update_zoombar()

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
        if getattr(self, "_gesture_start_job", None):
            self.win.after_cancel(self._gesture_start_job)
            self._gesture_start_job = None
        if getattr(self, "_gestures", None):
            self._gestures.close()
            self._gestures = None
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

    # ---- keys and clicks
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
        w.bind("<Right>", key(lambda: self.prompt.step_question(1) if self.prompt.overlay_open else self.step_agent(1)))
        w.bind("<Left>", key(lambda: self.prompt.step_question(-1) if self.prompt.overlay_open else self.step_agent(-1)))
        w.bind("<Down>", key(lambda: self.prompt.step_option(1) if self.prompt.overlay_open else self.logs.text.yview_scroll(1, "units")))
        w.bind("<Up>", key(lambda: self.prompt.step_option(-1) if self.prompt.overlay_open else self.logs.text.yview_scroll(-1, "units")))
        for k in ("a", "A"):
            w.bind(k, key(lambda: self.select(-1)))
        for k in ("l", "L"):
            w.bind(k, key(lambda: self.tool("fold_logs")))
        for k in ("plus", "equal", "KP_Add"):
            w.bind(f"<{k}>", key(lambda: self.graph.zoom(1.25)))
        for k in ("minus", "KP_Subtract"):
            w.bind(f"<{k}>", key(lambda: self.graph.zoom(1 / 1.25)))
        w.bind("0", key(lambda: self.graph.zoom(reset=True) if not self.prompt.overlay_open else None))
        w.bind("<Prior>", key(lambda: self.step_session(-1), True))
        w.bind("<Next>", key(lambda: self.step_session(1), True))
        w.bind("<space>", key(lambda: self.prompt.open(not self.prompt.overlay_open)))
        w.bind("<Escape>", key(self._escape, True))
        w.bind("<Return>", key(lambda: self.prompt.choose(self.prompt.option) if self.prompt.overlay_open else None))
        for n in range(1, 10):
            w.bind(str(n), key(lambda n=n: self.prompt.choose(n - 1) if self.prompt.overlay_open else None))

    def _escape(self):
        if self.graph.stat_open:
            self.graph.toggle_stat(None)
        elif self.settings.open:
            self.settings.toggle(False)
        elif self.prompt.overlay_open:
            self.prompt.open(False)
        else:
            self.close()

    def _click_anywhere(self, e):
        """A click outside the settings drop-down folds it; a borderless window takes the keyboard on any click."""
        try:
            inside = str(e.widget).startswith(str(self.settings.frame)) or e.widget is self.settings.cog
        except (tk.TclError, AttributeError):
            inside = True
        if self.settings.open and not inside:
            self.settings.toggle(False)
        try:
            in_stats = str(e.widget).startswith(str(self.graph.statdrop)) or str(e.widget).startswith(str(self.graph.statc))
        except (tk.TclError, AttributeError):
            in_stats = True
        if self.graph.stat_open and not in_stats:
            self.graph.toggle_stat(None)
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

    def _typing(self):
        try:
            return isinstance(self.win.focus_get(), tk.Entry)
        except (tk.TclError, KeyError):
            return False

    # ---- theme
    @property
    def P(self):
        return PALETTES.get(theme.T.get("name"), PALETTES["light"])

    # ---- data
    def show_session(self, key=None):
        item = pick_session(getattr(self.pet, "_last_items", []), key)
        new_key = item.get("key") if item else None
        if self.pinned and key:
            new_key = key  # its own session, even after it ends
        if new_key != self.key:
            self.key, self.sel = new_key, -1
            self.prompt.open(False)
            self.logs.sig = self.graph.sig = self.prompt.sig = None
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
            diagnostics.log_error(f"agent network: {e!r}")
        self._after = self.win.after(REFRESH_MS, self._tick)

    def _network(self, item):
        """A session's agent network. While the background tracker runs, the one it read last: reading transcripts
        here, on the UI thread (and waiting for the tracker's lock), made the pet stutter while this window was open.
        A session it hasn't read yet shows empty for a moment and asks it to read now."""
        tools = self.opts.get("tools", True)
        if not tracker.running():
            return network.read_network(item, include_tools=tools)
        net = tracker.latest(item.get("key"))
        if net is None:
            tracker.wake()
            return {"agents": [], "log": []}
        return net if tools else network.without_tools(net)

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
            self.net = self._network(self.item)
        if len(self.net["agents"]) != n_before:
            self._update_minsize()
        self.blocks = [(self.key, self.item, self.net)] if self.item else []
        if self.graph.stacked_mode():
            # compact, not tabbed: every session's graph, one under the other
            self.blocks = [(i["key"], i, self.net if i["key"] == self.key else self._network(i))
                           for i in sessions]
        self.stats = stats.network_stats(self.net)
        if self.graph.stat_open:
            self.graph.fill_stat()
        if selected_id is not None:
            self.sel = next((i for i, a in enumerate(self.net["agents"]) if a["id"] == selected_id), -1)
        elif self.sel >= len(self.net["agents"]):
            self.sel = -1
        self.graph.draw()
        self.logs.draw_tabs()
        self.logs.update_filters()
        if not self.prompt.covers_logs():  # hidden under the open prompt: catch up once it folds
            self.logs.draw()
        self.prompt.draw()
        self.graph.maybe_auto_fit()

    # ---- selection
    def select(self, i, clear_filters=False):
        if clear_filters:
            self.logs.filters = log_filters.default_log_filters()
        if 0 <= i < len(self.net["agents"]) and self.net["agents"][i].get("tool"):
            tool = self.net["agents"][i]
            self.logs.filters.update(show_tools=True, primary=False, only_tool=tool["name"],
                                     tools=set() if is_mcp_tool(tool["name"]) else {tool["name"]},
                                     mcp={tool["name"]} if is_mcp_tool(tool["name"]) else set())
            i = next((j for j, a in enumerate(self.net["agents"]) if a["id"] == tool["parent"]), -1)
        self.sel = i
        self.graph.sig = self.logs.sig = None
        self.graph.draw()
        self.logs.draw_tabs()
        self.logs.update_filters()
        self.logs.draw()
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

    # The graph is drawn at its own zoom (self.graph.gz) on top of the window's scale: gpx() for sizes, g_* fonts.
    # Panning moves the canvas view over it (scan_mark / scan_dragto); everything else stays put.

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

    # ---- trackpad gestures (macOS)
    def _start_gestures(self):
        self._gesture_start_job = None
        try:
            from aipetlib.mac_gestures import MacGraphGestures
            self._gestures = MacGraphGestures(self, diagnostics.log_error)
        except Exception as error:
            diagnostics.log_error(f"graph gestures unavailable: {error!r}")

    # ---- text
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
