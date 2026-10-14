"""The app's menus drawn from the shared menu spec: Tk menus (all platforms) and the native Win32 popup menu on
Windows, used by the pet's right-click menu and the AIPet menu window."""
import os
import sys
import tkinter as tk

from .. import geometry
from . import theme

IS_MAC = sys.platform == "darwin"


def fill_menu(menu, entries):
    """Render the shared app menu spec in Tk, including native macOS menus."""
    menu.delete(0, "end")
    for child in menu.winfo_children():
        child.destroy()
    menu._check_vars = []
    for entry in entries:
        if entry is None:
            menu.add_separator()
            continue
        options = dict(label=entry["label"], state="normal" if entry.get("enabled", True) else "disabled")
        if entry.get("submenu") is not None:
            sub = tk.Menu(menu, tearoff=0)
            fill_menu(sub, entry["submenu"])
            menu.add_cascade(menu=sub, **options)
        elif "checked" in entry:
            var = tk.BooleanVar(master=menu, value=entry["checked"])
            menu._check_vars.append(var)
            menu.add_checkbutton(variable=var, command=entry.get("action"), **options)
        else:
            menu.add_command(command=entry.get("action"), **options)
    theme.style_menu(menu)


def popup_menu(menu, x, y):
    """Move a context menu up when it would otherwise scroll at the screen edge."""
    menu.update_idletasks()
    ref = (x, y)
    height = menu.winfo_reqheight()
    bounds = geometry.work_area(x, y, menu)
    if bounds and y + height + 8 > bounds[3]:
        y -= height + 8
    x, y = geometry.fit_on_screen(x, y, menu.winfo_reqwidth(), height, ref, menu, margin=8)
    menu.tk_popup(x, y)


# --------------------------------------------------------------------------- native context menu (Windows)
# Tk draws its Windows menu items itself (classic look). Building a real Win32 menu instead lets Windows draw it, so the
# pet's right-click menu looks like the tray menu and every other Windows 11 menu (rounded corners, shadow, spacing).
NATIVE_WIN_MENU = os.name == "nt"
MF_GRAYED, MF_CHECKED, MF_POPUP, MF_SEPARATOR = 0x1, 0x8, 0x10, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD, GA_ROOT, WM_NULL = 0x2, 0x100, 2, 0
_WIN_MENU_API = None


def _win_menu_api():
    """A private user32 handle, so these prototypes never clash with pystray's or _user32()'s."""
    global _WIN_MENU_API
    if _WIN_MENU_API is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.CreatePopupMenu.argtypes = []
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        u.AppendMenuW.restype = wintypes.BOOL
        u.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
        u.TrackPopupMenuEx.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       ctypes.c_void_p]
        u.TrackPopupMenuEx.restype = ctypes.c_int  # with TPM_RETURNCMD: the chosen id, 0 when dismissed
        u.DestroyMenu.argtypes = [wintypes.HMENU]
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        _WIN_MENU_API = u
    return _WIN_MENU_API


def build_win_menu(u, entries, actions):
    """Turn the shared menu spec into an HMENU. Command id N runs actions[N - 1]."""
    hmenu = u.CreatePopupMenu()
    if not hmenu:
        raise OSError("CreatePopupMenu failed")
    try:
        for entry in entries:
            if entry is None:
                u.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                continue
            label = str(entry["label"]).replace("&", "&&")  # a single & would underline the next letter
            flags = 0 if entry.get("enabled", True) else MF_GRAYED
            if entry.get("submenu") is not None:
                sub = build_win_menu(u, entry["submenu"], actions)
                if not u.AppendMenuW(hmenu, flags | MF_POPUP, sub, label):
                    u.DestroyMenu(sub)
                    raise OSError("AppendMenuW failed")
                continue
            if entry.get("checked"):
                flags |= MF_CHECKED
            actions.append(entry.get("action"))
            if not u.AppendMenuW(hmenu, flags, len(actions), label):
                raise OSError("AppendMenuW failed")
            if entry.get("default"):
                u.SetMenuDefaultItem(hmenu, len(actions), 0)
    except Exception:
        u.DestroyMenu(hmenu)  # also frees the submenus already attached
        raise
    return hmenu


def popup_native_menu(widget, entries, x, y, api=None):
    """Show the menu spec as a Win32 menu at (x, y). Returns the chosen action, or None when dismissed."""
    u = api or _win_menu_api()
    actions = []
    hmenu = build_win_menu(u, entries, actions)
    try:
        hwnd = u.GetAncestor(widget.winfo_id(), GA_ROOT) or widget.winfo_id()
        u.SetForegroundWindow(hwnd)  # without this, clicking elsewhere does not close the menu
        chosen = u.TrackPopupMenuEx(hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON, int(x), int(y), hwnd, None)
        u.PostMessageW(hwnd, WM_NULL, 0, 0)  # documented fix: lets the next menu open on the first click
    finally:
        u.DestroyMenu(hmenu)
    return actions[chosen - 1] if 0 < chosen <= len(actions) else None
