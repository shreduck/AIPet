"""CoworkFlow: the tray app's cowork operations."""
import os
import sys
import subprocess
import threading
import tkinter as tk

from ..ui import theme
from ..integrations import cowork as hook_cowork
from ..integrations import locations as hook_locations
from .presentation import APP_NAME

IS_MAC = sys.platform == "darwin"


class CoworkFlow:
    """Owns the instructions window; receives root/UI dispatch and a path-opening callback."""

    def __init__(self, app, open_path):
        self.app = app
        self.open_path = open_path
        self.win = None

    def show(self):
        if getattr(self, "win", None) is not None:
            try:
                self.win.lift()
                return
            except tk.TclError:
                self.win = None
        threading.Thread(target=self.prepare, daemon=True).start()

    def prepare(self):
        app = self.app
        try:
            res = hook_cowork.build_plugin()
        except Exception as e:
            err = f"Couldn't build the Cowork plugin:\n{e}"
            app.ui(lambda: app.info(err, error=True))
            return
        app.ui(lambda: self.build(res))

    def build(self, res):
        app = self.app
        if getattr(self, "win", None) is not None:
            return
        win = self.win = tk.Toplevel(app.root)
        win.title(f"{APP_NAME} - Cowork and CLI plugin")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        grey, wrap = "#6b7280", 470

        def close():
            self.win = None
            win.destroy()

        def copy(text):
            app.root.clipboard_clear()
            app.root.clipboard_append(text)

        def h(text):
            tk.Label(win, text=text, font=(theme.UI_FONT, 11, "bold")).pack(anchor="w", padx=16, pady=(12, 2))

        def p(text, **kw):
            tk.Label(win, text=text, justify="left", wraplength=wrap, font=(theme.UI_FONT, 9), **kw
                     ).pack(anchor="w", padx=16)

        def path_row(path, open_cmd):
            row = tk.Frame(win)
            row.pack(fill="x", padx=16, pady=(4, 2))
            e = tk.Entry(row, width=58, font=("Consolas", 9))
            e.insert(0, path)
            e.configure(state="readonly")
            e.pack(side="left", fill="x", expand=True)
            tk.Button(row, text="Copy", command=lambda: copy(path)).pack(side="left", padx=(6, 0))
            tk.Button(row, text="Open folder", command=open_cmd).pack(side="left", padx=(6, 0))

        def reveal(path):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                self.open_path(os.path.dirname(path))

        h("Claude desktop app (Cowork)")
        p("Cowork runs its own Claude Code that never reads settings.json, so the pet's hooks reach it as a plugin. "
          "Only the Claude app can install plugins, so AIPet.exe can't do this step for you. Your plugin zip:",
          fg=grey)
        path_row(res["zip"], lambda: reveal(res["zip"]))
        p("1. Open the Claude app > Customize > Plugins.\n"
          "2. Upload a plugin and pick " + hook_cowork.COWORK_ZIP + ". Make sure the plugin and its hooks are enabled.\n"
          "3. Restart the Claude app, then start a new Cowork session - it appears on the pet like any other.\n"
          "To remove it: Customize > Plugins > " + hook_cowork.PLUGIN_NAME + " > Uninstall.")

        h("Claude Code CLI (optional)")
        p("The terminal, VS Code and the app's Code tab already work through settings.json (Claude Code hooks > "
          "Install). Use the plugin there only instead of those hooks, never both, or every event reaches the pet "
          "twice. Run in a terminal:", fg=grey)
        cmds = "\n".join(hook_cowork.cli_plugin_commands(res["market"]))
        box = tk.Text(win, height=2, width=64, font=("Consolas", 9), relief="solid", borderwidth=1)
        box.insert("1.0", cmds)
        box.configure(state="disabled")
        box.pack(anchor="w", padx=16, pady=(4, 2))
        row = tk.Frame(win)
        row.pack(fill="x", padx=16)
        tk.Button(row, text="Copy commands", command=lambda: copy(cmds)).pack(side="left")
        tk.Button(row, text="Open folder", command=lambda: self.open_path(res["market"])).pack(side="left", padx=(6, 0))
        p(f"Inside Claude Code the same works with /plugin marketplace add and /plugin install. "
          f"To remove it: claude plugin uninstall {hook_cowork.PLUGIN_NAME}@{hook_cowork.PLUGIN_MARKET}", fg=grey)

        p("\nThe plugin calls the hook in " + hook_locations.INSTALL_DIR + ", which AIPet keeps up to date. "
          "Rebuild and re-upload only if that folder moves.", fg=grey)
        tk.Button(win, text="Close", width=12, command=close, default="active").pack(anchor="e", padx=16, pady=12)
        win.protocol("WM_DELETE_WINDOW", close)
        theme.theme_window(win)
        win.update_idletasks()
        win.geometry(f"+{max(0, (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2)}"
                     f"+{max(0, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)}")
