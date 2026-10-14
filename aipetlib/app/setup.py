"""SetupFlow: the tray app's setup operations."""
import os
import sys
import subprocess
import threading
import tkinter as tk
import webbrowser

from ..runtime import paths
from ..ui import theme
from ..integrations import cowork as hook_cowork
from ..integrations import discovery as hook_discovery
from .presentation import APP_NAME

IS_MAC = sys.platform == "darwin"


SETUP_MARKER = os.path.join(paths.HOME_DIR, "setup-done")

def setup_is_done():
    return os.path.exists(SETUP_MARKER)

def mark_setup_done():
    try:
        os.makedirs(paths.HOME_DIR, exist_ok=True)
        open(SETUP_MARKER, "w").close()
    except OSError:
        pass


class SetupFlow:
    """Owns the setup window; uses root, UI dispatch, dialogs, Cowork flow and hook jobs."""

    def __init__(self, app):
        self.app = app
        self.win = None

    def show(self, check_stopped=False):
        if self.win is not None:
            try:
                self.win.lift()
                return
            except tk.TclError:
                self.win = None
        threading.Thread(target=self.detect, args=(check_stopped,), daemon=True).start()

    def detect(self, check_stopped):
        app = self.app
        try:
            targets, hidden = hook_discovery.detect_targets(check_stopped)
        except Exception as e:
            app.ui(lambda: app.info(f"Couldn't detect Claude Code installs:\n{e}", error=True))
            return
        zip_path = None
        if os.name == "nt" or IS_MAC:
            try:
                zip_path = hook_cowork.build_plugin()["zip"]  # the installer always leaves the Cowork zip ready
            except Exception:
                pass
        app.ui(lambda: self.build(targets, hidden, zip_path))

    def build(self, targets, hidden, zip_path=None):
        app = self.app
        if self.win is not None:
            return
        win = self.win = tk.Toplevel(app.root)
        win.title(f"{APP_NAME} setup")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        grey = "#6b7280"

        tk.Label(win, text="Where should AIPet watch Claude Code?", font=(theme.UI_FONT, 12, "bold")
                 ).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, justify="left", wraplength=430, fg=grey,
                 text="These are the Claude Code installs I found. Tick the ones to hook up.").pack(anchor="w", padx=16)

        picks, runtime = {}, {}
        body = tk.Frame(win)
        body.pack(fill="x", padx=16, pady=10)
        stopped = [t for t in targets if t["kind"] == "unknown"]
        targets = [t for t in targets if t["kind"] != "unknown"]
        for t in targets:
            usable = t["kind"] in ("ready", "installed", "outdated")
            var = tk.BooleanVar(value=t["kind"] in ("ready", "outdated"))
            picks[t["key"]] = var
            row = tk.Frame(body)
            row.pack(fill="x", pady=3)
            tk.Checkbutton(row, text=t["label"], variable=var, state="normal" if usable else "disabled",
                           font=(theme.UI_FONT, 10)).pack(anchor="w")
            state_text, color = {
                "ready": ("not installed yet", grey),
                "installed": ("hooks installed and up to date", "#15803d"),
                "outdated": ("hooks installed but out of date - tick to update them", "#b45309"),
                "blocked": ("settings.json can't be read (invalid JSON?) - fix it first; nothing is changed", "#b91c1c"),
                "needs_python": (t["note"], "#b91c1c"),
                "missing": (t["note"], grey),
            }.get(t["kind"], (t["note"], grey))
            tk.Label(row, text=state_text, fg=color, font=(theme.UI_FONT, 8, "bold"), wraplength=400, justify="left"
                     ).pack(anchor="w", padx=(26, 0))
            if t["kind"] in ("ready", "installed", "outdated"):
                tk.Label(row, text=t["note"], fg=grey, font=(theme.UI_FONT, 8), wraplength=400, justify="left"
                         ).pack(anchor="w", padx=(26, 0))
            if t["key"] == "mac" and usable:
                self.runtime_controls(row, t, runtime)
        if not targets and not stopped:
            tk.Label(body, text="No Claude Code installs found.", fg=grey).pack(anchor="w")
        if hidden:
            tk.Label(body, fg=grey, font=(theme.UI_FONT, 8), wraplength=420, justify="left",
                     text=f"{hidden} WSL distro{'s' if hidden != 1 else ''} hidden: Claude Code isn't installed there."
                     ).pack(anchor="w", pady=(6, 0))

        if stopped:
            tk.Label(body, fg=grey, font=(theme.UI_FONT, 8), wraplength=420, justify="left",
                     text="Not checked (not running): " + ", ".join(t["label"][5:] for t in stopped)
                     ).pack(anchor="w", pady=(6, 0))
            def check_stopped():
                if app.ask("Checking stopped WSL distros starts them. Continue?"):
                    self.close()
                    self.show(check_stopped=True)
            tk.Button(win, text="Check stopped WSL distros (starts them)...", command=check_stopped
                      ).pack(anchor="w", padx=16, pady=(0, 6))

        tk.Label(win, justify="left", wraplength=430, fg=grey, font=(theme.UI_FONT, 8),
                 text="Each settings.json is backed up first, and your other hooks and settings are kept. "
                      "Only Claude Code sessions started afterwards show up. You can change this any time from "
                      "the tray menu: Claude Code hooks."
                 ).pack(anchor="w", padx=16, pady=(0, 8))

        cw = tk.LabelFrame(win, text=" Claude desktop app - Cowork ", font=(theme.UI_FONT, 9, "bold"))
        cw.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(cw, justify="left", wraplength=410, font=(theme.UI_FONT, 8),
                 text="Cowork ignores settings.json, so it needs the hooks as a plugin, which only the Claude app "
                      "can install. AIPet built it for you:\n" + (zip_path or hook_cowork.COWORK_ZIP + " (not built yet)") + "\n"
                      "1. Claude app > Customize > Plugins > upload that zip, and keep its hooks on.\n"
                      "2. Restart the Claude app and start a new Cowork session."
                 ).pack(anchor="w", padx=8, pady=(4, 2))
        tk.Button(cw, text="Build the zip and show full instructions (app + CLI)...", command=app.cowork.show
                  ).pack(anchor="w", padx=8, pady=(0, 6))

        def install():
            keys = [k for k, v in picks.items() if v.get()]
            if not keys:
                app.info("Nothing is ticked. Tick at least one install, or press Skip for now.")
                return
            if app.hooks.busy:
                app.info("Another hook operation is still running - try again in a moment.")
                return
            self.close()
            app.hooks.busy = True
            runtimes = {"mac": runtime["var"].get()} if "var" in runtime else {}
            threading.Thread(target=app.hooks._update_job, args=(keys, runtimes), daemon=True).start()

        def skip():
            self.close()
            app.info("No problem. You can set this up later from the tray icon: Claude Code hooks, "
                      "or Run setup again.")

        buttons = tk.Frame(win)
        buttons.pack(fill="x", padx=16, pady=(0, 14))
        tk.Button(buttons, text="Install selected", command=install, default="active", width=16).pack(side="right")
        tk.Button(buttons, text="Skip for now", command=skip, width=12).pack(side="right", padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", skip)
        theme.theme_window(win)
        win.update_idletasks()
        win.geometry(f"+{max(0, (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2)}"
                     f"+{max(0, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)}")

    def runtime_controls(self, parent, t, runtime):
        """macOS only: choose between the user's Python 3 (fast) and the bundled hook (nothing to install)."""
        py = t.get("python")
        var = runtime["var"] = tk.StringVar(value="python" if py else "builtin")
        box = tk.Frame(parent)
        box.pack(anchor="w", padx=(26, 0), pady=(4, 0))
        tk.Label(box, text="Hook runtime", font=(theme.UI_FONT, 8, "bold")).pack(anchor="w")
        tk.Radiobutton(box, variable=var, value="python", state="normal" if py else "disabled", font=(theme.UI_FONT, 9),
                       text=f"Your Python 3 (faster) - {py}" if py else "Your Python 3 (faster) - not found"
                       ).pack(anchor="w")
        tk.Radiobutton(box, variable=var, value="builtin", font=(theme.UI_FONT, 9),
                       text="Built-in hook (nothing to install)").pack(anchor="w")
        warn = tk.Label(box, fg="#b45309", font=(theme.UI_FONT, 8), wraplength=380, justify="left")
        warn.pack(anchor="w", pady=(2, 0))

        def refresh(*_):
            warn.config(text="Heads up: Python 3 is faster. The built-in hook works with nothing to install, but it "
                              "starts a bigger program on every tool call, so expect a small delay. You can switch "
                              "any time by running setup again." if var.get() == "builtin" else "")
        var.trace_add("write", refresh)
        refresh()
        if not py:
            row = tk.Frame(box)
            row.pack(anchor="w", pady=(4, 0))
            tk.Button(row, text="How to get Python 3...", command=self.python_help).pack(side="left")
            tk.Button(row, text="Re-check", command=self.recheck).pack(side="left", padx=6)

    def recheck(self):
        self.close()
        self.show()

    def python_help(self):
        app = self.app
        if app.ask("Python 3 makes AIPet's hook faster.\n\nThe simplest way is the installer from python.org. "
                    "Open the download page in your browser?"):
            webbrowser.open("https://www.python.org/downloads/macos/")
        if app.ask("Alternatively, Apple's Command Line Tools include Python 3 (about 1 GB). This opens Apple's own "
                    "installer window; nothing is installed unless you confirm there.\n\nOpen it now?"):
            try:
                subprocess.Popen(["xcode-select", "--install"])
            except OSError as e:
                app.info(f"Couldn't start Apple's installer:\n{e}", error=True)
        app.info("When the installation has finished, press Re-check in the setup window.")

    def close(self):
        mark_setup_done()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None
