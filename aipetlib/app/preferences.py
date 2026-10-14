"""Apply pet preferences without owning the pet lifecycle."""
import os
import sys
import tkinter as tk

from ..runtime.preferences import ANSWER_WAIT, CARD_SCALE, DONE_TIMEOUT, HEALTH, clamp_wait, clamp_card_scale, clamp_done, clamp_health
from ..runtime.settings import save_setting
from ..runtime.diagnostics import log_error
from ..permissions import write_answer_wait
from ..ui.theme import T, set_theme, retheme_all, style_menu, native_menu_theme
from ..ui.pet_style import rescale_card_fonts

IS_MAC = sys.platform == "darwin"


class PetPreferences:
    """Uses app config, menu variables, detail cards, root and sound preview callback."""

    def __init__(self, app):
        self.app = app

    def apply_theme(self, name):
        """Switch theme live: name tags and tooltips pick it up on their own; bubbles are rebuilt."""
        app = self.app
        set_theme(name)
        app.cfg["theme"] = T["name"]
        reopen = list(app.details)
        for d in list(app.details.values()):  # their colours are fixed at creation: rebuild them in the new theme
            d.destroy()
        for key in reopen:
            app.open_detail(key)
        retheme_all()
        style_menu(app.menu)
        native_menu_theme()

    def set_answer_wait(self, seconds):
        app = self.app
        ANSWER_WAIT["v"] = clamp_wait(round(float(seconds) / 15) * 15)
        app.cfg["answer_wait_seconds"] = ANSWER_WAIT["v"]
        save_setting("answer_wait_seconds", ANSWER_WAIT["v"])
        write_answer_wait(ANSWER_WAIT["v"])

    def set_done_timeout(self, minutes):
        app = self.app
        DONE_TIMEOUT["v"] = clamp_done(minutes)
        app.cfg["done_timeout_minutes"] = DONE_TIMEOUT["v"]
        save_setting("done_timeout_minutes", DONE_TIMEOUT["v"])

    def set_health_interval(self, seconds):
        app = self.app
        HEALTH["v"] = clamp_health(round(float(seconds) / 5) * 5)
        app.cfg["health_check_seconds"] = HEALTH["v"]
        save_setting("health_check_seconds", HEALTH["v"])

    def set_sound_style(self, style):
        app = self.app
        if style not in ("chimes", "system"):
            return
        app.cfg["sound_style"] = style
        save_setting("sound_style", style)
        app.beep(False, force=True)  # let the user hear the choice

    def set_session_titles(self, mode):
        """Session name (default) or latest prompt as the extra title (same-folder sessions, expanded badges)."""
        app = self.app
        mode = "prompt" if mode == "prompt" else "name"
        app.cfg["session_titles"] = mode
        save_setting("session_titles", mode)
        if hasattr(app, "titles_var") and app.titles_var.get() != mode:
            app.titles_var.set(mode)

    def set_click_to_focus(self, on):
        """Clicking a pet / badge goes to the session's window (on by default). Off: a click only acknowledges it."""
        app = self.app
        app.cfg["click_to_focus"] = bool(on)
        save_setting("click_to_focus", app.cfg["click_to_focus"])
        if hasattr(app, "click_focus_var") and app.click_focus_var.get() != app.cfg["click_to_focus"]:
            app.click_focus_var.set(app.cfg["click_to_focus"])

    def set_context_banner(self, on):
        """Each AI's context in use beside the pet (on by default); the pets redraw with or without it."""
        app = self.app
        app.cfg["context_banner"] = bool(on)
        save_setting("context_banner", app.cfg["context_banner"])
        if hasattr(app, "context_banner_var") and app.context_banner_var.get() != app.cfg["context_banner"]:
            app.context_banner_var.set(app.cfg["context_banner"])

    def set_all_spaces(self, on):
        """Show the pet on every virtual desktop, or only its own."""
        app = self.app
        app.cfg["all_spaces"] = bool(on)
        save_setting("all_spaces", app.cfg["all_spaces"])
        if hasattr(app, "all_spaces_var") and app.all_spaces_var.get() != app.cfg["all_spaces"]:
            app.all_spaces_var.set(app.cfg["all_spaces"])
        self._apply_all_spaces()

    def _apply_all_spaces(self):
        app = self.app
        try:
            on = bool(app.cfg.get("all_spaces", True))
            app.root.update_idletasks()
            if IS_MAC:
                # Keep Tk's attributes in sync so remapping the overlay does not
                # replace the native Space behavior.
                try:
                    kind, attrs = app.root.tk.call("::tk::unsupported::MacWindowStyle", "style", app.root._w)
                    attrs = [a for a in app.root.tk.splitlist(attrs)
                             if a not in ("canJoinAllSpaces", "moveToActiveSpace", "hideOnFullScreen", "hideOnSuspend")]
                    if on:
                        attrs.append("canJoinAllSpaces")
                    if "doesNotHide" not in attrs:
                        attrs.append("doesNotHide")
                    app.root.tk.call("::tk::unsupported::MacWindowStyle", "style", app.root._w, kind, tuple(attrs))
                except tk.TclError as e:
                    log_error(f"all spaces: Tk window style: {e!r}")
                import mac_statusbar
                if not mac_statusbar.set_all_spaces(app.root.title(), on):
                    log_error("all spaces: pet window not found")
            elif os.name == "nt":
                from pyvda import AppView
                view = AppView(hwnd=int(app.root.wm_frame(), 16))
                view.pin() if on else view.unpin()
        except Exception as e:
            log_error(f"all spaces: {e!r}")

    def toggle_compact(self, value=None):
        app = self.app
        app.cfg["compact"] = (not app.cfg.get("compact", False)) if value is None else bool(value)
        save_setting("compact", app.cfg["compact"])
        if hasattr(app, "compact_var") and app.compact_var.get() != app.cfg["compact"]:
            app.compact_var.set(app.cfg["compact"])

    def set_card_scale(self, value):
        """Size of the permission / question cards (all open ones follow at once); saved as card_scale."""
        app = self.app
        CARD_SCALE["v"] = clamp_card_scale(value)
        app.cfg["card_scale"] = CARD_SCALE["v"]
        save_setting("card_scale", CARD_SCALE["v"])
        rescale_card_fonts()
        for d in list(app.details.values()):
            d.apply_scale()
