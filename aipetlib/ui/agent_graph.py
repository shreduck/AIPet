"""Agent graph layout, rendering, navigation and activity animation.

The owning window supplies its widgets and state; shared values come from their owner modules.
No application entry point is imported by this component.
"""
import math
import time
import tkinter as tk

from ..agents.records import fmt_tokens
from . import agent_common as common
from . import sprites


class GraphView:
    """The agent window's graph: one node per agent (and per tool), the edges between them, the session bands and
    titles, the zoom bar, the stats node, its tooltip and drop-down, and the viewport (zoom, pan, centre, fit, auto-fit).

    Owns those widgets and that state. It reads what to show from the window (blocks, net, sel, opts, palette P, fonts,
    scale s, theme, stats, key, item, in_window) and tells it what was clicked (select, show_session) and what changed
    (the logs and the prompt redraw). Call invalidate() when what it draws from changed underneath (theme, scale).
    """

    def __init__(self, window):
        self.window = window
        self.gz = 1.0  # the graph's own zoom (zoom buttons, ctrl + wheel, + / -)
        self.sig = None  # what the graph last drew
        self.edges, self.dots = [], []
        self.content, self.center_next, self.panned = None, True, False
        self.bands, self.leaving, self.titles, self.animating = {}, {}, [], False
        self.robots = {}  # robot images by (state, height, theme)

    FADE_S, SLIDE_S = 0.45, 0.35  # stacked sessions (compact, not tabbed): bands fade out and slide up

    def invalidate(self):
        """Draw the graph again next time, even if its data looks unchanged."""
        self.sig = None

    def build(self):
        """The graph's widgets: its canvas, the zoom bar (top left), the stats node (bottom right), the tooltip."""
        window = self.window
        w = window.win
        self.canvas = tk.Canvas(window.body, highlightthickness=0, bd=0, height=window.px(150), width=window.px(200))
        self.canvas.bind("<Configure>", window._on_resize)
        self.canvas.bind("<ButtonPress-1>", self.pan_start)
        self.canvas.bind("<B1-Motion>", self.pan_move)
        self.canvas.bind("<ButtonRelease-1>", self.pan_end, add="+")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(seq, self.wheel)
        # zoom controls (top left of the graph) and the stats node (bottom right): fixed, outside the zoom
        self.zoombar = tk.Frame(self.canvas, bd=0, highlightthickness=1)
        self.zoom_btns = []
        for text, fn in (("[-]", lambda: self.zoom(1 / 1.25)), (None, None), ("[+]", lambda: self.zoom(1.25)),
                         ("[reset]", lambda: self.zoom(reset=True)), ("[center]", lambda: self.center_view(by_user=True)),
                         ("[fit all]", self.fit_all)):
            lab = tk.Label(self.zoombar, text=text or "100%", font=window.f_small, cursor="" if fn is None else "hand2",
                           padx=window.px(2))
            lab.pack(side="left")
            if fn:
                lab.bind("<Button-1>", lambda e, fn=fn: fn())
            else:
                self.zoom_label = lab
            self.zoom_btns.append(lab)
        self.auto_fit_var = tk.BooleanVar(master=window.win, value=window.opts.get("auto_fit", False))
        self.auto_fit_btn = tk.Checkbutton(self.zoombar, text="auto", font=window.f_small,
                                          variable=self.auto_fit_var, command=lambda: window.tool("auto_fit"),
                                          bd=0, highlightthickness=0, padx=window.px(2), pady=0, cursor="hand2")
        self.auto_fit_btn.pack(side="left")
        self.zoombar.place(x=window.px(4), y=window.px(4), anchor="nw")
        self.statc = tk.Canvas(self.canvas, bd=0, highlightthickness=1)
        self.statc.place(relx=1.0, rely=1.0, x=-window.px(6), y=-window.px(6), anchor="se")
        self.tip = tk.Label(w, font=window.f_small, justify="left", anchor="w", padx=window.px(6), pady=window.px(3))

    def build_stat_drop(self):
        """The stats node's drop-down (opened by clicking a stats row)."""
        window = self.window
        w = window.win
        self.statdrop = tk.Frame(w, bd=0, highlightthickness=1)
        self.stathead = tk.Label(self.statdrop, font=window.f_small, anchor="w", cursor="hand2")
        self.stathead.pack(fill="x", padx=window.px(6), pady=(window.px(3), 0))
        self.stathead.bind("<Button-1>", lambda e: self.toggle_stat(self.stat_open))
        self.stattext = tk.Text(self.statdrop, font=window.f_small, bd=0, highlightthickness=0, wrap="none",
                                takefocus=0, cursor="xterm", padx=window.px(6), pady=window.px(3))
        self.stattext.bind("<Control-c>", lambda e: window._copy(self.stattext))
        self.stattext.bind("<Button-1>", lambda e: self.stattext.focus_set(), add="+")
        self.stattext.pack(fill="both", expand=True)
        self.stat_open = None

    def robot(self, state, height):
        window = self.window
        key = (state, height, window.theme)
        if key not in self.robots:
            from PIL import Image
            im = sprites.state_image(common.ROBOT_STATE.get(state, "idle")).convert("RGBA")
            if state == "needs_input":  # red when that agent needs you
                a = im.split()[3]
                tinted = Image.blend(im, Image.new("RGBA", im.size, (214, 48, 40, 255)), 0.55)
                tinted.putalpha(a)
                im = tinted
            width = max(1, round(im.width * height / im.height))
            im = im.resize((width, max(1, height)), Image.LANCZOS)
            self.robots[key] = sprites._to_photo(im)
        return self.robots[key]

    def gpx(self, n):
        window = self.window
        return int(round(n * window.s * self.gz))

    def fonts(self):
        window = self.window
        size = max(1, int(round(8 * window._font_scale() * self.gz)))
        small = max(1, int(round(7 * window._font_scale() * self.gz)))
        for f, sz in ((window.g_main, size), (window.g_bold, size), (window.g_small, small)):
            if f.cget("size") != sz:
                f.configure(size=sz)

    def line_height(self):
        # Tk's minimum font size must not prevent a large diagram from fitting in the viewport.
        window = self.window
        return window.f_main.metrics("linespace") * self.gz if self.gz < 0.4 else window.g_main.metrics("linespace")

    def block_height(self, net, H):
        """Height one session's graph needs (tabbed off: the sessions are stacked)."""
        window = self.window
        cols, beside = window._columns(net)
        rows = max((len(c) for c in cols), default=0)
        lh = self.line_height()
        main_h = lh * 4 + self.gpx(10)
        sub_h = lh * 4 + self.gpx(8)
        return int(max(main_h, rows * (sub_h + self.gpx(12 if common.IS_MAC else 6))) + 2 * self.gpx(10) + lh * 1.6)

    def layout(self, net, W, H, reserve=None):
        """Node boxes (one per agent, in net["agents"] order): main on the left, its subagents in a column to its
        right, agents started by those further right. reserve: (x, y) of a box at the bottom right that the columns
        should stay above. -> (boxes, beside)"""
        window = self.window
        lh = self.line_height()
        pad = self.gpx(10)
        main_w, main_h = self.gpx(150), lh * 4 + pad
        boxes = [None] * len(net["agents"])
        boxes[0] = (pad, max(pad, (H - main_h) // 2), main_w, main_h)
        cols, beside = window._columns(net)
        if not cols:
            return boxes, False
        sub_w = self.gpx(140)
        sub_h = lh * 4 + self.gpx(8)
        x0 = pad + main_w + self.gpx(40 if common.IS_MAC else 70)
        gap_x = sub_w + self.gpx(40 if common.IS_MAC else 60)
        if reserve and x0 + (len(cols) - 1) * gap_x + sub_w > reserve[0] - pad:
            H = max(sub_h + 2 * pad, reserve[1])
        if common.IS_MAC:
            # Keep siblings together on Mac instead of spreading them across the
            # viewport. Preserve the existing Windows layout and display scaling.
            step = sub_h + self.gpx(12)
            H = max(H, max(len(col) for col in cols) * step + 2 * pad)
            boxes[0] = (pad, (H - main_h) // 2, main_w, main_h)
        for c, col in enumerate(cols):
            rows = len(col)
            if not common.IS_MAC:
                step = max(sub_h + self.gpx(6), (H - 2 * pad) / max(1, rows))
            for row, i in enumerate(col):
                top = pad + (H - 2 * pad - step * rows) / 2 + row * step + (step - sub_h) / 2
                boxes[i] = (x0 + c * gap_x, int(max(pad // 2, top)), sub_w, sub_h)
        return boxes, beside

    def draw(self, force=False):
        window = self.window
        c, P = self.canvas, window.P
        W, H = max(50, c.winfo_width()), max(50, c.winfo_height())
        blocks = window.blocks
        sig = (W, H, self.gz, window.sel, window.key, window.theme, window.s, window.opts["node_alpha"], window.prompt.in_window,
               tuple((k, tuple((a["id"], a.get("parent", ""), a.get("depth", 0),
                                a["name"], a["state"], a["model_label"], a["effort"], a["ctx"], a["thought"] if a["main"] else "")
                               for a in net["agents"])) for k, _, net in blocks))
        if sig == self.sig and not force:
            self.draw_stats()
            return
        self.sig = sig
        self.fonts()
        # keep the view where it was (as a fraction of the drawing) unless a centre was asked for
        old = getattr(self, "content", None)
        view = (c.canvasx(0), c.canvasy(0)) if old else None
        c.delete("all")
        self.edges, self.node_rects, self.title_rects = [], [], []
        fading = self.stacked_mode() and (self.leaving or any(
            b.get("net", {}).get("agents") for b in self.bands.values()))  # the last session still fading out
        if (not blocks or not any(net["agents"] for _, _, net in blocks)) and not fading:
            c.configure(scrollregion=(0, 0, W, H))
            c.xview_moveto(0)
            c.yview_moveto(0)
            c.create_text(W // 2, H // 2, text="$ waiting for a session in claude code or codex_",
                          fill=P["mu"], font=window.f_main)
            self.dots, self.content = [], None
            self.draw_stats()
            return
        VW = max(W * self.gz, self.gpx(420))
        stacked = self.stacked_mode() or bool(self.leaving)
        sbox = self.stats_size()
        now = time.time()
        self.titles = []
        animating = False
        if stacked:
            y1 = self.plan_bands(blocks, H, now)
            tidy = window.opts.get("tidy")
            lh = self.line_height()
            head = int(lh * 1.6)
            order = [(k, None) for k in self.leaving] + [(k, b) for b, (k, _, _) in enumerate(blocks)]
            for key, b in order:
                leaving = key in self.leaving
                band = self.leaving[key] if leaving else self.bands[key]
                fade = min(1.0, (now - band["t0"]) / self.FADE_S) if leaving else 0.0
                y = band["y"] if leaving else self.band_y(band, now)
                animating = animating or leaving or now < band["t"] + self.SLIDE_S
                self.draw_band(b if b is not None else -1, key, band["item"], band["net"], y, band["h"], VW, head,
                                tidy, fade)
        else:
            self.bands, y1 = {}, 0
            key, item, net = blocks[0]
            bh = max(H * self.gz, self.block_height(net, H))
            self.bands[key] = {"from": 0, "to": 0, "t": 0, "y": 0, "item": item, "net": net, "h": bh}
            reserve = (VW - sbox[0] - self.gpx(8), bh - sbox[1] - self.gpx(8))
            self.draw_block(0, key, net, 0, 0, VW, bh, reserve)
            y1 = bh
        self.animating = animating
        x1 = VW
        bounds = self.bounds()
        if bounds:
            x1, y1 = max(x1, bounds[2]), max(y1, bounds[3])
        content_old = self.content
        self.content = (x1, y1)
        # scroll region: the drawing plus a view's worth around it, so it can be panned anywhere
        c.configure(scrollregion=(min(-W, bounds[0] - W) if bounds else -W,
                                  min(-H, bounds[1] - H) if bounds else -H, x1 + W, y1 + H))
        layout = ("stacked",) if stacked else ("single", blocks[0][0])
        if layout != getattr(self, "last_layout", None):
            self.last_layout, self.center_next, self.user_moved = layout, True, False
        if self.center_next or not old:
            if W > 80 and H > 80 and c.winfo_ismapped():
                self.center_next = False  # centred for real (not on the 1-pixel canvas of a window still opening)
            self.center_view()
        elif view and (animating or getattr(self, "was_animating", False)):
            self.scroll_to(*view)  # sessions sliding: the view stays exactly where it is
        elif view:
            fx, fy = (view[0] + W / 2) / max(1, old[0]), (view[1] + H / 2) / max(1, old[1])
            self.scroll_to(fx * x1 - W / 2, fy * y1 - H / 2)
        self.was_animating = animating
        if not self.center_next and not self.shift_into_view(margin=0.0, check_only=True):
            self.center_view()  # nothing of the graph in sight (say, its only session moved): show it again
        self.dots = [c.create_oval(0, 0, 0, 0, fill=col, outline="", state="hidden")
                      for pts, st, col in self.edges for _ in range(2)]
        self.draw_stats()

    def stacked_mode(self):
        window = self.window
        return not window.pinned and not window.opts["separate"] and not window.opts["tabbed"]

    def plan_bands(self, blocks, H, now):
        """Where each session's band goes. A session that left fades where it was; the others then slide to their
        new place. Returns the drawing's height."""
        window = self.window
        gap = self.gpx(14) if window.opts.get("tidy") else 0
        head = int(self.line_height() * 1.6)
        targets, heights, y = {}, {}, 0
        for key, item, net in blocks:
            heights[key] = self.block_height(net, H) + (self.gpx(6) if window.opts.get("tidy") else 0)
            targets[key] = y
            y += heights[key] + gap
        prev = getattr(self, "bands", {}) or {}
        for key, band in prev.items():
            if key not in targets and key not in self.leaving and band.get("net", {}).get("agents"):
                self.leaving[key] = dict(band, y=self.band_y(band, now), t0=now)
        self.leaving = {k: v for k, v in self.leaving.items() if now - v["t0"] < self.FADE_S and k not in targets}
        delay = max([self.FADE_S - (now - v["t0"]) for v in self.leaving.values()] or [0])
        bands = {}
        for key, item, net in blocks:
            band = prev.get(key)
            if band is None or getattr(self, "fit_view", False):
                band = {"from": targets[key], "to": targets[key], "t": now - self.SLIDE_S}
            elif abs(band["to"] - targets[key]) > 0.5:  # its place changed: slide there (after any fade)
                band = {"from": self.band_y(band, now), "to": targets[key], "t": now + delay}
            bands[key] = dict(band, item=item, net=net, h=heights[key])
        self.bands = bands
        end = max([b["to"] + b["h"] for b in bands.values()] + [v["y"] + v["h"] for v in self.leaving.values()]
                  + [head])
        return end

    def band_y(self, band, now):
        p = min(1.0, max(0.0, (now - band["t"]) / self.SLIDE_S))
        ease = 1 - (1 - p) ** 3
        return band["from"] + (band["to"] - band["from"]) * ease

    def draw_band(self, b, key, item, net, y, bh, VW, head, tidy, fade):
        window = self.window
        c, P = self.canvas, window.P
        F = (lambda col: common.mix(col, P["bg"], fade)) if fade else (lambda col: col)
        on = key == window.key and not fade
        tag = f"band{b}" if b >= 0 else f"gone{abs(hash(key)) % 10000}"
        title = common.session_label(item).replace("agent-network ▸ ", "")
        tbar = f"tbar{b}" if b >= 0 else tag  # the session's title bar: a click opens its [all] log
        pad = self.gpx(8)
        if tidy:  # a group: a title bar on top of the session's nodes, its name sliding when it doesn't fit
            x0, x1 = pad, VW - pad
            if b >= 0:
                self.title_rects.append((x0, y, x1, y + head))
            c.create_rectangle(x0, y + head, x1, y + bh, fill="", outline=F(P["blue"] if on else P["line"]),
                               dash=() if on else (2, 3))
            c.create_rectangle(x0, y, x1, y + head, fill=F(P["sel"] if on else P["bar"]),
                               outline=F(P["blue"] if on else P["line"]), tags=(tag, tbar))
            font = window.g_bold if on else window.g_main
            entry = (f"ttl{b}_{abs(hash(key)) % 10000}", ("● " if on else "○ ") + title, font,
                     F(P["hi"] if on else P["tx"]), x0 + pad, x1 - x0 - 2 * pad, y + head / 2, (tag, tbar))
            self.titles.append(entry)
            self.draw_title(entry, time.time())
        else:  # bands: a line between sessions; the name is a title bar on the session's main node
            far = 50000  # bands run on forever sideways, however far you pan
            c.create_rectangle(-far, y, VW + far, y + bh, fill=P["sel"] if on else P["bg"], outline="",
                               stipple="gray25" if on else "", tags=tag)
            c.create_line(-far, y, VW + far, y, fill=F(P["line"]))
        if b >= 0:
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=key: self.pick_session(k))
        self.draw_block(b, key, net, pad if tidy else 0, y + head, VW - (2 * pad if tidy else 0), bh - head, None,
                         fade=fade, title=None if tidy else (title, on, (tag, tbar)))
        if b >= 0:
            c.tag_bind(tbar, "<ButtonRelease-1>", lambda e, k=key: self.title_click(k))
            c.tag_bind(tbar, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tbar, "<Leave>", lambda e: c.configure(cursor=""))

    def draw_title(self, entry, now):
        """A group's title: as is when it fits, else scrolling round like the pet's name tags (characters drawn one
        by one, only while wholly inside the bar: a Tk canvas can't clip text)."""
        window = self.window
        tag, text, font, fill, left, room, ymid, click = entry
        c = self.canvas
        c.delete(tag)
        if font.measure(text) <= room:
            c.create_text(left, ymid, text=text, anchor="w", fill=fill, font=font, tags=(tag,) + tuple(click))
            return False
        loop_text = text + "    "
        loop = font.measure(loop_text)
        offset = (now * 30 * window.s * self.gz) % loop
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

    def draw_block(self, b, key, net, ox, oy, W, H, reserve, fade=0.0, title=None):
        """One session's graph, at (ox, oy) in a W x H area. fade: 0 as is .. 1 gone (a session being cleared)."""
        window = self.window
        c, P0 = self.canvas, window.P
        P = {k: (common.mix(v, P0["bg"], fade) if fade and isinstance(v, str) and v.startswith("#") else v)
             for k, v in P0.items()}
        ags = net["agents"]
        if not ags:
            return
        current = key == window.key
        boxes, beside = self.layout(net, W, H, reserve)
        boxes = [(x + ox, y + oy, w, h) for x, y, w, h in boxes]
        lh = self.line_height()
        index = {a["id"]: k for k, a in enumerate(ags)}
        for i, (a, (x, y, w, h)) in enumerate(zip(ags[1:], boxes[1:]), 1):  # edges first, under the nodes
            mx, my, mw, mh = boxes[index.get(a.get("parent", ""), 0)]  # from whoever started it
            p0, p3 = (mx + mw, my + mh / 2), (x, y + h / 2)
            mid = (p0[0] + p3[0]) / 2
            pts = (p0, (mid, p0[1]), (mid, p3[1]), p3)
            col = {"working": P["blue"], "needs_input": P["red"], "error": P["red"],
                   "done": P["grn"]}.get(a["state"], P["line"])
            dash = () if a["state"] == "done" else (3, 4)
            line = [coord for t in range(0, 21) for coord in common.bezier(*pts, t / 20)]
            c.create_line(*line, fill=col, width=max(1, self.gpx(1)), dash=dash, smooth=True)
            if not fade:
                self.edges.append((pts, a["state"], col))
        for i, (a, (x, y, w, h)) in enumerate(zip(ags, boxes)):
            status_col, status = common.STATUS.get(a["state"], common.STATUS["idle"])
            border = P[status_col] if a["state"] != "idle" else P["line"]
            if a["main"] and a["state"] == "waiting":
                border = P["amb"]
            focused = a.get("tool") and a["name"] == window.logs.filters.get("only_tool") and \
                0 <= window.sel < len(ags) and a["parent"] == ags[window.sel]["id"]
            picked = current and (i == window.sel or focused)
            fill = P["sel"] if picked else P["node"]
            tag = f"node{b}_{i}" if b >= 0 else "gone"
            if b >= 0:
                self.node_rects.append((x, y, x + w, y + h))
            c.create_rectangle(x, y, x + w, y + h, fill=fill, stipple=common.dither(window.opts["node_alpha"]),
                               outline=border, width=max(1, self.gpx(1.3 if picked else 1)), tags=tag)
            if a["main"] and title:  # the session's name as a title bar on top of its main node
                text, on, click = title
                th = int(lh * 1.35)
                if b >= 0:
                    self.title_rects.append((x, y - th, x + w, y))
                c.create_rectangle(x, y - th, x + w, y, fill=P["sel"] if on else P["bar"],
                                   outline=P["blue"] if on else border, tags=click)
                entry = (f"mttl{b}_{abs(hash(key)) % 10000}", ("● " if on else "○ ") + text,
                         window.g_bold if on else window.g_small, P["hi"] if on else P["tx"], x + self.gpx(5),
                         w - self.gpx(10), y - th / 2, click)
                self.titles.append(entry)
                self.draw_title(entry, time.time())
            if h < window.g_main.metrics("linespace") * 4:
                # At overview scales the coloured nodes stay visible; zooming restores their details.
                if b >= 0:
                    c.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=key, i=i: self.click_node(e, k, i))
                continue
            if a.get("tool"):
                c.create_text(x + self.gpx(6), y + self.gpx(5), text="⚙", font=window.g_bold,
                              anchor="nw", fill=border, tags=tag)
                icon_w = window.g_bold.measure("⚙")
            else:
                img = self.robot(a["state"], max(1, int(lh * 1.15)))
                if fade < 0.35:  # an image can't fade: it goes early
                    c.create_image(x + self.gpx(6), y + self.gpx(5), image=img, anchor="nw", tags=tag)
                icon_w = img.width()
            tx = x + self.gpx(6) + icon_w + self.gpx(5)
            inner = x + w - tx - self.gpx(4)
            c.create_text(tx, y + self.gpx(5), text=window._fit(a["name"], inner, window.g_bold), anchor="nw",
                          fill=common.mix(window.color(i), P0["bg"], fade) if fade else window.color(i), font=window.g_bold,
                          tags=tag)
            # a subagent's type (general-purpose, Explore...) when its name is its description, then model and effort
            info = " · ".join(v for v in ((a.get("kind") if not a["main"] else ""), a["model_label"], a["effort"]) if v)
            ctx = fmt_tokens(a["ctx"])
            ctx_line = (f"ctx {ctx}" + (f"/{fmt_tokens(a['ctx_max'])}" if a["ctx_max"] else "")) if ctx else ""
            rows = [info, ctx_line]  # the context always on a line of its own (what it says: in the logs)
            ly = y + self.gpx(5) + lh
            for row in rows:
                if row:
                    c.create_text(x + self.gpx(8), ly, text=window._fit(row, w - self.gpx(14), window.g_small),
                                  anchor="nw", fill=P["mu"], font=window.g_small, tags=tag)
                ly += lh
            label = status
            if current and i == window.prompt.in_window:
                label = "! answer in the window"
            if a["main"] and a["state"] == "waiting" and a["thought"]:
                label = "… " + a["thought"]
            c.create_text(x + self.gpx(8), y + h - self.gpx(4), text=window._fit(label, w - self.gpx(14), window.g_small),
                          anchor="sw", fill=P[status_col], font=window.g_small, tags=tag)
            if b >= 0:
                c.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=key, i=i: self.click_node(e, k, i))
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))

    def title_click(self, key):
        """A click (not a drag) on a session's title bar: that session, with its [all] log."""
        window = self.window
        if self.panned:
            return
        if key != window.key:
            self.pick_session(key)
        window.select(-1, clear_filters=True)

    def click_node(self, e, key, i):
        window = self.window
        if self.panned:
            return  # that was a drag
        if key != window.key:
            self.pick_session(key)
        window.select(i, clear_filters=True)
        if common.prompt_agent(window.net, window.item) == i:  # the agent that is asking: open its prompt
            window.prompt.draw()
            window.prompt.open(True)

    def pick_session(self, key):
        window = self.window
        if self.panned or key == window.key:
            return
        window.key, window.sel = key, -1
        window.prompt.open(False)
        window.logs.sig = self.sig = window.prompt.sig = window.logs.tabs_sig = None
        window._refresh()

    def zoom(self, factor=None, at=None, reset=False, precise=False):
        """Zoom the graph around a point of the view (default: its middle)."""
        window = self.window
        c = self.canvas
        old = self.gz
        requested = 1.0 if reset else old * factor
        new = min(common.ZOOM_MAX, max(common.ZOOM_MIN, requested if precise else round(requested, 3)))
        if abs(new - old) < (1e-9 if precise else 1e-3) and not reset:
            return "break"
        self.gesture_remainder = (0.0, 0.0)
        self.user_moved = not reset
        W, H = c.winfo_width(), c.winfo_height()
        mx, my = at if at else (W / 2, H / 2)
        content = getattr(self, "content", None)
        frac = None
        if content:
            frac = (c.canvasx(mx) / max(1, content[0]), c.canvasy(my) / max(1, content[1]))
        self.gz = new
        self.robots.clear()
        self.center_next = reset
        self.draw(force=True)
        if frac and not reset and self.content:
            self.scroll_to(frac[0] * self.content[0] - mx, frac[1] * self.content[1] - my)
            self.shift_into_view()
        self.update_zoombar()
        return "break"

    def center_view(self, by_user=False):
        """Place the average of the node centres in the viewport centre, keeping the current zoom."""
        window = self.window
        if by_user:
            self.user_moved = False
        c = self.canvas
        W, H = c.winfo_width(), c.winfo_height()
        rects = getattr(self, "node_rects", [])
        if rects:
            cx = sum((x0 + x1) / 2 for x0, _, x1, _ in rects) / len(rects)
            cy = sum((y0 + y1) / 2 for _, y0, _, y1 in rects) / len(rects)
            self.scroll_to(cx - W / 2, cy - H / 2)
        return "break"

    def bounds(self):
        """Actual nodes and titles, excluding empty layout space and fixed controls."""
        window = self.window
        rects = getattr(self, "node_rects", []) + getattr(self, "title_rects", [])
        if not rects:
            return None
        return (min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects))

    def fit_all(self):
        """Choose a zoom that fits every visible node and title, then centre their bounds."""
        window = self.window
        if not self.bounds():
            return "break"
        c = self.canvas
        W, H = c.winfo_width(), c.winfo_height()
        pad = max(8, window.px(10))
        top = pad + self.zoombar.winfo_reqheight()
        bottom = pad + self.stats_size()[1]
        width, height = max(1, W - 2 * pad), max(1, H - top - bottom)
        self.user_moved, self.center_next, self.fit_view = True, False, True
        try:
            # Measure the rendered geometry: fonts round to discrete sizes and overview nodes simplify.
            lo, hi, best = common.ZOOM_MIN, common.ZOOM_MAX, common.ZOOM_MIN
            for step in range(13):
                self.gz = hi if step == 0 else (lo + hi) / 2
                self.robots.clear()
                self.draw(force=True)
                x0, y0, x1, y1 = self.bounds()
                if x1 - x0 <= width and y1 - y0 <= height:
                    best = lo = self.gz
                    if step == 0:
                        break
                else:
                    hi = self.gz
            if self.gz != best:
                self.gz = best
                self.robots.clear()
                self.draw(force=True)
            x0, y0, x1, y1 = self.bounds()
            self.scroll_to((x0 + x1 - W) / 2, (y0 + y1) / 2 - (top + height / 2))
            self.update_zoombar()
        finally:
            self.fit_view = False
        return "break"

    def maybe_auto_fit(self, force=False):
        """Fit on membership changes, including tool nodes and every displayed session."""
        window = self.window
        signature = frozenset((key, a["id"]) for key, _, net in window.blocks for a in net["agents"])
        changed = signature != getattr(self, "auto_fit_signature", None)
        self.auto_fit_signature = signature
        if not window.opts.get("auto_fit"):
            self.auto_fit_pending = False
            return
        if not (force or changed or getattr(self, "auto_fit_pending", False)):
            return
        c = self.canvas
        if not c.winfo_ismapped() or c.winfo_width() <= 80 or c.winfo_height() <= 80:
            self.auto_fit_pending = True
            return  # wait for opening/resizing to settle before choosing a zoom
        self.auto_fit_pending = False
        self.fit_all()

    def update_auto_fit(self):
        window = self.window
        self.auto_fit_var.set(bool(window.opts.get("auto_fit")))
        P = window.P
        self.auto_fit_btn.configure(bg=P["bar"], fg=P["tx"], selectcolor=P["bar"],
                                    activebackground=P["bar"], activeforeground=P["hi"])

    def scroll_to(self, left, top):
        c = self.canvas
        try:
            x0, y0, x1, y1 = (float(v) for v in str(c.cget("scrollregion")).split())
        except ValueError:
            return
        c.xview_moveto((left - x0) / max(1, x1 - x0))
        c.yview_moveto((top - y0) / max(1, y1 - y0))

    def pan_start(self, e):
        self.gesture_remainder = (0.0, 0.0)
        self.panned = False
        self.pan_from = (e.x, e.y)
        self.canvas.scan_mark(e.x, e.y)

    def pan(self, dx, dy):
        """Pan by viewport distance, independent of graph zoom and UI scale."""
        window = self.window
        if not all(math.isfinite(v) for v in (dx, dy)) or not (dx or dy):
            return "break"
        c = self.canvas
        rx, ry = getattr(self, "gesture_remainder", (0.0, 0.0))
        left, top = c.canvasx(0) + dx + rx, c.canvasy(0) + dy + ry
        self.user_moved, self.center_next = True, False
        self.scroll_to(left, top)
        self.shift_into_view()
        # Tk rounds viewport origins to pixels. Preserve subpixel motion, but
        # discard blocked movement at bounds so reversing direction responds now.
        rx, ry = left - c.canvasx(0), top - c.canvasy(0)
        self.gesture_remainder = (rx if abs(rx) < 1 else 0.0, ry if abs(ry) < 1 else 0.0)
        return "break"

    def pan_move(self, e):
        window = self.window
        fx, fy = getattr(self, "pan_from", (e.x, e.y))
        if self.panned or abs(e.x - fx) + abs(e.y - fy) > 4:
            self.panned = self.user_moved = True
            self.canvas.configure(cursor="fleur")
            self.canvas.scan_dragto(e.x, e.y, gain=1)
            if self.shift_into_view():  # held at the edge: re-anchor, so the drag goes on from here
                self.canvas.scan_mark(e.x, e.y)

    def shift_into_view(self, margin=0.1, check_only=False):
        """Keep at least one node at least partly inside the view (less a 10% margin all round): panning, scrolling or
        zooming stops there. Returns True when the view had to be moved (check_only: whether a node is in sight)."""
        window = self.window
        c = self.canvas
        rects = getattr(self, "node_rects", [])
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
        self.scroll_to(c.canvasx(0) + best[0], c.canvasy(0) + best[1])
        return True

    def pan_end(self, e):
        window = self.window
        if self.panned:
            self.canvas.configure(cursor="")
            window.win.after_idle(lambda: setattr(self, "panned", False))

    def wheel(self, e):
        """Ctrl + wheel zooms at the pointer; the wheel alone scrolls (shift: sideways)."""
        up = (getattr(e, "delta", 0) > 0) or getattr(e, "num", 0) == 4
        if e.state & 0x4 or (common.IS_MAC and e.state & 0x8):
            return self.zoom(1.15 if up else 1 / 1.15, at=(e.x, e.y))
        self.user_moved = True
        if e.state & 0x1:
            self.canvas.xview_scroll(-2 if up else 2, "units")
        else:
            self.canvas.yview_scroll(-2 if up else 2, "units")
        self.shift_into_view()
        return "break"

    def update_zoombar(self):
        try:
            self.zoom_label.configure(text=f"{int(round(self.gz * 100))}%")
        except (tk.TclError, AttributeError):
            pass

    def stats_size(self):
        window = self.window
        sl = window.f_small.metrics("linespace")
        return window.px(150), sl * 4 + window.px(10)

    def draw_stats(self):
        window = self.window
        sc, P, st = self.statc, window.P, window.stats
        w, h = self.stats_size()
        sig = (w, h, window.theme, repr(st), self.stat_open, window.opts["node_alpha"])
        if sig == getattr(self, "statc_sig", None):
            return
        self.statc_sig = sig
        sl = window.f_small.metrics("linespace")
        try:
            sc.configure(width=w, height=h, bg=P["node"], highlightbackground=P["line"], highlightthickness=1)
            sc.delete("all")
        except tk.TclError:
            return
        sc.create_text(window.px(6), window.px(4), text="≡ this task", anchor="nw", fill=P["hi"], font=window.f_bold)
        if st.get("missing"):  # the task began before the part of a transcript that was read
            sc.create_text(w - window.px(6), window.px(4), text="ⓘ", anchor="ne", fill=P["mu"], font=window.f_bold,
                           tags="warn")
            sc.tag_bind("warn", "<Enter>", lambda e: self.show_tip(
                "Background tracking is catching up with this transcript. Older subagents and tool calls may "
                "be missing temporarily. Logs keep a bounded history after tracking catches up.", sc, w))
            sc.tag_bind("warn", "<Leave>", lambda e: self.hide_tip())
        n_calls = sum(n for _, n in st["subagents"])
        tool_calls = sum(n for _, n in st["tools"])
        rows = (("subs", f"subagents {n_calls} ▾"), ("ctx", f"context {fmt_tokens(st['ctx_total']) or 0} ▾"),
                ("tools", f"tools {len(st['tools'])} · {tool_calls} calls ▾"))
        for k, (kind, text) in enumerate(rows):
            tag = f"stat_{kind}"
            on = self.stat_open == kind
            sc.create_text(window.px(8), window.px(6) + sl * (k + 1), text=window._fit(text, w - window.px(12), window.f_small),
                           anchor="nw", fill=P["blue"] if on else P["tx"], font=window.f_small, tags=tag)
            sc.tag_bind(tag, "<Button-1>", lambda e, k=kind: self.toggle_stat(k))
            sc.tag_bind(tag, "<Enter>", lambda e: sc.configure(cursor="hand2"))
            sc.tag_bind(tag, "<Leave>", lambda e: sc.configure(cursor=""))

    def show_tip(self, text, near, width):
        window = self.window
        P = window.P
        try:
            self.tip.configure(text=text, bg=P["bar"], fg=P["tx"], wraplength=window.px(260),
                               highlightbackground=P["line"], highlightthickness=1)
            self.tip.place(in_=near, x=width, y=0, anchor="se")
            self.tip.lift()
        except tk.TclError:
            pass

    def hide_tip(self):
        try:
            self.tip.place_forget()
        except tk.TclError:
            pass

    def toggle_stat(self, kind):
        """Open (or fold) a read-only drop-down of the stats node: subagents, every context, tools and skills."""
        window = self.window
        if self.stat_open == kind:
            kind = None
        self.stat_open = kind
        if kind is None:
            self.statdrop.place_forget()
        else:
            if window.menu_open:
                window.toggle_menu(False)
            self.fill_stat()
        self.draw_stats()
        return "break"

    def stat_lines(self, kind):
        window = self.window
        st = window.stats
        if kind == "subs":
            if not st["subagents"]:
                return [("# no subagents in this task", "mu")]
            return [(f"{name:<22} ×{n}", "") for name, n in st["subagents"]]
        if kind == "ctx":
            lines = [(f"{'#':<3}{'agent':<18}{'model':<14}{'context':>8}", "mu")]
            for k, (name, model, ctx) in enumerate(st["calls"], 1):
                lines.append((f"{k:<3}{name[:17]:<18}{model[:13]:<14}{fmt_tokens(ctx) or '-':>8}", ""))
            lines.append((f"{'':<3}{'total':<32}{fmt_tokens(st['ctx_total']) or '0':>8}", "mu"))
            return lines
        lines = [(f"{name:<26} ×{n}", "") for name, n in st["tools"]] or [("# no tool calls in this task", "mu")]
        lines.append(("── skills ──", "mu"))
        lines += [(f"{name:<26} ×{n}", "") for name, n in st["skills"]] or [("# no skills used", "mu")]
        if st.get("missing"):
            lines.append(("ⓘ older entries may be missing (long task)", "mu"))
        return lines

    def fill_stat(self):
        window = self.window
        P, t = window.P, self.stattext
        lines = self.stat_lines(self.stat_open)
        sig = (self.stat_open, tuple(lines), window.theme, window.s)
        if sig == getattr(self, "stat_sig", None) and self.statdrop.winfo_ismapped():
            return
        self.stat_sig = sig
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
        self.statdrop.place(in_=self.statc, relx=1.0, x=0, y=-window.px(4), anchor="se")
        self.statdrop.lift()

    def animate(self):
        window = self.window
        if not window.alive():
            return
        if time.time() < window._moving_until:  # being dragged / resized: drawing now leaves trails on Windows
            window._anim = window.win.after(common.FRAME_MS, self.animate)
            return
        try:
            t = time.time() - window.t0
            r = max(2, self.gpx(2.2))
            for k, (pts, state, col) in enumerate(self.edges):
                for j in range(2):
                    if 2 * k + j >= len(self.dots):
                        break
                    dot = self.dots[2 * k + j]
                    if state == "working":
                        u = (t / 1.6 + j / 2) % 1.0  # two dots flowing out
                    elif state == "done" and j == 0:
                        u = 1 - (t / 2.4) % 1.0  # one flowing back with the result
                    else:
                        self.canvas.itemconfigure(dot, state="hidden")
                        continue
                    x, y = common.bezier(*pts, u)
                    self.canvas.coords(dot, x - r, y - r, x + r, y + r)
                    self.canvas.itemconfigure(dot, state="normal")
            if self.animating or self.leaving:  # sessions fading out / sliding into place
                self.draw(force=True)
            else:
                now = time.time()
                for entry in self.titles:  # long group titles scroll like the pet's name tags
                    if entry[2].measure(entry[1]) > entry[5]:
                        self.draw_title(entry, now)
            # the cogwheel turns while hovered, while its drop-down is open, and for a moment after a change
            if window.menu_open or getattr(window, "_cog_hover", False) or time.time() < window._spin_until:
                window._spin = (window._spin + 0.12) % (2 * math.pi)
                window._draw_cog()
        except tk.TclError:
            pass
        window._anim = window.win.after(common.FRAME_MS, self.animate)
