"""
macOS menu-bar icon (NSStatusItem) for AIPet, through the Objective-C runtime with ctypes - no extra packages.

pystray can't run next to Tk on macOS (both want the main thread), so the Mac build had no menu-bar icon. Tk already
runs the Cocoa event loop on the main thread, so a status item created here from the Tk thread just works: its menu
clicks arrive on the main thread during Tk's event processing.

* The icon is a line-art robot set as a "template" image, so macOS draws it in the menu bar's own colour (dark or
  light); while a session needs you it becomes the red robot (a normal, coloured image).
* The menu is rebuilt every time it opens (menuNeedsUpdate:), from a spec the app provides: a list of entries
  {"label", "action", "checked", "enabled", "submenu"} or None for a separator.
"""
import ctypes
import ctypes.util
import io
import platform
from ctypes import c_bool, c_char_p, c_double, c_long, c_size_t, c_ulong, c_void_p

_lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
_lib.objc_getClass.restype, _lib.objc_getClass.argtypes = c_void_p, [c_char_p]
_lib.sel_registerName.restype, _lib.sel_registerName.argtypes = c_void_p, [c_char_p]
_lib.objc_allocateClassPair.restype, _lib.objc_allocateClassPair.argtypes = c_void_p, [c_void_p, c_char_p, c_size_t]
_lib.class_addMethod.restype, _lib.class_addMethod.argtypes = c_bool, [c_void_p, c_void_p, c_void_p, c_char_p]
_lib.objc_registerClassPair.restype, _lib.objc_registerClassPair.argtypes = None, [c_void_p]
_MSGSEND = ctypes.cast(_lib.objc_msgSend, c_void_p).value
_PROTOS = {}


class NSSize(ctypes.Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


class NSPoint(ctypes.Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


def cls(name):
    return _lib.objc_getClass(name.encode())


def sel(name):
    return _lib.sel_registerName(name.encode())


def send(obj, name, *args, restype=c_void_p, argtypes=()):
    """[obj name:args...] with objc_msgSend cast to the exact prototype (required on arm64)."""
    key = (restype, tuple(argtypes))
    f = _PROTOS.get(key)
    if f is None:
        f = _PROTOS[key] = ctypes.CFUNCTYPE(restype, c_void_p, c_void_p, *argtypes)(_MSGSEND)
    return f(obj, sel(name), *args)


def nsstr(text):
    return send(cls("NSString"), "stringWithUTF8String:", str(text).encode("utf-8"), argtypes=[c_char_p])


def nsimage(pil_image, height_pt=18.0, template=False):
    buf = io.BytesIO()
    pil_image.save(buf, "PNG")
    raw = buf.getvalue()
    data = send(cls("NSData"), "dataWithBytes:length:", raw, len(raw), argtypes=[c_char_p, c_ulong])
    img = send(send(cls("NSImage"), "alloc"), "initWithData:", data, argtypes=[c_void_p])
    w = height_pt * pil_image.width / pil_image.height
    send(img, "setSize:", NSSize(w, height_pt), restype=None, argtypes=[NSSize])
    send(img, "setTemplate:", bool(template), restype=None, argtypes=[c_bool])
    return img


_IMP = ctypes.CFUNCTYPE(None, c_void_p, c_void_p, c_void_p)  # -(void)method:(id)arg


class StatusItem:
    def __init__(self, spec, run, icons):
        """spec(): the menu entries; run(fn): run an action on the app's UI queue; icons: {"normal", "attention"} ->
        PIL images (normal is drawn as a template)."""
        self.spec, self.run, self.actions = spec, run, {}
        self._imps = [_IMP(self._on_action), _IMP(self._on_menu_needs_update)]  # keep the callbacks alive
        target_cls = cls("AIPetStatusTarget")
        if not target_cls:
            target_cls = _lib.objc_allocateClassPair(cls("NSObject"), b"AIPetStatusTarget", 0)
            _lib.class_addMethod(target_cls, sel("menuAction:"), ctypes.cast(self._imps[0], c_void_p), b"v@:@")
            _lib.class_addMethod(target_cls, sel("menuNeedsUpdate:"), ctypes.cast(self._imps[1], c_void_p), b"v@:@")
            _lib.objc_registerClassPair(target_cls)
        self.target = send(send(target_cls, "alloc"), "init")
        bar = send(cls("NSStatusBar"), "systemStatusBar")
        self.item = send(bar, "statusItemWithLength:", -1.0, argtypes=[c_double])  # NSVariableStatusItemLength
        send(self.item, "retain")
        self.button = send(self.item, "button")
        self.images = {"normal": nsimage(icons["normal"], template=True), "attention": nsimage(icons["attention"])}
        self.menu = send(send(cls("NSMenu"), "alloc"), "initWithTitle:", nsstr("AIPet"), argtypes=[c_void_p])
        send(self.menu, "setAutoenablesItems:", False, restype=None, argtypes=[c_bool])
        send(self.menu, "setDelegate:", self.target, restype=None, argtypes=[c_void_p])
        send(self.item, "setMenu:", self.menu, restype=None, argtypes=[c_void_p])
        self._state = None
        self.set_state("normal", "AIPet")

    def set_state(self, which, tooltip=""):
        if which != self._state:
            self._state = which
            send(self.button, "setImage:", self.images[which], restype=None, argtypes=[c_void_p])
        send(self.button, "setToolTip:", nsstr(tooltip), restype=None, argtypes=[c_void_p])

    def remove(self):
        try:
            send(send(cls("NSStatusBar"), "systemStatusBar"), "removeStatusItem:", self.item, restype=None,
                 argtypes=[c_void_p])
        except Exception:
            pass

    # ---- Objective-C callbacks (main thread)
    def _on_action(self, _self, _cmd, sender):
        try:
            fn = self.actions.get(send(sender, "tag", restype=c_long))
            if fn:
                self.run(fn)
        except Exception:
            pass

    def _on_menu_needs_update(self, _self, _cmd, menu):
        try:
            if menu == self.menu:
                self.actions = {}
                send(menu, "removeAllItems", restype=None)
                self._fill(menu, self.spec())
        except Exception as e:
            try:
                import aipet
                aipet.log_error(f"menu bar menu: {e!r}")
            except Exception:
                pass

    def _fill(self, menu, entries):
        for e in entries:
            if e is None:
                send(menu, "addItem:", send(cls("NSMenuItem"), "separatorItem"), restype=None, argtypes=[c_void_p])
                continue
            has_action = bool(e.get("action"))
            mi = send(send(cls("NSMenuItem"), "alloc"), "initWithTitle:action:keyEquivalent:", nsstr(e["label"]),
                      sel("menuAction:") if has_action else None, nsstr(""), argtypes=[c_void_p, c_void_p, c_void_p])
            if has_action:
                tag = len(self.actions) + 1
                self.actions[tag] = e["action"]
                send(mi, "setTarget:", self.target, restype=None, argtypes=[c_void_p])
                send(mi, "setTag:", tag, restype=None, argtypes=[c_long])
            if e.get("checked"):
                send(mi, "setState:", 1, restype=None, argtypes=[c_long])
            enabled = e.get("enabled", True) and (has_action or bool(e.get("submenu")))
            send(mi, "setEnabled:", bool(enabled), restype=None, argtypes=[c_bool])
            if e.get("submenu") is not None:
                sub = send(send(cls("NSMenu"), "alloc"), "initWithTitle:", nsstr(e["label"]), argtypes=[c_void_p])
                send(sub, "setAutoenablesItems:", False, restype=None, argtypes=[c_bool])
                self._fill(sub, e["submenu"])
                send(mi, "setSubmenu:", sub, restype=None, argtypes=[c_void_p])
                send(sub, "release", restype=None)
            send(menu, "addItem:", mi, restype=None, argtypes=[c_void_p])
            send(mi, "release", restype=None)


class _BlockDescriptor(ctypes.Structure):
    _fields_ = [("reserved", c_ulong), ("size", c_ulong)]


class _EventBlock(ctypes.Structure):
    _fields_ = [("isa", c_void_p), ("flags", ctypes.c_int), ("reserved", ctypes.c_int),
                ("invoke", c_void_p), ("descriptor", ctypes.POINTER(_BlockDescriptor))]


class PanelMouseBridge:
    """Forward only this panel's local Cocoa mouse events to Tk 8.6.

    tkProcessMouseEvent accepts TKWindow but skips TKPanel. A local monitor
    needs no Accessibility/Input Monitoring access and never observes other apps.
    Keep its Objective-C block and callback alive until removeMonitor: completes.
    """
    def __init__(self, root):
        self.root = root
        self.window = window_titled(root.title())
        self._capture = self._hover = None
        self._closed = False
        self._pending = []
        self.received = 0
        self.last_target = None
        self._invoke = ctypes.CFUNCTYPE(c_void_p, c_void_p, c_void_p)(self._event)
        self._descriptor = _BlockDescriptor(0, ctypes.sizeof(_EventBlock))
        self._system = ctypes.cdll.LoadLibrary(ctypes.util.find_library("System"))
        isa = ctypes.addressof((c_void_p * 32).in_dll(self._system, "_NSConcreteGlobalBlock"))
        self._block = _EventBlock(isa, 1 << 28, 0, ctypes.cast(self._invoke, c_void_p),
                                  ctypes.pointer(self._descriptor))
        mask = sum(1 << kind for kind in (1, 2, 3, 4, 5, 6, 7, 8, 9, 25, 26, 27))
        self.monitor = send(cls("NSEvent"), "addLocalMonitorForEventsMatchingMask:handler:", mask,
                            ctypes.byref(self._block), argtypes=[c_ulong, c_void_p])
        send(self.window, "setAcceptsMouseMovedEvents:", True, restype=None, argtypes=[c_bool])
        self._timer = root.after(10, self._poll)

    def _event(self, _block, event):
        if self._closed or send(event, "window") != self.window:
            return event
        try:
            self.received += 1
            point = send(event, "locationInWindow", restype=NSPoint)
            point = send(self.window, "convertPointToScreen:", point, restype=NSPoint, argtypes=[NSPoint])
            screens = send(cls("NSScreen"), "screens")
            main = send(screens, "objectAtIndex:", 0, argtypes=[c_ulong])
            x, y = int(point.x), int(_rect(main, "frame").height - point.y)
            flags = send(event, "modifierFlags", restype=c_ulong)
            state = (1 if flags & (1 << 17) else 0) | (4 if flags & (1 << 18) else 0)
            state |= (8 if flags & (1 << 19) else 0) | (16 if flags & (1 << 20) else 0)
            kind = send(event, "type", restype=c_ulong)
            button = send(event, "buttonNumber", restype=c_long) + 1
            # Do not re-enter Tcl from Cocoa's event monitor. The Tk timer
            # delivers these events after the native callback has returned.
            self._pending.append((kind, button, x, y, state))
            return None
        except Exception as error:
            import aipet
            aipet.log_error(f"panel mouse: {error!r}")
            return event

    def _poll(self):
        if self._closed:
            return
        pending, self._pending = self._pending, []
        for event in pending:
            self._dispatch(*event)
        if not self._closed:
            self._timer = self.root.after(10, self._poll)

    def _dispatch(self, kind, button, x, y, state):
        if self._closed:
            return
        import tkinter as tk
        try:
            # Tk's native window lookup also excludes TKPanel. Resolve the
            # widget from this panel's own Tk geometry instead.
            def widget_at(widget):
                if not widget.winfo_ismapped():
                    return None
                left, top = widget.winfo_rootx(), widget.winfo_rooty()
                if not (left <= x < left + widget.winfo_width() and top <= y < top + widget.winfo_height()):
                    return None
                for child in reversed(widget.winfo_children()):
                    if not isinstance(child, tk.Toplevel):
                        match = widget_at(child)
                        if match is not None:
                            return match
                return widget
            target = widget_at(self.root)
            self.last_target = str(target)
            if target is not None and target.winfo_toplevel() is not self.root:
                target = None
            if kind == 9:
                target = None
            if target is not self._hover:
                if self._hover is not None and self._hover.winfo_exists():
                    self._hover.event_generate("<Leave>")
                self._hover = target
                if target is not None:
                    target.event_generate("<Enter>", x=x - target.winfo_rootx(), y=y - target.winfo_rooty())
            if kind in (1, 3, 25):
                self._capture = target
            elif kind in (2, 4, 6, 7, 26, 27):
                target = self._capture or target
            if target is None:
                return
            options = dict(x=x - target.winfo_rootx(), y=y - target.winfo_rooty(), rootx=x, rooty=y, state=state)
            if kind in (1, 3, 25):
                # Establish Canvas 'current' so badge and bubble tag bindings
                # receive the same press as the Canvas widget itself.
                target.event_generate("<Motion>", **options)
                target.event_generate(f"<ButtonPress-{button}>", **options)
            elif kind in (2, 4, 26):
                target.event_generate(f"<ButtonRelease-{button}>", **options)
                self._capture = None
            elif kind in (5, 6, 7, 8, 27):
                if kind in (6, 7, 27):
                    options["state"] |= 1 << (button + 7)
                target.event_generate("<Motion>", **options)
        except tk.TclError:
            self._capture = self._hover = None  # the pet may disappear during a click

    def close(self):
        self._closed = True
        self.root.after_cancel(self._timer)
        send(cls("NSEvent"), "removeMonitor:", self.monitor, restype=None, argtypes=[c_void_p])
        self.monitor = None
        self._pending.clear()


class NSRect(ctypes.Structure):
    _fields_ = [("x", c_double), ("y", c_double), ("width", c_double), ("height", c_double)]


def _rect(obj, name):
    """An NSRect-returning message: objc_msgSend on arm64, objc_msgSend_stret on Intel (structs over 16 bytes)."""
    import platform
    fn = _lib.objc_msgSend if platform.machine() == "arm64" else _lib.objc_msgSend_stret
    f = ctypes.CFUNCTYPE(NSRect, c_void_p, c_void_p)(ctypes.cast(fn, c_void_p).value)
    return f(obj, sel(name))


def screen_rects(visible=True):
    """Every screen as (left, top, right, bottom) in Tk's coordinates: origin at the top-left of the main screen, y down
    (Cocoa puts the origin at the main screen's bottom-left, y up). visible: without the menu bar and the Dock, which
    sits above every window, so a pet placed over it would hide its name tags behind it."""
    screens = send(cls("NSScreen"), "screens")
    n = send(screens, "count", restype=c_ulong)
    objs = [send(screens, "objectAtIndex:", i, argtypes=[c_ulong]) for i in range(n)]
    if not objs:
        return []
    main_h = _rect(objs[0], "frame").height  # screens[0] is the one with the menu bar: Cocoa's origin
    frames = [_rect(o, "visibleFrame" if visible else "frame") for o in objs]
    return [(int(f.x), int(main_h - f.y - f.height), int(f.x + f.width), int(main_h - f.y)) for f in frames]


def window_titled(title):
    """This app's NSWindow with that title (Tk's toplevel for the pet), or None."""
    app = send(cls("NSApplication"), "sharedApplication")
    wins = send(app, "windows")
    for i in range(send(wins, "count", restype=c_ulong)):
        w = send(wins, "objectAtIndex:", i, argtypes=[c_ulong])
        t = send(w, "title")
        name = send(t, "UTF8String", restype=c_char_p) if t else None
        if name and name.decode("utf-8", "replace") == title:
            return w
    return None


def raise_modal(title, parent_title):
    """Keep a modal card above its owner's topmost window, including after owner activation."""
    window, parent = window_titled(title), window_titled(parent_title)
    if not window or not parent:
        return False
    level = send(parent, "level", restype=c_long)
    send(window, "setLevel:", max(19, level + 1), restype=None, argtypes=[c_long])
    return True


# NSWindowCollectionBehavior: on every Space, not moved by Mission Control, allowed over full-screen apps, and left
# out of the window cycle (cmd-`)
ALL_SPACES = (1 << 0) | (1 << 4) | (1 << 6) | (1 << 8)
ALL_APPLICATIONS = 1 << 18  # macOS 13+: join other apps' Stage Manager sets / full-screen Spaces
ALL_SPACES_CONFLICTS = ((1 << 1) | (1 << 2) | (1 << 3) | (1 << 5) | (1 << 7) | (1 << 9) |
                        (1 << 16) | (1 << 17))  # Primary / Auxiliary conflict with AllApplications


def supports_all_applications():
    return int(platform.mac_ver()[0].split(".")[0]) >= 13


def set_all_spaces(title, on=True):
    """Show the window titled `title` on every desktop (Space), or only on its own. True if the window was found."""
    w = window_titled(title)
    if not w:
        return False
    cur = send(w, "collectionBehavior", restype=c_ulong)
    # Each Cocoa behavior group is mutually exclusive: clear MoveToActiveSpace,
    # Managed / Transient, ParticipatesInCycle and FullScreenPrimary / None.
    enabled = ALL_SPACES | (ALL_APPLICATIONS if supports_all_applications() else 0)
    new = ((cur & ~ALL_SPACES_CONFLICTS) | enabled) if on else (cur & ~(ALL_SPACES | ALL_APPLICATIONS))
    send(w, "setCollectionBehavior:", new, restype=None, argtypes=[c_ulong])
    # Tk's utility style can mark -topmost true without raising a newly created
    # panel. Match Tk's kCGUtilityWindowLevel so the pet is above video windows.
    send(w, "setLevel:", 19, restype=None, argtypes=[c_long])
    return True
