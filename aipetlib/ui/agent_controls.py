"""The agent window's own controls: options, scale, theme, backdrop, and moving / resizing the window.

WindowControls is a base class of the agent window (these are the window's methods, kept in their own module);
the settings drop-down is its own component (agent_menu.SettingsMenu).
No application entry point is imported by this module.
"""
import sys
import time
import tkinter as tk

from ..runtime import diagnostics
from ..runtime import settings
from . import agent_common as common
from . import theme


class WindowControls:
    """Options, scale, theme, backdrop and window geometry of the agent window."""

    def tool(self, name, d=1):
        o = self.opts
        if name == "close":
            self.close()
            return "break"
        toggles = {"frame": "frameless", "pin": "on_top", "each": "separate", "tabs": "tabbed", "tidy": "tidy",
                   "tail": "log_tail", "tools": "tools", "auto_fit": "auto_fit", "fold_logs": "log_collapsed"}
        if name in toggles:
            o[toggles[name]] = not o[toggles[name]]
            if name == "fold_logs":  # only the tabs stay, or the logs come back (drawn now: skipped while folded)
                self.logs.arrange()
                self.logs.update_tailbtn()
                self.graph.center_next = True
                if not o["log_collapsed"]:
                    self.logs.invalidate()
                    self.logs.draw()
            if name == "tail":
                self.logs.update_tailbtn()
                if o["log_tail"]:
                    self.logs.text.see("end")
        elif name == "alpha":
            o["alpha"] = common._snap(o["alpha"] + d * common.ALPHA_STEP, common.ALPHA_MIN, 100, common.ALPHA_STEP)
        elif name == "logs":
            o["log_side"] = common.LOG_SIDES[(common.LOG_SIDES.index(o.get("log_side", "bottom")) + d) % len(common.LOG_SIDES)]
            self.logs.arrange()
            self.graph.center_next = True
        elif name == "nodes":
            o["node_alpha"] = common._snap(o["node_alpha"] + d * common.OPACITY_STEP, common.ALPHA_MIN, 100, common.OPACITY_STEP)
        self.settings.spin_until = time.time() + 0.6  # the cog turns a little on every change
        if name in ("tabs", "tools"):
            self.graph.center_next = True
            self._refresh()
        if name != "each":
            self._apply_options(changed=name)
            if self.settings.open:
                self.settings.build()
        if name == "auto_fit":
            self.graph.update_auto_fit()
            self.graph.maybe_auto_fit(force=o["auto_fit"])
        self._save(name)
        return "break"

    def _save(self, name, delay=0):
        def save():
            self._save_job = None
            try:
                self.pet.cfg[common.OPTIONS_KEY] = dict(self.opts)
                settings.save_setting(common.OPTIONS_KEY, dict(self.opts))
            except Exception as e:
                diagnostics.log_error(f"agent network options: {e!r}")
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
            self.graph.center_next = True
        if any(self.opts.get(k) != old.get(k) for k in ("scale", "text_scale")):
            self._apply_scale()
        if any(self.opts.get(k) != old.get(k) for k in ("log_side", "log_lines", "log_cols", "log_collapsed")):
            self.logs.arrange()
            self.logs.update_tailbtn()
        if self.opts.get("tools") != old.get("tools"):
            self._refresh()
        self._apply_options()
        self.graph.update_auto_fit()
        if self.opts.get("auto_fit") != old.get("auto_fit"):
            self.graph.maybe_auto_fit(force=self.opts.get("auto_fit", False))

    def reset_appearance(self, opts, cascade=0):
        """Default look again, centred on the main screen (the AIPet menu's Reset agent network appearance)."""
        self.settings.toggle(False)
        self.opts = dict(opts)
        self.logs.arrange()
        self._apply_scale(grow=False)
        self._apply_options()
        self.cascade, self.at = cascade, None
        self._place()
        self.raise_()
        self.graph.user_moved = False
        self.win.after(150, self._settle)  # and the graph at 100%, centred

    def set_scale(self, u, save=True, grow=True):
        """Resize every font and drawing. grow: make the window big enough for the new scale."""
        u = common._snap(u * 100, common.SCALE_MIN, common.SCALE_MAX, common.SCALE_STEP) / 100
        if abs(u - self.u) < 1e-6:
            return
        self.opts["scale"] = int(round(u * 100))
        self._apply_scale(grow=grow)
        if save:
            self._save("scale", delay=500)

    def set_text_scale(self, value, save=True):
        value = common._snap(value, common.TEXT_SCALE_MIN, common.TEXT_SCALE_MAX, common.TEXT_SCALE_STEP)
        if value == self.opts["text_scale"]:
            return
        self.opts["text_scale"] = value
        self._apply_scale(grow=False)
        if save:
            self._save("text_scale", delay=500)

    def _font_scale(self):
        return self.s * self.opts.get("text_scale", 100) / 100

    def _apply_scale(self, grow=True):
        self.u = self.opts["scale"] / 100
        self.s = 1.5 * self.u
        fs = self._font_scale()
        self.f_main.configure(size=max(5, int(round(8 * fs))))
        self.f_bold.configure(size=max(5, int(round(8 * fs))))
        self.f_small.configure(size=max(5, int(round(7 * fs))))
        self.graph.robots.clear()
        self.graph.sig = self.logs.tabs_sig = self.prompt.sig = self.logs.sig = None
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
        self.settings.size_cog()
        if self.settings.open:  # resize the drop-down in place (rebuilding it would drop a slider being dragged)
            for sl in self.settings.sliders:
                sl.size(self.px(150), self.f_small.metrics("linespace") + self.px(4))
            if self.settings.scale_slider:
                self.settings.scale_slider.set(self.opts["scale"], notify=False)
            if self.settings.text_scale_slider:
                self.settings.text_scale_slider.set(self.opts["text_scale"], notify=False)
            self.settings.place()
        self._refresh()

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
            c = self.graph.canvas
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
        """Resize the viewport without silently shrinking and saving the user's UI scale."""
        if e.widget is self.win:
            self._moving_until = time.time() + 0.3  # moved or resized: animations pause until it settles
        if e.widget is self.win and getattr(self, "backdrop", None) is not None:
            self.win.after_idle(self._sync_backdrop)  # moved or resized: the backdrop follows

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
        see_through = common.BG_OPACITY and o["bg_alpha"] < 100
        want = {"topmost": bool(o["on_top"]), "alpha": o["alpha"] / 100,
                "clear": (common.key_color(P["bg"]) if not common.IS_MAC else "mac") if see_through else ""}
        try:
            if self._applied.get("topmost") != want["topmost"]:
                w.attributes("-topmost", want["topmost"])
            if self._applied.get("alpha") != want["alpha"]:
                w.attributes("-alpha", want["alpha"])
            if self._applied.get("clear") != want["clear"]:
                if common.IS_MAC:
                    w.attributes("-transparent", bool(want["clear"]))
                elif sys.platform == "win32":
                    w.attributes("-transparentcolor", want["clear"])
            self._applied = want
        except tk.TclError:
            pass
        if see_through:
            bg = "systemTransparent" if common.IS_MAC else want["clear"] or P["bg"]
        if sys.platform == "win32":
            self._win_caption(not o["frameless"])
        elif changed in (None, "frame") and not common.IS_MAC:
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
            self.graph.canvas.configure(bg=bg)  # only the graph goes see-through: the logs stay readable
        except tk.TclError:
            pass
        self._sync_backdrop()
        frameless = o["frameless"] and not common.IS_MAC
        if frameless:
            self.close_btn.pack(side="right", before=self.settings.cog)
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
        self.graph.sig = self.logs.tabs_sig = None
        self.graph.draw(force=True)
        self.logs.draw_tabs()
        self.graph.update_auto_fit()

    def _win_caption(self, show):
        common.set_caption(self.win, show)

    def toggle_maximize(self, e=None):
        """Double-click the top bar: maximise (leaving the taskbar / Dock alone), or back."""
        return common.toggle_maximize(self.win, self)

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
        if not getattr(self.graph, "user_moved", False):
            self.graph.center_next = True  # until you pan or zoom, the graph stays centred as the window opens / resizes
        self.graph.draw(force=True)
        if getattr(self, "backdrop", None) is not None:
            self.win.after_idle(self._sync_backdrop)
        if self.prompt.overlay_open:
            self.prompt.place()
        if self.settings.open:
            self.settings.place()
        self.graph.maybe_auto_fit(force=True)  # the viewport changed even when the nodes did not

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
            mw, mh = self.needed(common.SCALE_MIN / 100)
            dx, dy = e.x_root - x0, e.y_root - y0
            w = ww + dx if "e" in side else (ww - dx if "w" in side else ww)
            h = wh + dy if "s" in side else wh
            w, h = max(mw, w), max(mh, h)
            x = wx + (ww - w) if "w" in side else wx
            self.win.geometry(f"{w}x{h}+{x}+{wy}")

    def _apply_theme(self):
        name = theme.T.get("name", "light")
        if name == self.theme:
            return
        self.theme = name
        P = self.P
        for wdg in (self.win, self.body, self.logs.tabs, self.logs.filterbar):
            wdg.configure(bg=P["bg"])
        for wdg in (self.head, self.title, self.prev_btn, self.next_btn, self.count, self.foot, self.footbar,
                    self.grip, self.close_btn, self.prompt_mark):
            wdg.configure(bg=P["bar"])
        for wdg in (self.title, self.prev_btn, self.next_btn, self.close_btn):
            wdg.configure(fg=P["hi"])
        self.prompt_mark.configure(fg=P["blue"])
        for wdg in (self.count, self.foot, self.grip):
            wdg.configure(fg=P["mu"])
        self.graph.canvas.configure(bg=P["bg"])
        self.logs.sep.configure(bg=P["line"])
        self.logs.text.configure(bg=P["bg"], fg=P["tx"], insertbackground=P["bg"], selectbackground=common.mix(P["blue"], P["bg"], 0.65),
                           selectforeground=P["hi"], inactiveselectbackground=common.mix(P["blue"], P["bg"], 0.75))
        self.prompt.chip.configure(bg=P["redbg"], fg=P["red"], highlightbackground=P["red"])
        self.graph.zoombar.configure(bg=P["bar"], highlightbackground=P["line"])
        for lab in self.graph.zoom_btns:
            lab.configure(bg=P["bar"], fg=P["mu"] if lab is self.graph.zoom_label else P["tx"])
        self.logs.update_filters()
        self.graph.statc_sig = None
        self.prompt.overlay.configure(bg=P["node"], highlightbackground=P["red"], highlightthickness=1, bd=0)
        try:
            theme.titlebar_theme(self.win)
        except Exception:
            pass
        self.graph.robots.clear()
        self.logs.sig = self.graph.sig = self.prompt.sig = None
        if self._applied.get("clear"):
            self._applied["clear"] = None  # the see-through key colour follows the theme
        self._apply_options()
        self.settings.draw_cog()
