"""Pet appearance and behavior slider controls.

The owning app supplies its widgets and state; shared values come from their owner modules.
No application entry point is imported by this component.
"""
import tkinter as tk

from .. import geometry
from .. import text as textfmt
from ..runtime import preferences
from ..runtime import settings
from . import pet_style
from . import theme

def set_size(app, size):
    app.set_scale(float(size) * preferences.SCALE_UNIT)
    app.reposition()
    app.cfg["size"] = round(preferences.SCALE["v"] / preferences.SCALE_UNIT, 2)
    settings.save_setting("size", app.cfg["size"])


def set_scale(app, v):
    v = preferences.clamp_scale(v)
    if abs(v - preferences.SCALE["v"]) < 0.005:
        return
    preferences.SCALE["v"] = v
    for pet in app.pets.values():
        pet.canvas.config(width=pet_style.px(pet_style.CANVAS_W + getattr(pet, "gutter", 0) + getattr(pet, "usage_extra", 0)), height=pet_style.px(pet_style.CANVAS_H))
    app._sync_usage_padding()


def _place_above_pet(app, w):
    """Put a small window directly above the pet overlay, centred on it (just below it if there is no room)."""
    w.update_idletasks()
    app.root.update_idletasks()
    ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
    ox, oy, ow, oh = app.root.winfo_x(), app.root.winfo_y(), app.root.winfo_width(), app.root.winfo_height()
    ref = (ox + ow // 2, oy + oh // 2)  # the pet's monitor
    area = geometry.work_area(*ref, w)
    x, y = ox + ow // 2 - ww // 2, oy - wh - 10
    if area and y < area[1]:
        y = oy + oh + 10  # no room above: just below
    w.geometry(geometry.geo(*geometry.fit_on_screen(x, y, ww, wh, ref, w, margin=10)))


def open_size_slider(app):
    """A small window with a slider (30% - 300%; 100% = SCALE_UNIT). The pet follows it live, saved on release."""
    if app.size_win is not None:
        try:
            app.size_win.lift()
            return
        except tk.TclError:
            app.size_win = None
    from tkinter import ttk
    w = app.size_win = tk.Toplevel(app.root)
    w.title("Pet size")
    w.attributes("-topmost", True)
    w.resizable(False, False)
    var = tk.DoubleVar(value=preferences.SCALE["v"] / preferences.SCALE_UNIT)
    pct = tk.Label(w, width=6, font=(theme.UI_FONT, 12, "bold"))

    def apply(_v=None):
        app.set_scale(var.get() * preferences.SCALE_UNIT)
        app.reposition()
        pct.config(text=f"{int(round(preferences.SCALE['v'] / preferences.SCALE_UNIT * 100))}%")

    def commit(_e=None):
        app.set_size(var.get())
        if app.size_win is not None:
            app._place_above_pet(w)  # the pet grew/shrank: stay directly above it

    def reset():
        var.set(1.0)
        apply()
        commit()

    def close():
        commit()
        app.size_win = None
        w.destroy()

    tk.Label(w, text="Pet size", font=(theme.UI_FONT, 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
    scale = ttk.Scale(w, from_=0.3, to=3.0, orient="horizontal", length=260, variable=var, command=apply)
    scale.grid(row=1, column=0, padx=(14, 6), pady=8)
    scale.bind("<ButtonRelease-1>", commit)
    pct.grid(row=1, column=1, padx=(0, 14))
    row = tk.Frame(w)
    row.grid(row=2, column=0, columnspan=2, sticky="e", padx=14, pady=(0, 12))
    tk.Button(row, text="100%", width=8, command=reset).pack(side="left", padx=(0, 6))
    tk.Button(row, text="Done", width=8, command=close, default="active").pack(side="left")
    w.protocol("WM_DELETE_WINDOW", close)
    w.bind("<Escape>", lambda e: close())
    apply()
    theme.theme_window(w)
    app._place_above_pet(w)


def open_tooltip_text_slider(app):
    """The context menu's text-size slider; settings embeds the same control."""
    existing = getattr(app, "tooltip_text_win", None)
    if existing is not None:
        try:
            existing.lift()
            return
        except tk.TclError:
            app.tooltip_text_win = None
    from tkinter import ttk
    w = app.tooltip_text_win = tk.Toplevel(app.root)
    w.title("Tooltip text size")
    w.attributes("-topmost", True)
    w.resizable(False, False)
    var = tk.DoubleVar(w, value=app.tooltip_text_scale())
    pct = tk.Label(w, width=6, font=(theme.UI_FONT, 12, "bold"))

    def preview(_v=None):
        pct.config(text=f"{round(var.get() * 100)}%")

    def commit(_e=None):
        app.set_tooltip_text_scale(var.get())

    def reset():
        var.set(1.0)
        preview()
        commit()

    def close():
        commit()
        app.tooltip_text_win = None
        w.destroy()

    tk.Label(w, text="Tooltip text size", font=(theme.UI_FONT, 10, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
    slider = ttk.Scale(w, from_=0.5, to=2.5, orient="horizontal", length=260,
                       variable=var, command=preview)
    slider.grid(row=1, column=0, padx=(14, 6), pady=8)
    slider.bind("<ButtonRelease-1>", commit)
    slider.bind("<KeyRelease>", commit)
    pct.grid(row=1, column=1, padx=(0, 14))
    row = tk.Frame(w)
    row.grid(row=2, column=0, columnspan=2, sticky="e", padx=14, pady=(0, 12))
    tk.Button(row, text="100%", width=8, command=reset).pack(side="left", padx=(0, 6))
    tk.Button(row, text="Done", width=8, command=close, default="active").pack(side="left")
    w.protocol("WM_DELETE_WINDOW", close)
    w.bind("<Escape>", lambda e: close())
    preview()
    theme.theme_window(w)
    app._place_above_pet(w)


def open_answer_slider(app):
    """Slider for how long a permission prompt can be answered from the pet: 0 (off) to 5 minutes."""
    if getattr(app, "answer_win", None) is not None:
        try:
            app.answer_win.lift()
            return
        except tk.TclError:
            app.answer_win = None
    from tkinter import ttk
    w = app.answer_win = tk.Toplevel(app.root)
    w.title("Answer timeout")
    w.attributes("-topmost", True)
    w.resizable(False, False)
    var = tk.DoubleVar(value=preferences.ANSWER_WAIT["v"])
    val = tk.Label(w, width=10, font=(theme.UI_FONT, 12, "bold"))

    def apply(_v=None):
        preferences.ANSWER_WAIT["v"] = preferences.clamp_wait(round(var.get() / 15) * 15)  # steps of 15 s
        val.config(text=textfmt.fmt_wait(preferences.ANSWER_WAIT["v"]))

    def commit(_e=None):
        apply()
        app.set_answer_wait(preferences.ANSWER_WAIT["v"])

    def reset():
        var.set(180)
        commit()

    def close():
        commit()
        app.answer_win = None
        w.destroy()

    tk.Label(w, text="Answer permission prompts from the pet for up to:", font=(theme.UI_FONT, 10, "bold")
             ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
    scale = ttk.Scale(w, from_=0, to=preferences.MAX_WAIT, orient="horizontal", length=260, variable=var, command=apply)
    scale.grid(row=1, column=0, padx=(14, 6), pady=8)
    scale.bind("<ButtonRelease-1>", commit)
    val.grid(row=1, column=1, padx=(0, 14))
    tk.Label(w, justify="left", wraplength=360, fg="#6b7280", font=(theme.UI_FONT, 8),
             text="0 means no limit: the pet waits until you answer (or until it is closed). "
                  "While the pet waits, Claude Code's own prompt is still shown and the first answer wins, "
                  "except for background subagents, where Claude Code may hold its prompt until this time is up "
                  "- with no limit, until you answer from the pet."
             ).grid(row=2, column=0, columnspan=2, sticky="w", padx=14)
    row = tk.Frame(w)
    row.grid(row=3, column=0, columnspan=2, sticky="e", padx=14, pady=(8, 12))
    tk.Button(row, text="Default (3 min)", command=reset).pack(side="left", padx=(0, 6))
    tk.Button(row, text="Done", width=8, command=close, default="active").pack(side="left")
    w.protocol("WM_DELETE_WINDOW", close)
    w.bind("<Escape>", lambda e: close())
    apply()
    theme.theme_window(w)
    app._place_above_pet(w)


def open_done_slider(app):
    """Slider for how long a finished session's pet stays: 0 (never cleared) to 30 minutes."""
    if getattr(app, "done_win", None) is not None:
        try:
            app.done_win.lift()
            return
        except tk.TclError:
            app.done_win = None
    from tkinter import ttk
    w = app.done_win = tk.Toplevel(app.root)
    w.title("Clear finished sessions")
    w.attributes("-topmost", True)
    w.resizable(False, False)
    var = tk.DoubleVar(value=preferences.DONE_TIMEOUT["v"])
    val = tk.Label(w, width=10, font=(theme.UI_FONT, 12, "bold"))

    def apply(_v=None):
        preferences.DONE_TIMEOUT["v"] = preferences.clamp_done(var.get())  # whole minutes
        val.config(text="never" if preferences.DONE_TIMEOUT["v"] <= 0 else f"{preferences.DONE_TIMEOUT['v']} min")

    def commit(_e=None):
        apply()
        app.set_done_timeout(preferences.DONE_TIMEOUT["v"])

    def reset():
        var.set(3)
        commit()

    def close():
        commit()
        app.done_win = None
        w.destroy()

    tk.Label(w, text="Clear a finished session's pet after:", font=(theme.UI_FONT, 10, "bold")
             ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
    scale = ttk.Scale(w, from_=0, to=preferences.MAX_WAIT // 60, orient="horizontal", length=260, variable=var, command=apply)
    scale.grid(row=1, column=0, padx=(14, 6), pady=8)
    scale.bind("<ButtonRelease-1>", commit)
    val.grid(row=1, column=1, padx=(0, 14))
    tk.Label(w, justify="left", wraplength=360, fg="#6b7280", font=(theme.UI_FONT, 8),
             text="Counted from when the session finished. 0 keeps finished pets until you dismiss them "
                  "(right-click > Dismiss / Clear finished). A new message brings a cleared session back."
             ).grid(row=2, column=0, columnspan=2, sticky="w", padx=14)
    row = tk.Frame(w)
    row.grid(row=3, column=0, columnspan=2, sticky="e", padx=14, pady=(8, 12))
    tk.Button(row, text="Default (3 min)", command=reset).pack(side="left", padx=(0, 6))
    tk.Button(row, text="Done", width=8, command=close, default="active").pack(side="left")
    w.protocol("WM_DELETE_WINDOW", close)
    w.bind("<Escape>", lambda e: close())
    apply()
    theme.theme_window(w)
    app._place_above_pet(w)


def reset_scale(app):
    app.set_size(1.0)


def open_health_slider(app):
    """Slider for how often a working session's process is checked: 0 (off) to 5 minutes."""
    if getattr(app, "health_win", None) is not None:
        try:
            app.health_win.lift()
            return
        except tk.TclError:
            app.health_win = None
    from tkinter import ttk
    w = app.health_win = tk.Toplevel(app.root)
    w.title("Health check")
    w.attributes("-topmost", True)
    w.resizable(False, False)
    var = tk.DoubleVar(value=preferences.HEALTH["v"])
    val = tk.Label(w, width=10, font=(theme.UI_FONT, 12, "bold"))

    def apply(_v=None):
        preferences.HEALTH["v"] = preferences.clamp_health(round(var.get() / 5) * 5)  # steps of 5 s
        val.config(text="off" if preferences.HEALTH["v"] <= 0 else textfmt.fmt_wait(preferences.HEALTH["v"]))

    def commit(_e=None):
        apply()
        app.set_health_interval(preferences.HEALTH["v"])

    def reset():
        var.set(15)
        commit()

    def close():
        commit()
        app.health_win = None
        w.destroy()

    tk.Label(w, text="Check that working sessions are still running every:", font=(theme.UI_FONT, 10, "bold")
             ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
    scale = ttk.Scale(w, from_=0, to=preferences.MAX_HEALTH, orient="horizontal", length=260, variable=var, command=apply)
    scale.grid(row=1, column=0, padx=(14, 6), pady=8)
    scale.bind("<ButtonRelease-1>", commit)
    val.grid(row=1, column=1, padx=(0, 14))
    tk.Label(w, justify="left", wraplength=360, fg="#6b7280", font=(theme.UI_FONT, 8),
             text="A working or waiting session whose Claude Code / Codex process has ended (the app was closed, or "
                  "the conversation moved to a new session) is shown as finished. 0 turns the check off. Sessions in "
                  "WSL can't be checked; use right-click > Mark as finished for those."
             ).grid(row=2, column=0, columnspan=2, sticky="w", padx=14)
    row = tk.Frame(w)
    row.grid(row=3, column=0, columnspan=2, sticky="e", padx=14, pady=(8, 12))
    tk.Button(row, text="Default (15 s)", command=reset).pack(side="left", padx=(0, 6))
    tk.Button(row, text="Done", width=8, command=close, default="active").pack(side="left")
    w.protocol("WM_DELETE_WINDOW", close)
    w.bind("<Escape>", lambda e: close())
    apply()
    theme.theme_window(w)
    app._place_above_pet(w)
