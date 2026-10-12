"""Build the agent window widgets and wire their callbacks to the owner.

The owner supplies widgets and UI state; services supply shared behavior.
No application entry point is imported by this component.
"""
import sys
import tkinter as tk


def _build(window, services):
    s = window._font_scale()
    window.f_main = services.tkfont.Font(root=window.win, family=services.core.UI_FONT, size=max(5, int(round(8 * s))))
    window.f_bold = services.tkfont.Font(root=window.win, family=services.core.UI_FONT, size=max(5, int(round(8 * s))), weight="bold")
    window.f_small = services.tkfont.Font(root=window.win, family=services.core.UI_FONT, size=max(5, int(round(7 * s))))
    window.g_main = services.tkfont.Font(root=window.win, family=services.core.UI_FONT, size=max(5, int(round(8 * s))))
    window.g_bold = services.tkfont.Font(root=window.win, family=services.core.UI_FONT, size=max(5, int(round(8 * s))), weight="bold")
    window.g_small = services.tkfont.Font(root=window.win, family=services.core.UI_FONT, size=max(5, int(round(7 * s))))
    w = window.win
    window.head = tk.Frame(w)
    window.head.pack(fill="x")
    # Packed right to left before the title, and the title asks for no width of its own: whatever the scale,
    # the cogwheel and the session arrows keep their place and the title gets cut instead
    window.cog = tk.Canvas(window.head, highlightthickness=0, bd=0, cursor="hand2")
    window.cog.pack(side="right", padx=(window.px(6), window.px(6)))
    window.cog.bind("<Button-1>", lambda e: window.toggle_menu())
    window.cog.bind("<Enter>", lambda e: setattr(window, "_cog_hover", True))
    window.cog.bind("<Leave>", lambda e: setattr(window, "_cog_hover", False))
    window.close_btn = tk.Label(window.head, text="✕", font=window.f_small, cursor="hand2", padx=window.px(4))
    window.close_btn.bind("<Button-1>", lambda e: window.close())
    window.next_btn = tk.Label(window.head, text="›", font=window.f_bold, cursor="hand2", padx=window.px(4))
    window.next_btn.pack(side="right")
    window.count = tk.Label(window.head, font=window.f_small)
    window.count.pack(side="right")
    window.prev_btn = tk.Label(window.head, text="‹", font=window.f_bold, cursor="hand2", padx=window.px(4))
    window.prev_btn.pack(side="right")
    window.prompt_mark = tk.Label(window.head, text="▍", font=window.f_bold)
    window.prompt_mark.pack(side="left")
    window.title = tk.Label(window.head, font=window.f_main, anchor="w", width=1)
    window.title.pack(side="left", fill="x", expand=True, pady=window.px(3))
    for wdg in (window.head, window.title, window.prompt_mark):  # drag the bar to move (needed without a title bar)
        wdg.bind("<Button-1>", window._drag_start)
        wdg.bind("<B1-Motion>", window._drag_move)
        wdg.bind("<Double-Button-1>", window.toggle_maximize)
    window.prev_btn.bind("<Button-1>", lambda e: window.step_session(-1))
    window.next_btn.bind("<Button-1>", lambda e: window.step_session(1))
    window._size_cog()

    window.footbar = tk.Frame(w)
    window.footbar.pack(side="bottom", fill="x")
    window.foot = tk.Label(window.footbar, font=window.f_small, anchor="w", padx=window.px(8), pady=window.px(3), text=services.HINT)
    window.foot.pack(side="left", fill="x", expand=True)
    resize_cursor = "size_nw_se" if sys.platform == "win32" else "bottom_right_corner"
    window.grip = tk.Label(w, text="◢", font=window.f_small, cursor=resize_cursor, padx=window.px(2), pady=0)
    window.grip.bind("<Button-1>", window._drag_start)
    window.grip.bind("<B1-Motion>", window._resize_move)
    cursors = {"e": "sb_h_double_arrow", "w": "sb_h_double_arrow", "s": "sb_v_double_arrow",
               "sw": "size_ne_sw" if sys.platform == "win32" else "bottom_left_corner"}
    window.handles = {}
    for side, cur in cursors.items():
        try:
            h = tk.Frame(w, cursor=cur, bd=0, highlightthickness=0)
        except tk.TclError:
            h = tk.Frame(w, bd=0, highlightthickness=0)
        h.bind("<Button-1>", window._drag_start)
        h.bind("<B1-Motion>", lambda e, side=side: window._resize_move(e, side))
        window.handles[side] = h
    window.body = tk.Frame(w)
    window.body.pack(fill="both", expand=True)
    window.canvas = tk.Canvas(window.body, highlightthickness=0, bd=0, height=window.px(150), width=window.px(200))
    window.canvas.bind("<Configure>", window._on_resize)
    window.canvas.bind("<ButtonPress-1>", window._pan_start)
    window.canvas.bind("<B1-Motion>", window._pan_move)
    window.canvas.bind("<ButtonRelease-1>", window._pan_end, add="+")
    for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        window.canvas.bind(seq, window._wheel)
    # zoom controls (top left of the graph) and the stats node (bottom right): fixed, outside the zoom
    window.zoombar = tk.Frame(window.canvas, bd=0, highlightthickness=1)
    window.zoom_btns = []
    for text, fn in (("[-]", lambda: window.zoom(1 / 1.25)), (None, None), ("[+]", lambda: window.zoom(1.25)),
                     ("[reset]", lambda: window.zoom(reset=True)), ("[center]", lambda: window.center_view(by_user=True)),
                     ("[fit all]", window.fit_all)):
        lab = tk.Label(window.zoombar, text=text or "100%", font=window.f_small, cursor="" if fn is None else "hand2",
                       padx=window.px(2))
        lab.pack(side="left")
        if fn:
            lab.bind("<Button-1>", lambda e, fn=fn: fn())
        else:
            window.zoom_label = lab
        window.zoom_btns.append(lab)
    window.auto_fit_var = tk.BooleanVar(master=window.win, value=window.opts.get("auto_fit", False))
    window.auto_fit_btn = tk.Checkbutton(window.zoombar, text="auto", font=window.f_small,
                                      variable=window.auto_fit_var, command=lambda: window.tool("auto_fit"),
                                      bd=0, highlightthickness=0, padx=window.px(2), pady=0, cursor="hand2")
    window.auto_fit_btn.pack(side="left")
    window.zoombar.place(x=window.px(4), y=window.px(4), anchor="nw")
    window.statc = tk.Canvas(window.canvas, bd=0, highlightthickness=1)
    window.statc.place(relx=1.0, rely=1.0, x=-window.px(6), y=-window.px(6), anchor="se")
    window.tip = tk.Label(w, font=window.f_small, justify="left", anchor="w", padx=window.px(6), pady=window.px(3))
    # the split between graph and logs: drag it (the logs get up to 80% of the window)
    window.sep = tk.Frame(window.body, height=max(3, window.px(3)), width=max(3, window.px(3)))
    window.sep.bind("<Button-1>", window._split_start)
    window.sep.bind("<B1-Motion>", window._split_move)
    window.sep.bind("<ButtonRelease-1>", lambda e: window._save("log_lines", delay=300))
    window.logbox = tk.Frame(window.body)
    window.tabs = tk.Frame(window.logbox)
    window.tabs.pack(fill="x", padx=window.px(6))
    window.filterbar = tk.Frame(window.logbox)
    window.filterbar.pack(fill="x", padx=window.px(6), pady=(0, window.px(3)))
    def filter_button(text, action):
        button = tk.Label(window.filterbar, text=text, font=window.f_small, cursor="hand2",
                          padx=window.px(3), pady=window.px(1), takefocus=1, highlightthickness=1)
        def activate(event):
            action()
            return "break"
        for event in ("<Button-1>", "<space>", "<Return>"):
            button.bind(event, activate)
        return button
    window.show_tools_btn = filter_button("[x] show tools", lambda: window.set_log_filter(
        "show_tools", not window._log_filters["show_tools"]))
    window.primary_btn = filter_button("[ ] primary", lambda: window.set_log_filter(
        "primary", not window._log_filters["primary"]))
    window.tool_filter_btn = filter_button("[tools ▾]", lambda: window.open_tool_filters(False))
    window.mcp_filter_btn = filter_button("[MCP ▾]", lambda: window.open_tool_filters(True))
    window.clear_filters_btn = filter_button("[clear filters]", window.clear_log_filters)
    window._filter_buttons = (window.show_tools_btn, window.primary_btn, window.tool_filter_btn,
                            window.mcp_filter_btn, window.clear_filters_btn)
    window.filterbar.bind("<Configure>", window._layout_log_filters)
    window.log = tk.Text(window.logbox, height=6, width=40, wrap="word", bd=0, highlightthickness=0,
                       font=window.f_main, padx=window.px(8), pady=window.px(4), cursor="xterm", takefocus=0,
                       exportselection=True)
    window._log_borders = {}
    window.log.configure(yscrollcommand=window._position_log_borders)
    window.log.pack(fill="both", expand=True)
    window._arrange()
    window.log.bind("<Control-c>", lambda e: window._copy(window.log))
    window.log.bind("<Control-a>", lambda e: (window.log.tag_add("sel", "1.0", "end"), "break")[1])
    window.log.bind("<Button-1>", lambda e: window.log.focus_set(), add="+")  # so Ctrl+C reaches it
    window.log.bind("<Configure>", lambda e: window._draw_log(keep=window.log.yview()[0])
                  if window.theme is not None else None)
    window.win.bind("<Configure>", window._on_window_resize, add="+")
    for seq in ("<FocusIn>", "<Map>", "<Unmap>"):  # brought to the front, minimised or restored
        window.win.bind(seq, window._window_event, add="+")

    window.chip = tk.Label(w, font=window.f_main, cursor="hand2", padx=window.px(6), pady=window.px(1), bd=1,
                         relief="solid")
    window.chip.bind("<Button-1>", lambda e: window.open_overlay(True))
    window.overlay = tk.Frame(w, bd=1, relief="solid")
    window.menu = tk.Frame(w, bd=0, highlightthickness=1)
    window.statdrop = tk.Frame(w, bd=0, highlightthickness=1)
    window.stathead = tk.Label(window.statdrop, font=window.f_small, anchor="w", cursor="hand2")
    window.stathead.pack(fill="x", padx=window.px(6), pady=(window.px(3), 0))
    window.stathead.bind("<Button-1>", lambda e: window.toggle_stat(window.stat_open))
    window.stattext = tk.Text(window.statdrop, font=window.f_small, bd=0, highlightthickness=0, wrap="none",
                            takefocus=0, cursor="xterm", padx=window.px(6), pady=window.px(3))
    window.stattext.bind("<Control-c>", lambda e: window._copy(window.stattext))
    window.stattext.bind("<Button-1>", lambda e: window.stattext.focus_set(), add="+")
    window.stattext.pack(fill="both", expand=True)
    window.stat_open = None
    window.stats = services.agents.network_stats({"agents": []})
