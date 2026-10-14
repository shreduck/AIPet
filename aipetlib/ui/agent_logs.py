"""LogPanel: the agent window's logs (tabs, filter bar, foldable entries, the split line to the graph).

No application entry point is imported by this component.
"""
import time
import tkinter as tk

from ..agents import log_filters
from ..agents import context as agent_context
from ..agents.records import fmt_tokens, short
from . import agent_common as common


class LogPanel:
    """The agent window's logs: tabs, filter bar, the log text with its foldable entries, and the split line to the graph.

    Owns its widgets and its state (filters, shown and unfolded entries, what was drawn last). It reads what to show from
    the window (net, sel, opts, palette P, fonts, px, color, theme, s) and asks it to redraw the graph when a filter
    changes what the graph highlights. Call invalidate() when something it draws from changed underneath (theme, scale).
    """

    def __init__(self, window):
        self.window = window
        self.filters = log_filters.default_log_filters()
        self.entries, self.expanded = {}, set()  # log entries shown (by entry number) / unfolded
        self.drawn = None  # what the log text holds, so new lines can be appended
        self.borders = {}  # outlines of unfolded entries
        self.sig = self.tabs_sig = None  # what the log text and the tabs last showed

    def invalidate(self):
        """Draw the log and the tabs again next time, even if their data looks unchanged."""
        self.sig = self.tabs_sig = None
        self.drawn = None

    def build(self):
        """The logs' widgets: the split line, the tabs, the filter bar and the log text (in the window's body)."""
        window = self.window
        # the split between graph and logs: drag it (the logs get up to 80% of the window)
        self.sep = tk.Frame(window.body, height=max(3, window.px(3)), width=max(3, window.px(3)))
        self.sep.bind("<Button-1>", self.split_start)
        self.sep.bind("<B1-Motion>", self.split_move)
        self.sep.bind("<ButtonRelease-1>", lambda e: window._save("log_lines", delay=300))
        self.box = tk.Frame(window.body)
        self.tabs = tk.Frame(self.box)
        self.tabs.pack(fill="x", padx=window.px(6))
        self.filterbar = tk.Frame(self.box)
        self.filterbar.pack(fill="x", padx=window.px(6), pady=(0, window.px(3)))
        def filter_button(text, action):
            button = tk.Label(self.filterbar, text=text, font=window.f_small, cursor="hand2",
                              padx=window.px(3), pady=window.px(1), takefocus=1, highlightthickness=1)
            def activate(event):
                action()
                return "break"
            for event in ("<Button-1>", "<space>", "<Return>"):
                button.bind(event, activate)
            return button
        self.show_tools_btn = filter_button("[x] show tools", lambda: self.set_filter(
            "show_tools", not self.filters["show_tools"]))
        self.primary_btn = filter_button("[ ] primary", lambda: self.set_filter(
            "primary", not self.filters["primary"]))
        self.tool_filter_btn = filter_button("[tools ▾]", lambda: self.open_tool_filters(False))
        self.mcp_filter_btn = filter_button("[MCP ▾]", lambda: self.open_tool_filters(True))
        self.clear_filters_btn = filter_button("[clear filters]", self.clear_filters)
        self.filter_buttons = (self.show_tools_btn, self.primary_btn, self.tool_filter_btn,
                                self.mcp_filter_btn, self.clear_filters_btn)
        self.filterbar.bind("<Configure>", self.layout_filters)
        self.text = tk.Text(self.box, height=6, width=40, wrap="word", bd=0, highlightthickness=0,
                           font=window.f_main, padx=window.px(8), pady=window.px(4), cursor="xterm", takefocus=0,
                           exportselection=True)
        self.borders = {}
        self.text.configure(yscrollcommand=self.position_borders)
        self.text.pack(fill="both", expand=True)
        self.arrange()
        self.text.bind("<Control-c>", lambda e: window._copy(self.text))
        self.text.bind("<Control-a>", lambda e: (self.text.tag_add("sel", "1.0", "end"), "break")[1])
        self.text.bind("<Button-1>", lambda e: self.text.focus_set(), add="+")  # so Ctrl+C reaches it
        self.text.bind("<Configure>", lambda e: self.draw(keep=self.text.yview()[0])
                      if window.theme is not None else None)

    def arrange(self):
        """The logs below, above, left or right of the graph (settings: logs position), split by a draggable line."""
        window = self.window
        side = window.opts.get("log_side", "bottom")
        across = side in ("left", "right")
        for wdg in (window.graph.canvas, self.sep, self.box):
            wdg.pack_forget()
        self.box.pack(side=side, fill="y" if across else "x")
        self.sep.pack(side=side, fill="y" if across else "x")
        self.sep.configure(cursor="sb_h_double_arrow" if across else "sb_v_double_arrow")
        window.graph.canvas.pack(side=side, fill="both", expand=True)
        self.size()

    def size(self):
        """The logs' size from the settings: lines (above / below) or a width in characters (left / right)."""
        window = self.window
        if window.opts.get("log_side", "bottom") in ("left", "right"):
            self.text.configure(width=window.opts.get("log_cols", 60), height=1)
        else:
            self.text.configure(height=window.opts.get("log_lines", 6), width=1)

    def split_start(self, e):
        self._split = (e.x_root, e.y_root, self.text.winfo_width(), self.text.winfo_height())

    def split_move(self, e):
        """Drag the split: the logs from a few lines (or 20 characters) up to 80% of the window."""
        window = self.window
        side = window.opts.get("log_side", "bottom")
        x0, y0, w0, h0 = getattr(self, "_split", (e.x_root, e.y_root, self.text.winfo_width(), self.text.winfo_height()))
        if side in ("left", "right"):
            col = max(1, window.f_main.measure("0"))
            want = w0 + ((e.x_root - x0) if side == "left" else (x0 - e.x_root))
            most = int(window.win.winfo_width() * 0.8)
            cols = int(max(20, min(most // col, want // col)))
            if cols != window.opts.get("log_cols"):
                window.opts["log_cols"] = cols
                self.size()
            return
        line = max(1, window.f_main.metrics("linespace"))
        want = h0 + ((e.y_root - y0) if side == "top" else (y0 - e.y_root))
        most = int(window.win.winfo_height() * 0.8) - self.tabs.winfo_height() - window.px(8)
        lines = int(max(3, min(most // line, want // line)))
        if lines != window.opts.get("log_lines"):
            window.opts["log_lines"] = lines
            self.size()

    def set_filter(self, key, value):
        self.filters[key] = value
        self.sig = None
        self.update_filters()
        self.draw()

    def clear_filters(self):
        window = self.window
        self.filters = log_filters.default_log_filters()
        self.sig = window.graph.sig = None
        self.update_filters()
        window.graph.draw()
        self.draw()
        return "break"

    def update_filters(self):
        window = self.window
        P, filters = window.P, self.filters
        for button, key, label in ((self.show_tools_btn, "show_tools", "show tools"),
                                   (self.primary_btn, "primary", "primary")):
            button.configure(text=f"[{'x' if filters[key] else ' '}] {label}", bg=P["bar"],
                             fg=P["blue"] if filters[key] else P["tx"])
        self.filterbar.configure(bg=P["bar"])
        for button, group, label in ((self.tool_filter_btn, "tools", "tools"), (self.mcp_filter_btn, "mcp", "MCP")):
            chosen = filters[group]
            count = "" if chosen is None else f" {len(chosen)}"
            button.configure(text=f"[{label}{count} ▾]", bg=P["bar"], fg=P["blue"] if chosen is not None else P["tx"])
        self.clear_filters_btn.configure(bg=P["bar"], fg=P["blue"])
        for button in self.filter_buttons:
            button.configure(highlightbackground=P["bar"], highlightcolor=P["blue"])
        self.layout_filters()

    def layout_filters(self, event=None):
        """Align filter controls using their measured size; wrap in narrow log panes."""
        window = self.window
        width = self.filterbar.winfo_width()
        if width <= 1:
            return
        buttons = self.filter_buttons
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
        if int(self.filterbar.cget("height")) != needed:
            self.filterbar.configure(height=needed)

    def choose_tools(self, group, selected):
        window = self.window
        self.filters[group] = selected
        self.filters["only_tool"] = None
        window.graph.sig = self.sig = None
        self.update_filters()
        window.graph.draw()
        self.draw()

    def toggle_tool(self, name, mcp):
        window = self.window
        group = "mcp" if mcp else "tools"
        selected = self.filters[group]
        selected = set(log_filters.available_log_tools(window.net, window.sel, mcp)) if selected is None else set(selected)
        if name in selected:
            selected.remove(name)
        else:
            selected.add(name)
        self.choose_tools(group, selected)

    def open_tool_filters(self, mcp):
        window = self.window
        group = "mcp" if mcp else "tools"
        P = window.P
        menu = tk.Menu(window.win, tearoff=0, bg=P["bar"], fg=P["tx"], activebackground=P["sel"],
                       activeforeground=P["hi"], font=window.f_small)
        menu.add_command(label="Show all", command=lambda: self.choose_tools(group, None))
        menu.add_command(label="Hide all", command=lambda: self.choose_tools(group, set()))
        menu.add_separator()
        selected = self.filters[group]
        menu._variables = []
        names = log_filters.available_log_tools(window.net, window.sel, mcp)
        for name in names:
            var = tk.BooleanVar(master=window.win, value=selected is None or name in selected)
            menu._variables.append(var)
            menu.add_checkbutton(label=name, variable=var, command=lambda n=name: self.toggle_tool(n, mcp))
        if not names:
            menu.add_command(label="No MCP calls recorded" if mcp else "No tool calls recorded", state="disabled")
        button = self.mcp_filter_btn if mcp else self.tool_filter_btn
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()
        return "break"

    def draw_tabs(self):
        window = self.window
        P = window.P
        choices = [(-1, "all")] + [(i, a["name"]) for i, a in enumerate(window.net["agents"]) if not a.get("tool")]
        width = max(window.px(200), self.tabs.winfo_width())
        sig = (tuple(choices), width, window.sel, window.theme, window.s)
        if self.tabs_sig == sig:
            return
        self.tabs_sig = sig
        for wdg in self.tabs.winfo_children():
            wdg.destroy()
        self.tailbtn = tk.Label(self.tabs, font=window.f_small, cursor="hand2", padx=window.px(3))
        self.tailbtn.pack(side="right")
        self.tailbtn.bind("<Button-1>", lambda e: window.tool("tail"))
        self.update_tailbtn()
        strip = tk.Canvas(self.tabs, height=window.f_main.metrics("linespace") + window.px(8),
                          bg=P["bg"], bd=0, highlightthickness=0)
        strip.pack(fill="x", expand=True)
        scroll = tk.Scrollbar(self.tabs, orient="horizontal", command=strip.xview, width=max(8, window.px(7)))
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

    def update_tailbtn(self):
        window = self.window
        P = window.P
        try:
            on = window.opts.get("log_tail", True)
            self.tailbtn.configure(text="[tail ▼]" if on else "[tail —]", bg=P["bg"], fg=P["blue"] if on else P["mu"])
        except (tk.TclError, AttributeError):
            pass

    def draw(self, keep=None):
        """keep: the scroll position to stay at (folding an entry), instead of following the newest line.
        New lines at the end are appended (and the oldest dropped) without rebuilding the rest: rebuilding a long
        log on every refresh made the window sluggish while agents work."""
        window = self.window
        log = self.text
        ags = window.net["agents"]
        rows = log_filters.filtered_log_rows(window.net, window.sel, self.filters)[-160:]
        filter_sig = tuple((k, tuple(sorted(v)) if isinstance(v, set) else v) for k, v in self.filters.items())
        available = max(0, log.winfo_width() - 2 * int(log.cget("padx")) - 2)
        usage = self.row_usage(rows, ags)
        finals = self.final_rows(rows, ags)
        sig = (window.sel, window.theme, window.s, window.opts.get("text_scale", 100), available,
               tuple(rows), len(ags), filter_sig, tuple(usage), tuple(sorted(finals)))
        if sig == self.sig:
            return
        switched = (self.sig or (None,))[0] != sig[0]
        try:
            if not switched and log.tag_ranges("sel"):
                return  # you are selecting text: new lines wait until the selection is gone
        except tk.TclError:
            pass
        base = (window.sel, window.theme, window.s, window.opts.get("text_scale", 100), available,
                tuple(a["name"] for a in ags), filter_sig)
        head = self.header(ags)
        appendable = keep is None and self.sig is not None and not switched
        self.sig = sig
        if appendable and self.append(base, rows, usage, finals, head):
            return
        self.rebuild(base, rows, usage, finals, head, keep, switched)

    def header(self, ags):
        """The selected agent's line above its log: model, effort, context, tokens, type."""
        window = self.window
        if not (window.sel >= 0 and ags):
            return None
        a = ags[window.sel]
        info = " · ".join(v for v in (a["model_label"], a["effort"],
                                      f"ctx {fmt_tokens(a['ctx'])}" if a["ctx"] else "",
                                      agent_context.tokens_text(a.get("tokens"))) if v)
        return f"$ tail {a['name']} ", "# " + info + (f" · {a['kind']}" if a["kind"] and not a["main"] else "") + "\n"

    @staticmethod
    def final_rows(rows, ags):
        """Positions of each finished agent's final reply (its result): its last "say" row."""
        finals = set()
        for i, a in enumerate(ags):
            if a["state"] not in ("working", "needs_input", "waiting"):
                last = next((k for k in range(len(rows) - 1, -1, -1) if rows[k][1] == i and rows[k][2] == "say"), None)
                if last is not None:
                    finals.add(last)
        return finals

    def rebuild(self, base, rows, usage, finals, head, keep, switched):
        window = self.window
        P, log = window.P, self.text
        ags = window.net["agents"]
        top = log.yview()[0]
        log.configure(state="normal")
        for edges in self.borders.values():
            for edge in edges:
                edge.destroy()
        self.borders.clear()
        log.delete("1.0", "end")
        for n in getattr(self, "entries", {}):  # the old entries' tags (numbered on from a previous build)
            log.tag_delete(f"entry{n}")
        for i in range(len(ags)):
            log.tag_configure(f"a{i}", foreground=window.color(i), font=window.f_bold)
        log.tag_configure("mu", foreground=P["mu"])
        log.tag_configure("think", foreground=P["blue"])
        log.tag_configure("you", foreground=P["hi"], font=window.f_bold)
        log.tag_configure("error", foreground=P["red"])
        log.tag_configure("spawn", foreground=P["amb"])
        if head:
            log.insert("end", head[0], f"a{window.sel}")
            log.insert("end", head[1], ("mu", "head"))
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
        self.entries = {}
        for n, row in enumerate(rows):
            self.insert_row(n, row, usage[n], n in finals, width, base[4])
        log.configure(state="disabled")
        self.drawn = {"base": base, "rows": rows, "usage": usage, "finals": finals, "offset": 0, "head": head,
                      "width": width}
        if keep is not None:
            log.yview_moveto(keep)
        elif window.opts.get("log_tail", True) or switched:
            log.see("end")  # tail: follow the newest line
        else:
            log.yview_moveto(top)  # stays where you are reading
        log.after_idle(self.position_borders)

    def append(self, base, rows, usage, finals, head):
        """Only new lines at the end (the oldest ones dropping off the top): add those. False: rebuild instead."""
        window = self.window
        log = self.text
        drawn = getattr(self, "drawn", None)
        if not drawn or drawn["base"] != base or not drawn["rows"] or not rows:
            return False
        old = drawn["rows"]
        drop = next((k for k in range(len(old)) if old[k:] == rows[:len(old) - k]), None)
        if drop is None:
            return False
        kept = len(old) - drop
        # the rows that stay must look the same: their request tokens and final-reply marks
        if usage[:kept] != drawn["usage"][drop:] or \
                {p for p in finals if p < kept} != {p - drop for p in drawn["finals"] if p >= drop}:
            return False
        ags = window.net["agents"]
        if any(self.row_key(row, ags) in self.expanded for row in rows[kept:]):
            return False
        offset = drawn["offset"]
        log.configure(state="normal")
        try:
            log.mark_set("aipet_view", "@0,0")  # where you are reading, wherever the lines above it go
            log.mark_gravity("aipet_view", "left")
        except tk.TclError:
            pass
        if head != drawn["head"] and head and drawn["head"]:  # the selected agent's figures changed
            at = log.index("head.first")
            log.delete("head.first", "head.last")
            log.insert(at, head[1], ("mu", "head"))
        if drop:
            log.delete(f"entry{offset}.first", f"entry{offset + drop}.first")
            for n in range(offset, offset + drop):
                log.tag_delete(f"entry{n}")
                self.entries.pop(n, None)
                for edge in self.borders.pop(f"entry{n}", ()):
                    edge.destroy()
        offset += drop
        for p in range(kept, len(rows)):
            self.insert_row(offset + p, rows[p], usage[p], p in finals, drawn["width"], base[4])
        log.configure(state="disabled")
        self.drawn = dict(drawn, rows=rows, usage=usage, finals=finals, offset=offset, head=head)
        if window.opts.get("log_tail", True):
            log.see("end")
        else:
            try:
                log.yview("aipet_view")
            except tk.TclError:
                pass
        log.after_idle(self.position_borders)
        return True

    @staticmethod
    def row_key(row, ags):
        t, i, kind, text = row
        return (t, ags[i]["name"], kind, hash(text))

    def insert_row(self, n, row, usage, final, width, available):
        """One log entry at the end, tagged entry<n> (n counts on across appends, so tags stay unique)."""
        window = self.window
        log = self.text
        ags = window.net["agents"]
        t, i, kind, text = row
        meta = f"  · {agent_context.line_usage(usage)}" if usage else ""  # the request's tokens
        # long entries show their first line with ›: click (or right-click) to unfold them, ‹ to fold again
        key = self.row_key(row, ags)
        stamp = time.strftime("%H:%M:%S ", time.localtime(t)) if t else " " * 9
        name = ags[i]["name"].ljust(width)[:width]
        mark = common.KIND_MARK.get(kind, "")
        mark_font = window.f_bold if kind == "you" else window.f_main
        prefix_width = (window.f_main.measure(("▍" if final else "") + stamp + " │ ") +
                        window.f_bold.measure(name) + mark_font.measure(mark + " " if mark else ""))
        text_font = window.f_bold if kind == "you" else window.f_main
        shown, big = common.log_preview(text, available - prefix_width - window.f_main.measure(meta), text_font,
                                        window.f_main.measure("  ›"))
        expanded = key in self.expanded
        if expanded:
            shown = common.formatted_log_content(text)
            big = True  # keep collapse available even after widening the pane
        self.entries[n] = (key, text, ags[i]["name"], big)
        start = log.index("end-1c")
        if expanded:
            title = {"you": "Query", "say": "Response", "think": "Thinking", "input": "Input",
                     "output": "Output", "tool": "Tool", "error": "Error", "spawn": "Agent started"}.get(kind, kind)
            log.insert("end", f"{stamp}{ags[i]['name']} · {title}{meta}  ‹\n", ("expanded_header", f"a{i}"))
            log.insert("end", shown + "\n", "expanded_body")
            tag = f"entry{n}"
            edges = tuple(tk.Frame(log, bg="#244c80", bd=0, takefocus=0) for _ in range(4))
            self.borders[tag] = edges
            for edge in edges:
                edge.bind("<ButtonRelease-1>", lambda e, n=n: self.entry_click(n))
        else:
            if final:
                log.insert("end", "▍", "finalmark")
            log.insert("end", stamp, "mu")
            log.insert("end", name, f"a{i}")
            log.insert("end", " │ ", "mu")
            if mark:
                log.insert("end", mark + " ", "mu" if kind in ("tool", "say") else kind)
            log.insert("end", shown, (kind if kind in ("think", "you", "error", "spawn") else "say", "hang"))
            if big:
                log.insert("end", "  ›", ("fold", "hang"))
            if meta:
                log.insert("end", meta, ("mu", "hang"))
            log.insert("end", "\n")
        log.tag_add(f"entry{n}", start, "end-1c")
        log.tag_configure(f"entry{n}", wrap="word" if expanded else "none")
        if n % 2:
            log.tag_add("odd", start, "end-1c")
        if final:
            log.tag_add("final", start, "end-1c")
        log.tag_bind(f"entry{n}", "<ButtonRelease-1>", lambda e, n=n: self.entry_click(n))
        for b in ("<Button-3>",) + (("<Button-2>", "<Control-Button-1>") if common.IS_MAC else ()):
            log.tag_bind(f"entry{n}", b, lambda e, n=n: self.entry_menu(e, n))
        if expanded:
            log.insert("end", "\n", ())  # space between outlined entries; outside the clickable entry

    @staticmethod
    def row_usage(rows, ags):
        """Per row: the tokens of the request it starts ((prompt, output)), once per request, else None."""
        seen, out = set(), []
        for t, i, _, _ in rows:
            u = (ags[i].get("usage_at") or {}).get(t) if i < len(ags) else None
            out.append(u if u and (i, t) not in seen else None)
            if u:
                seen.add((i, t))
        return out

    def position_borders(self, *args):
        """Contour expanded text without embedding widgets that break selection and copying."""
        window = self.window
        log = self.text
        if not window.alive():
            return
        height, width = log.winfo_height(), log.winfo_width()
        visible_first, visible_last = log.index("@0,0"), log.index(f"@0,{height}")
        for tag, edges in self.borders.items():
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

    def entry_click(self, n):
        try:
            if self.text.tag_ranges("sel"):
                return  # that was a selection, not a click
        except tk.TclError:
            pass
        self.toggle_entry(n)

    def toggle_entry(self, n):
        window = self.window
        if n not in self.entries or not self.entries[n][3]:
            return
        key = self.entries[n][0]
        try:
            self.text.tag_remove("sel", "1.0", "end")
        except tk.TclError:
            pass
        if key in self.expanded:
            self.expanded.discard(key)
        else:
            self.expanded.add(key)
            if window.opts.get("log_tail", True):  # unfolding to read: stop following the tail
                window.opts["log_tail"] = False
                window._save("tail")
                self.update_tailbtn()
                if window.settings.open:
                    window.settings.build()
        self.sig = None
        self.draw(keep=self.text.yview()[0])
        try:
            self.text.see(f"entry{n}.first")  # its first line stays in sight (unfolding near the end scrolls)
        except tk.TclError:
            pass

    def entry_menu(self, e, n):
        window = self.window
        if n not in self.entries:
            return "break"
        key, text, who, big = self.entries[n]
        P = window.P
        menu = tk.Menu(window.win, tearoff=0, bg=P["bar"], fg=P["tx"], activebackground=P["sel"],
                       activeforeground=P["hi"], font=window.f_small)
        if big:
            menu.add_command(label="collapse" if key in self.expanded else "expand",
                             command=lambda: self.toggle_entry(n))
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
