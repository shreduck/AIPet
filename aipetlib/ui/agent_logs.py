"""Agent log filters, tabs, expanded entries and split layout.

The owning window supplies its widgets and state; shared values come from their owner modules.
No application entry point is imported by this component.
"""
import time
import tkinter as tk

from ..agents import log_filters
from ..agents.records import fmt_tokens, short
from . import agent_common as common

def _arrange(window):
    """The logs below, above, left or right of the graph (settings: logs position), split by a draggable line."""
    side = window.opts.get("log_side", "bottom")
    across = side in ("left", "right")
    for wdg in (window.canvas, window.sep, window.logbox):
        wdg.pack_forget()
    window.logbox.pack(side=side, fill="y" if across else "x")
    window.sep.pack(side=side, fill="y" if across else "x")
    window.sep.configure(cursor="sb_h_double_arrow" if across else "sb_v_double_arrow")
    window.canvas.pack(side=side, fill="both", expand=True)
    window._size_log()


def _size_log(window):
    """The logs' size from the settings: lines (above / below) or a width in characters (left / right)."""
    if window.opts.get("log_side", "bottom") in ("left", "right"):
        window.log.configure(width=window.opts.get("log_cols", 60), height=1)
    else:
        window.log.configure(height=window.opts.get("log_lines", 6), width=1)


def _split_start(window, e):
    window._split = (e.x_root, e.y_root, window.log.winfo_width(), window.log.winfo_height())


def _split_move(window, e):
    """Drag the split: the logs from a few lines (or 20 characters) up to 80% of the window."""
    side = window.opts.get("log_side", "bottom")
    x0, y0, w0, h0 = getattr(window, "_split", (e.x_root, e.y_root, window.log.winfo_width(), window.log.winfo_height()))
    if side in ("left", "right"):
        col = max(1, window.f_main.measure("0"))
        want = w0 + ((e.x_root - x0) if side == "left" else (x0 - e.x_root))
        most = int(window.win.winfo_width() * 0.8)
        cols = int(max(20, min(most // col, want // col)))
        if cols != window.opts.get("log_cols"):
            window.opts["log_cols"] = cols
            window._size_log()
        return
    line = max(1, window.f_main.metrics("linespace"))
    want = h0 + ((e.y_root - y0) if side == "top" else (y0 - e.y_root))
    most = int(window.win.winfo_height() * 0.8) - window.tabs.winfo_height() - window.px(8)
    lines = int(max(3, min(most // line, want // line)))
    if lines != window.opts.get("log_lines"):
        window.opts["log_lines"] = lines
        window._size_log()


def set_log_filter(window, key, value):
    window._log_filters[key] = value
    window._log_sig = None
    window._update_log_filters()
    window._draw_log()


def clear_log_filters(window):
    window._log_filters = log_filters.default_log_filters()
    window._log_sig = window._graph_sig = None
    window._update_log_filters()
    window._draw_graph()
    window._draw_log()
    return "break"


def _update_log_filters(window):
    P, filters = window.P, window._log_filters
    for button, key, label in ((window.show_tools_btn, "show_tools", "show tools"),
                               (window.primary_btn, "primary", "primary")):
        button.configure(text=f"[{'x' if filters[key] else ' '}] {label}", bg=P["bar"],
                         fg=P["blue"] if filters[key] else P["tx"])
    window.filterbar.configure(bg=P["bar"])
    for button, group, label in ((window.tool_filter_btn, "tools", "tools"), (window.mcp_filter_btn, "mcp", "MCP")):
        chosen = filters[group]
        count = "" if chosen is None else f" {len(chosen)}"
        button.configure(text=f"[{label}{count} ▾]", bg=P["bar"], fg=P["blue"] if chosen is not None else P["tx"])
    window.clear_filters_btn.configure(bg=P["bar"], fg=P["blue"])
    for button in window._filter_buttons:
        button.configure(highlightbackground=P["bar"], highlightcolor=P["blue"])
    window._layout_log_filters()


def _layout_log_filters(window, event=None):
    """Align filter controls using their measured size; wrap in narrow log panes."""
    width = window.filterbar.winfo_width()
    if width <= 1:
        return
    buttons = window._filter_buttons
    height = max(button.winfo_reqheight() for button in buttons)
    gap = window.px(2)
    x = y = 0
    for button in buttons:
        wanted = min(width, button.winfo_reqwidth())
        if x and x + wanted > width:
            x, y = 0, y + height + gap
        button.place(x=x, y=y, width=wanted, height=height)
        x += wanted + gap
    needed = y + height
    if int(window.filterbar.cget("height")) != needed:
        window.filterbar.configure(height=needed)


def _choose_tools(window, group, selected):
    window._log_filters[group] = selected
    window._log_filters["only_tool"] = None
    window._graph_sig = window._log_sig = None
    window._update_log_filters()
    window._draw_graph()
    window._draw_log()


def _toggle_log_tool(window, name, mcp):
    group = "mcp" if mcp else "tools"
    selected = window._log_filters[group]
    selected = set(log_filters.available_log_tools(window.net, window.sel, mcp)) if selected is None else set(selected)
    if name in selected:
        selected.remove(name)
    else:
        selected.add(name)
    window._choose_tools(group, selected)


def open_tool_filters(window, mcp):
    group = "mcp" if mcp else "tools"
    P = window.P
    menu = tk.Menu(window.win, tearoff=0, bg=P["bar"], fg=P["tx"], activebackground=P["sel"],
                   activeforeground=P["hi"], font=window.f_small)
    menu.add_command(label="Show all", command=lambda: window._choose_tools(group, None))
    menu.add_command(label="Hide all", command=lambda: window._choose_tools(group, set()))
    menu.add_separator()
    selected = window._log_filters[group]
    menu._variables = []
    names = log_filters.available_log_tools(window.net, window.sel, mcp)
    for name in names:
        var = tk.BooleanVar(master=window.win, value=selected is None or name in selected)
        menu._variables.append(var)
        menu.add_checkbutton(label=name, variable=var, command=lambda n=name: window._toggle_log_tool(n, mcp))
    if not names:
        menu.add_command(label="No MCP calls recorded" if mcp else "No tool calls recorded", state="disabled")
    button = window.mcp_filter_btn if mcp else window.tool_filter_btn
    try:
        menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
    finally:
        menu.grab_release()
    return "break"


def _draw_tabs(window):
    P = window.P
    choices = [(-1, "all")] + [(i, a["name"]) for i, a in enumerate(window.net["agents"]) if not a.get("tool")]
    width = max(window.px(200), window.tabs.winfo_width())
    sig = (tuple(choices), width, window.sel, window.theme, window.s)
    if window._tabs_sig == sig:
        return
    window._tabs_sig = sig
    for wdg in window.tabs.winfo_children():
        wdg.destroy()
    window.tailbtn = tk.Label(window.tabs, font=window.f_small, cursor="hand2", padx=window.px(3))
    window.tailbtn.pack(side="right")
    window.tailbtn.bind("<Button-1>", lambda e: window.tool("tail"))
    window._update_tailbtn()
    strip = tk.Canvas(window.tabs, height=window.f_main.metrics("linespace") + window.px(8),
                      bg=P["bg"], bd=0, highlightthickness=0)
    strip.pack(fill="x", expand=True)
    scroll = tk.Scrollbar(window.tabs, orient="horizontal", command=strip.xview, width=max(8, window.px(7)))
    scroll.pack(fill="x")
    strip.configure(xscrollcommand=scroll.set)
    row = tk.Frame(strip, bg=P["bg"])
    strip.create_window(0, 0, window=row, anchor="nw")
    row.bind("<Configure>", lambda e: strip.configure(scrollregion=strip.bbox("all")))
    selected = None
    for i, name in choices:
        on = i == window.sel
        col = P["hi"] if i < 0 else window.color(i)
        label = f"[{short(name, 45)}]"
        font = window.f_bold if on else window.f_small
        lab = tk.Label(row, text=label, font=font, cursor="hand2",
                       bg=col if on else P["bg"], fg=P["bg"] if on else col, padx=window.px(3), pady=window.px(1))
        lab.pack(side="left", padx=(0, window.px(3)), pady=window.px(2))
        lab.bind("<Button-1>", lambda e, i=i: window.select(i))
        if on:
            selected = lab
    strip.update_idletasks()
    if selected is not None:
        strip.xview_moveto(max(0, (selected.winfo_x() - strip.winfo_width() / 2) /
                              max(1, row.winfo_reqwidth())))


def _update_tailbtn(window):
    P = window.P
    try:
        on = window.opts.get("log_tail", True)
        window.tailbtn.configure(text="[tail ▼]" if on else "[tail —]", bg=P["bg"], fg=P["blue"] if on else P["mu"])
    except (tk.TclError, AttributeError):
        pass


def _draw_log(window, keep=None):
    """keep: the scroll position to stay at (folding an entry), instead of following the newest line."""
    P, log = window.P, window.log
    ags = window.net["agents"]
    rows = log_filters.filtered_log_rows(window.net, window.sel, window._log_filters)[-160:]
    filter_sig = tuple((k, tuple(sorted(v)) if isinstance(v, set) else v) for k, v in window._log_filters.items())
    available = max(0, log.winfo_width() - 2 * int(log.cget("padx")) - 2)
    sig = (window.sel, window.theme, window.s, window.opts.get("text_scale", 100), available,
           tuple(rows), len(ags), filter_sig)
    if sig == window._log_sig:
        return
    switched = (window._log_sig or (None,))[0] != sig[0]
    try:
        if not switched and log.tag_ranges("sel"):
            return  # you are selecting text: new lines wait until the selection is gone
    except tk.TclError:
        pass
    window._log_sig = sig
    top = log.yview()[0]
    log.configure(state="normal")
    for edges in window._log_borders.values():
        for edge in edges:
            edge.destroy()
    window._log_borders.clear()
    log.delete("1.0", "end")
    for i in range(len(ags)):
        log.tag_configure(f"a{i}", foreground=window.color(i), font=window.f_bold)
    log.tag_configure("mu", foreground=P["mu"])
    log.tag_configure("think", foreground=P["blue"])
    log.tag_configure("you", foreground=P["hi"], font=window.f_bold)
    log.tag_configure("error", foreground=P["red"])
    log.tag_configure("spawn", foreground=P["amb"])
    if window.sel >= 0 and ags:
        a = ags[window.sel]
        info = " · ".join(v for v in (a["model_label"], a["effort"],
                                      f"ctx {fmt_tokens(a['ctx'])}" if a["ctx"] else "") if v)
        log.insert("end", f"$ tail {a['name']} ", f"a{window.sel}")
        log.insert("end", "# " + info + (f" · {a['kind']}" if a["kind"] and not a["main"] else "") + "\n", "mu")
    if not rows:
        log.insert("end", "(nothing logged yet)", "mu")
    width = min(32, max((len(a["name"]) for a in ags), default=4))
    # long and multi-line entries (a full reply) wrap under their text, not under the time stamp
    hang = 9 + width + 3
    log.tag_configure("hang", lmargin2=window.f_main.measure(" " * (hang + 2)))
    log.tag_configure("fold", foreground=P["blue"])
    log.tag_configure("odd", background=common.mix(P["bar"], P["bg"], 0.35))  # every other entry shaded
    # an agent's final reply (its result): a light green tint, and a green mark before its time stamp
    log.tag_configure("final", background=common.mix(P["grn"], P["bg"], 0.9))
    log.tag_configure("finalmark", foreground=P["grn"])
    log.tag_configure("expanded_body", lmargin1=window.px(6), lmargin2=window.px(6),
                      rmargin=window.px(6), spacing1=window.px(3), spacing3=window.px(3), font=window.f_main)
    log.tag_configure("expanded_header", lmargin1=window.px(6), lmargin2=window.px(6),
                      rmargin=window.px(6), spacing1=window.px(4), spacing3=window.px(3), font=window.f_bold)
    finals = set()
    for i, a in enumerate(ags):
        if a["state"] not in ("working", "needs_input", "waiting"):
            last = next((k for k in range(len(rows) - 1, -1, -1) if rows[k][1] == i and rows[k][2] == "say"), None)
            if last is not None:
                finals.add(last)
    window._entries = []
    for n, (t, i, kind, text) in enumerate(rows):
        # long entries show their first line with ›: click (or right-click) to unfold them, ‹ to fold again
        key = (t, ags[i]["name"], kind, hash(text))
        stamp = time.strftime("%H:%M:%S ", time.localtime(t)) if t else " " * 9
        name = ags[i]["name"].ljust(width)[:width]
        mark = common.KIND_MARK.get(kind, "")
        mark_font = window.f_bold if kind == "you" else window.f_main
        prefix_width = (window.f_main.measure(("▍" if n in finals else "") + stamp + " │ ") +
                        window.f_bold.measure(name) + mark_font.measure(mark + " " if mark else ""))
        text_font = window.f_bold if kind == "you" else window.f_main
        shown, big = common.log_preview(text, available - prefix_width, text_font, window.f_main.measure("  ›"))
        expanded = key in window._expanded
        if expanded:
            shown = common.formatted_log_content(text)
            big = True  # keep collapse available even after widening the pane
        window._entries.append((key, text, ags[i]["name"], big))
        start = log.index("end-1c")
        if expanded:
            title = {"you": "Query", "say": "Response", "think": "Thinking", "input": "Input",
                     "output": "Output", "tool": "Tool", "error": "Error", "spawn": "Agent started"}.get(kind, kind)
            log.insert("end", f"{stamp}{ags[i]['name']} · {title}  ‹\n", ("expanded_header", f"a{i}"))
            log.insert("end", shown + "\n", "expanded_body")
            tag = f"entry{n}"
            edges = tuple(tk.Frame(log, bg="#244c80", bd=0, takefocus=0) for _ in range(4))
            window._log_borders[tag] = edges
            for edge in edges:
                edge.bind("<ButtonRelease-1>", lambda e, n=n: window._entry_click(n))
        else:
            if n in finals:
                log.insert("end", "▍", "finalmark")
            log.insert("end", stamp, "mu")
            log.insert("end", name, f"a{i}")
            log.insert("end", " │ ", "mu")
            if mark:
                log.insert("end", mark + " ", "mu" if kind in ("tool", "say") else kind)
            log.insert("end", shown, (kind if kind in ("think", "you", "error", "spawn") else "say", "hang"))
            if big:
                log.insert("end", "  ›", ("fold", "hang"))
            log.insert("end", "\n")
        log.tag_add(f"entry{n}", start, "end-1c")
        log.tag_configure(f"entry{n}", wrap="word" if expanded else "none")
        if n % 2:
            log.tag_add("odd", start, "end-1c")
        if n in finals:
            log.tag_add("final", start, "end-1c")
        log.tag_bind(f"entry{n}", "<ButtonRelease-1>", lambda e, n=n: window._entry_click(n))
        for b in ("<Button-3>",) + (("<Button-2>", "<Control-Button-1>") if common.IS_MAC else ()):
            log.tag_bind(f"entry{n}", b, lambda e, n=n: window._entry_menu(e, n))
        if expanded:
            log.insert("end", "\n", ())  # space between outlined entries; outside the clickable entry
    log.configure(state="disabled")
    if keep is not None:
        log.yview_moveto(keep)
    elif window.opts.get("log_tail", True) or switched:
        log.see("end")  # tail: follow the newest line
    else:
        log.yview_moveto(top)  # stays where you are reading
    log.after_idle(window._position_log_borders)


def _position_log_borders(window, *args):
    """Contour expanded text without embedding widgets that break selection and copying."""
    log = window.log
    if not window.alive():
        return
    height, width = log.winfo_height(), log.winfo_width()
    visible_first, visible_last = log.index("@0,0"), log.index(f"@0,{height}")
    for tag, edges in window._log_borders.items():
        for edge in edges:
            edge.place_forget()
        if not log.tag_ranges(tag):
            continue
        first, last = f"{tag}.first", f"{tag}.last-1c"
        if log.compare(last, "<", visible_first) or log.compare(first, ">", visible_last):
            continue
        top_line, bottom_line = log.dlineinfo(first), log.dlineinfo(last)
        top = max(0, top_line[1]) if top_line else 0
        bottom = min(height, bottom_line[1] + bottom_line[3]) if bottom_line else height
        thick = max(1, window.px(1))
        if bottom <= top:
            continue
        edges[0].place(x=0, y=top, width=thick, height=bottom - top, bordermode="ignore")
        edges[1].place(x=width - thick, y=top, width=thick, height=bottom - top, bordermode="ignore")
        if top_line and top_line[1] >= 0:
            edges[2].place(x=0, y=top, width=width, height=thick, bordermode="ignore")
        if bottom_line and bottom_line[1] + bottom_line[3] <= height:
            edges[3].place(x=0, y=bottom - thick, width=width, height=thick, bordermode="ignore")


def _entry_click(window, n):
    try:
        if window.log.tag_ranges("sel"):
            return  # that was a selection, not a click
    except tk.TclError:
        pass
    window._toggle_entry(n)


def _toggle_entry(window, n):
    if n >= len(window._entries) or not window._entries[n][3]:
        return
    key = window._entries[n][0]
    try:
        window.log.tag_remove("sel", "1.0", "end")
    except tk.TclError:
        pass
    if key in window._expanded:
        window._expanded.discard(key)
    else:
        window._expanded.add(key)
        if window.opts.get("log_tail", True):  # unfolding to read: stop following the tail
            window.opts["log_tail"] = False
            window._save("tail")
            window._update_tailbtn()
            if window.menu_open:
                window._build_menu()
    window._log_sig = None
    window._draw_log(keep=window.log.yview()[0])
    try:
        window.log.see(f"entry{n}.first")  # its first line stays in sight (unfolding near the end scrolls)
    except tk.TclError:
        pass


def _entry_menu(window, e, n):
    if n >= len(window._entries):
        return "break"
    key, text, who, big = window._entries[n]
    P = window.P
    menu = tk.Menu(window.win, tearoff=0, bg=P["bar"], fg=P["tx"], activebackground=P["sel"],
                   activeforeground=P["hi"], font=window.f_small)
    if big:
        menu.add_command(label="collapse" if key in window._expanded else "expand",
                         command=lambda: window._toggle_entry(n))
    menu.add_command(label="copy text", command=lambda: (window.win.clipboard_clear(),
                                                         window.win.clipboard_append(text)))
    first = text.strip().split("\n", 1)[0]
    title = f"{who} · {first[:70]}" + ("…" if len(first) > 70 else "")
    menu.add_command(label="open in a window", command=lambda: common.TextPopup(window, title, text))
    try:
        menu.tk_popup(e.x_root, e.y_root)
    finally:
        menu.grab_release()
    return "break"
