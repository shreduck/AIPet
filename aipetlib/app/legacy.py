"""LegacyFlow: the tray app's legacy operations."""
import os
import sys
import threading
import tkinter as tk

from ..runtime import diagnostics
from ..ui import theme
from ..integrations import targets as hook_targets
from ..integrations import legacy
from .presentation import APP_NAME

IS_MAC = sys.platform == "darwin"


class LegacyFlow:
    """Schedules reminders and builds migration findings; uses pet snapshots, UI dispatch and hook jobs."""

    def __init__(self, app):
        self.app = app

    def tick(self):
        """Tk thread: check once now (if not yet reminded today), then every hour, so a pet running for days still
        reminds on each new day."""
        app = self.app
        if legacy.due_today():
            self.check()
        app.root.after(3600 * 1000, self.tick)

    def check(self, force=False):
        app = self.app
        targets = {hook_targets.LOCAL: "This Mac" if IS_MAC else "This PC (Windows)"} if (os.name == "nt" or IS_MAC) else {}
        targets.update({"wsl:" + n: f"WSL: {n}" for n, st in app.distros if st.lower() == "running"})
        pets = [p.data for p in app.pet.pets.values()]

        def job():
            try:
                found = legacy.findings(targets, pets)
            except Exception as e:
                found = []
                diagnostics.log_error(f"legacy check failed: {e!r}")
            if found or force:
                app.ui(lambda: self.show(found))
            if found:
                legacy.mark_warned()
        threading.Thread(target=job, daemon=True).start()

    def show(self, found):
        app = self.app
        if not found:
            app.info("Nothing uses the old Claude Pet name any more. You can delete the old folder "
                      f"({legacy.LEGACY_DIR}) if it still exists.")
            return
        win = tk.Toplevel(app.root)
        win.title(f"{APP_NAME}: update old hooks")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        tk.Label(win, text="Some hooks still use the old Claude Pet name", font=(theme.UI_FONT, 12, "bold")
                 ).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, fg="#6b7280", justify="left", wraplength=480,
                 text="They keep working for now, but support for the old name will be removed in a future version. "
                      "This reminder shows once a day while anything is left.").pack(anchor="w", padx=16)
        body = tk.Frame(win)
        body.pack(fill="x", padx=16, pady=8)
        for what, how in found:
            tk.Label(body, text=what, font=(theme.UI_FONT, 9, "bold"), justify="left", wraplength=480).pack(anchor="w", pady=(6, 0))
            tk.Label(body, text=how, font=(theme.UI_FONT, 9), justify="left", wraplength=480).pack(anchor="w")
        row = tk.Frame(win)
        row.pack(fill="x", padx=16, pady=(6, 14))
        keys = [k for k in [hook_targets.LOCAL] + ["wsl:" + n for n, _ in app.distros] if any(
            f.startswith(("This PC", "This Mac") if k == hook_targets.LOCAL else f"WSL: {k[4:]}:") for f, _ in found)]

        def update():
            win.destroy()
            if app.hooks.busy:
                app.info("Another hook operation is still running - try again in a moment.")
                return
            app.hooks.busy = True
            threading.Thread(target=app.hooks._update_job, args=(keys,), daemon=True).start()
        if keys:
            tk.Button(row, text="Update these hooks now", default="active", command=update).pack(side="right")
        if any("Cowork" in f or "CLI" in f for f, _ in found):
            tk.Button(row, text="Cowork / CLI plugin...", command=lambda: (win.destroy(), app.cowork.show())
                      ).pack(side="right", padx=8)
        tk.Button(row, text="Remind me tomorrow", command=win.destroy).pack(side="left")
        theme.theme_window(win)
        win.update_idletasks()
        win.geometry(f"+{max(0, (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2)}"
                     f"+{max(0, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)}")
