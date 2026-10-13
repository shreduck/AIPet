"""Agent window appearance controls, theme, platform options and geometry.

The owning window supplies its widgets and state; shared values come from their owner modules.
No application entry point is imported by this component.
"""
import math
import sys
import time
import tkinter as tk

from ..runtime import diagnostics
from ..runtime import settings
from . import agent_common as common
from . import theme


def tool(window, name, d=1):
    o = window.opts
    if name == "close":
        window.close()
        return "break"
    toggles = {"frame": "frameless", "pin": "on_top", "each": "separate", "tabs": "tabbed", "tidy": "tidy",
               "tail": "log_tail", "tools": "tools", "auto_fit": "auto_fit"}
    if name in toggles:
        o[toggles[name]] = not o[toggles[name]]
        if name == "tail":
            window._update_tailbtn()
            if o["log_tail"]:
                window.log.see("end")
    elif name == "alpha":
        o["alpha"] = common._snap(o["alpha"] + d * common.ALPHA_STEP, common.ALPHA_MIN, 100, common.ALPHA_STEP)
    elif name == "logs":
        o["log_side"] = common.LOG_SIDES[(common.LOG_SIDES.index(o.get("log_side", "bottom")) + d) % len(common.LOG_SIDES)]
        window._arrange()
        window._center_next = True
    elif name == "nodes":
        o["node_alpha"] = common._snap(o["node_alpha"] + d * common.OPACITY_STEP, common.ALPHA_MIN, 100, common.OPACITY_STEP)
    window._spin_until = time.time() + 0.6  # the cog turns a little on every change
    if name in ("tabs", "tools"):
        window._center_next = True
        window._refresh()
    if name != "each":
        window._apply_options(changed=name)
        if window.menu_open:
            window._build_menu()
    if name == "auto_fit":
        window._update_auto_fit()
        window._maybe_auto_fit(force=o["auto_fit"])
    window._save(name)
    return "break"


def _save(window, name, delay=0):
    def save():
        window._save_job = None
        try:
            window.pet.cfg[common.OPTIONS_KEY] = dict(window.opts)
            settings.save_setting(common.OPTIONS_KEY, dict(window.opts))
        except Exception as e:
            diagnostics.log_error(f"agent network options: {e!r}")
        if window.on_options:
            window.on_options(dict(window.opts), window, name)
    if window._save_job:
        try:
            window.win.after_cancel(window._save_job)
        except tk.TclError:
            pass
        window._save_job = None
    if delay:
        window._save_job = window.win.after(delay, save)
    else:
        save()


def set_options(window, opts):
    """The same settings as another agent network window (changed there)."""
    old = window.opts
    window.opts = dict(opts)
    if window.opts.get("tabbed") != old.get("tabbed"):
        window._center_next = True
    if any(window.opts.get(k) != old.get(k) for k in ("scale", "text_scale")):
        window._apply_scale()
    if any(window.opts.get(k) != old.get(k) for k in ("log_side", "log_lines", "log_cols")):
        window._arrange()
    if window.opts.get("tools") != old.get("tools"):
        window._refresh()
    window._apply_options()
    window._update_auto_fit()
    if window.opts.get("auto_fit") != old.get("auto_fit"):
        window._maybe_auto_fit(force=window.opts.get("auto_fit", False))


def reset_appearance(window, opts, cascade=0):
    """Default look again, centred on the main screen (the AIPet menu's Reset agent network appearance)."""
    window.toggle_menu(False)
    window.opts = dict(opts)
    window._arrange()
    window._apply_scale(grow=False)
    window._apply_options()
    window.cascade, window.at = cascade, None
    window._place()
    window.raise_()
    window._user_moved = False
    window.win.after(150, window._settle)  # and the graph at 100%, centred


def set_scale(window, u, save=True, grow=True):
    """Resize every font and drawing. grow: make the window big enough for the new scale."""
    u = common._snap(u * 100, common.SCALE_MIN, common.SCALE_MAX, common.SCALE_STEP) / 100
    if abs(u - window.u) < 1e-6:
        return
    window.opts["scale"] = int(round(u * 100))
    window._apply_scale(grow=grow)
    if save:
        window._save("scale", delay=500)


def set_text_scale(window, value, save=True):
    value = common._snap(value, common.TEXT_SCALE_MIN, common.TEXT_SCALE_MAX, common.TEXT_SCALE_STEP)
    if value == window.opts["text_scale"]:
        return
    window.opts["text_scale"] = value
    window._apply_scale(grow=False)
    if save:
        window._save("text_scale", delay=500)


def _font_scale(window):
    return window.s * window.opts.get("text_scale", 100) / 100


def _apply_scale(window, grow=True):
    window.u = window.opts["scale"] / 100
    window.s = 1.5 * window.u
    fs = window._font_scale()
    window.f_main.configure(size=max(5, int(round(8 * fs))))
    window.f_bold.configure(size=max(5, int(round(8 * fs))))
    window.f_small.configure(size=max(5, int(round(7 * fs))))
    window._robots.clear()
    window._graph_sig = window._tabs_sig = window._prompt_sig = window._log_sig = None
    window._update_minsize()
    if grow:
        try:
            w, h = window.needed()
            cw, ch = window.win.winfo_width(), window.win.winfo_height()
            if cw < w or ch < h:
                window._fitting = True
                window.win.geometry(f"{max(cw, w)}x{max(ch, h)}")
                window.win.after(200, lambda: setattr(window, "_fitting", False))
        except tk.TclError:
            pass
    window._size_cog()
    if window.menu_open:  # resize the drop-down in place (rebuilding it would drop a slider being dragged)
        for sl in getattr(window, "_sliders", []):
            sl.size(window.px(150), window.f_small.metrics("linespace") + window.px(4))
        if getattr(window, "scale_slider", None):
            window.scale_slider.set(window.opts["scale"], notify=False)
        if getattr(window, "text_scale_slider", None):
            window.text_scale_slider.set(window.opts["text_scale"], notify=False)
        window._place_menu()
    window._refresh()


def _sync_backdrop(window):
    """Show / move / restyle the backdrop window: exactly under the graph, right below this window."""
    want = bool(window._applied.get("clear"))
    b = getattr(window, "backdrop", None)
    try:
        if not want:
            if b is not None:
                b.destroy()
            window.backdrop = None
            return
        if b is None or not b.winfo_exists():
            b = window.backdrop = tk.Toplevel(window.win)
            b.withdraw()
            b.overrideredirect(True)
            for seq in ("<Button-1>", "<Button-3>"):
                b.bind(seq, lambda e: window._restack(focus=True))
        P, o = window.P, window.opts
        b.configure(bg=P["bg"])
        b.attributes("-alpha", max(0.05, o["alpha"] * o["bg_alpha"] / 10000))
        b.attributes("-topmost", bool(o["on_top"]))
        c = window.canvas
        x, y, w, h = c.winfo_rootx(), c.winfo_rooty(), c.winfo_width(), c.winfo_height()
        if w > 1 and h > 1 and window.win.winfo_viewable():
            b.geometry(f"{w}x{h}+{x}+{y}")
            if b.state() != "normal":
                b.deiconify()
            window._restack()
        elif b.state() == "normal":
            b.withdraw()
    except tk.TclError:
        pass


def _restack(window, focus=False):
    """The backdrop right below this window (after this window was brought to the front, for instance)."""
    b = getattr(window, "backdrop", None)
    try:
        if b is not None and b.winfo_exists() and b.state() == "normal":
            b.lower(window.win)
        if focus:
            window.win.lift()
            window.win.focus_force()
    except tk.TclError:
        pass


def _window_event(window, e):
    if e.widget is window.win:
        window.win.after_idle(window._sync_backdrop)


def _on_window_resize(window, e):
    """Resize the viewport without silently shrinking and saving the user's UI scale."""
    if e.widget is window.win:
        window._moving_until = time.time() + 0.3  # moved or resized: animations pause until it settles
    if e.widget is window.win and getattr(window, "backdrop", None) is not None:
        window.win.after_idle(window._sync_backdrop)  # moved or resized: the backdrop follows


def _apply_options(window, changed=None):
    o, P, w = window.opts, window.P, window.win
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
        if window._applied.get("topmost") != want["topmost"]:
            w.attributes("-topmost", want["topmost"])
        if window._applied.get("alpha") != want["alpha"]:
            w.attributes("-alpha", want["alpha"])
        if window._applied.get("clear") != want["clear"]:
            if common.IS_MAC:
                w.attributes("-transparent", bool(want["clear"]))
            elif sys.platform == "win32":
                w.attributes("-transparentcolor", want["clear"])
        window._applied = want
    except tk.TclError:
        pass
    if see_through:
        bg = "systemTransparent" if common.IS_MAC else want["clear"] or P["bg"]
    if sys.platform == "win32":
        window._win_caption(not o["frameless"])
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
        window.canvas.configure(bg=bg)  # only the graph goes see-through: the logs stay readable
    except tk.TclError:
        pass
    window._sync_backdrop()
    frameless = o["frameless"] and not common.IS_MAC
    if frameless:
        window.close_btn.pack(side="right", before=window.cog)
    else:
        window.close_btn.pack_forget()
    if frameless:  # (Windows' own edges are only a few pixels wide: these give more to grab)
        # no title bar, so no window frame to drag: thin handles along the edges, and the corner grip
        t = max(6, window.px(4))  # a frame wide enough to grab
        for side, h in window.handles.items():
            spot = {"e": dict(relx=1.0, y=0, anchor="ne", width=t, relheight=1.0),
                    "w": dict(x=0, y=0, anchor="nw", width=t, relheight=1.0),
                    "s": dict(x=0, rely=1.0, anchor="sw", height=t, relwidth=1.0),
                    "sw": dict(x=0, rely=1.0, anchor="sw", width=3 * t, height=3 * t)}[side]
            h.configure(bg=window.P["bar"], highlightthickness=0)
            h.place(**spot)
            h.lift()
        window.grip.place(relx=1.0, rely=1.0, anchor="se")
        window.grip.lift()
    else:
        window.grip.place_forget()
        for h in window.handles.values():
            h.place_forget()
    window._update_minsize()
    window._graph_sig = window._tabs_sig = None
    window._draw_graph(force=True)
    window._draw_tabs()
    window._update_auto_fit()


def _win_caption(window, show):
    common.set_caption(window.win, show)


def toggle_maximize(window, e=None):
    """Double-click the top bar: maximise (leaving the taskbar / Dock alone), or back."""
    return common.toggle_maximize(window.win, window)


def _on_resize(window, e=None):
    """The graph is redrawn once the size has settled (redrawing on every step of a resize drag flickers)."""
    if getattr(window, "_resize_job", None):
        try:
            window.win.after_cancel(window._resize_job)
        except tk.TclError:
            pass
    window._resize_job = window.win.after(70, window._resized)


def _resized(window):
    window._resize_job = None
    if not getattr(window, "_user_moved", False):
        window._center_next = True  # until you pan or zoom, the graph stays centred as the window opens / resizes
    window._draw_graph(force=True)
    if getattr(window, "backdrop", None) is not None:
        window.win.after_idle(window._sync_backdrop)
    if window.overlay_open:
        window._place_overlay()
    if window.menu_open:
        window._place_menu()
    window._maybe_auto_fit(force=True)  # the viewport changed even when the nodes did not


def _drag_start(window, e):
    window._drag = (e.x_root, e.y_root, window.win.winfo_x(), window.win.winfo_y(), window.win.winfo_width(),
                  window.win.winfo_height())


def _drag_move(window, e):
    try:
        if window.win.state() == "zoomed" or getattr(window, "_restore_geo", None):
            return  # maximised: stays put (double-click the bar to restore)
    except tk.TclError:
        pass
    if window._drag:
        x0, y0, wx, wy, _, _ = window._drag
        window.win.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")


def _resize_move(window, e, side="se"):
    """Resize a window without a title bar from its edges (the left ones move it too)."""
    if window._drag:
        x0, y0, wx, wy, ww, wh = window._drag
        mw, mh = window.needed(common.SCALE_MIN / 100)
        dx, dy = e.x_root - x0, e.y_root - y0
        w = ww + dx if "e" in side else (ww - dx if "w" in side else ww)
        h = wh + dy if "s" in side else wh
        w, h = max(mw, w), max(mh, h)
        x = wx + (ww - w) if "w" in side else wx
        window.win.geometry(f"{w}x{h}+{x}+{wy}")


def _size_cog(window):
    d = window.f_bold.metrics("linespace")
    window.cog.configure(width=d, height=d)
    window._draw_cog()


def _draw_cog(window):
    c, P = window.cog, window.P
    try:
        d = int(c.cget("width"))
        c.delete("all")
        c.configure(bg=P["bar"])
        cx = cy = d / 2
        r_out, r_in, r_hole = d * 0.46, d * 0.33, d * 0.13
        teeth = 8
        pts = []
        for k in range(teeth * 4):
            a = window._spin + k * math.pi / (teeth * 2)
            r = r_out if (k % 4) in (1, 2) else r_in
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        col = P["blue"] if window.menu_open or getattr(window, "_cog_hover", False) else P["mu"]
        c.create_polygon(*pts, fill=col, outline="")
        c.create_oval(cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole, fill=P["bar"], outline="")
    except (tk.TclError, ValueError):
        pass


def toggle_menu(window, show=None):
    window.menu_open = (not window.menu_open) if show is None else bool(show)
    if window.menu_open:
        if window.overlay_open:
            window.open_overlay(False)
        window._build_menu()
    else:
        window.menu.place_forget()
    window._draw_cog()
    return "break"


def _build_menu(window):
    P, m = window.P, window.menu
    for wdg in m.winfo_children():
        wdg.destroy()
    m.configure(bg=P["bar"], highlightbackground=P["line"], highlightcolor=P["line"])
    pad = window.px(8)
    o = window.opts
    width = window.px(150)
    line = window.f_small.metrics("linespace")
    window._sliders = []

    def section(title, first=False):
        tk.Label(m, text=f"── {title} " + "─" * 24, font=window.f_small, bg=P["bar"], fg=P["mu"], anchor="w").pack(
            fill="x", padx=pad, pady=(pad // 2 if first else window.px(6), 0))

    def toggle(name, opt, text, inverted=False):
        on = bool(o[opt]) != inverted
        lab = tk.Label(m, text=f"{'[on ]' if on else '[off]'} {text}", font=window.f_main, bg=P["bar"],
                       fg=P["blue"] if on else P["tx"], anchor="w", cursor="hand2")
        lab.pack(fill="x", padx=pad)
        lab.bind("<Button-1>", lambda e, n=name: (window.tool(n), "break")[1])

    def slider(text, lo, hi, step, value, on_change, live=True):
        row = tk.Frame(m, bg=P["bar"])
        row.pack(fill="x", padx=pad, pady=(window.px(2), 0))
        tk.Label(row, text=text.ljust(8), font=window.f_small, bg=P["bar"], fg=P["tx"]).pack(side="left")
        sl = common.Slider(window, row, lo, hi, step, value, on_change, live=live)
        sl.c.pack(side="left", fill="x", expand=True)
        sl.size(width, line + window.px(4))
        window._sliders.append(sl)
        return sl

    section("window", first=True)
    toggle("pin", "on_top", "always on top")
    if not common.IS_MAC:
        toggle("frame", "frameless", "title bar", inverted=True)
    toggle("each", "separate", "compact (one window for every session)", inverted=True)
    if not o["separate"] and not window.pinned:
        toggle("tabs", "tabbed", "  tabbed (one session at a time)")
    slider("opacity", common.ALPHA_MIN, 100, common.ALPHA_STEP, o["alpha"], lambda v: window._slide("alpha", v))
    window.scale_slider = slider("window scale", common.SCALE_MIN, common.SCALE_MAX, common.SCALE_STEP, o["scale"],
                               lambda v: window.set_scale(v / 100), live=False)
    window.text_scale_slider = slider("text scale", common.TEXT_SCALE_MIN, common.TEXT_SCALE_MAX, common.TEXT_SCALE_STEP,
                                    o["text_scale"], window.set_text_scale, live=False)

    section("graph")
    toggle("auto_fit", "auto_fit", "auto fit all")
    toggle("tools", "tools", "tool calls")
    if not o["separate"] and not window.pinned and not o["tabbed"]:
        toggle("tidy", "tidy", "tidy (each session a titled group)")
    if common.BG_OPACITY:
        slider("backgrnd", common.ALPHA_MIN, 100, common.OPACITY_STEP, o["bg_alpha"], lambda v: window._slide("bg_alpha", v))
    slider("nodes", common.ALPHA_MIN, 100, common.OPACITY_STEP, o["node_alpha"], lambda v: window._slide("node_alpha", v))

    section("logs")
    side = tk.Label(m, text=f"[{o['log_side']:^6}] position (click to move)", font=window.f_main, bg=P["bar"],
                    fg=P["tx"], anchor="w", cursor="hand2")
    side.pack(fill="x", padx=pad)
    side.bind("<Button-1>", lambda e: (window.tool("logs"), "break")[1])
    toggle("tail", "log_tail", "tail (follow the newest line)")
    tk.Label(m, text="esc fold · click outside", font=window.f_small, bg=P["bar"], fg=P["mu"], anchor="w").pack(
        fill="x", padx=pad, pady=(window.px(6), pad // 2))
    window._place_menu()


def _slide(window, key, value):
    window.opts[key] = value
    window._spin_until = time.time() + 0.4
    window._apply_options(changed=key)
    window._save(key, delay=400)


def _place_menu(window):
    try:
        window.menu.place(relx=1.0, x=-window.px(4), y=window.head.winfo_height(), anchor="ne")
        window.menu.lift()
    except tk.TclError:
        pass


def _apply_theme(window):
    name = theme.T.get("name", "light")
    if name == window.theme:
        return
    window.theme = name
    P = window.P
    for wdg in (window.win, window.body, window.tabs, window.filterbar):
        wdg.configure(bg=P["bg"])
    for wdg in (window.head, window.title, window.prev_btn, window.next_btn, window.count, window.foot, window.footbar,
                window.grip, window.close_btn, window.prompt_mark):
        wdg.configure(bg=P["bar"])
    for wdg in (window.title, window.prev_btn, window.next_btn, window.close_btn):
        wdg.configure(fg=P["hi"])
    window.prompt_mark.configure(fg=P["blue"])
    for wdg in (window.count, window.foot, window.grip):
        wdg.configure(fg=P["mu"])
    window.canvas.configure(bg=P["bg"])
    window.sep.configure(bg=P["line"])
    window.log.configure(bg=P["bg"], fg=P["tx"], insertbackground=P["bg"], selectbackground=common.mix(P["blue"], P["bg"], 0.65),
                       selectforeground=P["hi"], inactiveselectbackground=common.mix(P["blue"], P["bg"], 0.75))
    window.chip.configure(bg=P["redbg"], fg=P["red"], highlightbackground=P["red"])
    window.zoombar.configure(bg=P["bar"], highlightbackground=P["line"])
    for lab in window.zoom_btns:
        lab.configure(bg=P["bar"], fg=P["mu"] if lab is window.zoom_label else P["tx"])
    window._update_log_filters()
    window._statc_sig = None
    window.overlay.configure(bg=P["node"], highlightbackground=P["red"], highlightthickness=1, bd=0)
    try:
        theme.titlebar_theme(window.win)
    except Exception:
        pass
    window._robots.clear()
    window._log_sig = window._graph_sig = window._prompt_sig = None
    if window._applied.get("clear"):
        window._applied["clear"] = None  # the see-through key colour follows the theme
    window._apply_options()
    window._draw_cog()
