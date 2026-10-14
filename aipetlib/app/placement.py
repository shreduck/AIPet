"""Where the pets sit: the window's bottom-right corner (the anchor) stays put while pets come, go and change size,
inside the monitor the pet is on; the shared right margin beside the usage column."""
import sys
import tkinter as tk

from .. import geometry
from ..ui import pet_style

IS_MAC = sys.platform == "darwin"


class Placement:
    """Owns the anchor (the window's bottom-right corner on screen) and the right margin shared by all pets.

    Needs from the app: root, frame (the pets' row), pets and order.
    """

    def __init__(self, app):
        self.app = app
        self.usage_frame_pad = pet_style.px(pet_style.USAGE_EDGE_ROOM)
        self.anchor = None  # set once the window exists (default_anchor)

    def sync_usage_padding(self):
        """Share one right margin across the overlay instead of padding every pet."""
        app = self.app
        last = app.pets.get(app.order[-1]) if app.order else None
        extra = getattr(last, "usage_extra", 0) if last else 0
        padding = max(0, pet_style.px(pet_style.USAGE_EDGE_ROOM - extra))
        delta = padding - self.usage_frame_pad
        if delta:
            self.usage_frame_pad = padding
            app.frame.pack_configure(padx=(0, padding))
        return delta

    def reposition(self, ref=None):
        """Keep the bottom-right corner (the anchor) where it is while the pets change size, but inside the monitor the
        pet is on. That monitor is found from the middle of the window as it is now, not from the anchor: an anchor
        just across a monitor edge used to pull a growing pet onto the neighbouring screen. The anchor follows any
        clamping, so the next resize starts from where the pet really is."""
        app = self.app
        r = app.root
        r.update_idletasks()
        w, h = r.winfo_reqwidth(), r.winfo_reqheight()
        ax, ay = self.anchor
        if ref is None:
            try:
                cw, ch = r.winfo_width(), r.winfo_height()
                ref = (r.winfo_x() + cw // 2, r.winfo_y() + ch // 2) if cw > 1 and ch > 1 else (ax - 1, ay - 1)
            except tk.TclError:
                ref = (ax - 1, ay - 1)
        x, y = geometry.fit_on_screen(ax - w, ay - h, w, h, ref, r)
        r.geometry(geometry.geo(x, y))
        self.anchor = [x + w, y + h]

    def default_anchor(self):
        """Where the pet's bottom-right corner starts: the main screen's bottom-right corner, just above the Windows
        taskbar; on macOS just above the Dock (the visible area of the main screen)."""
        app = self.app
        r = app.root
        if IS_MAC:
            area = geometry.mac_screen_at(1, 1)  # the main screen holds Tk's origin
            if area:
                return [area[2] - 24, area[3] - 8]
        return [r.winfo_screenwidth() - 24, r.winfo_screenheight() - 60]

    def shift_for_width(self, delta):
        """The overlay is about to become delta px wider (narrower if negative): move it now, before Tk redraws, so the
        new size and position land together and the pet stays put (reposition() would redraw at the old position
        first, flashing the pet sideways). Falls back to reposition() before the window is shown."""
        app = self.app
        r = app.root
        try:
            w, h = r.winfo_width(), r.winfo_height()
            if w <= 1 or h <= 1:
                r.after_idle(self.reposition)
                return
            w += delta
            ref = (r.winfo_x() + r.winfo_width() // 2, r.winfo_y() + h // 2)
            x, y = geometry.fit_on_screen(self.anchor[0] - w, self.anchor[1] - h, w, h, ref, r)
            r.geometry(geometry.geo(x, y))
            self.anchor = [x + w, y + h]
        except tk.TclError:
            r.after_idle(self.reposition)

    def reset_position(self):
        """Last resort for a pet lost off screen: back to the main screen's bottom-right corner."""
        app = self.app
        self.anchor = self.default_anchor()
        self.reposition(ref=(self.anchor[0] - 1, self.anchor[1] - 1))
        try:
            app.root.deiconify()
            app.root.lift()
            app.root.attributes("-topmost", True)
        except tk.TclError:
            pass
