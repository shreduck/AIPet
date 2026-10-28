"""Shared desktop themes, widget styling and modal dialogs.

This module owns the active theme (T, mutated in place by set_theme so every importer sees a switch), the theme
palettes, the console UI font and the list of live themed windows. It imports no application entry point; dialogs
are placed on their monitor with aipetlib.geometry.
"""
import os
import sys
import tkinter as tk
import weakref

from .. import geometry

IS_MAC = sys.platform == "darwin"
# Every AIPet window uses a console font, like the pet's own name tags and tooltips
UI_FONT = "Consolas" if os.name == "nt" else ("Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono")
INK = "#1f2937"

# Colours of what the pet draws around the creatures (name tags, bubbles, tooltips). Light is the default.
THEMES = {
    "light": {"tag_bg": "#ffffff", "tag_fg": "#111827", "label_dark": True,
              "bubble_bg": "#ffffff", "bubble_fg": "#111827", "bubble_msg": "#374151", "bubble_muted": "#6b7280",
              "tip_bg": "#ffffff", "tip_fg": "#111827", "tip_border": "#cbd5e1", "tag_outline": "#111827",
              "code_bg": "#f3f4f6", "code_fg": "#111827",
              # windows, dialogs and menus
              "win_bg": "#ffffff", "win_fg": "#111827", "muted": "#6b7280", "border": "#cbd5e1",
              "btn_bg": "#f3f4f6", "btn_fg": "#111827", "btn_active": "#e5e7eb",
              "entry_bg": "#f3f4f6", "entry_fg": "#111827", "select_bg": "#dbeafe",
              "primary": "#2563eb", "primary_active": "#1d4ed8", "primary_fg": "#ffffff",
              "danger": "#dc2626", "danger_active": "#b91c1c",
              "menu_bg": "#ffffff", "menu_fg": "#111827", "menu_active_bg": "#e5e7eb", "menu_active_fg": "#111827",
              "menu_disabled": "#9ca3af"},
    "dark": {"tag_bg": INK, "tag_fg": "#ffffff", "label_dark": False,
             "bubble_bg": "#111827", "bubble_fg": "#ffffff", "bubble_msg": "#e5e7eb", "bubble_muted": "#9ca3af",
             "tip_bg": "#111827", "tip_fg": "#f9fafb", "tip_border": "#111827", "tag_outline": "#0b1220",
             "code_bg": "#0b1220", "code_fg": "#e5e7eb",
             "win_bg": "#111827", "win_fg": "#f9fafb", "muted": "#9ca3af", "border": "#374151",
             "btn_bg": "#1f2937", "btn_fg": "#f9fafb", "btn_active": "#374151",
             "entry_bg": "#0b1220", "entry_fg": "#e5e7eb", "select_bg": "#1e3a8a",
             "primary": "#3b82f6", "primary_active": "#2563eb", "primary_fg": "#ffffff",
             "danger": "#ef4444", "danger_active": "#dc2626",
             "menu_bg": "#1f2937", "menu_fg": "#f9fafb", "menu_active_bg": "#374151", "menu_active_fg": "#ffffff",
             "menu_disabled": "#6b7280"},
}
# Fixed status colours used in the windows (green / amber / red notes) and their readable versions on a dark background
DARK_TEXT = {"#15803d": "#4ade80", "#b45309": "#fbbf24", "#b91c1c": "#f87171", "#dc2626": "#f87171",
             "#d97706": "#fbbf24"}
T = {}  # the active theme; set_theme() mutates it in place so every module sees the change

# --------------------------------------------------------------------------- themed windows, dialogs and menus
# The pet's own drawings use T directly. Ordinary Tk windows (setup, sliders, Cowork help...) are styled after they are
# built by theme_window(), which also remembers them so a theme switch restyles them live. Colours a window set on
# purpose (green "installed", amber "out of date"...) are kept, brightened on the dark background.
THEMED = []
OPEN_DIALOGS = weakref.WeakSet()  # defer optional startup offers without grabbing application input
_DEFAULT_FG = {"", "black", "#000000", "systembuttontext", "systemwindowtext", "systemmenutext", "#111827",
               "systemtextcolor", "systemlabelcolor", "systemcontroltextcolor"}
MUTED_FG = {"#6b7280", "grey", "gray"}
DIALOG_ACCENTS = {"info": "#5b8def", "question": "#5b8def", "warning": "#f59e0b", "error": "#ef4444"}


def set_theme(name):
    name = name if name in THEMES else "light"
    T.clear()
    T.update(THEMES[name], name=name)


def _fg_role(w, opt="fg"):
    roles = w.__dict__.setdefault("_pet_roles", {})
    if opt not in roles:
        try:
            v = str(w.cget(opt))
        except tk.TclError:
            return T["win_fg"]
        low = v.lower()
        roles[opt] = "fg" if low in _DEFAULT_FG else ("muted" if low in MUTED_FG else "keep:" + v)
    role = roles[opt]
    if role == "fg":
        return T["win_fg"]
    if role == "muted":
        return T["muted"]
    c = role[5:]
    return DARK_TEXT.get(c.lower(), c) if T.get("name") == "dark" else c


def themed_color(c):
    """A status colour readable on the current theme's window background."""
    return DARK_TEXT.get(c.lower(), c) if T.get("name") == "dark" else c


def button_style(kind="secondary"):
    """Options for primary, danger, soft orange warning, or secondary buttons."""
    if IS_MAC:
        # Aqua draws the button face itself and ignores our filled background.
        # Tk 8.6's Aqua face stays light even when systemButtonText resolves to
        # white in macOS dark appearance. Use dark ink on that native face.
        return dict(fg="#111827", activeforeground="#111827", disabledforeground="#6b7280",
                    bg=T["win_bg"], highlightbackground=T["win_bg"],
                    activebackground=T["win_bg"], cursor="hand2", padx=14, pady=4,
                    font=(UI_FONT, 9, "bold") if kind in ("primary", "danger") else (UI_FONT, 9))
    if kind == "primary":
        return dict(bg=T["primary"], fg=T["primary_fg"], activebackground=T["primary_active"],
                    activeforeground=T["primary_fg"], relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                    padx=14, pady=4, font=(UI_FONT, 9, "bold"))
    if kind == "danger":
        return dict(bg=T["danger"], fg="#ffffff", activebackground=T["danger_active"], activeforeground="#ffffff",
                    relief="flat", bd=0, highlightthickness=0, cursor="hand2", padx=14, pady=4,
                    font=(UI_FONT, 9, "bold"))
    if kind == 'warning':
        return dict(bg='#ffedd5', fg='#9a3412', activebackground='#fed7aa', activeforeground='#7c2d12',
                    relief='flat', bd=0, highlightthickness=0, cursor='hand2', padx=14, pady=4,
                    font=(UI_FONT, 9, 'bold'))
    return dict(bg=T["btn_bg"], fg=T["btn_fg"], activebackground=T["btn_active"], activeforeground=T["btn_fg"],
                relief="flat", bd=0, highlightthickness=1, highlightbackground=T["border"], highlightcolor=T["border"],
                cursor="hand2", padx=12, pady=3, disabledforeground=T["menu_disabled"], font=(UI_FONT, 9))


def style_menu(menu):
    """Colour a Tk menu and its submenus like the theme (Windows and Linux; macOS draws menus natively)."""
    try:
        menu.configure(bg=T["menu_bg"], fg=T["menu_fg"], activebackground=T["menu_active_bg"],
                       activeforeground=T["menu_active_fg"], disabledforeground=T["menu_disabled"],
                       selectcolor=T["menu_fg"], relief="flat", bd=1, activeborderwidth=0)
    except tk.TclError:
        return
    for child in menu.winfo_children():
        if isinstance(child, tk.Menu):
            style_menu(child)


def _ttk_scale_style(widget):
    try:
        from tkinter import ttk
        s = ttk.Style(widget)
        s.configure("Pet.Horizontal.TScale", background=T["win_bg"], troughcolor=T["entry_bg"])
        return "Pet.Horizontal.TScale"
    except Exception:
        return None


def theme_widget(w):
    """Style one widget and everything inside it."""
    cls, bg = w.winfo_class(), T["win_bg"]
    try:
        if cls in ("Frame", "Toplevel", "Tk", "Canvas"):
            w.configure(bg=bg)
        elif cls == "Labelframe":
            w.configure(bg=bg, fg=_fg_role(w), highlightbackground=T["border"])
        elif cls == "Label":
            w.configure(bg=bg, fg=_fg_role(w))
        elif cls == "Button":
            kind = w.__dict__.setdefault("_pet_kind", "primary" if str(w.cget("default")) == "active" else "secondary")
            opts = button_style(kind)
            if str(w.cget("font")) != "TkDefaultFont":
                opts.pop("font")  # keep a font the window chose itself (or the one set on an earlier pass)
            w.configure(**opts)
        elif cls in ("Checkbutton", "Radiobutton"):
            fg = _fg_role(w)
            w.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, selectcolor=T["entry_bg"],
                        disabledforeground=T["menu_disabled"], highlightthickness=0)
        elif cls == "Entry":
            w.configure(bg=T["entry_bg"], readonlybackground=T["entry_bg"], fg=T["entry_fg"],
                        insertbackground=T["entry_fg"], relief="flat", highlightthickness=1,
                        highlightbackground=T["border"], highlightcolor=T["primary"])
        elif cls == "Text":
            w.configure(bg=T["entry_bg"], fg=T["entry_fg"], insertbackground=T["entry_fg"], relief="flat",
                        highlightthickness=1, highlightbackground=T["border"], highlightcolor=T["primary"])
        elif cls == "Scale":
            w.configure(bg=bg, fg=T["win_fg"], troughcolor=T["entry_bg"], activebackground=T["primary"],
                        highlightthickness=0)
        elif cls == "TScale":
            style = _ttk_scale_style(w)
            if style:
                w.configure(style=style)
    except tk.TclError:
        pass
    for child in w.winfo_children():
        if isinstance(child, tk.Menu):
            style_menu(child)
        else:
            theme_widget(child)
    retheme = getattr(w, '_pet_retheme', None)
    if retheme is not None:
        retheme()


def titlebar_theme(win):
    """Windows 10/11: a dark title bar for a window while the dark theme is on."""
    if os.name != "nt":
        return
    try:
        import ctypes
        win.update_idletasks()
        u32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
        u32.GetParent.argtypes, u32.GetParent.restype = [ctypes.c_void_p], ctypes.c_void_p
        hwnd = u32.GetParent(win.winfo_id()) or win.winfo_id()
        val = ctypes.c_int(1 if T.get("name") == "dark" else 0)
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer builds, then 1809 - 1909)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
        u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
        u32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)  # redraw the frame now (NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED)
    except Exception:
        pass


def native_menu_theme():
    """Windows 10 1903+: menus the system draws (the tray icon's menu, menu borders) follow the theme. Uses uxtheme's
    SetPreferredAppMode / FlushMenuThemes (by ordinal, as they are not exported by name); harmless if unavailable."""
    if os.name != "nt":
        return
    try:
        import ctypes
        if sys.getwindowsversion().build < 18362:
            return
        ux = ctypes.WinDLL("uxtheme")
        set_mode = ux[135]
        set_mode.argtypes, set_mode.restype = [ctypes.c_int], ctypes.c_int
        set_mode(2 if T.get("name") == "dark" else 3)  # ForceDark / ForceLight
        ux[136]()  # FlushMenuThemes
    except Exception:
        pass


def use_console_font(root):
    """Widgets that don't name a font (buttons, check boxes, entries, Tk menus...) get the console font too."""
    try:
        from tkinter import font as tkfont
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
                     "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
            tkfont.Font(root=root, name=name, exists=True).configure(family=UI_FONT)
    except Exception:
        pass


def theme_window(win):
    """Style a finished Toplevel like the pet and keep it in step with later theme switches."""
    theme_widget(win)
    titlebar_theme(win)
    if win not in THEMED:
        THEMED.append(win)
        win.bind("<Destroy>", lambda e, w=win: THEMED.remove(w) if e.widget is w and w in THEMED else None, add="+")
    return win


def retheme_all():
    for w in list(THEMED):
        try:
            if w.winfo_exists():
                theme_widget(w)
                titlebar_theme(w)
                w.event_generate('<<AIPetThemeChanged>>', when='tail')
        except tk.TclError:
            pass


def themed_dialog(root, title, text, buttons=(("OK", True, "primary"),), kind="info", heading=None, cancel=None,
                  enter_confirms=True, on_result=None, content_builder=None, validate=None):
    """An independently interactive card in the pet's style, in place of native
    message boxes. buttons: (label, value, 'primary' | 'danger' | 'secondary'), left to right; the first primary or
    danger one is the default (Enter, unless enter_confirms is False: then Enter cancels, for risky choices).
    Escape or closing returns `cancel`. With on_result, deliver the result after
    closing without nesting a wait loop inside a native button callback.
    content_builder(body) adds custom controls; validate(value) may return False to keep the dialog open."""
    accent = DIALOG_ACCENTS.get(kind, DIALOG_ACCENTS["info"])
    bg, fg = T["win_bg"], T["win_fg"]
    w = tk.Toplevel(root)
    # A confirmation belongs to the window that opened it. Topmost alone does
    # not keep it above another topmost window on macOS.
    if root.winfo_viewable():
        w.transient(root.winfo_toplevel())
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    w.configure(bg=accent)
    w.title(title)
    result = {"v": cancel, "closing": False, "validating": False}
    OPEN_DIALOGS.add(w)
    card = tk.Frame(w, bg=bg)
    card.pack(fill='both', expand=True, padx=2, pady=2)
    tk.Frame(card, bg=accent, height=5).pack(fill="x")
    body = tk.Frame(card, bg=bg)
    body.pack(fill="both", expand=True, padx=18, pady=(12, 14))
    top = tk.Frame(body, bg=bg)
    top.pack(fill="x")
    head = tk.Label(top, text=heading or title, bg=bg, fg=fg, anchor="w", font=(UI_FONT, 12, "bold"))
    head.pack(side="left", fill="x", expand=True)
    x = tk.Label(top, text="✕", bg=bg, fg=T["muted"], cursor="hand2", font=(UI_FONT, 11), padx=4)
    x.pack(side="right")
    if kind in ("warning", "error"):
        tk.Label(body, text="warning" if kind == "warning" else "something went wrong", bg=bg, fg=accent, anchor="w",
                 font=(UI_FONT, 9, "bold")).pack(fill="x")
    tk.Frame(body, bg=T["border"], height=1).pack(fill="x", pady=8)
    tk.Label(body, text=text, bg=bg, fg=T["bubble_msg"], justify="left", anchor="w", wraplength=640 if content_builder else 440,
             font=(UI_FONT, 10)).pack(fill="x")
    if content_builder:
        content_builder(body)
    from .window_resize import corner_grip
    corner_grip(w)
    row = tk.Frame(body, bg=bg)
    row.pack(fill="x", pady=(14, 0))

    def finish():
        w.destroy()
        if on_result is not None and root.winfo_exists():
            root.after_idle(lambda: on_result(result["v"]))

    def commit(value):
        if result["closing"]:
            return
        result.update(v=value, closing=True)
        # Let Aqua finish tracking the clicked button before destroying it.
        w.after_idle(finish)

    def validate_choice(value):
        try:
            if not result['closing'] and w.winfo_exists() and validate(value):
                commit(value)
        finally:
            result['validating'] = False

    def done(value):
        if result['closing']:
            return
        if value == cancel or validate is None:
            commit(value)
        elif not result['validating']:
            result['validating'] = True
            # Let native button tracking/focus finish before validation changes layout or focus.
            w.after_idle(lambda: validate_choice(value))

    def destroyed(event):
        if event.widget is w:
            OPEN_DIALOGS.discard(w)
        if event.widget is w and not result["closing"]:
            result["closing"] = True
            if on_result is not None and root.winfo_exists():
                root.after_idle(lambda: on_result(cancel))

    w.bind("<Destroy>", destroyed, add="+")
    w.protocol("WM_DELETE_WINDOW", lambda: done(cancel))

    default = None
    for label, value, style in reversed(list(buttons)):  # packed from the right, so the last one ends up rightmost
        b = tk.Button(row, text=label, command=lambda v=value: done(v), **button_style(style))
        b.pack(side="right", padx=(8, 0))
        if style in ("primary", "danger") and default is None:
            default = value
    if default is None and buttons:
        default = list(buttons)[-1][1]
    x.bind("<Button-1>", lambda e: done(cancel))
    w.bind("<Escape>", lambda e: done(cancel))
    w.bind("<Return>", lambda e: done(default if enter_confirms else cancel))
    drag = {}
    for widget in (top, head):
        widget.bind("<ButtonPress-1>", lambda e: drag.update(d=(e.x_root - w.winfo_x(), e.y_root - w.winfo_y())))
        widget.bind("<B1-Motion>", lambda e: w.geometry(f"+{e.x_root - drag['d'][0]}+{e.y_root - drag['d'][1]}")
                    if "d" in drag else None)
    w.update_idletasks()
    ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
    try:
        ref = (root.winfo_x() + root.winfo_width() // 2, root.winfo_y() + root.winfo_height() // 2)
        left, top_, right, bottom = geometry.work_area(*ref, w) or (0, 0, w.winfo_screenwidth(), w.winfo_screenheight())
    except Exception:
        left, top_, right, bottom = 0, 0, w.winfo_screenwidth(), w.winfo_screenheight()
    w.geometry(geometry.geo(left + (right - left - ww) // 2, max(top_ + 20, top_ + (bottom - top_ - wh) // 3)))
    w.deiconify()
    w.lift()
    w.focus_force()
    if IS_MAC:
        import mac_statusbar
        mac_statusbar.raise_modal(w.title(), root.winfo_toplevel().title())
        w._mouse_bridge = mac_statusbar.PanelMouseBridge(w)
        w.bind("<Destroy>", lambda event: w._mouse_bridge.close() if event.widget is w else None, add="+")
    if on_result is not None:
        return w
    # wait_window pumps Tk events; unlike a grab, it leaves other windows usable.
    root.wait_window(w)
    return result["v"]


set_theme("light")
