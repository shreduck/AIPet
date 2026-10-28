"""Session and usage hover tooltip presentation.

The owning app supplies its widgets and state; shared values come from their owner modules.
No application entry point is imported by this component.
"""
import sys
import time
import tkinter as tk

import aipet_usage as usage

from .. import geometry
from .. import text as textfmt
from ..agents import context as agent_context
from ..mac_panels import MacOverlayWindow
from ..runtime import settings
from . import pet_style
from . import sprites
from . import theme

IS_MAC = sys.platform == "darwin"


class Tooltips:
    """Owns the hover panel; uses the app root, config, pets and menu visibility."""

    def __init__(self, app):
        self.app = app
        self.tip = None

    def tooltip_text_scale(self):
        app = self.app
        return max(0.5, min(2.5, float(app.cfg.get("tooltip_text_scale", 1.0))))

    def set_tooltip_text_scale(self, scale):
        app = self.app
        app.cfg["tooltip_text_scale"] = round(max(0.5, min(2.5, float(scale))), 2)
        settings.save_setting("tooltip_text_scale", app.cfg["tooltip_text_scale"])
        self.hide_tip()

    def show_tip(self, pet):
        app = self.app
        if getattr(app, "_menu_open", False):
            return
        self.hide_tip()
        if not app.cfg.get("session_tooltips", True):
            return
        d = pet.data
        lines = [d.get("title", ""), f"{d.get('where') or pet_style.SOURCE_NAMES.get(d.get('source'), '')} · {pet_style.LABELS.get(d.get('state'), d.get('state'))}".strip(" ·")]
        overrides = app.cfg.get("context_windows")
        if d.get("everyone"):  # compact mode: every session, the one in front first
            lines += [""] + [f"{'> ' if m['key'] == d.get('focus') else '   '}{m.get('title', '')} · "
                             f"{pet_style.LABELS.get(m.get('state'), m.get('state'))}"
                             + (f" · ctx {agent_context.banner_rows([m], overrides)[0]['percent']}%" if m.get("context") else "")
                             for m in d["everyone"][:12]]
        elif d.get("context"):
            lines.append(agent_context.describe(agent_context.banner_rows([d], overrides)[0]))
        if d.get("message"):
            lines.append(d["message"][:200])
        if d.get("detail"):
            lines.append(d["detail"])
        if d.get("changed"):
            lines.append("since " + textfmt.ago(d["changed"]))
        if d.get("pid"):
            lines.append(f"PID {d['pid']}" + (" (WSL)" if d.get("env") == "wsl" else ""))
        if d.get("auto_what") and time.time() - (d.get("auto_t") or 0) < 120:
            action = "Rejected by blacklist" if d.get("auto_kind") == "blacklist" else "Auto-approved"
            lines.append(f"{action} {textfmt.ago(d['auto_t'])}: {d['auto_what'][:90]}")
        if d.get("auto_approved"):
            lines.append(f"Auto-approved {d['auto_approved']} permission prompt{'s' if d['auto_approved'] != 1 else ''}")
        self._show_tooltip(pet, "\n".join(lines))

    def show_usage_tip(self, pet, detail):
        app = self.app
        if getattr(app, "_menu_open", False):
            return
        self.hide_tip()
        if not app.cfg.get("usage_tooltips", True):
            return
        text = agent_context.tooltip(detail) if detail.get("kind") == "context" else usage.tooltip(detail)
        self._show_tooltip(pet, text, usage_detail=detail)

    def set_tooltip(self, kind, on):
        app = self.app
        if kind not in ("session", "usage"):
            return
        app.cfg[kind + "_tooltips"] = bool(on)
        settings.save_setting(kind + "_tooltips", bool(on))
        var = getattr(app, "tooltip_vars", {}).get(kind)
        if var is not None:
            var.set(bool(on))
        self.hide_tip()
        for pet in app.pets.values():
            pet._usage_hover = None

    def _show_tooltip(self, pet, text, usage_detail=None):
        app = self.app
        if getattr(app, "_menu_open", False):
            return
        if IS_MAC:
            import mac_statusbar as mac
            with mac.passive_window_mapping():
                self._build_tooltip(pet, text, usage_detail)
        else:
            self._build_tooltip(pet, text, usage_detail)

    def _build_tooltip(self, pet, text, usage_detail):
        app = self.app
        tip = self.tip = MacOverlayWindow(app.root) if IS_MAC else tk.Toplevel(app.root)
        # Configure the native panel and position it before it becomes visible.
        tip.withdraw()
        # Cocoa can retain a closed panel through the next event cycle. A unique
        # title keeps native configuration from finding an earlier hover panel.
        tip.title(f"AIPet tooltip {time.monotonic_ns()}")
        if IS_MAC:
            tip.attributes("-alpha", 0.0)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tip.configure(bg=pet_style.TRANSPARENT)
        try:
            tip.attributes("-transparent", True) if IS_MAC else tip.attributes("-transparentcolor", pet_style.TRANSPARENT)
        except tk.TclError:
            pass
        c = tk.Canvas(tip, bg=pet_style.TRANSPARENT, highlightthickness=0, bd=0)
        c.pack()
        title, _, body = text.partition("\n")
        title = title.strip() or "Session details"
        data = usage_detail if usage_detail is not None else getattr(pet, "data", {})
        agent = data.get("agent")
        accent = "#5b8def" if agent == "codex" else "#d98960" if agent == "claude" or data.get("source") == "CC" else theme.T["tag_fg"]
        text_scale = self.tooltip_text_scale()
        wrap_width = round(320 * text_scale)
        head = c.create_text(12, 10, text=title, font=(sprites.MONO_FAMILY, round(9 * text_scale), "bold"),
                             fill=accent, anchor="nw", width=wrap_width)
        header_box = c.bbox(head)
        w, h = header_box[2] + 12, header_box[3] + 10
        if body.strip():
            content = c.create_text(12, header_box[3] + 6, text=body, font=(sprites.MONO_FAMILY, round(8 * text_scale)),
                                    fill=theme.T["tag_fg"], anchor="nw", width=wrap_width)
            box = c.bbox(content)
            w, h = max(header_box[2], box[2]) + 12, box[3] + 10
        border = c.create_polygon(6, 1, w - 6, 1, w - 6, 3, w - 3, 3, w - 3, 6, w - 1, 6,
                                 w - 1, h - 6, w - 3, h - 6, w - 3, h - 3, w - 6, h - 3, w - 6, h - 1,
                                 6, h - 1, 6, h - 3, 3, h - 3, 3, h - 6, 1, h - 6, 1, 6, 3, 6, 3, 3, 6, 3,
                                 fill=theme.T["tag_bg"], outline=theme.T["tag_outline"], width=2)
        c.tag_lower(border)
        c.config(width=w, height=h)
        tip.update_idletasks()
        # Both session and usage details belong above the pet, clear of its body
        # and badges. Centre on the pet itself, excluding the usage badge gutter.
        x = pet.canvas.winfo_rootx() + pet_style.px(pet_style.BADGE_GUTTER + getattr(pet, "gutter", 0) + pet_style.PET_W / 2) - tip.winfo_reqwidth() // 2
        y = pet.canvas.winfo_rooty() - tip.winfo_reqheight() - 6
        tip.geometry(geometry.geo(*geometry.fit_on_screen(x, y, tip.winfo_reqwidth(), tip.winfo_reqheight(),
                                        (pet.canvas.winfo_rootx() + 10, pet.canvas.winfo_rooty() + 10), tip)))
        tip.deiconify()
        if IS_MAC:
            import mac_statusbar as mac
            # Tk creates/resets the native window on mapping. Keep it invisible
            # until its Space and mouse behavior have been applied.
            tip.update_idletasks()
            mac.set_all_spaces(tip.title(), app.cfg.get("all_spaces", True))
            window = mac.window_titled(tip.title())
            if window:
                parent = mac.window_titled(app.root.title())
                if parent:
                    level = mac.send(parent, "level", restype=mac.c_long)
                    mac.send(window, "setLevel:", level + 1, restype=None, argtypes=[mac.c_long])
                # Hover text must never intercept clicks or become the key window.
                mac.send(window, "setIgnoresMouseEvents:", True, restype=None, argtypes=[mac.c_bool])
            tip.attributes("-alpha", 1.0)

    def hide_tip(self):
        if self.tip:
            if IS_MAC:
                import mac_statusbar as mac
                # Tk's panel teardown can select and raise another app window.
                with mac.passive_window_mapping():
                    self.tip.destroy()
            else:
                self.tip.destroy()
            self.tip = None
