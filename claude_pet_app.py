#!/usr/bin/env python3
"""
Claude Pet - tray application (entry point for ClaudePet.exe).

* Floating pet window (claude_pet.PetApp) that can be hidden to the system tray.
* Tray icon changes colour with the most urgent session and shows a Windows
  notification when a session needs your input.
* Tray menu "Claude Code hooks" installs/removes hooks for Windows and for every
  auto-detected WSL distro.
* "Start with Windows" toggle (HKCU Run key).
"""
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox

import claude_pet as core
import hooks_installer as hi

IS_MAC = sys.platform == "darwin"
try:
    if IS_MAC:  # pystray wants the main thread, which Tk already owns; macOS uses the pet's own menu instead
        raise ImportError
    from PIL import Image, ImageDraw
    import pystray
except ImportError:  # still runs, just without a tray icon
    pystray = None

APP_NAME = "Claude Pet"
PET_STYLES = [("robot", "Robot"), ("mole", "Mole"), ("cat", "Cat")]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/com.claudepet.app.plist")
RANK = {"needs_input": 0, "error": 1, "working": 2, "done": 3, "idle": 4}
_mutex = None
SETUP_MARKER = os.path.join(core.HOME_DIR, "setup-done")
DEBUG_FLAG = os.path.join(core.HOME_DIR, "debug-events")  # while it exists, hooks append to events.log


# --------------------------------------------------------------------------- helpers
def single_instance():
    global _mutex
    if os.name != "nt":
        try:
            import fcntl
            os.makedirs(core.HOME_DIR, exist_ok=True)
            _mutex = open(os.path.join(core.HOME_DIR, "app.lock"), "w")  # kept open for the process lifetime
            fcntl.flock(_mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except (ImportError, OSError):
            pass
        return True
    k32 = ctypes.windll.kernel32
    _mutex = k32.CreateMutexW(None, False, "Local\\ClaudePetSingleton")
    return k32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pyw if os.path.exists(pyw) else sys.executable}" "{os.path.abspath(__file__)}"'


def open_path(path):
    """Open a folder/file with the platform's default handler."""
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["open" if IS_MAC else "xdg-open", path])
    except (AttributeError, OSError):
        pass


def windows_sees_autostart():
    """Ask Windows itself (WMI, served by a normal system service) whether ClaudePet is a startup command.
    A plain registry read can be fooled: a process started inside another app's container (for example the Claude
    desktop app) reads and writes a private overlay of the registry that Windows ignores at login.
    Returns True/False, or None if it can't be determined."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_StartupCommand | Where-Object { $_.Name -eq 'ClaudePet' } | Measure-Object).Count"],
            capture_output=True, text=True, timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        return int(out) > 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def autostart_enabled():
    if IS_MAC:
        return os.path.exists(LAUNCH_AGENT)
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "ClaudePet")
            return True
    except OSError:
        return False


def set_autostart(on):
    if IS_MAC:  # takes effect at the next login
        if on:
            import plistlib
            args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
            os.makedirs(os.path.dirname(LAUNCH_AGENT), exist_ok=True)
            with open(LAUNCH_AGENT, "wb") as f:
                plistlib.dump({"Label": "com.claudepet.app", "ProgramArguments": args, "RunAtLoad": True}, f)
        else:
            try:
                os.remove(LAUNCH_AGENT)
            except FileNotFoundError:
                pass
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "ClaudePet", 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, "ClaudePet")
            except FileNotFoundError:
                pass


def make_icon(state):
    col, dark = core.COLORS.get(state, core.COLORS["idle"]), core.DARK.get(state, core.DARK["idle"])
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    spr = core.load_sprites() if core.STYLE["v"] == "robot" else False
    if spr:  # the robot with the state's face, on a dot in the state colour
        im = core.state_image(state, spr)
        k = min(60 / im.width, 60 / im.height)
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.NEAREST)
        img.paste(im, ((64 - im.width) // 2, 62 - im.height), im)
        d.ellipse((46, 46, 62, 62), fill=col, outline="white", width=2)
    elif core.STYLE["v"] != "cat":  # the mole: head poking out of a hill, ground rim in the state colour
        d.ellipse((2, 44, 62, 62), fill=core.MOLE_EDGE, outline=col, width=4)
        d.ellipse((14, 4, 50, 56), fill=core.MOLE_BODY, outline=core.MOLE_EDGE, width=3)
        if state in ("done", "idle"):
            d.arc((20, 20, 29, 28), 180, 360, fill=core.INK, width=3)
            d.arc((35, 20, 44, 28), 180, 360, fill=core.INK, width=3)
        else:
            for x in (25, 39):
                d.ellipse((x - 3, 17, x + 3, 28), fill=core.INK)
        d.ellipse((22, 29, 42, 44), fill=core.MOLE_NOSE, outline=core.MOLE_NOSE_EDGE, width=2)
        d.ellipse((8, 46, 56, 62), fill=core.MOLE_HILL, outline=core.MOLE_HILL_EDGE, width=2)
    else:
        d.polygon([(13, 24), (17, 4), (29, 15)], fill=col, outline=dark)
        d.polygon([(51, 24), (47, 4), (35, 15)], fill=col, outline=dark)
        d.ellipse((6, 12, 58, 60), fill=col, outline=dark, width=3)
        if state in ("done", "idle"):
            d.arc((17, 30, 29, 40), 180, 360, fill=core.INK, width=3)
            d.arc((35, 30, 47, 40), 180, 360, fill=core.INK, width=3)
        else:
            for x in (23, 41):
                d.ellipse((x - 6, 29, x + 6, 41), fill="white", outline=core.INK, width=2)
                d.ellipse((x - 3, 32, x + 3, 38), fill=core.INK)
    if state == "needs_input":
        d.ellipse((42, 0, 63, 21), fill="#dc2626", outline="white", width=2)
        d.rectangle((51, 4, 54, 12), fill="white")
        d.rectangle((51, 15, 54, 17), fill="white")
    return img


def setup_is_done():
    return os.path.exists(SETUP_MARKER)


def mark_setup_done():
    try:
        os.makedirs(core.HOME_DIR, exist_ok=True)
        open(SETUP_MARKER, "w").close()
    except OSError:
        pass


# --------------------------------------------------------------------------- app
class TrayApp:
    def __init__(self):
        core.ensure_home()
        self.pet = core.PetApp()
        self.root = self.pet.root
        self.q = queue.Queue()
        self.hidden = False
        self.busy = False
        self.setup_win = None
        self.distros = []          # [(name, state)]
        self.probed = False
        self.status = {hi.LOCAL: "checking…"}
        # caches read by the tray thread (never touch Tk from there)
        self.c_muted = self.pet.muted.get()
        self.c_autostart = autostart_enabled()
        self.c_notify = bool(self.pet.cfg.get("notifications", False))
        self.c_wb = "off"
        self._icon_key = None

        self.pet.on_alert = self.on_alert
        self.cowork_win = None
        m = self.pet.menu
        hooks_menu = tk.Menu(m, tearoff=0)
        hooks_menu.configure(postcommand=lambda: self._fill_hooks_menu(hooks_menu))
        if pystray:
            q = m.index("Quit")
            m.insert_cascade(q, label="Claude Code hooks", menu=hooks_menu)
            m.insert_separator(q + 1)
            m.insert_command(m.index("Quit"), label="Hide to tray", command=self.hide)
        else:  # no tray icon (macOS): the tray menu's essentials live in the pet's right-click menu
            self.notify_var = tk.BooleanVar(value=self.c_notify)
            self.autostart_var = tk.BooleanVar(value=self.c_autostart)
            q = m.index("Quit")
            m.insert_separator(q)
            m.insert_cascade(q + 1, label="Claude Code hooks", menu=hooks_menu)
            m.insert_checkbutton(q + 2, label="Notifications", variable=self.notify_var,
                                 command=lambda: self._set_notify(self.notify_var.get()))
            self.debug_var = tk.BooleanVar(value=os.path.exists(DEBUG_FLAG))
            m.insert_checkbutton(m.index("Quit"), label="Log hook events (debug)", variable=self.debug_var,
                                 command=self.toggle_debug)
            self.theme_var = tk.BooleanVar(value=core.T.get("name") == "dark")
            m.insert_checkbutton(m.index("Quit"), label="Dark theme", variable=self.theme_var, command=self.toggle_theme)
            self.style_var = tk.StringVar(value=core.STYLE["v"])
            for key, label in PET_STYLES:
                m.insert_radiobutton(m.index("Quit"), label=f"Pet: {label}", variable=self.style_var, value=key,
                                     command=lambda k=key: self.set_style(k))
            if IS_MAC:
                m.insert_checkbutton(q + 3, label="Start at login", variable=self.autostart_var,
                                     command=self.toggle_autostart)
            m.insert_command(m.index("Quit"), label="Open config folder", command=self.open_config)
        m.entryconfigure(m.index("Quit"), command=self.quit)

        self.icon = None
        if pystray:
            self.icon = pystray.Icon("claude-pet", make_icon("idle"), APP_NAME, menu=self.build_menu())
            self.icon.run_detached()

        threading.Thread(target=self._startup_jobs, daemon=True).start()
        self.pump()

    # ---- threading
    def ui(self, fn):
        """Run fn on the Tk thread."""
        self.q.put(fn)

    def pump(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        except Exception as e:
            print(f"[claude-pet] ui error: {e}", file=sys.stderr)
        self.c_muted = self.pet.muted.get()
        self.c_wb = self.pet.wb.status if self.pet.wb else "off"
        self.update_tray()
        self.root.after(250, self.pump)

    def _check_autostart(self):
        """At startup: show what Windows really sees, not an overlay (see windows_sees_autostart)."""
        seen = windows_sees_autostart()
        if seen is not None and seen != self.c_autostart:
            self.c_autostart = seen
            self.refresh_menu()

    def _startup_jobs(self):
        self._check_autostart()
        try:
            hi.migrate_legacy_backups()  # pull backups over from the old %LOCALAPPDATA% location
        except Exception as e:
            print(f"[claude-pet] backup migration failed: {e}", file=sys.stderr)
        try:
            hi.deploy_files(only_if_deployed=True)  # refresh an existing install only; never create one
            if os.path.isdir(hi.INSTALL_DIR) and (os.name == "nt" or IS_MAC):
                hi.build_plugin()  # keep the Cowork plugin zip next to the exe current
        except Exception:
            pass
        self.refresh_targets()
        stale = [k for k, v in self.status.items() if v == hi.STALE]
        if not setup_is_done():
            self.ui(self.show_setup)  # first run: shows what is installed / up to date / outdated per target
        elif stale:
            self.ui(lambda: self.offer_update(stale))

    # ---- first-run setup
    def show_setup(self, check_stopped=False):
        if self.setup_win is not None:
            try:
                self.setup_win.lift()
                return
            except tk.TclError:
                self.setup_win = None
        threading.Thread(target=self._detect_for_setup, args=(check_stopped,), daemon=True).start()

    def _detect_for_setup(self, check_stopped):
        try:
            targets, hidden = hi.detect_targets(check_stopped)
        except Exception as e:
            self.ui(lambda: self.info(f"Couldn't detect Claude Code installs:\n{e}", error=True))
            return
        zip_path = None
        if os.name == "nt" or IS_MAC:
            try:
                zip_path = hi.build_plugin()["zip"]  # the installer always leaves the Cowork zip ready
            except Exception:
                pass
        self.ui(lambda: self._build_setup(targets, hidden, zip_path))

    def _build_setup(self, targets, hidden, zip_path=None):
        if self.setup_win is not None:
            return
        win = self.setup_win = tk.Toplevel(self.root)
        win.title(f"{APP_NAME} setup")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        grey = "#6b7280"

        tk.Label(win, text="Where should Claude Pet watch Claude Code?", font=("Segoe UI", 12, "bold")
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
                           font=("Segoe UI", 10)).pack(anchor="w")
            state_text, color = {
                "ready": ("not installed yet", grey),
                "installed": ("hooks installed and up to date", "#15803d"),
                "outdated": ("hooks installed but out of date - tick to update them", "#b45309"),
                "blocked": ("settings.json can't be read (invalid JSON?) - fix it first; nothing is changed", "#b91c1c"),
                "needs_python": (t["note"], "#b91c1c"),
                "missing": (t["note"], grey),
            }.get(t["kind"], (t["note"], grey))
            tk.Label(row, text=state_text, fg=color, font=("Segoe UI", 8, "bold"), wraplength=400, justify="left"
                     ).pack(anchor="w", padx=(26, 0))
            if t["kind"] in ("ready", "installed", "outdated"):
                tk.Label(row, text=t["note"], fg=grey, font=("Segoe UI", 8), wraplength=400, justify="left"
                         ).pack(anchor="w", padx=(26, 0))
            if t["key"] == "mac" and usable:
                self._mac_runtime_ui(row, t, runtime)
        if not targets and not stopped:
            tk.Label(body, text="No Claude Code installs found.", fg=grey).pack(anchor="w")
        if hidden:
            tk.Label(body, fg=grey, font=("Segoe UI", 8), wraplength=420, justify="left",
                     text=f"{hidden} WSL distro{'s' if hidden != 1 else ''} hidden: Claude Code isn't installed there."
                     ).pack(anchor="w", pady=(6, 0))

        if stopped:
            tk.Label(body, fg=grey, font=("Segoe UI", 8), wraplength=420, justify="left",
                     text="Not checked (not running): " + ", ".join(t["label"][5:] for t in stopped)
                     ).pack(anchor="w", pady=(6, 0))
            def check_stopped():
                if self.ask("Checking stopped WSL distros starts them. Continue?"):
                    self._close_setup()
                    self.show_setup(check_stopped=True)
            tk.Button(win, text="Check stopped WSL distros (starts them)...", command=check_stopped
                      ).pack(anchor="w", padx=16, pady=(0, 6))

        tk.Label(win, justify="left", wraplength=430, fg=grey, font=("Segoe UI", 8),
                 text="Each settings.json is backed up first, and your other hooks and settings are kept. "
                      "Only Claude Code sessions started afterwards show up. You can change this any time from "
                      "the tray menu: Claude Code hooks."
                 ).pack(anchor="w", padx=16, pady=(0, 8))

        cw = tk.LabelFrame(win, text=" Claude desktop app - Cowork ", font=("Segoe UI", 9, "bold"))
        cw.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(cw, justify="left", wraplength=410, font=("Segoe UI", 8),
                 text="Cowork ignores settings.json, so it needs the hooks as a plugin, which only the Claude app "
                      "can install. Claude Pet built it for you:\n" + (zip_path or hi.COWORK_ZIP + " (not built yet)") + "\n"
                      "1. Claude app > Customize > Plugins > upload that zip, and keep its hooks on.\n"
                      "2. Restart the Claude app and start a new Cowork session."
                 ).pack(anchor="w", padx=8, pady=(4, 2))
        tk.Button(cw, text="Build the zip and show full instructions (app + CLI)...", command=self.show_cowork
                  ).pack(anchor="w", padx=8, pady=(0, 6))

        def install():
            keys = [k for k, v in picks.items() if v.get()]
            if not keys:
                self.info("Nothing is ticked. Tick at least one install, or press Skip for now.")
                return
            if self.busy:
                self.info("Another hook operation is still running - try again in a moment.")
                return
            self._close_setup()
            self.busy = True
            runtimes = {"mac": runtime["var"].get()} if "var" in runtime else {}
            threading.Thread(target=self._update_job, args=(keys, runtimes), daemon=True).start()

        def skip():
            self._close_setup()
            self.info("No problem. You can set this up later from the tray icon: Claude Code hooks, "
                      "or Run setup again.")

        buttons = tk.Frame(win)
        buttons.pack(fill="x", padx=16, pady=(0, 14))
        tk.Button(buttons, text="Install selected", command=install, default="active", width=16).pack(side="right")
        tk.Button(buttons, text="Skip for now", command=skip, width=12).pack(side="right", padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", skip)
        win.update_idletasks()
        win.geometry(f"+{max(0, (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2)}"
                     f"+{max(0, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)}")

    def _mac_runtime_ui(self, parent, t, runtime):
        """macOS only: choose between the user's Python 3 (fast) and the bundled hook (nothing to install)."""
        py = t.get("python")
        var = runtime["var"] = tk.StringVar(value="python" if py else "builtin")
        box = tk.Frame(parent)
        box.pack(anchor="w", padx=(26, 0), pady=(4, 0))
        tk.Label(box, text="Hook runtime", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Radiobutton(box, variable=var, value="python", state="normal" if py else "disabled", font=("Segoe UI", 9),
                       text=f"Your Python 3 (faster) - {py}" if py else "Your Python 3 (faster) - not found"
                       ).pack(anchor="w")
        tk.Radiobutton(box, variable=var, value="builtin", font=("Segoe UI", 9),
                       text="Built-in hook (nothing to install)").pack(anchor="w")
        warn = tk.Label(box, fg="#b45309", font=("Segoe UI", 8), wraplength=380, justify="left")
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
            tk.Button(row, text="Re-check", command=self._recheck_setup).pack(side="left", padx=6)

    # ---- Cowork / CLI plugin
    def show_cowork(self):
        if getattr(self, "cowork_win", None) is not None:
            try:
                self.cowork_win.lift()
                return
            except tk.TclError:
                self.cowork_win = None
        threading.Thread(target=self._cowork_job, daemon=True).start()

    def _cowork_job(self):
        try:
            res = hi.build_plugin()
        except Exception as e:
            err = f"Couldn't build the Cowork plugin:\n{e}"
            self.ui(lambda: self.info(err, error=True))
            return
        self.ui(lambda: self._build_cowork(res))

    def _build_cowork(self, res):
        if getattr(self, "cowork_win", None) is not None:
            return
        win = self.cowork_win = tk.Toplevel(self.root)
        win.title(f"{APP_NAME} - Cowork and CLI plugin")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        grey, wrap = "#6b7280", 470

        def close():
            self.cowork_win = None
            win.destroy()

        def copy(text):
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

        def h(text):
            tk.Label(win, text=text, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(12, 2))

        def p(text, **kw):
            tk.Label(win, text=text, justify="left", wraplength=wrap, font=("Segoe UI", 9), **kw
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
                open_path(os.path.dirname(path))

        h("Claude desktop app (Cowork)")
        p("Cowork runs its own Claude Code that never reads settings.json, so the pet's hooks reach it as a plugin. "
          "Only the Claude app can install plugins, so ClaudePet.exe can't do this step for you. Your plugin zip:",
          fg=grey)
        path_row(res["zip"], lambda: reveal(res["zip"]))
        p("1. Open the Claude app > Customize > Plugins.\n"
          "2. Upload a plugin and pick " + hi.COWORK_ZIP + ". Make sure the plugin and its hooks are enabled.\n"
          "3. Restart the Claude app, then start a new Cowork session - it appears on the pet like any other.\n"
          "To remove it: Customize > Plugins > " + hi.PLUGIN_NAME + " > Uninstall.")

        h("Claude Code CLI (optional)")
        p("The terminal, VS Code and the app's Code tab already work through settings.json (Claude Code hooks > "
          "Install). Use the plugin there only instead of those hooks, never both, or every event reaches the pet "
          "twice. Run in a terminal:", fg=grey)
        cmds = "\n".join(hi.cli_plugin_commands(res["market"]))
        box = tk.Text(win, height=2, width=64, font=("Consolas", 9), relief="solid", borderwidth=1)
        box.insert("1.0", cmds)
        box.configure(state="disabled")
        box.pack(anchor="w", padx=16, pady=(4, 2))
        row = tk.Frame(win)
        row.pack(fill="x", padx=16)
        tk.Button(row, text="Copy commands", command=lambda: copy(cmds)).pack(side="left")
        tk.Button(row, text="Open folder", command=lambda: open_path(res["market"])).pack(side="left", padx=(6, 0))
        p(f"Inside Claude Code the same works with /plugin marketplace add and /plugin install. "
          f"To remove it: claude plugin uninstall {hi.PLUGIN_NAME}@{hi.PLUGIN_MARKET}", fg=grey)

        p("\nThe plugin calls the hook in " + hi.INSTALL_DIR + ", which Claude Pet keeps up to date. "
          "Rebuild and re-upload only if that folder moves.", fg=grey)
        tk.Button(win, text="Close", width=12, command=close, default="active").pack(anchor="e", padx=16, pady=12)
        win.protocol("WM_DELETE_WINDOW", close)
        win.update_idletasks()
        win.geometry(f"+{max(0, (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2)}"
                     f"+{max(0, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)}")

    # ---- pet right-click: the same "Claude Code hooks" menu as the tray
    def _fill_hooks_menu(self, menu):
        menu.delete(0, "end")

        def target(name, key):
            st = self.status.get(key, "checking…")
            sub = tk.Menu(menu, tearoff=0)
            sub.add_command(label="Install / update hooks", command=lambda: self.confirm(key, True))
            sub.add_command(label="Remove hooks", command=lambda: self.confirm(key, False))
            sub.add_separator()
            rest = tk.Menu(sub, tearoff=0)
            backups = hi.list_backups(key)[:15]
            if not backups:
                rest.add_command(label="No backups yet", state="disabled")
            for b in backups:
                note = "no settings.json" if b["absent"] else (
                    ("with pet hooks" if b["pet_hooks"] else "no pet hooks") + ("" if b["valid_json"] else ", invalid JSON"))
                rest.add_command(label=f"{b['when']} - {b['reason']} ({note}){'  [original]' if b['original'] else ''}",
                                 command=lambda b=b: self.confirm_restore(key, b))
            sub.add_cascade(label="Restore backup", menu=rest)
            sub.add_command(label="Back up now", command=lambda: self.start_job(key, "backup"))
            sub.add_command(label="Open backups folder", command=lambda: self.open_backups(key))
            menu.add_cascade(label=f"{'✓ ' if st.startswith('installed') else ''}{name}  ({st})", menu=sub)

        target("This Mac" if IS_MAC else "This PC (Windows)", hi.LOCAL)
        menu.add_separator()
        if self.distros:
            for n, _ in self.distros:
                target(f"WSL: {n}", "wsl:" + n)
        else:
            menu.add_command(label="No WSL distros found" if self.probed else "Detecting WSL distros…", state="disabled")
        menu.add_separator()
        menu.add_command(label="Cowork (Claude desktop app)...", command=self.show_cowork)
        menu.add_command(label="Run setup again...", command=self.show_setup)
        menu.add_command(label="Re-detect / refresh status",
                         command=lambda: threading.Thread(target=self.refresh_targets, daemon=True).start())

    def _recheck_setup(self):
        self._close_setup()
        self.show_setup()

    def python_help(self):
        if self.ask("Python 3 makes Claude Pet's hook faster.\n\nThe simplest way is the installer from python.org. "
                    "Open the download page in your browser?"):
            webbrowser.open("https://www.python.org/downloads/macos/")
        if self.ask("Alternatively, Apple's Command Line Tools include Python 3 (about 1 GB). This opens Apple's own "
                    "installer window; nothing is installed unless you confirm there.\n\nOpen it now?"):
            try:
                subprocess.Popen(["xcode-select", "--install"])
            except OSError as e:
                self.info(f"Couldn't start Apple's installer:\n{e}", error=True)
        self.info("When the installation has finished, press Re-check in the setup window.")

    def _close_setup(self):
        mark_setup_done()
        if self.setup_win is not None:
            try:
                self.setup_win.destroy()
            except tk.TclError:
                pass
            self.setup_win = None

    def offer_update(self, keys):
        names = "\n".join("  - " + hi.describe(k) for k in keys)
        if self.ask("Claude Pet's hooks here point at an older location and may not be working:\n\n"
                    f"{names}\n\nUpdate them now? Your other hooks and settings are kept, and each file is "
                    "backed up first."):
            if self.busy:
                self.info("Another hook operation is still running - use tray > Claude Code hooks > Install / update.")
                return
            self.busy = True
            threading.Thread(target=self._update_job, args=(keys,), daemon=True).start()

    def _update_job(self, keys, runtimes=None):
        results = []
        try:
            for k in keys:
                try:
                    results.append(f"{hi.describe(k)}: {hi.install(k, (runtimes or {}).get(k)).splitlines()[0]}")
                except Exception as e:
                    results.append(f"{hi.describe(k)}: FAILED - {e}")
                self.status[k] = hi.status(k)
            msg = "\n\n".join(results)
            self.ui(lambda: self.info(msg))
        finally:
            self.busy = False
            self.refresh_menu()

    def refresh_targets(self):
        self.status[hi.LOCAL] = hi.status(hi.LOCAL) if (os.name == "nt" or IS_MAC) else "n/a"
        self.distros = hi.list_distros()
        self.probed = True
        for name, state in self.distros:
            # Only probe running distros so we never boot one just to show a menu.
            self.status["wsl:" + name] = hi.status("wsl:" + name) if state.lower() == "running" else f"{state.lower()} · status unknown"
        self.refresh_menu()

    def refresh_menu(self):
        if self.icon:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    # ---- tray
    def summary(self):
        counts = {}
        for key, pet in self.pet.pets.items():
            if key != "_none":
                st = pet.data.get("state", "idle")
                counts[st] = counts.get(st, 0) + 1
        top = min(counts, key=lambda s: RANK.get(s, 9)) if counts else "idle"
        parts = [f"{counts[s]} {core.LABELS.get(s, s).rstrip('…!')}" for s in sorted(counts, key=lambda s: RANK.get(s, 9))]
        text = f"{APP_NAME} - " + (", ".join(parts) if parts else "no active sessions")
        return top, text[:120]

    def update_tray(self):
        if not self.icon:
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
        if not item or not self.c_notify or not (self.icon or IS_MAC):
            return
        title = item.get("title", "session")
        where = item.get("where", "")
        try:
            if state == "needs_input":
                self.notify(item.get("message") or where or "Claude is waiting for you", f"{title} needs your input")
            elif state == "error":
                self.notify(where or "Something went wrong", f"{title} hit an error")
            elif state == "done" and (self.hidden or IS_MAC) and old in ("working", "needs_input"):
                self.notify(where or "Finished", f"{title} is done")
        except Exception:
            pass

    def build_menu(self):
        M, I = pystray.Menu, pystray.MenuItem

        def act(fn, *args):  # zero-arg action marshalled to the Tk thread
            return lambda: self.ui(lambda: fn(*args))

        def label(name, key):
            st = self.status.get(key, "checking…")
            return f"{'✓ ' if st.startswith('installed') else ''}{name}  ({st})"

        def restore_items(key):
            backups = hi.list_backups(key)[:15]
            if not backups:
                return [I("No backups yet", None, enabled=False)]
            items = []
            for b in backups:
                if b["absent"]:
                    note = "no settings.json"
                else:
                    note = ("with pet hooks" if b["pet_hooks"] else "no pet hooks") + ("" if b["valid_json"] else ", invalid JSON")
                text = f"{b['when']} - {b['reason']} ({note}){'  [original]' if b['original'] else ''}"
                items.append(I(text, act(self.confirm_restore, key, b)))
            return items

        def target_menu(name, key):
            return I(label(name, key), M(
                I("Install / update hooks", act(self.confirm, key, True)),
                I("Remove hooks", act(self.confirm, key, False)),
                M.SEPARATOR,
                I("Restore backup", M(*restore_items(key))),
                I("Back up now", act(self.start_job, key, "backup")),
                I("Open backups folder", act(self.open_backups, key)),
            ))

        def hook_items():
            items = [target_menu("This Mac" if IS_MAC else "This PC (Windows)", hi.LOCAL), M.SEPARATOR]
            if self.distros:
                items += [target_menu(f"WSL: {n}", "wsl:" + n) for n, _ in self.distros]
            else:
                items.append(I("No WSL distros found" if self.probed else "Detecting WSL distros…", None, enabled=False))
            items += [M.SEPARATOR,
                      I("Cowork (Claude desktop app)...", act(self.show_cowork)),
                      I("Run setup again...", act(self.show_setup)),
                      I("Re-detect / refresh status", lambda: threading.Thread(target=self.refresh_targets, daemon=True).start())]
            return items

        return M(
            I(lambda item: "Show pet" if self.hidden else "Hide pet", act(self.toggle), default=True),
            M.SEPARATOR,
            I("Claude Code hooks", M(hook_items)),
            I("Mute sounds", act(self.toggle_mute), checked=lambda item: self.c_muted),
            I("Windows notifications", act(self.toggle_notify), checked=lambda item: self.c_notify),
            I("Dark theme", act(self.toggle_theme), checked=lambda item: core.T.get("name") == "dark"),
            I("Pet style", M(*[I(label, act(self.set_style, key), checked=lambda item, k=key: core.STYLE["v"] == k,
                                 radio=True) for key, label in PET_STYLES])),
            I("Pet size...", act(self.pet.open_size_slider)),
            I("Reset pet size", act(self.pet.reset_scale)),
            I("Answer timeout...", act(self.pet.open_answer_slider)),
            I("Log hook events (debug)", act(self.toggle_debug), checked=lambda item: os.path.exists(DEBUG_FLAG)),
            I("Start with Windows", act(self.toggle_autostart), checked=lambda item: self.c_autostart,
              visible=os.name == "nt"),
            I(lambda item: f"Workbench: {self.c_wb}", None, enabled=False),
            I("Open config folder", act(self.open_config)),
            M.SEPARATOR,
            I("Quit", act(self.quit)),
        )

    # ---- actions (Tk thread)
    def _dialog_parent(self):
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.attributes("-topmost", True)
        return top

    def ask(self, text):
        p = self._dialog_parent()
        try:
            return messagebox.askyesno(APP_NAME, text, parent=p)
        finally:
            p.destroy()

    def info(self, text, error=False):
        p = self._dialog_parent()
        try:
            (messagebox.showerror if error else messagebox.showinfo)(APP_NAME, text, parent=p)
        finally:
            p.destroy()

    def confirm(self, key, install):
        where = hi.describe(key)
        if install:
            text = (f"Add Claude Pet hooks to:\n{where}\n\nYour existing hooks and settings are kept, and the "
                    f"current file is backed up first (tray > Claude Code hooks > Restore backup).\n"
                    f"Only Claude Code sessions started afterwards will show up.")
        else:
            text = f"Remove Claude Pet hooks from:\n{where}\n\nOther hooks are left untouched and a backup is taken first."
        if self.ask(text):
            self.start_job(key, "install" if install else "remove")

    def confirm_restore(self, key, b):
        lines = [f"Restore {hi.describe(key)}", f"to the backup from {b['when']} ({b['reason']})?", ""]
        if b["absent"]:
            lines.append("settings.json did not exist at that point, so it will be DELETED.")
        else:
            lines.append(f"That version {'contains' if b['pet_hooks'] else 'does not contain'} Claude Pet hooks "
                         f"({b['size']} bytes).")
            if not b["valid_json"]:
                lines.append("Warning: that backup is not valid JSON - Claude Code may reject it.")
        lines += ["", "The current file is backed up first, so you can undo this from the same menu.",
                  "Restart running Claude Code sessions for the change to take effect."]
        if self.ask("\n".join(lines)):
            self.start_job(key, "restore", b["path"])

    def start_job(self, key, action, arg=None):
        if self.busy:
            self.info("Another hook operation is still running - try again in a moment.")
            return
        self.busy = True
        threading.Thread(target=self._run_job, args=(key, action, arg), daemon=True).start()

    def _run_job(self, key, action, arg=None):
        try:
            if action == "install":
                msg = hi.install(key)
            elif action == "remove":
                msg = hi.uninstall(key)
            elif action == "restore":
                msg = hi.restore_backup(key, arg)
            else:
                path = hi.make_backup(key, "manual")
                msg = f"Backed up {hi.describe(key)}\nto {path}"
            self.status[key] = hi.status(key)
            self.ui(lambda: self.info(msg))
        except Exception as e:
            err = f"{action.capitalize()} failed for {hi.describe(key)}:\n{e}"
            self.ui(lambda: self.info(err, error=True))
        finally:
            self.busy = False
            self.refresh_menu()

    def open_backups(self, key):
        d = hi.backup_dir(key)
        os.makedirs(d, exist_ok=True)
        open_path(d)

    def hide(self):
        self.pet.hide_tip()
        self.root.withdraw()
        self.hidden = True
        self.refresh_menu()

    def show(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.hidden = False
        self.refresh_menu()

    def toggle(self):
        self.show() if self.hidden else self.hide()

    def toggle_mute(self):
        self.pet.muted.set(not self.pet.muted.get())
        self.c_muted = self.pet.muted.get()
        self.refresh_menu()

    def toggle_notify(self):
        self._set_notify(not self.c_notify)

    def _set_notify(self, value):
        self.c_notify = bool(value)
        self.pet.cfg["notifications"] = self.c_notify
        if not core.save_setting("notifications", self.c_notify):
            self.info("Couldn't save this setting (config.json isn't valid JSON?). It applies until you quit.",
                      error=True)
        self.refresh_menu()

    def set_style(self, name):
        if name not in dict(PET_STYLES):
            return
        core.STYLE["v"] = name
        self.pet.cfg["pet_style"] = name
        core.save_setting("pet_style", name)
        self._icon_key = None  # redraw the tray icon in the new style
        if hasattr(self, "style_var"):
            self.style_var.set(name)
        self.refresh_menu()

    def toggle_theme(self):
        name = "light" if core.T.get("name") == "dark" else "dark"
        self.pet.apply_theme(name)
        if not core.save_setting("theme", name):
            self.info("Couldn't save the theme (config.json isn't valid JSON?). It applies until you quit.", error=True)
        if hasattr(self, "theme_var"):
            self.theme_var.set(name == "dark")
        self.refresh_menu()

    def toggle_debug(self):
        """Start/stop writing ~/.claude-pet/events.log (metadata only; see the hook's debug_log)."""
        try:
            if os.path.exists(DEBUG_FLAG):
                os.remove(DEBUG_FLAG)
            else:
                os.makedirs(core.HOME_DIR, exist_ok=True)
                open(DEBUG_FLAG, "w").close()
        except OSError:
            pass
        if hasattr(self, "debug_var"):
            self.debug_var.set(os.path.exists(DEBUG_FLAG))
        self.refresh_menu()

    def toggle_autostart(self):
        turning_on = not autostart_enabled()
        try:
            set_autostart(turning_on)
        except OSError as e:
            self.info(f"Couldn't change startup setting:\n{e}", error=True)
        if turning_on and windows_sees_autostart() is False:
            try:
                set_autostart(False)  # don't leave a checkmark that Windows will never honour
            except OSError:
                pass
            self.info("Windows does not see the startup entry, so it would not start at login.\n\n"
                      "This usually means this copy of Claude Pet was started from inside another app (for example the Claude "
                      "desktop app), which keeps its registry changes in a private area that Windows ignores at login.\n\n"
                      "Quit Claude Pet, start ClaudePet.exe from File Explorer, and switch 'Start with Windows' on again.",
                      error=True)
        self.c_autostart = autostart_enabled()
        if hasattr(self, "autostart_var"):
            self.autostart_var.set(self.c_autostart)
        self.refresh_menu()

    def open_config(self):
        open_path(core.HOME_DIR)

    def quit(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
        self.root.destroy()


def main():
    if "--probe-workbench" in sys.argv:
        if sys.stdout is None:  # windowed exe: write the report to a file and open it
            core.ensure_home()
            path = os.path.join(core.HOME_DIR, "workbench-probe.txt")
            with open(path, "w", encoding="utf-8") as f:
                sys.stdout = f
                try:
                    core.probe_workbench()
                except Exception as e:
                    print(f"Probe failed: {e}")
            os.startfile(path)
        else:
            core.probe_workbench()
        return
    if not single_instance():
        return  # already running in the tray
    TrayApp().root.mainloop()


if __name__ == "__main__":
    main()
