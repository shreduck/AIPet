"""Monitor geometry and overlay positioning.

Services are supplied by the compatibility entry point; this module does not import aipet.
"""
import os
import time


def work_area(services, x, y, widget):
    """(left, top, right, bottom) of the monitor nearest to screen point (x, y) (the whole monitor, taskbar included,
    so a pet parked over the taskbar stays there).
    Windows asks the OS (monitors left of / above the main one have negative coordinates). macOS returns None: Tk
    gives no per-monitor geometry there, so callers don't clamp and trust positions that came from a drag.
    Elsewhere (X11) the root window spans every monitor."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
            u = ctypes.windll.user32
            u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            u.MonitorFromPoint.restype = wintypes.HMONITOR
            u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
            mon = u.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)  # MONITOR_DEFAULTTONEAREST
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if mon and u.GetMonitorInfoW(mon, ctypes.byref(mi)):
                r = mi.rcMonitor
                return r.left, r.top, r.right, r.bottom
        except Exception:
            pass
    if services.IS_MAC:
        return services.mac_screen_at(x, y)
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def mac_screen_at(services, x, y):
    """macOS: (left, top, right, bottom) in Tk coordinates of the visible part of the screen holding (x, y) (without
    the menu bar and the Dock, which covers windows), else the nearest one; None if the screens can't be read
    (callers then don't clamp). Read from NSScreen (cached for 3 s)."""
    now = time.time()
    if now - services._MAC_SCREENS["t"] > 3:
        try:
            import mac_statusbar
            services._MAC_SCREENS["v"] = mac_statusbar.screen_rects()
        except Exception as e:
            services._MAC_SCREENS["v"] = []
            services.log_error(f"mac screens: {e!r}")
        services._MAC_SCREENS["t"] = now
    rects = services._MAC_SCREENS["v"]
    if not rects:
        return None

    def dist(r):
        dx = max(r[0] - x, 0, x - r[2])
        dy = max(r[1] - y, 0, y - r[3])
        return dx * dx + dy * dy
    return min(rects, key=dist)


def fit_on_screen(services, x, y, w, h, ref, widget, margin=0):
    """Clamp a w x h window at (x, y) into the monitor holding screen point ref (on macOS: unchanged)."""
    r = services.work_area(ref[0], ref[1], widget)
    if not r:
        return int(x), int(y)
    left, top, right, bottom = r
    return (int(max(left, min(x, right - w - margin))), int(max(top, min(y, bottom - h - margin))))


def geo(services, x, y):
    """Tk geometry offset that also works for negative coordinates (monitors left of / above the main one)."""
    return f"+{int(x)}+{int(y)}"
