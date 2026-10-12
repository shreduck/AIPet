"""Shared desktop themes, widget styling and modal dialogs.

Services are supplied by the compatibility entry point; this module does not import aipet.
"""
import os
import sys
import tkinter as tk


def set_theme(services, name):
    name = name if name in services.THEMES else "light"
    services.T.clear()
    services.T.update(services.THEMES[name], name=name)


def _fg_role(services, w, opt="fg"):
    roles = w.__dict__.setdefault("_pet_roles", {})
    if opt not in roles:
        try:
            v = str(w.cget(opt))
        except tk.TclError:
            return services.T["win_fg"]
        low = v.lower()
        roles[opt] = "fg" if low in services._DEFAULT_FG else ("muted" if low in services.MUTED_FG else "keep:" + v)
    role = roles[opt]
    if role == "fg":
        return services.T["win_fg"]
    if role == "muted":
        return services.T["muted"]
    c = role[5:]
    return services.DARK_TEXT.get(c.lower(), c) if services.T.get("name") == "dark" else c


def themed_color(services, c):
    """A status colour readable on the current theme's window background."""
    return services.DARK_TEXT.get(c.lower(), c) if services.T.get("name") == "dark" else c


def button_style(services, kind="secondary"):
    """Options for a tk.Button in the pet's style: 'primary' (filled), 'danger' or 'secondary' (outlined)."""
    if services.IS_MAC:
        # Aqua draws the button face itself and ignores our filled background.
        # Its matching system text stays readable in both macOS appearances.
        return dict(fg="systemButtonText", activeforeground="systemButtonText",
                    bg=services.T["win_bg"], highlightbackground=services.T["win_bg"],
                    cursor="hand2", padx=14, pady=4,
                    font=(services.UI_FONT, 9, "bold") if kind in ("primary", "danger") else (services.UI_FONT, 9))
    if kind == "primary":
        return dict(bg=services.T["primary"], fg=services.T["primary_fg"], activebackground=services.T["primary_active"],
                    activeforeground=services.T["primary_fg"], relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                    padx=14, pady=4, font=(services.UI_FONT, 9, "bold"))
    if kind == "danger":
        return dict(bg=services.T["danger"], fg="#ffffff", activebackground=services.T["danger_active"], activeforeground="#ffffff",
                    relief="flat", bd=0, highlightthickness=0, cursor="hand2", padx=14, pady=4,
                    font=(services.UI_FONT, 9, "bold"))
    return dict(bg=services.T["btn_bg"], fg=services.T["btn_fg"], activebackground=services.T["btn_active"], activeforeground=services.T["btn_fg"],
                relief="flat", bd=0, highlightthickness=1, highlightbackground=services.T["border"], highlightcolor=services.T["border"],
                cursor="hand2", padx=12, pady=3, disabledforeground=services.T["menu_disabled"], font=(services.UI_FONT, 9))


def style_menu(services, menu):
    """Colour a Tk menu and its submenus like the theme (Windows and Linux; macOS draws menus natively)."""
    try:
        menu.configure(bg=services.T["menu_bg"], fg=services.T["menu_fg"], activebackground=services.T["menu_active_bg"],
                       activeforeground=services.T["menu_active_fg"], disabledforeground=services.T["menu_disabled"],
                       selectcolor=services.T["menu_fg"], relief="flat", bd=1, activeborderwidth=0)
    except tk.TclError:
        return
    for child in menu.winfo_children():
        if isinstance(child, tk.Menu):
            services.style_menu(child)


def _ttk_scale_style(services, widget):
    try:
        from tkinter import ttk
        s = ttk.Style(widget)
        s.configure("Pet.Horizontal.TScale", background=services.T["win_bg"], troughcolor=services.T["entry_bg"])
        return "Pet.Horizontal.TScale"
    except Exception:
        return None


def theme_widget(services, w):
    """Style one widget and everything inside it."""
    cls, bg = w.winfo_class(), services.T["win_bg"]
    try:
        if cls in ("Frame", "Toplevel", "Tk", "Canvas"):
            w.configure(bg=bg)
        elif cls == "Labelframe":
            w.configure(bg=bg, fg=services._fg_role(w), highlightbackground=services.T["border"])
        elif cls == "Label":
            w.configure(bg=bg, fg=services._fg_role(w))
        elif cls == "Button":
            kind = w.__dict__.setdefault("_pet_kind", "primary" if str(w.cget("default")) == "active" else "secondary")
            opts = services.button_style(kind)
            if str(w.cget("font")) != "TkDefaultFont":
                opts.pop("font")  # keep a font the window chose itself (or the one set on an earlier pass)
            w.configure(**opts)
        elif cls in ("Checkbutton", "Radiobutton"):
            fg = services._fg_role(w)
            w.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, selectcolor=services.T["entry_bg"],
                        disabledforeground=services.T["menu_disabled"], highlightthickness=0)
        elif cls == "Entry":
            w.configure(bg=services.T["entry_bg"], readonlybackground=services.T["entry_bg"], fg=services.T["entry_fg"],
                        insertbackground=services.T["entry_fg"], relief="flat", highlightthickness=1,
                        highlightbackground=services.T["border"], highlightcolor=services.T["primary"])
        elif cls == "Text":
            w.configure(bg=services.T["entry_bg"], fg=services.T["entry_fg"], insertbackground=services.T["entry_fg"], relief="flat",
                        highlightthickness=1, highlightbackground=services.T["border"], highlightcolor=services.T["primary"])
        elif cls == "Scale":
            w.configure(bg=bg, fg=services.T["win_fg"], troughcolor=services.T["entry_bg"], activebackground=services.T["primary"],
                        highlightthickness=0)
        elif cls == "TScale":
            style = services._ttk_scale_style(w)
            if style:
                w.configure(style=style)
    except tk.TclError:
        pass
    for child in w.winfo_children():
        if isinstance(child, tk.Menu):
            services.style_menu(child)
        else:
            services.theme_widget(child)


def titlebar_theme(services, win):
    """Windows 10/11: a dark title bar for a window while the dark theme is on."""
    if os.name != "nt":
        return
    try:
        import ctypes
        win.update_idletasks()
        u32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
        u32.GetParent.argtypes, u32.GetParent.restype = [ctypes.c_void_p], ctypes.c_void_p
        hwnd = u32.GetParent(win.winfo_id()) or win.winfo_id()
        val = ctypes.c_int(1 if services.T.get("name") == "dark" else 0)
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer builds, then 1809 - 1909)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
        u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
        u32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)  # redraw the frame now (NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED)
    except Exception:
        pass


def native_menu_theme(services):
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
        set_mode(2 if services.T.get("name") == "dark" else 3)  # ForceDark / ForceLight
        ux[136]()  # FlushMenuThemes
    except Exception:
        pass


def use_console_font(services, root):
    """Widgets that don't name a font (buttons, check boxes, entries, Tk menus...) get the console font too."""
    try:
        from tkinter import font as tkfont
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
                     "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
            tkfont.Font(root=root, name=name, exists=True).configure(family=services.UI_FONT)
    except Exception:
        pass


def theme_window(services, win):
    """Style a finished Toplevel like the pet and keep it in step with later theme switches."""
    services.theme_widget(win)
    services.titlebar_theme(win)
    if win not in services.THEMED:
        services.THEMED.append(win)
        win.bind("<Destroy>", lambda e, w=win: services.THEMED.remove(w) if e.widget is w and w in services.THEMED else None, add="+")
    return win


def retheme_all(services):
    for w in list(services.THEMED):
        try:
            if w.winfo_exists():
                services.theme_widget(w)
                services.titlebar_theme(w)
        except tk.TclError:
            pass


def themed_dialog(services, root, title, text, buttons=(("OK", True, "primary"),), kind="info", heading=None, cancel=None,
                  enter_confirms=True, on_result=None):
    """A modal message card in the pet's style (accent border, bold heading, flat buttons), in place of the native
    message boxes. buttons: (label, value, 'primary' | 'danger' | 'secondary'), left to right; the first primary or
    danger one is the default (Enter, unless enter_confirms is False: then Enter cancels, for risky choices).
    Escape or closing returns `cancel`. With on_result, deliver the result after
    closing without nesting a wait loop inside a native button callback."""
    previous_focus, previous_grab = root.focus_get(), root.grab_current()
    previous_grab_status = previous_grab.grab_status() if previous_grab is not None else None
    accent = services.DIALOG_ACCENTS.get(kind, services.DIALOG_ACCENTS["info"])
    bg, fg = services.T["win_bg"], services.T["win_fg"]
    w = tk.Toplevel(root)
    # A modal card belongs to the window that opened it. Topmost alone does
    # not keep it above another topmost window on macOS.
    if root.winfo_viewable():
        w.transient(root.winfo_toplevel())
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    w.configure(bg=accent)
    w.title(title)
    result = {"v": cancel, "closing": False, "restored": False}
    card = tk.Frame(w, bg=bg)
    card.pack(padx=2, pady=2)
    tk.Frame(card, bg=accent, height=5).pack(fill="x")
    body = tk.Frame(card, bg=bg)
    body.pack(fill="both", padx=18, pady=(12, 14))
    top = tk.Frame(body, bg=bg)
    top.pack(fill="x")
    head = tk.Label(top, text=heading or title, bg=bg, fg=fg, anchor="w", font=(services.UI_FONT, 12, "bold"))
    head.pack(side="left", fill="x", expand=True)
    x = tk.Label(top, text="✕", bg=bg, fg=services.T["muted"], cursor="hand2", font=(services.UI_FONT, 11), padx=4)
    x.pack(side="right")
    if kind in ("warning", "error"):
        tk.Label(body, text="warning" if kind == "warning" else "something went wrong", bg=bg, fg=accent, anchor="w",
                 font=(services.UI_FONT, 9, "bold")).pack(fill="x")
    tk.Frame(body, bg=services.T["border"], height=1).pack(fill="x", pady=8)
    tk.Label(body, text=text, bg=bg, fg=services.T["bubble_msg"], justify="left", anchor="w", wraplength=440,
             font=(services.UI_FONT, 10)).pack(fill="x")
    row = tk.Frame(body, bg=bg)
    row.pack(fill="x", pady=(14, 0))

    def restore_input():
        if result["restored"]:
            return
        result["restored"] = True
        try:
            w.grab_release()
        except tk.TclError:
            pass
        try:
            if previous_grab is not None and previous_grab.winfo_exists():
                previous_grab.grab_set_global() if previous_grab_status == "global" else previous_grab.grab_set()
            if previous_focus is not None and previous_focus.winfo_exists():
                previous_focus.focus_set()
        except tk.TclError:
            pass

    def finish():
        restore_input()
        w.destroy()
        if on_result is not None and root.winfo_exists():
            root.after_idle(lambda: on_result(result["v"]))

    def done(value):
        if result["closing"]:
            return
        result.update(v=value, closing=True)
        # Let Aqua finish tracking the clicked button before destroying it.
        w.after_idle(finish)

    def destroyed(event):
        if event.widget is w and not result["closing"]:
            result["closing"] = True
            restore_input()
            if on_result is not None and root.winfo_exists():
                root.after_idle(lambda: on_result(cancel))

    w.bind("<Destroy>", destroyed, add="+")
    w.protocol("WM_DELETE_WINDOW", lambda: done(cancel))

    default = None
    for label, value, style in reversed(list(buttons)):  # packed from the right, so the last one ends up rightmost
        b = tk.Button(row, text=label, command=lambda v=value: done(v), **services.button_style(style))
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
        left, top_, right, bottom = services.work_area(*ref, w) or (0, 0, w.winfo_screenwidth(), w.winfo_screenheight())
    except Exception:
        left, top_, right, bottom = 0, 0, w.winfo_screenwidth(), w.winfo_screenheight()
    w.geometry(services.geo(left + (right - left - ww) // 2, max(top_ + 20, top_ + (bottom - top_ - wh) // 3)))
    w.deiconify()
    w.lift()
    w.focus_force()
    if services.IS_MAC:
        import mac_statusbar
        mac_statusbar.raise_modal(w.title(), root.winfo_toplevel().title())
        w._mouse_bridge = mac_statusbar.PanelMouseBridge(w)
        w.bind("<Destroy>", lambda event: w._mouse_bridge.close() if event.widget is w else None, add="+")
    try:
        w.grab_set()
    except tk.TclError:
        pass
    if on_result is not None:
        return w
    try:
        root.wait_window(w)
    finally:
        restore_input()
    return result["v"]
