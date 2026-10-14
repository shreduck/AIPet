"""The pointer on the pets: dragging the window, clicks (acknowledge, open the prompt, go to the session, expand the
compact badges) and click-through (the window passes clicks on unless the pointer is over a pet, a bubble or a badge)."""
import sys
import tkinter as tk

from ..runtime import diagnostics
from ..runtime import perf

IS_MAC = sys.platform == "darwin"


class Pointer:
    """Owns a drag in progress ([x, y, window x, window y, moved]) and whether clicks currently pass through.

    Needs from the app: root, pets, placement, clickthru, _menu_open, hide_tip(), cfg, _last_items, ack,
    badges_expanded, open_detail(), focus_session(), _acknowledge().
    """

    def __init__(self, app):
        self.app = app
        self.drag = None
        self.passing = None

    def dragging(self):
        """The pet is being moved right now (the agent network windows hold their drawing meanwhile)."""
        return bool(self.drag and self.drag[4])

    def over_hit(self, x, y, margin=3):
        app = self.app
        for pet in list(app.pets.values()):
            c = pet.canvas
            try:
                if not c.winfo_ismapped():
                    continue
                ox, oy = c.winfo_rootx(), c.winfo_rooty()
                boxes = [c.bbox(t) for t in ("hit", "ans", "badge", "usage") if c.find_withtag(t)] or [c.bbox("all")]  # mole/cat: all
            except tk.TclError:
                continue
            for bb in boxes:
                if bb and ox + bb[0] - margin <= x <= ox + bb[2] + margin and oy + bb[1] - margin <= y <= oy + bb[3] + margin:
                    return True
        return False

    def pass_tick(self):
        app = self.app
        try:
            x, y = app.root.winfo_pointerxy()
            want = not (app._menu_open or self.drag is not None or self.over_hit(x, y))
            if want != self.passing:
                self.passing = want
                app.clickthru.set(want)
                if want:
                    app.hide_tip()
        except Exception as e:
            diagnostics.log_error(f"click-through: {e!r}")
        app.root.after(50, self.pass_tick)

    def press(self, e):
        app = self.app
        self.drag = [e.x_root, e.y_root, app.root.winfo_x(), app.root.winfo_y(), False]

    @perf.timed("pet: drag step", slow_ms=15)
    def move(self, e):
        app = self.app
        if not self.drag:
            return
        sx, sy, wx, wy, _ = self.drag
        dx, dy = e.x_root - sx, e.y_root - sy
        if abs(dx) + abs(dy) > 4:
            self.drag[4] = True
        if self.drag[4]:
            app.hide_tip()
            app.root.geometry(f"+{wx + dx}+{wy + dy}")

    def release(self, e, pet):
        app = self.app
        if IS_MAC and (e.state & 0x4):  # Control-click is a context menu, never a session click.
            self.drag = None
            pet._on_bubble, pet._on_badge = False, None
            return "break"
        on_bubble, pet._on_bubble = pet._on_bubble, False
        on_badge, pet._on_badge = pet._on_badge, None
        if self.drag and self.drag[4]:
            app.root.update_idletasks()
            app.placement.anchor = [app.root.winfo_x() + app.root.winfo_width(),
                                    app.root.winfo_y() + app.root.winfo_height()]
        elif on_badge in ("__expand", "__collapse"):  # compact mode: show every session's badge, or the merged one
            app.badges_expanded = on_badge == "__expand"
        elif on_badge and on_badge != "_none":  # a badge: that session's prompt window, or its window if it isn't waiting
            item = next((i for i in app._last_items if i["key"] == on_badge), None)
            if item:
                app.ack[on_badge] = True
                if item.get("state") == "needs_input":
                    app.open_detail(on_badge)
                elif app.cfg.get("click_to_focus", True):
                    import types
                    app.focus_session(types.SimpleNamespace(key=on_badge, data=item))
        elif on_bubble and pet.key != "_none":
            app._acknowledge(pet)
            app.open_detail(pet.data.get("focus", pet.key))  # the bubble: the full question and the answer buttons
        else:
            app._acknowledge(pet)  # click: acknowledge (stops jumping / reminders) and go to the session's window
            if pet.key != "_none" and app.cfg.get("click_to_focus", True):
                app.focus_session(pet)
        self.drag = None
