"""Tray, Dock and menu-bar presentation, notifications and tray menu adaptation."""
import json
import subprocess
import sys
import tkinter as tk

from ..runtime import diagnostics
from ..ui.pet_style import LABELS
from ..ui.tray_icons import make_icon, dock_image, menu_bar_icons

IS_MAC = sys.platform == "darwin"
try:
    if IS_MAC:  # Tk owns the main thread; macOS uses its native menu-bar item.
        raise ImportError
    import pystray
except ImportError:
    pystray = None

APP_NAME = "AIPet"
RANK = {"needs_input": 0, "error": 1, "working": 2, "done": 3, "idle": 4}


class TrayPresenter:
    """Owns tray/menu-bar handles, icon caches and the Dock image reference.

    Uses app root, pet session snapshots, approvals, cached notification preference,
    visibility, UI dispatch and the shared menu specification.
    """

    def __init__(self, app):
        self.app = app
        self.icon = self.status_item = None
        self._icon_key = self._dock_key = self._dock_ph = None

    def start(self):
        app = self.app
        if pystray:
            self.icon = pystray.Icon("aipet", make_icon("idle"), APP_NAME, menu=self.build_menu())
            self.icon.run_detached()
        if IS_MAC:
            try:
                import mac_statusbar
                self.status_item = mac_statusbar.StatusItem(app._mac_menu_spec, app.ui, menu_bar_icons())
            except Exception as e:
                diagnostics.log_error(f"menu bar icon: {e!r}")

    def invalidate(self):
        self._icon_key = None

    def refresh_menu(self):
        if self.icon:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def stop(self):
        if self.status_item:
            self.status_item.remove()
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass

    def summary(self):
        app = self.app
        counts = {}
        for it in app.pet._last_items:  # every session, also in compact mode (one pet for all)
            st = it.get("state", "idle")
            counts[st] = counts.get(st, 0) + 1
        top = min(counts, key=lambda s: RANK.get(s, 9)) if counts else "idle"
        parts = [f"{counts[s]} {LABELS.get(s, s).rstrip('…!')}" for s in sorted(counts, key=lambda s: RANK.get(s, 9))]
        text = f"{APP_NAME} - " + ("AUTO APPROVE ON - " if app.approvals.targets_on else "") + (
            ", ".join(parts) if parts else "no active sessions")
        return top, text[:120]

    def update_dock(self):
        """macOS: the Dock is the taskbar - show the robot there, red while a session needs you, and bounce the icon
        once when that starts."""
        app = self.app
        top, _ = self.summary()
        key = "attention" if top in ("needs_input", "error") else "normal"
        if key == getattr(self, "_dock_key", None):
            return
        self._dock_key = key
        try:
            import base64
            import io
            buf = io.BytesIO()
            dock_image(top if key == "attention" else "idle").save(buf, "PNG")
            self._dock_ph = tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"), format="png")
            app.root.iconphoto(True, self._dock_ph)
            if key == "attention":
                app.root.attributes("-notify", True)  # Tk on macOS: bounce the Dock icon
        except Exception as e:
            diagnostics.log_error(f"dock icon: {e!r}")

    def update_tray(self):
        if not self.icon:
            if IS_MAC:
                self.update_dock()
                if self.status_item:
                    top, text = self.summary()
                    if (top, text) != self._icon_key:
                        self._icon_key = (top, text)
                        try:
                            self.status_item.set_state("attention" if top in ("needs_input", "error") else "normal", text)
                        except Exception as e:
                            diagnostics.log_error(f"menu bar icon: {e!r}")
            return
        top, text = self.summary()
        if (top, text) != self._icon_key:
            self._icon_key = (top, text)
            try:
                self.icon.icon = make_icon(top)
                self.icon.title = text
            except Exception:
                pass

    def notify(self, body, title):
        if self.icon:
            self.icon.notify(body, title)
        elif IS_MAC:
            subprocess.Popen(["osascript", "-e", "display notification %s with title %s" % (
                json.dumps(body, ensure_ascii=False), json.dumps(title, ensure_ascii=False))])

    def on_alert(self, state, old, item):
        app = self.app
        if not item or not app.c_notify or not (self.icon or IS_MAC):
            return
        title = item.get("title", "session")
        where = item.get("where", "")
        try:
            if state == "needs_input":
                self.notify(item.get("message") or where or "Claude is waiting for you", f"{title} needs your input")
            elif state == "error":
                self.notify(where or "Something went wrong", f"{title} hit an error")
            elif state == "done" and (app.hidden or IS_MAC) and old in ("working", "needs_input"):
                self.notify(where or "Finished", f"{title} is done")
        except Exception:
            pass

    def build_menu(self):
        app = self.app
        def dispatch(fn):
            return lambda: app.ui(fn)

        def entries():
            def convert(spec):
                result = []
                for entry in spec:
                    if entry is None:
                        result.append(pystray.Menu.SEPARATOR)
                        continue
                    action = entry.get("action")
                    submenu = entry.get("submenu")
                    handler = pystray.Menu(*convert(submenu)) if submenu is not None else (
                        dispatch(action) if action else None)
                    checked = (lambda item, value=entry["checked"]: value) if "checked" in entry else None
                    result.append(pystray.MenuItem(entry["label"], handler, checked=checked,
                                                   enabled=entry.get("enabled", True), default=entry.get("default", False)))
                return result
            return convert(app._mac_menu_spec())
        return pystray.Menu(entries)
