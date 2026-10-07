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


# NSWindowCollectionBehavior: on every Space, not moved by Mission Control, allowed over full-screen apps, and left
# out of the window cycle (cmd-`)
ALL_SPACES = (1 << 0) | (1 << 4) | (1 << 6) | (1 << 8)


def set_all_spaces(title, on=True):
    """Show the window titled `title` on every desktop (Space), or only on its own. True if the window was found."""
    w = window_titled(title)
    if not w:
        return False
    cur = send(w, "collectionBehavior", restype=c_ulong)
    new = (cur | ALL_SPACES) if on else (cur & ~ALL_SPACES)
    send(w, "setCollectionBehavior:", new, restype=None, argtypes=[c_ulong])
    return True
