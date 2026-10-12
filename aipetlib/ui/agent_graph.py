"""Agent graph layout, rendering, navigation and activity animation.

The owner supplies widgets and UI state; services supply shared behavior.
No application entry point is imported by this component.
"""
import math
import time
import tkinter as tk


def robot(window, services, state, height):
    key = (state, height, window.theme)
    if key not in window._robots:
        from PIL import Image
        im = services.core.state_image(services.ROBOT_STATE.get(state, "idle")).convert("RGBA")
        if state == "needs_input":  # red when that agent needs you
            a = im.split()[3]
            tinted = Image.blend(im, Image.new("RGBA", im.size, (214, 48, 40, 255)), 0.55)
            tinted.putalpha(a)
            im = tinted
        width = max(1, round(im.width * height / im.height))
        im = im.resize((width, max(1, height)), Image.LANCZOS)
        window._robots[key] = services.core._to_photo(im)
    return window._robots[key]


def gpx(window, services, n):
    return int(round(n * window.s * window.gz))


def _graph_fonts(window, services):
    size = max(1, int(round(8 * window._font_scale() * window.gz)))
    small = max(1, int(round(7 * window._font_scale() * window.gz)))
    for f, sz in ((window.g_main, size), (window.g_bold, size), (window.g_small, small)):
        if f.cget("size") != sz:
            f.configure(size=sz)


def _graph_line_height(window, services):
    # Tk's minimum font size must not prevent a large diagram from fitting in the viewport.
    return window.f_main.metrics("linespace") * window.gz if window.gz < 0.4 else window.g_main.metrics("linespace")


def _block_height(window, services, net, H):
    """Height one session's graph needs (tabbed off: the sessions are stacked)."""
    cols, beside = window._columns(net)
    rows = max((len(c) for c in cols), default=0)
    lh = window._graph_line_height()
    main_h = lh * 4 + window.gpx(10)
    sub_h = lh * 4 + window.gpx(8)
    return int(max(main_h, rows * (sub_h + window.gpx(12 if services.IS_MAC else 6))) + 2 * window.gpx(10) + lh * 1.6)


def _layout(window, services, net, W, H, reserve=None):
    """Node boxes (one per agent, in net["agents"] order): main on the left, its subagents in a column to its
    right, agents started by those further right. reserve: (x, y) of a box at the bottom right that the columns
    should stay above. -> (boxes, beside)"""
    lh = window._graph_line_height()
    pad = window.gpx(10)
    main_w, main_h = window.gpx(150), lh * 4 + pad
    boxes = [None] * len(net["agents"])
    boxes[0] = (pad, max(pad, (H - main_h) // 2), main_w, main_h)
    cols, beside = window._columns(net)
    if not cols:
        return boxes, False
    sub_w = window.gpx(140)
    sub_h = lh * 4 + window.gpx(8)
    x0 = pad + main_w + window.gpx(40 if services.IS_MAC else 70)
    gap_x = sub_w + window.gpx(40 if services.IS_MAC else 60)
    if reserve and x0 + (len(cols) - 1) * gap_x + sub_w > reserve[0] - pad:
        H = max(sub_h + 2 * pad, reserve[1])
    if services.IS_MAC:
        # Keep siblings together on Mac instead of spreading them across the
        # viewport. Preserve the existing Windows layout and display scaling.
        step = sub_h + window.gpx(12)
        H = max(H, max(len(col) for col in cols) * step + 2 * pad)
        boxes[0] = (pad, (H - main_h) // 2, main_w, main_h)
    for c, col in enumerate(cols):
        rows = len(col)
        if not services.IS_MAC:
            step = max(sub_h + window.gpx(6), (H - 2 * pad) / max(1, rows))
        for row, i in enumerate(col):
            top = pad + (H - 2 * pad - step * rows) / 2 + row * step + (step - sub_h) / 2
            boxes[i] = (x0 + c * gap_x, int(max(pad // 2, top)), sub_w, sub_h)
    return boxes, beside


def _draw_graph(window, services, force=False):
    c, P = window.canvas, window.P
    W, H = max(50, c.winfo_width()), max(50, c.winfo_height())
    blocks = window.blocks
    sig = (W, H, window.gz, window.sel, window.key, window.theme, window.s, window.opts["node_alpha"], window.in_window,
           tuple((k, tuple((a["id"], a.get("parent", ""), a.get("depth", 0),
                            a["name"], a["state"], a["model_label"], a["effort"], a["ctx"], a["thought"] if a["main"] else "")
                           for a in net["agents"])) for k, _, net in blocks))
    if sig == window._graph_sig and not force:
        window._draw_stats()
        return
    window._graph_sig = sig
    window._graph_fonts()
    # keep the view where it was (as a fraction of the drawing) unless a centre was asked for
    old = getattr(window, "_content", None)
    view = (c.canvasx(0), c.canvasy(0)) if old else None
    c.delete("all")
    window.edges, window._node_rects, window._title_rects = [], [], []
    fading = window._stacked_mode() and (window._leaving or any(
        b.get("net", {}).get("agents") for b in window._bands.values()))  # the last session still fading out
    if (not blocks or not any(net["agents"] for _, _, net in blocks)) and not fading:
        c.configure(scrollregion=(0, 0, W, H))
        c.xview_moveto(0)
        c.yview_moveto(0)
        c.create_text(W // 2, H // 2, text="$ waiting for a session in claude code or codex_",
                      fill=P["mu"], font=window.f_main)
        window._dots, window._content = [], None
        window._draw_stats()
        return
    VW = max(W * window.gz, window.gpx(420))
    stacked = window._stacked_mode() or bool(window._leaving)
    sbox = window._stats_size()
    now = time.time()
    window._titles = []
    animating = False
    if stacked:
        y1 = window._plan_bands(blocks, H, now)
        tidy = window.opts.get("tidy")
        lh = window._graph_line_height()
        head = int(lh * 1.6)
        order = [(k, None) for k in window._leaving] + [(k, b) for b, (k, _, _) in enumerate(blocks)]
        for key, b in order:
            leaving = key in window._leaving
            band = window._leaving[key] if leaving else window._bands[key]
            fade = min(1.0, (now - band["t0"]) / window.FADE_S) if leaving else 0.0
            y = band["y"] if leaving else window._band_y(band, now)
            animating = animating or leaving or now < band["t"] + window.SLIDE_S
            window._draw_band(b if b is not None else -1, key, band["item"], band["net"], y, band["h"], VW, head,
                            tidy, fade)
    else:
        window._bands, y1 = {}, 0
        key, item, net = blocks[0]
        bh = max(H * window.gz, window._block_height(net, H))
        window._bands[key] = {"from": 0, "to": 0, "t": 0, "y": 0, "item": item, "net": net, "h": bh}
        reserve = (VW - sbox[0] - window.gpx(8), bh - sbox[1] - window.gpx(8))
        window._draw_block(0, key, net, 0, 0, VW, bh, reserve)
        y1 = bh
    window._animating = animating
    x1 = VW
    bounds = window._graph_bounds()
    if bounds:
        x1, y1 = max(x1, bounds[2]), max(y1, bounds[3])
    content_old = window._content
    window._content = (x1, y1)
    # scroll region: the drawing plus a view's worth around it, so it can be panned anywhere
    c.configure(scrollregion=(min(-W, bounds[0] - W) if bounds else -W,
                              min(-H, bounds[1] - H) if bounds else -H, x1 + W, y1 + H))
    layout = ("stacked",) if stacked else ("single", blocks[0][0])
    if layout != getattr(window, "_last_layout", None):
        window._last_layout, window._center_next, window._user_moved = layout, True, False
    if window._center_next or not old:
        if W > 80 and H > 80 and c.winfo_ismapped():
            window._center_next = False  # centred for real (not on the 1-pixel canvas of a window still opening)
        window.center_view()
    elif view and (animating or getattr(window, "_was_animating", False)):
        window._scroll_to(*view)  # sessions sliding: the view stays exactly where it is
    elif view:
        fx, fy = (view[0] + W / 2) / max(1, old[0]), (view[1] + H / 2) / max(1, old[1])
        window._scroll_to(fx * x1 - W / 2, fy * y1 - H / 2)
    window._was_animating = animating
    if not window._center_next and not window._shift_into_view(margin=0.0, check_only=True):
        window.center_view()  # nothing of the graph in sight (say, its only session moved): show it again
    window._dots = [c.create_oval(0, 0, 0, 0, fill=col, outline="", state="hidden")
                  for pts, st, col in window.edges for _ in range(2)]
    window._draw_stats()


def _stacked_mode(window, services):
    return not window.pinned and not window.opts["separate"] and not window.opts["tabbed"]


def _plan_bands(window, services, blocks, H, now):
    """Where each session's band goes. A session that left fades where it was; the others then slide to their
    new place. Returns the drawing's height."""
    gap = window.gpx(14) if window.opts.get("tidy") else 0
    head = int(window._graph_line_height() * 1.6)
    targets, heights, y = {}, {}, 0
    for key, item, net in blocks:
        heights[key] = window._block_height(net, H) + (window.gpx(6) if window.opts.get("tidy") else 0)
        targets[key] = y
        y += heights[key] + gap
    prev = getattr(window, "_bands", {}) or {}
    for key, band in prev.items():
        if key not in targets and key not in window._leaving and band.get("net", {}).get("agents"):
            window._leaving[key] = dict(band, y=window._band_y(band, now), t0=now)
    window._leaving = {k: v for k, v in window._leaving.items() if now - v["t0"] < window.FADE_S and k not in targets}
    delay = max([window.FADE_S - (now - v["t0"]) for v in window._leaving.values()] or [0])
    bands = {}
    for key, item, net in blocks:
        band = prev.get(key)
        if band is None or getattr(window, "_fit_view", False):
            band = {"from": targets[key], "to": targets[key], "t": now - window.SLIDE_S}
        elif abs(band["to"] - targets[key]) > 0.5:  # its place changed: slide there (after any fade)
            band = {"from": window._band_y(band, now), "to": targets[key], "t": now + delay}
        bands[key] = dict(band, item=item, net=net, h=heights[key])
    window._bands = bands
    end = max([b["to"] + b["h"] for b in bands.values()] + [v["y"] + v["h"] for v in window._leaving.values()]
              + [head])
    return end


def _band_y(window, services, band, now):
    p = min(1.0, max(0.0, (now - band["t"]) / window.SLIDE_S))
    ease = 1 - (1 - p) ** 3
    return band["from"] + (band["to"] - band["from"]) * ease


def _draw_band(window, services, b, key, item, net, y, bh, VW, head, tidy, fade):
    c, P = window.canvas, window.P
    F = (lambda col: services.mix(col, P["bg"], fade)) if fade else (lambda col: col)
    on = key == window.key and not fade
    tag = f"band{b}" if b >= 0 else f"gone{abs(hash(key)) % 10000}"
    title = services.session_label(item).replace("agent-network ▸ ", "")
    tbar = f"tbar{b}" if b >= 0 else tag  # the session's title bar: a click opens its [all] log
    pad = window.gpx(8)
    if tidy:  # a group: a title bar on top of the session's nodes, its name sliding when it doesn't fit
        x0, x1 = pad, VW - pad
        if b >= 0:
            window._title_rects.append((x0, y, x1, y + head))
        c.create_rectangle(x0, y + head, x1, y + bh, fill="", outline=F(P["blue"] if on else P["line"]),
                           dash=() if on else (2, 3))
        c.create_rectangle(x0, y, x1, y + head, fill=F(P["sel"] if on else P["bar"]),
                           outline=F(P["blue"] if on else P["line"]), tags=(tag, tbar))
        font = window.g_bold if on else window.g_main
        entry = (f"ttl{b}_{abs(hash(key)) % 10000}", ("● " if on else "○ ") + title, font,
                 F(P["hi"] if on else P["tx"]), x0 + pad, x1 - x0 - 2 * pad, y + head / 2, (tag, tbar))
        window._titles.append(entry)
        window._draw_title(entry, time.time())
    else:  # bands: a line between sessions; the name is a title bar on the session's main node
        far = 50000  # bands run on forever sideways, however far you pan
        c.create_rectangle(-far, y, VW + far, y + bh, fill=P["sel"] if on else P["bg"], outline="",
                           stipple="gray25" if on else "", tags=tag)
        c.create_line(-far, y, VW + far, y, fill=F(P["line"]))
    if b >= 0:
        c.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=key: window._pick_session(k))
    window._draw_block(b, key, net, pad if tidy else 0, y + head, VW - (2 * pad if tidy else 0), bh - head, None,
                     fade=fade, title=None if tidy else (title, on, (tag, tbar)))
    if b >= 0:
        c.tag_bind(tbar, "<ButtonRelease-1>", lambda e, k=key: window._title_click(k))
        c.tag_bind(tbar, "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind(tbar, "<Leave>", lambda e: c.configure(cursor=""))


def _draw_title(window, services, entry, now):
    """A group's title: as is when it fits, else scrolling round like the pet's name tags (characters drawn one
    by one, only while wholly inside the bar: a Tk canvas can't clip text)."""
    tag, text, font, fill, left, room, ymid, click = entry
    c = window.canvas
    c.delete(tag)
    if font.measure(text) <= room:
        c.create_text(left, ymid, text=text, anchor="w", fill=fill, font=font, tags=(tag,) + tuple(click))
        return False
    loop_text = text + "    "
    loop = font.measure(loop_text)
    offset = (now * 30 * window.s * window.gz) % loop
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


def _draw_block(window, services, b, key, net, ox, oy, W, H, reserve, fade=0.0, title=None):
    """One session's graph, at (ox, oy) in a W x H area. fade: 0 as is .. 1 gone (a session being cleared)."""
    c, P0 = window.canvas, window.P
    P = {k: (services.mix(v, P0["bg"], fade) if fade and isinstance(v, str) and v.startswith("#") else v)
         for k, v in P0.items()}
    ags = net["agents"]
    if not ags:
        return
    current = key == window.key
    boxes, beside = window._layout(net, W, H, reserve)
    boxes = [(x + ox, y + oy, w, h) for x, y, w, h in boxes]
    lh = window._graph_line_height()
    index = {a["id"]: k for k, a in enumerate(ags)}
    for i, (a, (x, y, w, h)) in enumerate(zip(ags[1:], boxes[1:]), 1):  # edges first, under the nodes
        mx, my, mw, mh = boxes[index.get(a.get("parent", ""), 0)]  # from whoever started it
        p0, p3 = (mx + mw, my + mh / 2), (x, y + h / 2)
        mid = (p0[0] + p3[0]) / 2
        pts = (p0, (mid, p0[1]), (mid, p3[1]), p3)
        col = {"working": P["blue"], "needs_input": P["red"], "error": P["red"],
               "done": P["grn"]}.get(a["state"], P["line"])
        dash = () if a["state"] == "done" else (3, 4)
        line = [coord for t in range(0, 21) for coord in services.bezier(*pts, t / 20)]
        c.create_line(*line, fill=col, width=max(1, window.gpx(1)), dash=dash, smooth=True)
        if not fade:
            window.edges.append((pts, a["state"], col))
    for i, (a, (x, y, w, h)) in enumerate(zip(ags, boxes)):
        status_col, status = services.STATUS.get(a["state"], services.STATUS["idle"])
        border = P[status_col] if a["state"] != "idle" else P["line"]
        if a["main"] and a["state"] == "waiting":
            border = P["amb"]
        focused = a.get("tool") and a["name"] == window._log_filters.get("only_tool") and \
            0 <= window.sel < len(ags) and a["parent"] == ags[window.sel]["id"]
        picked = current and (i == window.sel or focused)
        fill = P["sel"] if picked else P["node"]
        tag = f"node{b}_{i}" if b >= 0 else "gone"
        if b >= 0:
            window._node_rects.append((x, y, x + w, y + h))
        c.create_rectangle(x, y, x + w, y + h, fill=fill, stipple=services.dither(window.opts["node_alpha"]),
                           outline=border, width=max(1, window.gpx(1.3 if picked else 1)), tags=tag)
        if a["main"] and title:  # the session's name as a title bar on top of its main node
            text, on, click = title
            th = int(lh * 1.35)
            if b >= 0:
                window._title_rects.append((x, y - th, x + w, y))
            c.create_rectangle(x, y - th, x + w, y, fill=P["sel"] if on else P["bar"],
                               outline=P["blue"] if on else border, tags=click)
            entry = (f"mttl{b}_{abs(hash(key)) % 10000}", ("● " if on else "○ ") + text,
                     window.g_bold if on else window.g_small, P["hi"] if on else P["tx"], x + window.gpx(5),
                     w - window.gpx(10), y - th / 2, click)
            window._titles.append(entry)
            window._draw_title(entry, time.time())
        if h < window.g_main.metrics("linespace") * 4:
            # At overview scales the coloured nodes stay visible; zooming restores their details.
            if b >= 0:
                c.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=key, i=i: window._click_node(e, k, i))
            continue
        if a.get("tool"):
            c.create_text(x + window.gpx(6), y + window.gpx(5), text="⚙", font=window.g_bold,
                          anchor="nw", fill=border, tags=tag)
            icon_w = window.g_bold.measure("⚙")
        else:
            img = window.robot(a["state"], max(1, int(lh * 1.15)))
            if fade < 0.35:  # an image can't fade: it goes early
                c.create_image(x + window.gpx(6), y + window.gpx(5), image=img, anchor="nw", tags=tag)
            icon_w = img.width()
        tx = x + window.gpx(6) + icon_w + window.gpx(5)
        inner = x + w - tx - window.gpx(4)
        c.create_text(tx, y + window.gpx(5), text=window._fit(a["name"], inner, window.g_bold), anchor="nw",
                      fill=services.mix(window.color(i), P0["bg"], fade) if fade else window.color(i), font=window.g_bold,
                      tags=tag)
        # a subagent's type (general-purpose, Explore...) when its name is its description, then model and effort
        info = " · ".join(v for v in ((a.get("kind") if not a["main"] else ""), a["model_label"], a["effort"]) if v)
        ctx = services.agents.fmt_tokens(a["ctx"])
        ctx_line = (f"ctx {ctx}" + (f"/{services.agents.fmt_tokens(a['ctx_max'])}" if a["ctx_max"] else "")) if ctx else ""
        rows = [info, ctx_line]  # the context always on a line of its own (what it says: in the logs)
        ly = y + window.gpx(5) + lh
        for row in rows:
            if row:
                c.create_text(x + window.gpx(8), ly, text=window._fit(row, w - window.gpx(14), window.g_small),
                              anchor="nw", fill=P["mu"], font=window.g_small, tags=tag)
            ly += lh
        label = status
        if current and i == window.in_window:
            label = "! answer in the window"
        if a["main"] and a["state"] == "waiting" and a["thought"]:
            label = "… " + a["thought"]
        c.create_text(x + window.gpx(8), y + h - window.gpx(4), text=window._fit(label, w - window.gpx(14), window.g_small),
                      anchor="sw", fill=P[status_col], font=window.g_small, tags=tag)
        if b >= 0:
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=key, i=i: window._click_node(e, k, i))
        c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))


def _title_click(window, services, key):
    """A click (not a drag) on a session's title bar: that session, with its [all] log."""
    if window._panned:
        return
    if key != window.key:
        window._pick_session(key)
    window.select(-1, clear_filters=True)


def _click_node(window, services, e, key, i):
    if window._panned:
        return  # that was a drag
    if key != window.key:
        window._pick_session(key)
    window.select(i, clear_filters=True)
    if services.prompt_agent(window.net, window.item) == i:  # the agent that is asking: open its prompt
        window._draw_prompt()
        window.open_overlay(True)


def _pick_session(window, services, key):
    if window._panned or key == window.key:
        return
    window.key, window.sel = key, -1
    window.open_overlay(False)
    window._log_sig = window._graph_sig = window._prompt_sig = window._tabs_sig = None
    window._refresh()


def zoom(window, services, factor=None, at=None, reset=False):
    """Zoom the graph around a point of the view (default: its middle)."""
    c = window.canvas
    old = window.gz
    new = 1.0 if reset else min(services.ZOOM_MAX, max(services.ZOOM_MIN, round(old * factor, 3)))
    if abs(new - old) < 1e-3 and not reset:
        return "break"
    window._user_moved = not reset
    W, H = c.winfo_width(), c.winfo_height()
    mx, my = at if at else (W / 2, H / 2)
    content = getattr(window, "_content", None)
    frac = None
    if content:
        frac = (c.canvasx(mx) / max(1, content[0]), c.canvasy(my) / max(1, content[1]))
    window.gz = new
    window._robots.clear()
    window._center_next = reset
    window._draw_graph(force=True)
    if frac and not reset and window._content:
        window._scroll_to(frac[0] * window._content[0] - mx, frac[1] * window._content[1] - my)
        window._shift_into_view()
    window._update_zoombar()
    return "break"


def center_view(window, services, by_user=False):
    """Place the average of the node centres in the viewport centre, keeping the current zoom."""
    if by_user:
        window._user_moved = False
    c = window.canvas
    W, H = c.winfo_width(), c.winfo_height()
    rects = getattr(window, "_node_rects", [])
    if rects:
        cx = sum((x0 + x1) / 2 for x0, _, x1, _ in rects) / len(rects)
        cy = sum((y0 + y1) / 2 for _, y0, _, y1 in rects) / len(rects)
        window._scroll_to(cx - W / 2, cy - H / 2)
    return "break"


def _graph_bounds(window, services):
    """Actual nodes and titles, excluding empty layout space and fixed controls."""
    rects = getattr(window, "_node_rects", []) + getattr(window, "_title_rects", [])
    if not rects:
        return None
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def fit_all(window, services):
    """Choose a zoom that fits every visible node and title, then centre their bounds."""
    if not window._graph_bounds():
        return "break"
    c = window.canvas
    W, H = c.winfo_width(), c.winfo_height()
    pad = max(8, window.px(10))
    top = pad + window.zoombar.winfo_reqheight()
    bottom = pad + window._stats_size()[1]
    width, height = max(1, W - 2 * pad), max(1, H - top - bottom)
    window._user_moved, window._center_next, window._fit_view = True, False, True
    try:
        # Measure the rendered geometry: fonts round to discrete sizes and overview nodes simplify.
        lo, hi, best = services.ZOOM_MIN, services.ZOOM_MAX, services.ZOOM_MIN
        for step in range(13):
            window.gz = hi if step == 0 else (lo + hi) / 2
            window._robots.clear()
            window._draw_graph(force=True)
            x0, y0, x1, y1 = window._graph_bounds()
            if x1 - x0 <= width and y1 - y0 <= height:
                best = lo = window.gz
                if step == 0:
                    break
            else:
                hi = window.gz
        if window.gz != best:
            window.gz = best
            window._robots.clear()
            window._draw_graph(force=True)
        x0, y0, x1, y1 = window._graph_bounds()
        window._scroll_to((x0 + x1 - W) / 2, (y0 + y1) / 2 - (top + height / 2))
        window._update_zoombar()
    finally:
        window._fit_view = False
    return "break"


def _maybe_auto_fit(window, services, force=False):
    """Fit on membership changes, including tool nodes and every displayed session."""
    signature = frozenset((key, a["id"]) for key, _, net in window.blocks for a in net["agents"])
    changed = signature != getattr(window, "_auto_fit_signature", None)
    window._auto_fit_signature = signature
    if not window.opts.get("auto_fit"):
        window._auto_fit_pending = False
        return
    if not (force or changed or getattr(window, "_auto_fit_pending", False)):
        return
    c = window.canvas
    if not c.winfo_ismapped() or c.winfo_width() <= 80 or c.winfo_height() <= 80:
        window._auto_fit_pending = True
        return  # wait for opening/resizing to settle before choosing a zoom
    window._auto_fit_pending = False
    window.fit_all()


def _update_auto_fit(window, services):
    window.auto_fit_var.set(bool(window.opts.get("auto_fit")))
    P = window.P
    window.auto_fit_btn.configure(bg=P["bar"], fg=P["tx"], selectcolor=P["bar"],
                                activebackground=P["bar"], activeforeground=P["hi"])


def _scroll_to(window, services, left, top):
    c = window.canvas
    try:
        x0, y0, x1, y1 = (float(v) for v in str(c.cget("scrollregion")).split())
    except ValueError:
        return
    c.xview_moveto((left - x0) / max(1, x1 - x0))
    c.yview_moveto((top - y0) / max(1, y1 - y0))


def _pan_start(window, services, e):
    window._panned = False
    window._pan_from = (e.x, e.y)
    window.canvas.scan_mark(e.x, e.y)


def _pan_move(window, services, e):
    fx, fy = getattr(window, "_pan_from", (e.x, e.y))
    if window._panned or abs(e.x - fx) + abs(e.y - fy) > 4:
        window._panned = window._user_moved = True
        window.canvas.configure(cursor="fleur")
        window.canvas.scan_dragto(e.x, e.y, gain=1)
        if window._shift_into_view():  # held at the edge: re-anchor, so the drag goes on from here
            window.canvas.scan_mark(e.x, e.y)


def _shift_into_view(window, services, margin=0.1, check_only=False):
    """Keep at least one node at least partly inside the view (less a 10% margin all round): panning, scrolling or
    zooming stops there. Returns True when the view had to be moved (check_only: whether a node is in sight)."""
    c = window.canvas
    rects = getattr(window, "_node_rects", [])
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
    window._scroll_to(c.canvasx(0) + best[0], c.canvasy(0) + best[1])
    return True


def _pan_end(window, services, e):
    if window._panned:
        window.canvas.configure(cursor="")
        window.win.after_idle(lambda: setattr(window, "_panned", False))


def _wheel(window, services, e):
    """Ctrl + wheel zooms at the pointer; the wheel alone scrolls (shift: sideways)."""
    up = (getattr(e, "delta", 0) > 0) or getattr(e, "num", 0) == 4
    if e.state & 0x4 or (services.IS_MAC and e.state & 0x8):
        return window.zoom(1.15 if up else 1 / 1.15, at=(e.x, e.y))
    window._user_moved = True
    if e.state & 0x1:
        window.canvas.xview_scroll(-2 if up else 2, "units")
    else:
        window.canvas.yview_scroll(-2 if up else 2, "units")
    window._shift_into_view()
    return "break"


def _update_zoombar(window, services):
    try:
        window.zoom_label.configure(text=f"{int(round(window.gz * 100))}%")
    except (tk.TclError, AttributeError):
        pass


def _stats_size(window, services):
    sl = window.f_small.metrics("linespace")
    return window.px(150), sl * 4 + window.px(10)


def _draw_stats(window, services):
    sc, P, st = window.statc, window.P, window.stats
    w, h = window._stats_size()
    sig = (w, h, window.theme, repr(st), window.stat_open, window.opts["node_alpha"])
    if sig == getattr(window, "_statc_sig", None):
        return
    window._statc_sig = sig
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
        sc.tag_bind("warn", "<Enter>", lambda e: window._show_tip(
            "Background tracking is catching up with this transcript. Older subagents and tool calls may "
            "be missing temporarily. Logs keep a bounded history after tracking catches up.", sc, w))
        sc.tag_bind("warn", "<Leave>", lambda e: window._hide_tip())
    n_calls = sum(n for _, n in st["subagents"])
    tool_calls = sum(n for _, n in st["tools"])
    rows = (("subs", f"subagents {n_calls} ▾"), ("ctx", f"context {services.agents.fmt_tokens(st['ctx_total']) or 0} ▾"),
            ("tools", f"tools {len(st['tools'])} · {tool_calls} calls ▾"))
    for k, (kind, text) in enumerate(rows):
        tag = f"stat_{kind}"
        on = window.stat_open == kind
        sc.create_text(window.px(8), window.px(6) + sl * (k + 1), text=window._fit(text, w - window.px(12), window.f_small),
                       anchor="nw", fill=P["blue"] if on else P["tx"], font=window.f_small, tags=tag)
        sc.tag_bind(tag, "<Button-1>", lambda e, k=kind: window.toggle_stat(k))
        sc.tag_bind(tag, "<Enter>", lambda e: sc.configure(cursor="hand2"))
        sc.tag_bind(tag, "<Leave>", lambda e: sc.configure(cursor=""))


def _show_tip(window, services, text, near, width):
    P = window.P
    try:
        window.tip.configure(text=text, bg=P["bar"], fg=P["tx"], wraplength=window.px(260),
                           highlightbackground=P["line"], highlightthickness=1)
        window.tip.place(in_=near, x=width, y=0, anchor="se")
        window.tip.lift()
    except tk.TclError:
        pass


def _hide_tip(window, services):
    try:
        window.tip.place_forget()
    except tk.TclError:
        pass


def toggle_stat(window, services, kind):
    """Open (or fold) a read-only drop-down of the stats node: subagents, every context, tools and skills."""
    if window.stat_open == kind:
        kind = None
    window.stat_open = kind
    if kind is None:
        window.statdrop.place_forget()
    else:
        if window.menu_open:
            window.toggle_menu(False)
        window._fill_stat()
    window._draw_stats()
    return "break"


def _stat_lines(window, services, kind):
    st = window.stats
    if kind == "subs":
        if not st["subagents"]:
            return [("# no subagents in this task", "mu")]
        return [(f"{name:<22} ×{n}", "") for name, n in st["subagents"]]
    if kind == "ctx":
        lines = [(f"{'#':<3}{'agent':<18}{'model':<14}{'context':>8}", "mu")]
        for k, (name, model, ctx) in enumerate(st["calls"], 1):
            lines.append((f"{k:<3}{name[:17]:<18}{model[:13]:<14}{services.agents.fmt_tokens(ctx) or '-':>8}", ""))
        lines.append((f"{'':<3}{'total':<32}{services.agents.fmt_tokens(st['ctx_total']) or '0':>8}", "mu"))
        return lines
    lines = [(f"{name:<26} ×{n}", "") for name, n in st["tools"]] or [("# no tool calls in this task", "mu")]
    lines.append(("── skills ──", "mu"))
    lines += [(f"{name:<26} ×{n}", "") for name, n in st["skills"]] or [("# no skills used", "mu")]
    if st.get("missing"):
        lines.append(("ⓘ older entries may be missing (long task)", "mu"))
    return lines


def _fill_stat(window, services):
    P, t = window.P, window.stattext
    lines = window._stat_lines(window.stat_open)
    sig = (window.stat_open, tuple(lines), window.theme, window.s)
    if sig == getattr(window, "_stat_sig", None) and window.statdrop.winfo_ismapped():
        return
    window._stat_sig = sig
    title = {"subs": "subagents used", "ctx": "context per agent call", "tools": "tools and skills"}[window.stat_open]
    window.stathead.configure(text=f"── {title} ── [esc]", bg=P["bar"], fg=P["mu"])
    window.statdrop.configure(bg=P["bar"], highlightbackground=P["line"], highlightcolor=P["line"])
    t.configure(state="normal", bg=P["bar"], fg=P["tx"], width=max(len(x) for x, _ in lines) + 1,
                height=min(14, len(lines)))
    t.delete("1.0", "end")
    t.tag_configure("mu", foreground=P["mu"])
    t.tag_configure("warn", foreground=P["amb"])
    for text, tag in lines:
        t.insert("end", text + "\n", tag)
    t.configure(state="disabled")
    window.statdrop.place(in_=window.statc, relx=1.0, x=0, y=-window.px(4), anchor="se")
    window.statdrop.lift()


def _animate(window, services):
    if not window.alive():
        return
    if time.time() < window._moving_until:  # being dragged / resized: drawing now leaves trails on Windows
        window._anim = window.win.after(services.FRAME_MS, window._animate)
        return
    try:
        t = time.time() - window.t0
        r = max(2, window.gpx(2.2))
        for k, (pts, state, col) in enumerate(window.edges):
            for j in range(2):
                if 2 * k + j >= len(window._dots):
                    break
                dot = window._dots[2 * k + j]
                if state == "working":
                    u = (t / 1.6 + j / 2) % 1.0  # two dots flowing out
                elif state == "done" and j == 0:
                    u = 1 - (t / 2.4) % 1.0  # one flowing back with the result
                else:
                    window.canvas.itemconfigure(dot, state="hidden")
                    continue
                x, y = services.bezier(*pts, u)
                window.canvas.coords(dot, x - r, y - r, x + r, y + r)
                window.canvas.itemconfigure(dot, state="normal")
        if window._animating or window._leaving:  # sessions fading out / sliding into place
            window._draw_graph(force=True)
        else:
            now = time.time()
            for entry in window._titles:  # long group titles scroll like the pet's name tags
                if entry[2].measure(entry[1]) > entry[5]:
                    window._draw_title(entry, now)
        # the cogwheel turns while hovered, while its drop-down is open, and for a moment after a change
        if window.menu_open or getattr(window, "_cog_hover", False) or time.time() < window._spin_until:
            window._spin = (window._spin + 0.12) % (2 * math.pi)
            window._draw_cog()
    except tk.TclError:
        pass
    window._anim = window.win.after(services.FRAME_MS, window._animate)
