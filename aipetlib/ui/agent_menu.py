"""The agent window's settings drop-down and the cog that opens it.

SettingsMenu owns the drop-down frame, the cog, the sliders and whether the menu is open; the window holds it as
`window.settings` and the menu changes options through the window's own methods (tool, set_scale, ...).
"""
import math
import time
import tkinter as tk

from . import agent_common as common


class SettingsMenu:
    """The settings drop-down: window, graph and log options, opened from the cog."""

    def __init__(self, window):
        self.window = window
        self.open = False  # the drop-down is showing
        self.hover = False  # the pointer is on the cog
        self.spin, self.spin_until = 0.0, 0.0  # the cog's angle; it turns until spin_until (and while open / hovered)
        self.sliders = []
        self.scale_slider = self.text_scale_slider = None
        self.frame = self.cog = None  # built by the window shell

    def size_cog(self):
        window = self.window
        d = window.f_bold.metrics("linespace")
        self.cog.configure(width=d, height=d)
        self.draw_cog()

    def draw_cog(self):
        window = self.window
        c, P = self.cog, window.P
        try:
            d = int(c.cget("width"))
            c.delete("all")
            c.configure(bg=P["bar"])
            cx = cy = d / 2
            r_out, r_in, r_hole = d * 0.46, d * 0.33, d * 0.13
            teeth = 8
            pts = []
            for k in range(teeth * 4):
                a = self.spin + k * math.pi / (teeth * 2)
                r = r_out if (k % 4) in (1, 2) else r_in
                pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
            col = P["blue"] if self.open or self.hover else P["mu"]
            c.create_polygon(*pts, fill=col, outline="")
            c.create_oval(cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole, fill=P["bar"], outline="")
        except (tk.TclError, ValueError):
            pass

    def toggle(self, show=None):
        window = self.window
        self.open = (not self.open) if show is None else bool(show)
        if self.open:
            if window.prompt.overlay_open:
                window.prompt.open(False)
            self.build()
        else:
            self.frame.place_forget()
        self.draw_cog()
        return "break"

    def build(self):
        window = self.window
        P, m = window.P, self.frame
        for wdg in m.winfo_children():
            wdg.destroy()
        m.configure(bg=P["bar"], highlightbackground=P["line"], highlightcolor=P["line"])
        pad = window.px(8)
        o = window.opts
        width = window.px(150)
        line = window.f_small.metrics("linespace")
        self.sliders = []

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
            self.sliders.append(sl)
            return sl

        section("window", first=True)
        toggle("pin", "on_top", "always on top")
        if not common.IS_MAC:
            toggle("frame", "frameless", "title bar", inverted=True)
        toggle("each", "separate", "compact (one window for every session)", inverted=True)
        if not o["separate"] and not window.pinned:
            toggle("tabs", "tabbed", "  tabbed (one session at a time)")
        slider("opacity", common.ALPHA_MIN, 100, common.ALPHA_STEP, o["alpha"], lambda v: self.slide("alpha", v))
        self.scale_slider = slider("window scale", common.SCALE_MIN, common.SCALE_MAX, common.SCALE_STEP, o["scale"],
                                   lambda v: window.set_scale(v / 100), live=False)
        self.text_scale_slider = slider("text scale", common.TEXT_SCALE_MIN, common.TEXT_SCALE_MAX, common.TEXT_SCALE_STEP,
                                        o["text_scale"], window.set_text_scale, live=False)

        section("graph")
        toggle("auto_fit", "auto_fit", "auto fit all")
        toggle("tools", "tools", "tool calls")
        if not o["separate"] and not window.pinned and not o["tabbed"]:
            toggle("tidy", "tidy", "tidy (each session a titled group)")
        if common.BG_OPACITY:
            slider("backgrnd", common.ALPHA_MIN, 100, common.OPACITY_STEP, o["bg_alpha"], lambda v: self.slide("bg_alpha", v))
        slider("nodes", common.ALPHA_MIN, 100, common.OPACITY_STEP, o["node_alpha"], lambda v: self.slide("node_alpha", v))

        section("logs")
        side = tk.Label(m, text=f"[{o['log_side']:^6}] position (click to move)", font=window.f_main, bg=P["bar"],
                        fg=P["tx"], anchor="w", cursor="hand2")
        side.pack(fill="x", padx=pad)
        side.bind("<Button-1>", lambda e: (window.tool("logs"), "break")[1])
        toggle("tail", "log_tail", "tail (follow the newest line)")
        toggle("fold_logs", "log_collapsed", "folded (only the tabs; l)")
        tk.Label(m, text="esc fold · click outside", font=window.f_small, bg=P["bar"], fg=P["mu"], anchor="w").pack(
            fill="x", padx=pad, pady=(window.px(6), pad // 2))
        self.place()

    def slide(self, key, value):
        window = self.window
        window.opts[key] = value
        self.spin_until = time.time() + 0.4
        window._apply_options(changed=key)
        window._save(key, delay=400)

    def place(self):
        window = self.window
        try:
            self.frame.place(relx=1.0, x=-window.px(4), y=window.head.winfo_height(), anchor="ne")
            self.frame.lift()
        except tk.TclError:
            pass
