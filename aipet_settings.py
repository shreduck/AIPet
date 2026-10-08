"""AIPet settings window.

Built from the same menu spec as the pet's right-click menu, the Windows tray menu and the macOS menu bar, so every
switch and button here runs exactly the action the menu item runs. Spec entries may carry extra keys that only this
window reads: "help" (an explanation under the label), "icon" (a pixel icon name) and "choice" (a submenu whose items
are a pick-one list). The window rebuilds itself whenever the spec's state changes (a toggle flipped here, in a menu, or
by the app itself) and when the theme switches.
"""
import os
import sys
import tkinter as tk

import aipet as core

IS_MAC = sys.platform == "darwin"
UI_FONT = core.UI_FONT  # the console font all of AIPet's windows use
POLL_MS = 1200

# 9x9 pixel icons, drawn in the pet's pixel style ('#' = filled)
ICONS = {
    "home": ["....#....", "...###...", "..#####..", ".#######.", "#########", ".##...##.", ".##.#.##.", ".##.#.##.",
             ".#######."],
    "palette": ["..#####..", ".#######.", "##.###.##", "#########", "##.####..", "#######..", ".#.###.#.", "..######.",
                "...####.."],
    "gear": ["....#....", ".#.###.#.", "..#####..", ".###.###.", "####.####", ".###.###.", "..#####..", ".#.###.#.",
             "....#...."],
    "plug": ["..#...#..", "..#...#..", ".#######.", ".#######.", ".#######.", "..#####..", "...###...", "....#....",
             "....#...."],
    "shield": ["#########", "##.....##", "##.....##", "##.....##", ".##...##.", ".##...##.", "..##.##..", "...###...",
               "....#...."],
    "help": ["..#####..", ".##...##.", ".....##..", "....##...", "...##....", "...##....", ".........", "...##....",
             "...##...."],
    "bell": ["....#....", "..#####..", ".#######.", ".#######.", ".#######.", ".#######.", "#########", ".........",
             "...###..."],
    "clock": ["..#####..", ".#..#..#.", "#...#...#", "#...#...#", "#...###.#", "#.......#", "#.......#", ".#.....#.",
              "..#####.."],
    "refresh": ["..####.#.", ".#....##.", "#....###.", "#........", "#.......#", "........#", ".###....#", ".##....#.",
                ".#.####.."],
    "wrench": ["......##.", ".....#..#", ".....#.##", "....###..", "...###...", "..###....", ".###.....", "###......",
               ".#......."],
    "check": [".........", "........#", ".......##", "......##.", "#....##..", "##..##...", ".####....", "..##.....",
              "........."],
    "search": ["..####...", ".#....#..", "#......#.", "#......#.", "#......#.", ".#....#..", "..#####..", "......##.",
               ".......##"],
}
ICONS["speaker"] = ["....#....", "...##..#.", "#####...#", "#####.#.#", "#####.#.#", "#####.#.#", "#####...#",
                    "...##..#.", "....#...."]
ICONS["link"] = ICONS["plug"]
# Section accents use the robot's status-light colours (amber / green / red) and its screen cyan
SECTION_ACCENTS = {"home": "#22c55e", "palette": "#22d3ee", "gear": "#f5a524", "plug": "#22c55e", "shield": "#f5a524",
                   "help": "#22d3ee"}
DANGER_LABELS = {"Quit AIPet", "Remove hooks", "Turn all off"}
WHEEL_TAG = "AIPetSettingsWheel"


def clean(label):
    """Menu labels end in '...' when they open something; the window shows a chevron instead."""
    label = str(label)
    return label[:-3].rstrip() if label.endswith("...") else label


def page_name(label):
    """'Permissions (auto approve ON)' -> 'Permissions'."""
    return str(label).split(" (")[0]


def signature(spec, zoom=1.0):
    """Everything about the spec the window shows; it rebuilds when this changes."""
    def walk(entries):
        out = []
        for e in entries or []:
            if e is None:
                out.append(None)
                continue
            out.append((e.get("label"), e.get("checked"), e.get("enabled", True), (e.get("slider") or {}).get("value"),
                         walk(e.get("submenu")) if e.get("submenu") is not None else None))
        return tuple(out)
    return (core.T.get("name"), zoom, walk(spec))


def pages_from_spec(spec):
    """[(name, icon, help, entries)]: a General page for the top-level items, then one page per top-level submenu."""
    general, pages = [], []
    for e in spec:
        if e is not None and e.get("settings"):
            continue
        if e is not None and e.get("submenu") is not None:
            pages.append((page_name(e["label"]), e.get("icon") or "gear", e.get("help", ""), e["submenu"], e["label"]))
        else:
            general.append(e)
    while general and general[0] is None:
        general.pop(0)
    return [("General", "home", "Show or hide the pet, tidy up and quit.", general, "General")] + pages


def flatten(entries, path=()):
    """Every row the window can show, with the menu path that leads to it (for search)."""
    for e in entries or []:
        if e is None or e.get("settings"):
            continue
        sub = e.get("submenu")
        if sub is not None and not e.get("choice"):
            yield from flatten(sub, path + (page_name(clean(e["label"])),))
        else:
            yield path, e


TILE = 13  # icon tiles are 13 x 13 sprite pixels: outline, a pixel of screen margin, the 9 x 9 glyph
# (screen, glyph, highlighted glyph) per theme. Dark: the robot's own screen with its cyan face pixels. Light: the
# robot's white body colour, slightly tinted, with the cyan deepened so it reads on a light background.
ICON_COLORS = {"dark": ("#0b1220", "#6ee7f3", "#d6fbff"),
               "light": ("#ecfeff", "#0891b2", "#155e75")}


def icon_size(px=2):
    return TILE * px


def draw_icon(canvas, name, color=None, x=0, y=0, px=2, tile=True, bright=False):
    """A section icon in the pet's style: the glyph glows cyan on a little robot screen (dark, pixel-rounded,
    outlined like the robot). tile=False draws the bare glyph in `color` (the search box)."""
    def dot(c, r, fill):
        canvas.create_rectangle(x + c * px, y + r * px, x + (c + 1) * px - 1, y + (r + 1) * px - 1,
                                fill=fill, outline=fill)
    off = 0
    if tile:
        # dark: a grey rim, or the dark screen would melt into the dark sidebar
        outline = core.T.get("border", "#374151") if core.T.get("name") == "dark" else core.T.get("tag_outline", "#111827")
        screen, glyph, glyph_bright = ICON_COLORS["dark" if core.T.get("name") == "dark" else "light"]
        last = TILE - 1
        for r in range(TILE):
            for c in range(TILE):
                edge = r in (0, last) or c in (0, last)
                corner = (r, c) in ((0, 0), (0, last), (last, 0), (last, last))
                inner_corner = (r, c) in ((1, 1), (1, last - 1), (last - 1, 1), (last - 1, last - 1))
                if corner:
                    continue  # rounded: the outer corner pixels stay empty
                dot(c, r, outline if edge or inner_corner else screen)
        off, color = 2, glyph_bright if bright else glyph
    for r, row in enumerate(ICONS.get(name, ICONS["gear"])):
        for c, ch in enumerate(row):
            if ch == "#":
                dot(c + off, r + off, color)


ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.8, 2.0, 0.1
BASE_SCALE = 1.5  # "100%" is drawn half again as big as the pet's other windows: easier to read


def clamp_zoom(value):
    try:
        return round(min(ZOOM_MAX, max(ZOOM_MIN, float(value))), 2)
    except (TypeError, ValueError):
        return 1.0


class SettingsWindow:
    def __init__(self, root, spec_fn, version="", zoom_fn=None, set_zoom=None):
        """zoom_fn() -> the window's text / icon scale (1.0 = normal); set_zoom(value) stores a new one (the
        Appearance slider and Ctrl + plus / minus / 0 / mouse wheel all go through it)."""
        self.root, self.spec_fn, self.version = root, spec_fn, version
        self.zoom_fn, self.set_zoom = zoom_fn or (lambda: 1.0), set_zoom
        self.zoom = clamp_zoom(self.zoom_fn())
        self.page, self.query = "General", tk.StringVar(master=root)
        self._sig, self._after, self._wrap = None, None, []
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.title("AIPet settings")
        w.minsize(int(560 * BASE_SCALE), int(380 * BASE_SCALE))
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: self.close())
        mod = "Command" if IS_MAC else "Control"
        for seq, step in (("plus", 1), ("equal", 1), ("KP_Add", 1), ("minus", -1), ("KP_Subtract", -1),
                          ("0", 0), ("KP_0", 0)):
            w.bind(f"<{mod}-{seq}>", lambda e, s=step: self.zoom_by(s))
        self.query.trace_add("write", lambda *_: self._render(keep_scroll=False))
        self.refresh(force=True)
        self._place()
        w.deiconify()
        self.raise_()
        self._poll()

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
            self.win.focus_force()  # macOS: also brings AIPet to the front
        except tk.TclError:
            pass

    def close(self):
        if self._after:
            try:
                self.win.after_cancel(self._after)
            except tk.TclError:
                pass
        self._unbind_wheel()
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _place(self):
        w, h = int(840 * self.scale), int(600 * self.scale)
        try:
            ref = (self.root.winfo_x() + self.root.winfo_width() // 2, self.root.winfo_y() + self.root.winfo_height() // 2)
            left, top, right, bottom = core.work_area(*ref, self.win) or (
                0, 0, self.win.winfo_screenwidth(), self.win.winfo_screenheight())
        except Exception:
            left, top, right, bottom = 0, 0, self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        w, h = min(w, right - left - 40), min(h, bottom - top - 40)
        self.win.geometry(f"{w}x{h}+{left + (right - left - w) // 2}+{top + (bottom - top - h) // 3}")

    def _poll(self):
        if not self.alive():
            return
        self.refresh()
        self._after = self.win.after(POLL_MS, self._poll)

    # ---- size
    @property
    def scale(self):
        """What everything is multiplied by: the user's zoom on top of the comfortable base size."""
        return self.zoom * BASE_SCALE

    def font(self, size, *style):
        return (UI_FONT, max(6, int(round(size * self.scale)))) + style

    @property
    def px(self):  # sprite pixel size of icons
        return max(1, int(round(2 * self.scale)))

    @property
    def px_big(self):
        return max(2, int(round(3 * self.scale)))

    def zoom_by(self, step):
        """Ctrl + plus / minus (step 1 / -1), Ctrl + 0 (step 0: back to 100%)."""
        value = 1.0 if step == 0 else clamp_zoom(self.zoom + step * ZOOM_STEP)
        if self.set_zoom and value != self.zoom:
            self.set_zoom(value)
            self.refresh()
        return "break"

    # ---- state
    def refresh(self, force=False):
        """Rebuild if anything shown has changed (cheap to call often)."""
        if not self.alive():
            return
        try:
            spec = self.spec_fn()
        except Exception as e:
            core.log_error(f"settings spec: {e!r}")
            return
        zoom = clamp_zoom(self.zoom_fn())
        sig = signature(spec, zoom)
        if not force and sig == self._sig:
            return
        theme_changed = self._sig is None or sig[:2] != self._sig[:2]  # theme or size: rebuild everything
        self.zoom = zoom
        self._sig, self.spec = sig, spec
        if theme_changed:
            self._build_frame()
        self._render(keep_scroll=True)

    def run(self, action):
        """Run a menu action exactly as the menu would, then show its result."""
        if not action:
            return
        try:
            action()
        finally:
            if self.alive():
                self.win.after(80, self.refresh)

    # ---- frame: sidebar + scrolling content
    def _build_frame(self):
        T, w = core.T, self.win
        for child in w.winfo_children():
            child.destroy()
        self.bg, self.side_bg = T["win_bg"], T["code_bg"]
        w.configure(bg=self.bg)
        core.titlebar_theme(w)

        side = self.side = tk.Frame(w, bg=self.side_bg, width=int(220 * self.scale))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        head = tk.Frame(side, bg=self.side_bg)
        head.pack(fill="x", padx=16, pady=(18, 12))
        tk.Label(head, text="AIPet", bg=self.side_bg, fg=T["win_fg"], font=self.font(16, "bold"), anchor="w").pack(fill="x")
        tk.Label(head, text=f"Settings · {self.version}" if self.version else "Settings", bg=self.side_bg, fg=T["muted"],
                 font=self.font(9), anchor="w").pack(fill="x")

        box = tk.Frame(side, bg=T["entry_bg"], highlightthickness=1, highlightbackground=T["border"],
                       highlightcolor=T["primary"])
        box.pack(fill="x", padx=12, pady=(0, 10))
        icon = tk.Canvas(box, width=9 * self.px, height=9 * self.px, bg=T["entry_bg"], highlightthickness=0, bd=0)
        icon.pack(side="left", padx=(8, 2), pady=5)
        draw_icon(icon, "search", T["muted"], px=self.px, tile=False)
        self.search = tk.Entry(box, textvariable=self.query, bg=T["entry_bg"], fg=T["entry_fg"], relief="flat",
                               insertbackground=T["entry_fg"], highlightthickness=0, font=self.font(10))
        self.search.pack(side="left", fill="x", expand=True, padx=(4, 8), pady=5)
        self.search.bind("<Escape>", lambda e: (self.query.set(""), "break")[1])
        self.nav = tk.Frame(side, bg=self.side_bg)
        self.nav.pack(fill="both", expand=True, padx=8)

        tk.Frame(w, bg=T["border"], width=1).pack(side="left", fill="y")
        main = tk.Frame(w, bg=self.bg)
        main.pack(side="left", fill="both", expand=True)
        self.strip = tk.Frame(main, bg=T["primary"], height=5)  # the accent strip the pet's cards and dialogs have
        self.strip.pack(fill="x")
        self.header = tk.Frame(main, bg=self.bg)
        self.header.pack(fill="x", padx=28, pady=(20, 6))
        holder = tk.Frame(main, bg=self.bg)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=self.bg, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=self.bg)
        self._body_id = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_resize)
        # The wheel scrolls the page wherever the pointer is over it: a private bind tag on the canvas and every row
        self.win.bind_class(WHEEL_TAG, "<MouseWheel>", self._wheel)
        self.win.bind_class(WHEEL_TAG, "<Button-4>", lambda e: self._scroll(-3))
        self.win.bind_class(WHEEL_TAG, "<Button-5>", lambda e: self._scroll(3))
        self.win.bind_class(WHEEL_TAG, "<Control-Button-4>", lambda e: self.zoom_by(1))
        self.win.bind_class(WHEEL_TAG, "<Control-Button-5>", lambda e: self.zoom_by(-1))
        self._tag_wheel(self.win)  # Ctrl + wheel zooms anywhere in the window

    def _on_resize(self, e):
        self.canvas.itemconfigure(self._body_id, width=e.width)
        for label, inset in self._wrap:
            try:
                label.configure(wraplength=max(200, e.width - int(inset * self.scale)))
            except tk.TclError:
                pass

    def _tag_wheel(self, w):
        tags = w.bindtags()
        if WHEEL_TAG not in tags:
            w.bindtags((WHEEL_TAG,) + tags)
        for child in w.winfo_children():
            self._tag_wheel(child)

    def _unbind_wheel(self):
        pass  # the bind tag goes away with the widgets

    def _wheel(self, e):
        if e.state & (0x8 if IS_MAC else 0x4):  # Ctrl (Command on a Mac) + wheel: zoom
            return self.zoom_by(1 if e.delta > 0 else -1)
        self._scroll(-e.delta if IS_MAC else -int(e.delta / 120) * 3)

    def _scroll(self, step):
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(step, "units")

    # ---- rendering
    def _render(self, keep_scroll=True):
        if not self.alive() or not hasattr(self, "body"):
            return
        pages = pages_from_spec(self.spec)
        names = [p[0] for p in pages]
        if self.page not in names:
            self.page = "General"
        top = self.canvas.yview()[0] if keep_scroll else 0.0
        for frame in (self.nav, self.header, self.body):
            for child in frame.winfo_children():
                child.destroy()
        self._wrap = []
        query = self.query.get().strip().lower()
        for name, icon, _help, _entries, label in pages:
            self._nav_item(name, icon, label, selected=(name == self.page and not query))
        if query:
            self.strip.configure(bg=core.T["primary"])
            self._render_search(pages, query)
        else:
            name, icon, help_text, entries, label = next(p for p in pages if p[0] == self.page)
            self.strip.configure(bg=SECTION_ACCENTS.get(icon, core.T["primary"]))
            self._page_header(name, icon, help_text, label)
            self._entries(self.body, entries, 0)
        tk.Frame(self.body, bg=self.bg, height=24).pack(fill="x")
        self._tag_wheel(self.body)
        self._tag_wheel(self.nav)
        self.body.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(top)
        self._on_resize(type("E", (), {"width": self.canvas.winfo_width()})())

    def _nav_item(self, name, icon, label, selected):
        T = core.T
        bg = T["select_bg"] if selected else self.side_bg
        row = tk.Frame(self.nav, bg=bg, cursor="hand2")
        row.pack(fill="x", pady=1)
        tk.Frame(row, bg=SECTION_ACCENTS.get(icon, T["primary"]) if selected else bg, width=3).pack(side="left", fill="y")
        c = tk.Canvas(row, width=icon_size(self.px), height=icon_size(self.px), bg=bg, highlightthickness=0, bd=0)
        c.pack(side="left", padx=(9, 10), pady=5)
        draw_icon(c, icon, px=self.px, bright=selected)
        tk.Label(row, text=name, bg=bg, fg=T["win_fg"], font=self.font(10, "bold" if selected else "normal"),
                 anchor="w").pack(side="left", fill="x", expand=True)
        if label != name:  # e.g. "Permissions (auto approve ON)": an amber dot says something needs attention
            tk.Label(row, text="●", bg=bg, fg="#f59e0b", font=self.font(9)).pack(side="right", padx=8)

        def pick(_e=None):
            self.page = name
            if self.query.get():
                self.query.set("")  # re-renders through the trace
            else:
                self._render(keep_scroll=False)
        self._clickable(row, pick, hover=None if selected else T["btn_active"])

    def _page_header(self, name, icon, help_text, label):
        T = core.T
        top = tk.Frame(self.header, bg=self.bg)
        top.pack(fill="x")
        c = tk.Canvas(top, width=icon_size(self.px_big), height=icon_size(self.px_big), bg=self.bg, highlightthickness=0, bd=0)
        c.pack(side="left", padx=(0, 12))
        draw_icon(c, icon, px=self.px_big, bright=True)
        tk.Label(top, text=name, bg=self.bg, fg=T["win_fg"], font=self.font(18, "bold"), anchor="w").pack(side="left")
        if label != name:
            tk.Label(top, text=label[len(name):].strip(" ()"), bg="#f59e0b", fg="#111827", font=self.font(8, "bold"),
                     padx=8, pady=2).pack(side="left", padx=12)
        if help_text:
            tk.Label(self.header, text=help_text, bg=self.bg, fg=T["muted"], font=self.font(10), anchor="w",
                     justify="left").pack(fill="x", pady=(4, 0))

    def _render_search(self, pages, query):
        T = core.T
        tk.Label(self.header, text="Search", bg=self.bg, fg=T["win_fg"], font=self.font(18, "bold"), anchor="w"
                 ).pack(fill="x")
        found = 0
        for name, icon, _h, entries, _label in pages:
            hits = [(path, e) for path, e in flatten(entries)
                    if query in " ".join([clean(e["label"]), e.get("help", ""), *path]).lower()]
            if not hits:
                continue
            found += len(hits)
            self._section_heading(self.body, {"label": name, "icon": icon})
            card = self._card(self.body)
            for path, e in hits:
                self._row(card, e, where=" › ".join(path))
        if not found:
            tk.Label(self.body, text=f"Nothing matches “{self.query.get().strip()}”.", bg=self.bg,
                     fg=T["muted"], font=self.font(10), anchor="w").pack(fill="x", padx=28, pady=12)

    def _entries(self, parent, entries, depth, card=None):
        """Lay out a menu level: depth 0 = the page (cards and section headings), 1 = a card per submenu,
        2+ = rows inside that card (deeper submenus become drop-down rows)."""
        for e in entries:
            if e is None:
                if depth >= 2 and card is not None:
                    card._gap = True  # a thicker divider before the next row
                else:
                    card = None
                continue
            if e.get("settings"):
                continue
            sub = e.get("submenu")
            if sub is not None and not e.get("choice") and depth == 0:
                card = None
                self._section_heading(parent, e)
                self._entries(parent, sub, 1)
            elif sub is not None and not e.get("choice") and depth == 1:
                card = None
                group = self._card(parent)
                self._group_header(group, e)
                self._entries(group, sub, 2, card=group)
            else:
                if card is None:
                    card = self._card(parent)
                self._row(card, e)

    def _section_heading(self, parent, e):
        T = core.T
        f = tk.Frame(parent, bg=self.bg)
        f.pack(fill="x", padx=28, pady=(18, 6))
        if e.get("icon"):
            c = tk.Canvas(f, width=icon_size(self.px), height=icon_size(self.px), bg=self.bg, highlightthickness=0, bd=0)
            c.pack(side="left", padx=(0, 8))
            draw_icon(c, e["icon"], px=self.px)
        tk.Label(f, text=page_name(clean(e["label"])).upper(), bg=self.bg, fg=T["muted"], font=self.font(9, "bold"),
                 anchor="w").pack(side="left")
        if e.get("help"):
            lab = tk.Label(parent, text=e["help"], bg=self.bg, fg=T["muted"], font=self.font(9), anchor="w",
                           justify="left")
            lab.pack(fill="x", padx=28, pady=(0, 6))
            self._wrap.append((lab, 60))

    def _card(self, parent):
        T = core.T
        card = tk.Frame(parent, bg=self.bg, highlightthickness=1, highlightbackground=T["border"],
                        highlightcolor=T["border"])
        card.pack(fill="x", padx=28, pady=6)
        card._rows, card._gap = 0, False
        return card

    def _group_header(self, card, e):
        T = core.T
        row = tk.Frame(card, bg=T["code_bg"])
        row.pack(fill="x")
        if e.get("icon"):
            c = tk.Canvas(row, width=icon_size(self.px), height=icon_size(self.px), bg=T["code_bg"], highlightthickness=0, bd=0)
            c.pack(side="left", padx=(14, 0), pady=8)
            draw_icon(c, e["icon"], px=self.px)
        text = tk.Frame(row, bg=T["code_bg"])
        text.pack(side="left", fill="x", expand=True, padx=14, pady=8)
        tk.Label(text, text=clean(e["label"]), bg=T["code_bg"], fg=T["win_fg"], font=self.font(10, "bold"),
                 anchor="w").pack(fill="x")
        if e.get("help"):
            lab = tk.Label(text, text=e["help"], bg=T["code_bg"], fg=T["muted"], font=self.font(9), anchor="w",
                           justify="left")
            lab.pack(fill="x")
            self._wrap.append((lab, 120))
        card._rows = 1

    def _row(self, card, e, where=None):
        """One setting: a switch (checked items), a pick-one list (choice), a drop-down (deeper submenus),
        a button row (actions) or plain info (disabled items without an action)."""
        T = core.T
        if card._rows:
            tk.Frame(card, bg=T["border"], height=2 if card._gap else 1).pack(fill="x", padx=0 if card._gap else 14)
        card._rows += 1
        card._gap = False
        enabled = e.get("enabled", True)
        sub = e.get("submenu")
        kind = ("slider" if e.get("slider") else "choice" if e.get("choice") else "dropdown" if sub is not None else "toggle" if "checked" in e
                else "action" if e.get("action") else "info")
        if not enabled and kind != "dropdown":
            kind = "info" if kind == "action" or not e.get("action") else kind
        row = tk.Frame(card, bg=self.bg)
        row.pack(fill="x")
        ctl = tk.Frame(row, bg=self.bg)  # packed first so long explanations never squeeze the switch / chevron
        ctl.pack(side="right", padx=14)
        text = tk.Frame(row, bg=self.bg)
        text.pack(side="left", fill="x", expand=True, padx=14, pady=10)
        fg = (T["menu_disabled"] if not enabled else
              core.themed_color(T["danger"]) if e["label"] in DANGER_LABELS else T["win_fg"])
        tk.Label(text, text=clean(e["label"]), bg=self.bg, fg=fg, font=self.font(10), anchor="w",
                 justify="left").pack(fill="x")
        detail = " — ".join(x for x in (where, e.get("help")) if x)
        if detail:
            lab = tk.Label(text, text=detail, bg=self.bg, fg=T["muted"], font=self.font(9), anchor="w", justify="left")
            lab.pack(fill="x", pady=(1, 0))
            self._wrap.append((lab, 190))

        if kind == "toggle":
            self._switch(ctl, bool(e.get("checked")), enabled).pack()
            if enabled:
                self._clickable(row, lambda _e=None: self.run(e.get("action")), hover=T["btn_active"])
        elif kind == "slider":
            self._slider(text, e["slider"])
        elif kind == "choice":
            pills = tk.Frame(text, bg=self.bg)
            pills.pack(fill="x", pady=(8, 0))
            for opt in sub:
                if opt is None:
                    continue
                on = bool(opt.get("checked"))
                p = tk.Label(pills, text=clean(opt["label"]), font=self.font(9, "bold" if on else "normal"),
                             bg=T["primary"] if on else T["btn_bg"], fg=T["primary_fg"] if on else T["btn_fg"],
                             padx=12, pady=4, highlightthickness=1,
                             highlightbackground=T["primary"] if on else T["border"])
                p.pack(side="left", padx=(0, 6))
                if not on and opt.get("enabled", True) and opt.get("action"):
                    self._clickable(p, lambda _e=None, a=opt["action"]: self.run(a), hover=T["btn_active"])
        elif kind == "dropdown":
            count = sum(1 for x in sub if x is not None)
            tk.Label(ctl, text=f"{count}  ▾" if count else "▾", bg=self.bg, fg=T["muted"], font=self.font(10)).pack()
            if enabled:
                self._clickable(row, lambda ev, s=sub, r=row: self._dropdown(r, s), hover=T["btn_active"])
        elif kind == "action":
            tk.Label(ctl, text="›", bg=self.bg, fg=T["muted"], font=self.font(14)).pack()
            self._clickable(row, lambda _e=None: self.run(e.get("action")), hover=T["btn_active"])

    def _switch(self, parent, on, enabled):
        T = core.T
        z = self.scale
        c = tk.Canvas(parent, width=int(38 * z), height=int(20 * z), bg=self.bg, highlightthickness=0, bd=0)
        track = (T["primary"] if on else T["border"]) if enabled else T["menu_disabled"]

        def oval(x1, y1, x2, y2, fill):
            c.create_oval(x1 * z, y1 * z, x2 * z, y2 * z, fill=fill, outline=fill)
        oval(1, 1, 19, 19, track)
        oval(19, 1, 37, 19, track)
        c.create_rectangle(10 * z, 1 * z, 28 * z, 19 * z, fill=track, outline=track)
        x = 20 if on else 3
        oval(x, 3, x + 15, 18, "#ffffff")
        return c

    def _slider(self, parent, spec):
        """A slider row: {"value", "min", "max", "step", "set": fn(value), "format": fn(value) -> text}. The value
        is applied when the slider is let go (applying it rebuilds the window, which would end the drag)."""
        T = core.T
        fmt = spec.get("format") or (lambda v: f"{v:g}")
        line = tk.Frame(parent, bg=self.bg)
        line.pack(fill="x", pady=(8, 0))
        var = tk.DoubleVar(master=self.win, value=spec["value"])
        shown = tk.Label(line, text=fmt(spec["value"]), bg=self.bg, fg=T["win_fg"], font=self.font(10, "bold"),
                         width=6, anchor="e")
        scale = tk.Scale(line, from_=spec["min"], to=spec["max"], resolution=spec.get("step", 0.1), orient="horizontal",
                         variable=var, showvalue=False, length=int(260 * self.scale), sliderlength=int(18 * self.scale),
                         width=int(10 * self.scale), bg=self.bg, troughcolor=T["entry_bg"], activebackground=T["primary"],
                         highlightthickness=0, bd=0, relief="flat", command=lambda v: shown.configure(text=fmt(float(v))))
        scale.pack(side="left")
        shown.pack(side="left", padx=(10, 0))

        def commit(_e=None):
            if float(var.get()) != spec["value"]:
                self.run(lambda: spec["set"](float(var.get())))
        scale.bind("<ButtonRelease-1>", commit)
        scale.bind("<KeyRelease>", commit)

    def _dropdown(self, widget, entries):
        x, y = widget.winfo_rootx() + widget.winfo_width() - 220, widget.winfo_rooty() + widget.winfo_height()
        if core.NATIVE_WIN_MENU:
            try:
                self.run(core.popup_native_menu(widget, entries, x, y))
                return
            except Exception:
                pass
        menu = tk.Menu(self.win, tearoff=0)
        core.fill_menu(menu, entries)
        try:
            core.popup_menu(menu, x, y)
        finally:
            menu.grab_release()
            self.win.after(150, self.refresh)

    def _clickable(self, row, command, hover=None):
        """The whole row reacts: hand cursor, hover colour, click anywhere on it."""
        widgets = [row] + self._descendants(row)
        base = str(row.cget("bg"))
        own = {}  # each widget's own colour: only those in the row's colour take the hover tint (pills keep theirs)
        for w in widgets:
            try:
                own[w] = str(w.cget("bg"))
            except tk.TclError:
                pass

        def paint(on):
            for w, color in own.items():
                try:
                    w.configure(bg=hover if on and color == base else color)
                except tk.TclError:
                    pass

        def leave(_e):
            # Moving between the row's own label / switch / chevron also sends Leave: only un-tint when the pointer
            # has really left the row
            try:
                x, y = row.winfo_pointerxy()
                under = row.winfo_containing(x, y)
            except (tk.TclError, KeyError):
                under = None
            inside = under is not None and (str(under) == str(row) or str(under).startswith(str(row) + "."))
            if not inside:
                paint(False)
        for w in widgets:
            w.configure(cursor="hand2")
            w.bind("<Button-1>", command)
            if hover:
                w.bind("<Enter>", lambda e: paint(True))
                w.bind("<Leave>", leave)

    def _descendants(self, w):
        out = []
        for child in w.winfo_children():
            out.append(child)
            out.extend(self._descendants(child))
        return out
