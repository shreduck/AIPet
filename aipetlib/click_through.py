"""ClickThroughController: rendering and interaction, with explicit desktop services.

This component does not import the application entry point.
"""
import os


class ClickThroughController:
    """Lets mouse clicks pass through the pet window to whatever is behind it, or not. Windows: the WS_EX_TRANSPARENT
    style on the (already layered) window. macOS: NSWindow.ignoresMouseEvents through the Objective-C runtime (no
    extra packages). Elsewhere: does nothing."""

    def __init__(self, services, root):
        self.services = services
        self.root = root
        self._win = None

    def set(self, through):
        if os.name == "nt":
            self._set_windows(through)
        elif self.services.IS_MAC:
            self._set_mac(through)

    def _set_windows(self, through):
        import ctypes
        u = ctypes.windll.user32
        hwnd = int(self.root.wm_frame(), 16)
        u.GetWindowLongW.restype = ctypes.c_long
        ex = u.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        new = (ex | 0x20 | 0x80000) if through else (ex & ~0x20)  # WS_EX_TRANSPARENT (+ WS_EX_LAYERED)
        if new != ex:
            u.SetWindowLongW(hwnd, -20, ctypes.c_long(new))

    def _objc(self):
        import ctypes
        import ctypes.util
        if not hasattr(self, "_lib"):
            self._lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
            self._lib.objc_getClass.restype = ctypes.c_void_p
            self._lib.sel_registerName.restype = ctypes.c_void_p
        return self._lib

    def _send(self, obj, sel, *args, restype=None, argtypes=()):
        import ctypes
        lib = self._objc()
        f = lib.objc_msgSend
        f.restype = restype
        f.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + list(argtypes)
        return f(obj, lib.sel_registerName(sel.encode()), *args)

    def _set_mac(self, through):
        import ctypes
        if not self._win:
            app = self._send(self._objc().objc_getClass(b"NSApplication"), "sharedApplication", restype=ctypes.c_void_p)
            wins = self._send(app, "windows", restype=ctypes.c_void_p)
            n = self._send(wins, "count", restype=ctypes.c_ulong)
            title = self.root.title()
            for i in range(n):
                w = self._send(wins, "objectAtIndex:", i, restype=ctypes.c_void_p, argtypes=[ctypes.c_ulong])
                t = self._send(w, "title", restype=ctypes.c_void_p)
                name = self._send(t, "UTF8String", restype=ctypes.c_char_p) if t else None
                if name and name.decode("utf-8", "replace") == title:
                    self._win = w
                    break
            if not self._win:
                raise RuntimeError("pet window not found")
        self._send(self._win, "setIgnoresMouseEvents:", bool(through), argtypes=[ctypes.c_bool])
