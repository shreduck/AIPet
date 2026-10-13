"""Window-local macOS trackpad input for the agent graph, without PyObjC.

Native callbacks only queue input. Widget lookup and graph changes happen on
the Tk thread, outside Cocoa's event callback. Other windows, logs and
controls retain their normal native/Tk input paths.
"""
import ctypes
import tkinter as tk

import mac_statusbar as mac
from .gestures import GestureBatch, over_graph

SCROLL, MAGNIFY = 22, 30  # NSEventTypeScrollWheel / NSEventTypeMagnify
FRAME_MS = 16
_FILTER = None


class _TkEventFilter:
    """Intercept before Tk 8.6 converts wheel events, then restore on last close.

    AppKit local monitors run in sendEvent:, after Tk has already queued its
    MouseWheel event. Hook tkProcessEvent: instead so accepted gestures are
    handled once. Every other event goes through the original implementation.
    """
    def __init__(self):
        lib = mac._lib
        lib.class_getInstanceMethod.restype, lib.class_getInstanceMethod.argtypes = mac.c_void_p, [mac.c_void_p, mac.c_void_p]
        lib.method_getImplementation.restype, lib.method_getImplementation.argtypes = mac.c_void_p, [mac.c_void_p]
        lib.method_setImplementation.restype, lib.method_setImplementation.argtypes = mac.c_void_p, [mac.c_void_p, mac.c_void_p]
        app = mac.send(mac.cls("NSApplication"), "sharedApplication")
        self.method = lib.class_getInstanceMethod(mac.send(app, "class"), mac.sel("tkProcessEvent:"))
        if not self.method:
            raise RuntimeError("Tk native event filter is unavailable")
        self.handlers = []
        self.installed = False
        self._prototype = ctypes.CFUNCTYPE(mac.c_void_p, mac.c_void_p, mac.c_void_p, mac.c_void_p)
        self._callback = self._prototype(self._dispatch)
        self.address = ctypes.cast(self._callback, mac.c_void_p).value

    def add(self, handler):
        if not self.installed:
            self.original_address = mac._lib.method_getImplementation(self.method)
            self._original = self._prototype(self.original_address)
            mac._lib.method_setImplementation(self.method, self.address)
            self.installed = True
        self.handlers.append(handler)

    def _dispatch(self, app, command, event):
        try:
            if mac.send(event, "type", restype=mac.c_ulong) in (SCROLL, MAGNIFY):
                for handler in tuple(self.handlers):
                    if handler._event(None, event) is None:
                        return None
        except Exception:
            pass  # preserve Tk's normal path if the optional gesture path fails
        return self._original(app, command, event)

    def remove(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)
        if not self.handlers and self.installed:
            # Do not overwrite another component's later hook. Retain our
            # callback in that case: its chain may still reference it.
            if mac._lib.method_getImplementation(self.method) == self.address:
                mac._lib.method_setImplementation(self.method, self.original_address)
                self.installed = False


def _event_filter():
    global _FILTER
    if _FILTER is None:
        _FILTER = _TkEventFilter()
    return _FILTER


class MacGraphGestures:
    def __init__(self, owner, log):
        self.owner, self.win, self.log = owner, owner.win, log
        self.window = self._filter = self._timer = None
        self._closed = False
        self._blocked = False
        self._dirty = self._resolve_pending = True
        self._rect, self._exclusions = None, []
        self._error = None
        self._bindings = []
        self._batch = GestureBatch()
        try:
            self._refresh()
            self._filter = _event_filter()
            self._filter.add(self)
            for sequence in ("<Configure>", "<Map>", "<Unmap>", "<Destroy>"):
                binding = self.win.bind(sequence, self._changed, add="+")
                self._bindings.append((sequence, binding))
            self._timer = self.win.after(FRAME_MS, self._poll)
        except Exception:
            self.close()
            raise

    def _changed(self, event):
        if event.widget is self.win and event.type == tk.EventType.Destroy:
            self.close()  # also handle parent/app shutdown without owner.close()
            return
        self._dirty = True
        if event.widget is self.win and event.type == tk.EventType.Map:
            self._resolve_pending = True

    def _refresh(self):
        if self._resolve_pending:
            # Resolve this exact Toplevel even when two session titles match.
            # Both title writes are synchronous, before the next event-loop turn.
            title = self.win.title()
            unique = f"AIPet graph gestures {id(self)}"
            try:
                self.win.title(unique)
                self.window = mac.window_titled(unique)
            finally:
                self.win.title(title)
            if not self.window:
                raise RuntimeError("agent graph NSWindow not found")
            self._resolve_pending = False

        screens = mac.send(mac.cls("NSScreen"), "screens")
        primary = mac.send(screens, "objectAtIndex:", 0, argtypes=[mac.c_ulong])
        self._screen_height = mac._rect(primary, "frame").height

        def rect(widget):
            if widget is None or not widget.winfo_ismapped():
                return None
            x, y = widget.winfo_rootx(), widget.winfo_rooty()
            return x, y, x + widget.winfo_width(), y + widget.winfo_height()

        self._rect = rect(self.owner.canvas)
        if self._rect is None:
            self._batch.clear()
        self._exclusions = [r for name in ("zoombar", "statc", "tip", "chip", "overlay", "menu", "statdrop")
                            if (r := rect(getattr(self.owner, name, None))) is not None]
        self._dirty = False

    def _event(self, _block, event):
        try:
            if self._closed or self._blocked or not self._rect or mac.send(event, "window") != self.window:
                return event
            point = mac.send(event, "locationInWindow", restype=mac.NSPoint)
            point = mac.send(self.window, "convertPointToScreen:", point, restype=mac.NSPoint,
                             argtypes=[mac.NSPoint])
            screen = point.x, self._screen_height - point.y
            if not over_graph(screen, self._rect, self._exclusions):
                return event
            kind = mac.send(event, "type", restype=mac.c_ulong)
            if kind == SCROLL:
                # Mouse wheels and modifier+wheel shortcuts keep the Tk path.
                flags = mac.send(event, "modifierFlags", restype=mac.c_ulong)
                if flags & ((1 << 18) | (1 << 19) | (1 << 20)):
                    return event
                if not mac.send(event, "hasPreciseScrollingDeltas", restype=mac.c_bool):
                    return event
                dx = mac.send(event, "scrollingDeltaX", restype=mac.c_double)
                dy = mac.send(event, "scrollingDeltaY", restype=mac.c_double)
                if flags & (1 << 17) and not dx:
                    dx, dy = dy, 0  # shift+vertical scroll, unless Cocoa already translated it
                # Cocoa deltas already follow the system's scroll direction and
                # include momentum. Convert once into canvas viewport movement.
                self._batch.pan(-dx, -dy)
                return None  # never deliver the same precise event to Tk too
            if kind == MAGNIFY:
                amount = mac.send(event, "magnification", restype=mac.c_double)
                at = screen[0] - self._rect[0], screen[1] - self._rect[1]
                self._batch.pinch(amount, at)
                return None
        except Exception as error:
            self._error = f"graph gesture: {error!r}"
        return event

    def _poll(self):
        self._timer = None
        if self._closed:
            return
        try:
            grab = self.win.grab_current()
            self._blocked = grab is not None and grab.winfo_toplevel() is not self.win
            if self._blocked:
                self._batch.clear()
            if self._dirty:
                self._refresh()
            if self._error:
                error, self._error = self._error, None
                self.log(error)
            for kind, a, b in self._batch.drain():
                if kind == "pan":
                    self.owner.pan(a, b)
                else:
                    self.owner.zoom(a, at=b, precise=True)
        except Exception as error:
            self.log(f"graph gestures disabled: {error!r}")
            self.close()  # leave normal Tk input usable if the native adapter fails
            return
        self._timer = self.win.after(FRAME_MS, self._poll)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._filter:
            self._filter.remove(self)
            self._filter = None
        if self._timer:
            try:
                self.win.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None
        for sequence, binding in self._bindings:
            try:
                self.win.unbind(sequence, binding)
            except tk.TclError:
                pass
        self._bindings.clear()
        self._batch.clear()
