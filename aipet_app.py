#!/usr/bin/env python3
"""
AIPet - tray application (entry point for AIPet.exe).

* Floating pet window (aipet.PetApp) that can be hidden to the system tray.
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
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox

import aipet as core
import aipet_update as upd
import hooks_installer as hi
import legacy  # LEGACY: moving over from Claude Pet

IS_MAC = sys.platform == "darwin"
try:
    if IS_MAC:  # pystray wants the main thread, which Tk already owns; macOS uses the pet's own menu instead
        raise ImportError
    from PIL import Image, ImageDraw
    import pystray
except ImportError:  # still runs, just without a tray icon
    pystray = None

APP_NAME = "AIPet"
PET_STYLES = [("robot", "Robot"), ("mole", "Mole"), ("cat", "Cat")]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/com.aipet.app.plist")
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
    _mutex = k32.CreateMutexW(None, False, "Local\\AIPetSingleton")
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
    """Ask Windows itself (WMI, served by a normal system service) whether AIPet is a startup command.
    A plain registry read can be fooled: a process started inside another app's container (for example the Claude
    desktop app) reads and writes a private overlay of the registry that Windows ignores at login.
    Returns True/False, or None if it can't be determined."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_StartupCommand | Where-Object { $_.Name -eq 'AIPet' } | Measure-Object).Count"],
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
            winreg.QueryValueEx(k, "AIPet")
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
                plistlib.dump({"Label": "com.aipet.app", "ProgramArguments": args, "RunAtLoad": True}, f)
        else:
            try:
                os.remove(LAUNCH_AGENT)
            except FileNotFoundError:
                pass
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "AIPet", 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, "AIPet")
            except FileNotFoundError:
                pass


def make_icon(state):
    col, dark = core.COLORS.get(state, core.COLORS["idle"]), core.DARK.get(state, core.DARK["idle"])
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    spr = core.load_sprites() if core.STYLE["v"] == "robot" else False
    if spr:  # the robot with the state's face (red while a session needs you), on a dot in the state colour
        im = robot_icon(state)
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


def robot_icon(state):
    """The robot for the taskbar / tray / Dock at sprite resolution: white normally, red with a "?" face while a
    session needs you (or a worried face on an error)."""
    attention = state in ("needs_input", "error")
    face = "ask" if state == "needs_input" else ("worried" if state == "error" else core.STATE_FACE.get(state, "happy"))
    im = core.robot_image(face, ("red",) * 3 if attention else core.light_cycle(state, 0)).copy()
    if attention:
        px = im.load()
        for y in range(im.height):
            for x in range(im.width):
                r, g, b, a = px[x, y]
                if a and min(r, g, b) > 120 and max(r, g, b) - min(r, g, b) < 40:  # the white / grey body -> red
                    v = (r + g + b) / 3 / 255
                    px[x, y] = (int(150 + 105 * v), int(25 + 45 * v), int(25 + 45 * v), a)
    return im


def menu_bar_icons():
    """macOS menu bar: a line-art robot as a template image (macOS paints its opaque pixels in the menu bar's colour:
    outline, screen and lights solid, white body and cyan face left out) and the red robot for 'needs you'."""
    from PIL import Image  # this module skips the PIL import on macOS (no pystray tray there)
    im = core.robot_image("happy", ("off", "off", "off")).copy()
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            dark = a and (r * 299 + g * 587 + b * 114) / 1000 < 110
            px[x, y] = (0, 0, 0, 255) if dark else (0, 0, 0, 0)
    pad = Image.new("RGBA", (im.width + 4, im.height + 4), (0, 0, 0, 0))
    pad.paste(im, (2, 2), im)
    red = robot_icon("needs_input")
    red_pad = Image.new("RGBA", (red.width + 4, red.height + 4), (0, 0, 0, 0))
    red_pad.paste(red, (2, 2), red)
    return {"normal": pad, "attention": red_pad}


def dock_image(state, size=256):
    """macOS Dock icon (see robot_icon), centred on a square canvas."""
    from PIL import Image  # this module skips the PIL import on macOS (no tray there)
    im = robot_icon(state)
    k = max(1, int(size * 0.9 // im.height))
    big = im.resize((im.width * k, im.height * k), Image.NEAREST)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(big, ((size - big.width) // 2, (size - big.height) // 2), big)
    return out


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
        self.codex_targets = []    # [(label, key)] where Codex was found (this machine, running WSL distros)
        self.probed = False
        self.status = {hi.LOCAL: "checking…"}
        # caches read by the tray thread (never touch Tk from there)
        self.c_muted = self.pet.muted.get()
        self.c_autostart = autostart_enabled()
        self.c_notify = bool(self.pet.cfg.get("notifications", False))
        self.c_wb = "off"
        self.c_auto = core.auto_approve_targets()  # hook configs with auto-approval on (all off by default)
        self.version = upd.current_version()
        self.update_state = upd.State(core.HOME_DIR)
        found = self.update_state.load().get("latest") or {}
        self.update_info = found if upd.is_newer(found.get("tag"), self.version) else None  # a newer release, if any
        self._update_busy = False
        self._icon_key = None

        self.pet.on_alert = self.on_alert
        self.cowork_win = None
        m = self.pet.menu
        hooks_menu = tk.Menu(m, tearoff=0)
        hooks_menu.configure(postcommand=lambda: self._fill_hooks_menu(hooks_menu))
        codex_menu = tk.Menu(m, tearoff=0)
        codex_menu.configure(postcommand=lambda: self._fill_codex_menu(codex_menu))
        if pystray:
            q = m.index("Quit")
            m.insert_cascade(q, label="Claude Code hooks", menu=hooks_menu)
            m.insert_cascade(q + 1, label="Codex hooks", menu=codex_menu)
            m.insert_separator(q + 2)
            m.insert_command(m.index("Quit"), label="Hide to tray", command=self.hide)
        else:  # no tray icon (macOS): the tray menu's essentials live in the pet's right-click menu
            self.notify_var = tk.BooleanVar(value=self.c_notify)
            self.autostart_var = tk.BooleanVar(value=self.c_autostart)
            q = m.index("Quit")
            m.insert_separator(q)
            m.insert_cascade(q + 1, label="Claude Code hooks", menu=hooks_menu)
            m.insert_cascade(q + 2, label="Codex hooks", menu=codex_menu)
            m.insert_checkbutton(q + 3, label="Notifications", variable=self.notify_var,
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
                m.insert_checkbutton(q + 4, label="Start at login", variable=self.autostart_var,
                                     command=self.toggle_autostart)
            m.insert_command(m.index("Quit"), label="Open config folder", command=self.open_config)
        auto_menu = tk.Menu(m, tearoff=0)
        auto_menu.configure(postcommand=lambda: self._fill_auto_menu(auto_menu))
        m.insert_cascade(m.index("Codex hooks") + 1, label="Auto approve", menu=auto_menu)
        m.entryconfigure(m.index("Quit"), command=self.quit)
        m.insert_command(m.index("Quit"), label="About AIPet...", command=self.show_about)
        self.claude_oauth_var = tk.BooleanVar(value=bool(self.pet.cfg.get("claude_oauth_usage", False)))
        m.insert_checkbutton(m.index("Quit"), label="Claude account usage (unofficial)", variable=self.claude_oauth_var,
                             command=self.toggle_claude_oauth_usage)
        core.style_menu(m)

        self.icon = None
        if pystray:
            self.icon = pystray.Icon("aipet", make_icon("idle"), APP_NAME, menu=self.build_menu())
            self.icon.run_detached()
        self.status_item = None  # macOS menu bar (pystray can't share the main thread with Tk there)
        if IS_MAC:
            try:
                import mac_statusbar
                self.status_item = mac_statusbar.StatusItem(self._mac_menu_spec, self.ui, menu_bar_icons())
            except Exception as e:
                core.log_error(f"menu bar icon: {e!r}")

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
            print(f"[aipet] ui error: {e}", file=sys.stderr)
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
            print(f"[aipet] backup migration failed: {e}", file=sys.stderr)
        try:
            hi.deploy_files(only_if_deployed=True)  # refresh an existing install only; never create one
            if os.path.isdir(hi.INSTALL_DIR) and (os.name == "nt" or IS_MAC):
                hi.build_plugin()  # keep the Cowork plugin zip next to the exe current
        except Exception:
            pass
        self.refresh_targets()
        for key in [hi.LOCAL] + ["wsl:" + n for n, state in self.distros if state.lower() == "running"]:
            try:
                hi.migrate_legacy_usage(key)  # backup and restore v0.3.1's automatic wrapper once
            except Exception as e:
                core.log_error(f"restore legacy status line for {key}: {e!r}")
        self.ui(lambda: self.root.after(20000, self.legacy_tick))  # LEGACY: daily reminder about old-name hooks
        self.ui(lambda: self.root.after(30000, self.update_tick))
        self.ui(self._show_update_in_menus)
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

        tk.Label(win, text="Where should AIPet watch Claude Code?", font=("Segoe UI", 12, "bold")
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
                      "can install. AIPet built it for you:\n" + (zip_path or hi.COWORK_ZIP + " (not built yet)") + "\n"
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
        core.theme_window(win)
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
          "Only the Claude app can install plugins, so AIPet.exe can't do this step for you. Your plugin zip:",
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

        p("\nThe plugin calls the hook in " + hi.INSTALL_DIR + ", which AIPet keeps up to date. "
          "Rebuild and re-upload only if that folder moves.", fg=grey)
        tk.Button(win, text="Close", width=12, command=close, default="active").pack(anchor="e", padx=16, pady=12)
        win.protocol("WM_DELETE_WINDOW", close)
        core.theme_window(win)
        win.update_idletasks()
        win.geometry(f"+{max(0, (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2)}"
                     f"+{max(0, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)}")

    # ---- LEGACY: daily reminder about hooks / plugins still on the old name (see legacy.py)
    def legacy_tick(self):
        """Tk thread: check once now (if not yet reminded today), then every hour, so a pet running for days still
        reminds on each new day."""
        if legacy.due_today():
            self.check_legacy()
        self.root.after(3600 * 1000, self.legacy_tick)

    def check_legacy(self, force=False):
        targets = {hi.LOCAL: "This Mac" if IS_MAC else "This PC (Windows)"} if (os.name == "nt" or IS_MAC) else {}
        targets.update({"wsl:" + n: f"WSL: {n}" for n, st in self.distros if st.lower() == "running"})
        pets = [p.data for p in self.pet.pets.values()]

        def job():
            try:
                found = legacy.findings(targets, pets)
            except Exception as e:
                found = []
                core.log_error(f"legacy check failed: {e!r}")
            if found or force:
                self.ui(lambda: self._legacy_window(found))
            if found:
                legacy.mark_warned()
        threading.Thread(target=job, daemon=True).start()

    def _legacy_window(self, found):
        if not found:
            self.info("Nothing uses the old Claude Pet name any more. You can delete the old folder "
                      f"({legacy.LEGACY_DIR}) if it still exists.")
            return
        win = tk.Toplevel(self.root)
        win.title(f"{APP_NAME}: update old hooks")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        tk.Label(win, text="Some hooks still use the old Claude Pet name", font=("Segoe UI", 12, "bold")
                 ).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, fg="#6b7280", justify="left", wraplength=480,
                 text="They keep working for now, but support for the old name will be removed in a future version. "
                      "This reminder shows once a day while anything is left.").pack(anchor="w", padx=16)
        body = tk.Frame(win)
        body.pack(fill="x", padx=16, pady=8)
        for what, how in found:
            tk.Label(body, text=what, font=("Segoe UI", 9, "bold"), justify="left", wraplength=480).pack(anchor="w", pady=(6, 0))
            tk.Label(body, text=how, font=("Segoe UI", 9), justify="left", wraplength=480).pack(anchor="w")
        row = tk.Frame(win)
        row.pack(fill="x", padx=16, pady=(6, 14))
        keys = [k for k in [hi.LOCAL] + ["wsl:" + n for n, _ in self.distros] if any(
            f.startswith(("This PC", "This Mac") if k == hi.LOCAL else f"WSL: {k[4:]}:") for f, _ in found)]

        def update():
            win.destroy()
            if self.busy:
                self.info("Another hook operation is still running - try again in a moment.")
                return
            self.busy = True
            threading.Thread(target=self._update_job, args=(keys,), daemon=True).start()
        if keys:
            tk.Button(row, text="Update these hooks now", default="active", command=update).pack(side="right")
        if any("Cowork" in f or "CLI" in f for f, _ in found):
            tk.Button(row, text="Cowork / CLI plugin...", command=lambda: (win.destroy(), self.show_cowork())
                      ).pack(side="right", padx=8)
        tk.Button(row, text="Remind me tomorrow", command=win.destroy).pack(side="left")
        core.theme_window(win)
        win.update_idletasks()
        win.geometry(f"+{max(0, (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2)}"
                     f"+{max(0, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)}")

    # ---- pet right-click: the same "Claude Code hooks" menu as the tray
    def _fill_codex_menu(self, menu):
        """Pet right-click > Codex hooks: one entry per place Codex was found."""
        self._fill_hooks_menu(menu, codex=True)

    def _fill_hooks_menu(self, menu, codex=False):
        menu.delete(0, "end")

        def target(name, key):
            st = self.status.get(key, "checking…")
            sub = tk.Menu(menu, tearoff=0)
            sub.add_command(label="Install / update hooks", command=lambda: self.confirm(key, True))
            sub.add_command(label="Remove hooks", command=lambda: self.confirm(key, False))
            if not codex:
                sub.add_command(label="Claude usage status line...", command=lambda: self.confirm_usage(key))
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

        if codex:
            for lab, key in self.codex_targets:
                target(lab, key)
            if not self.codex_targets:
                menu.add_command(label="Codex not found (running WSL distros are checked)" if self.probed
                                 else "Detecting…", state="disabled")
            menu.add_separator()
            menu.add_command(label="Run setup again...", command=self.show_setup)
            menu.add_command(label="Re-detect / refresh status",
                             command=lambda: threading.Thread(target=self.refresh_targets, daemon=True).start())
            core.style_menu(menu)
            return
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
        menu.add_command(label="Check for old Claude Pet hooks...", command=lambda: self.check_legacy(True))  # LEGACY
        menu.add_command(label="Re-detect / refresh status",
                         command=lambda: threading.Thread(target=self.refresh_targets, daemon=True).start())
        core.style_menu(menu)

    # ---- Auto approve: answer every permission prompt of chosen hook configs with "allow" (all off by default)
    def auto_sections(self):
        """[(section title, [(label, key)])] for every hook config that can be auto-approved, plus any switched-on key
        that isn't detected right now, so it can always be switched off again."""
        claude = [("This Mac" if IS_MAC else "This PC (Windows)", hi.LOCAL)]
        claude += [(f"WSL: {n}", "wsl:" + n) for n, _ in self.distros]
        if os.name == "nt" or IS_MAC:
            claude.append(("Cowork (Claude desktop app)", "cowork"))
        sections = [("Claude Code", claude), ("Codex", list(self.codex_targets))]
        known = {k for _, items in sections for _, k in items}
        other = [(k, k) for k in self.c_auto if k not in known]
        if other:
            sections.append(("Not detected now", other))
        return sections

    def auto_label(self, label, key):
        """Menu text: the config, what its rules do when switched on, and a note when its hooks aren't installed."""
        rules = core.auto_approve_rules().get(key)
        text = label
        if rules and rules.get("enabled"):
            n = len([x for x in rules.get("whitelist") or [] if x.strip() and not x.strip().startswith("#")])
            text += " - allow all" if rules.get("allow_all") else f" - {n} whitelist rule{'s' if n != 1 else ''}"
        st = self.status.get(key, "")
        if key != "cowork" and st and not st.startswith("installed") and not any(
                w in st for w in ("unknown", "checking", "n/a")):
            text += "  (hooks not installed)"
        return text + "..."

    def _fill_auto_menu(self, menu):
        """Pet right-click > Auto approve: one entry per hook config, each opening its rules window."""
        menu.delete(0, "end")
        self.c_auto = core.auto_approve_targets()
        menu.add_command(label="Approve permission prompts automatically for:", state="disabled")
        for title, items in self.auto_sections():
            menu.add_separator()
            menu.add_command(label=title, state="disabled")
            if not items:
                menu.add_command(label="   not found", state="disabled")
            for label, key in items:
                menu.add_command(label=("\u2713 " if key in self.c_auto else "   ") + self.auto_label(label, key),
                                 command=lambda k=key, lab=label: self.open_auto_rules(k, lab))
        menu.add_separator()
        menu.add_command(label="Turn all off", command=self.auto_all_off, state="normal" if self.c_auto else "disabled")
        core.style_menu(menu)

    def open_auto_rules(self, key, label):
        """The rules window of one hook config: on/off, Allow all, and a whitelist and a blacklist of regexes."""
        wins = self.__dict__.setdefault("auto_wins", {})
        if key in wins:
            try:
                wins[key].lift()
                return
            except tk.TclError:
                wins.pop(key, None)
        old = core.auto_approve_rules().get(key) or core.default_auto_rules()
        win = wins[key] = tk.Toplevel(self.root)
        win.title(f"{APP_NAME} - auto approve - {label}")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        grey = "#6b7280"
        enabled = tk.BooleanVar(value=bool(old.get("enabled")))
        allow_all = tk.BooleanVar(value=bool(old.get("allow_all")))

        tk.Label(win, text=f"Auto approve - {label}", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, fg=grey, justify="left", wraplength=640, font=("Segoe UI", 9),
                 text="Permission prompts from this hook config that match the whitelist are approved without asking "
                      "you. Anything on the blacklist always asks you, even if it is also whitelisted; so does anything "
                      "on neither list.").pack(anchor="w", padx=16)
        tk.Checkbutton(win, text="Auto approve for this config", variable=enabled, font=("Segoe UI", 10, "bold")
                       ).pack(anchor="w", padx=12, pady=(10, 0))
        tk.Checkbutton(win, text="Allow all - approve every request without asking (ignores both lists)",
                       variable=allow_all, font=("Segoe UI", 10)).pack(anchor="w", padx=12)

        cols = tk.Frame(win)
        cols.pack(fill="x", padx=16, pady=(8, 0))
        boxes = {}
        for col, (name, title, hint, lines) in enumerate((
                ("whitelist", "Whitelist - approve automatically",
                 "One regex per line. Must match the WHOLE command (or file path / URL), the tool name, or "
                 "Tool(command). Examples: git (status|diff)   npm test   Read", old.get("whitelist") or []),
                ("blacklist", "Blacklist - always ask me",
                 "One regex per line, matched ANYWHERE in the same texts. Wins over the whitelist. The default .* "
                 "asks for everything: replace it to let the whitelist work. Tip: [;&|`$<>] asks for chained or "
                 "redirected commands, so a whitelisted one can't smuggle in another.", old.get("blacklist")
                 if old.get("blacklist") is not None else core.DEFAULT_BLACKLIST))):
            box = tk.Frame(cols)
            box.grid(row=0, column=col, sticky="nw", padx=(0 if col == 0 else 12, 0))
            tk.Label(box, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w")
            tk.Label(box, text=hint, fg=grey, justify="left", wraplength=300, font=("Segoe UI", 8)).pack(anchor="w")
            text = tk.Text(box, width=40, height=12, wrap="none", font=("Consolas", 10), undo=True, padx=6, pady=4)
            text.insert("1.0", "\n".join(lines))
            text.pack(anchor="w", pady=(4, 0))
            boxes[name] = text
        tk.Label(win, fg=grey, justify="left", wraplength=640, font=("Segoe UI", 8),
                 text="Empty lines and lines starting with # are ignored. Only works while AIPet is running and its "
                      "hooks are installed for this config; the VS Code extension sends no permission events."
                 ).pack(anchor="w", padx=16, pady=(6, 0))
        error = tk.Label(win, fg="#b91c1c", justify="left", wraplength=640, font=("Segoe UI", 9))
        error.pack(anchor="w", padx=16)

        def sync(*_):  # Allow all switches the lists off
            state = "disabled" if allow_all.get() else "normal"
            for t in boxes.values():
                t.configure(state=state, fg=core.T["muted"] if allow_all.get() else core.T["entry_fg"])
        allow_all.trace_add("write", sync)

        def close():
            wins.pop(key, None)
            win.destroy()

        def save():
            lists = {}
            for name, t in boxes.items():
                lists[name] = [ln.rstrip() for ln in t.get("1.0", "end-1c").splitlines() if ln.strip()]
            bad = [(name, n, pat, err) for name in lists for n, pat, err in core.bad_patterns(lists[name])]
            if bad:
                error.configure(text="Fix these patterns first:\n" + "\n".join(
                    f"{name} line {n}: {pat}  ({err})" for name, n, pat, err in bad[:6]))
                return
            rules = {"enabled": enabled.get(), "allow_all": allow_all.get(), **lists}
            newly_on = rules["enabled"] and not old.get("enabled")
            all_on = rules["enabled"] and rules["allow_all"] and not (old.get("enabled") and old.get("allow_all"))
            if (all_on or newly_on) and not self.warn_auto(label, rules):
                return
            if not core.save_auto_approve(key, rules):
                self.info("Couldn't save the auto approve setting (is ~/.aipet writable?).", error=True)
                return
            close()
            self._auto_changed()

        buttons = tk.Frame(win)
        buttons.pack(fill="x", padx=16, pady=(8, 14))
        tk.Button(buttons, text="Save", width=12, command=save, default="active").pack(side="right")
        tk.Button(buttons, text="Cancel", width=10, command=close).pack(side="right", padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: close())
        core.theme_window(win)
        sync()
        win.update_idletasks()
        win.geometry(f"+{max(0, (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2)}"
                     f"+{max(0, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)}")

    toggle_auto = open_auto_rules  # the menus' entry point

    def warn_auto(self, label, rules):
        if rules.get("allow_all"):
            heading = f"Auto approve everything from {label}?"
            what = (f"Every permission request that reaches AIPet's hook from {label} will be approved at once, "
                    "without asking you: shell commands, file edits and deletions, web access and any other tool, "
                    "in every session there, including subagents.")
        else:
            n = len([x for x in rules.get("whitelist") or [] if not x.strip().startswith("#")])
            heading = f"Turn on auto approve for {label}?"
            what = (f"Requests from {label} that match your {n} whitelist pattern{'s' if n != 1 else ''} and none of "
                    "your blacklist patterns will be approved without asking you, in every session there, including "
                    "subagents. A loose pattern can approve more than you meant: check them carefully.")
        return core.themed_dialog(
            self.root, f"{APP_NAME} - auto approve", kind="warning", heading=heading,
            text=(what + "\n\nIt takes effect immediately for running sessions and only while AIPet is running.\n\n"
                  "Turn it off any time: right-click the pet or the tray icon > Auto approve."),
            buttons=(("Cancel", False, "secondary"), ("Auto approve", True, "danger")),
            cancel=False, enter_confirms=False)

    def auto_all_off(self):
        core.save_auto_approve(None, None)
        self._auto_changed()

    def _auto_changed(self):
        self.c_auto = core.auto_approve_targets()
        self._icon_key = None  # the tray tooltip says when auto approve is on
        self.refresh_menu()

    # ---- new version check (GitHub releases; tells you and opens the page, never installs anything)
    def update_tick(self):
        """Tk thread: check once a day while automatic checks are on (looked at every hour)."""
        if self.pet.cfg.get("update_check", True) and self.update_state.due():
            self.check_updates(False)
        self.root.after(3600 * 1000, self.update_tick)

    def check_updates(self, manual=False):
        if self._update_busy:
            return
        self._update_busy = True

        def job():
            try:
                latest = upd.latest_release()
            except Exception as e:
                self.update_state.save(last_check=time.time(), error=repr(e)[:200])
                if manual:
                    err = f"Couldn't check for a new version:\n{e}\n\nReleases: {upd.RELEASES_URL}"
                    self.ui(lambda: self.info(err, error=True))
                return
            finally:
                self._update_busy = False
            state = self.update_state.save(last_check=time.time(), latest=latest, error="")
            newer = upd.is_newer(latest["tag"], self.version)
            self.update_info = latest if newer else None
            self.ui(self._show_update_in_menus)
            if manual:
                self.ui(lambda: self._update_dialog(latest, newer, manual=True))
            elif newer and latest["tag"] not in (state.get("skipped"), state.get("notified")):
                self.update_state.save(notified=latest["tag"])
                self.ui(lambda: self._update_dialog(latest, True))
        threading.Thread(target=job, daemon=True).start()

    def _update_dialog(self, latest, newer, manual=False):
        if not newer:
            known = upd.parse(self.version)
            text = (f"You have the newest version, {upd.short(self.version)}." if known else
                    f"This copy has no version number (built from source). The newest release is {latest['tag']}.")
            choice = core.themed_dialog(self.root, f"{APP_NAME} - updates", text, kind="info", heading="No new version",
                                        buttons=(("Open releases page", "open", "secondary"), ("OK", "ok", "primary")),
                                        cancel="ok")
            if choice == "open":
                webbrowser.open(latest.get("url") or upd.RELEASES_URL)
            return
        choice = core.themed_dialog(
            self.root, f"{APP_NAME} - update", kind="info", heading=f"AIPet {latest['tag']} is available",
            text=(f"You have {upd.short(self.version)}. The new version is on the release page: download "
                  f"{'AIPet-mac-arm64.zip' if IS_MAC else 'AIPet.exe'}, quit AIPet (tray > Quit) and replace your copy "
                  "with it. Your settings, hooks and backups in ~/.aipet are kept.\n\n"
                  "The menus show the update until you install it."),
            buttons=(("Skip this version", "skip", "secondary"), ("Later", "later", "secondary"),
                     ("Open release page", "open", "primary")), cancel="later")
        if choice == "open":
            webbrowser.open(latest.get("url") or upd.RELEASES_URL)
        elif choice == "skip":
            self.update_state.save(skipped=latest["tag"])

    def open_update(self):
        if self.update_info:
            self._update_dialog(self.update_info, True, manual=True)
        else:
            self.check_updates(True)

    def _show_update_in_menus(self):
        """The update entries live in the tray / menu-bar menu only (rebuilt when they open)."""
        self.refresh_menu()

    def toggle_update_check(self):
        on = not self.pet.cfg.get("update_check", True)
        self.pet.cfg["update_check"] = on
        core.save_setting("update_check", on)
        self.refresh_menu()

    def _recheck_setup(self):
        self._close_setup()
        self.show_setup()

    def python_help(self):
        if self.ask("Python 3 makes AIPet's hook faster.\n\nThe simplest way is the installer from python.org. "
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
        if self.ask("AIPet's hooks here point at an older location and may not be working:\n\n"
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
        codex = []
        if (os.name == "nt" or IS_MAC) and hi.detect_codex_local():
            codex.append(("This Mac" if IS_MAC else "This PC (Windows)", "codex:" + hi.LOCAL))
        for name, state in self.distros:
            try:
                if state.lower() == "running" and hi.codex_in_wsl(name):
                    codex.append((f"WSL: {name}", "codex:wsl:" + name))
            except Exception:
                pass
        for _, key in codex:
            self.status[key] = hi.status(key)
        self.codex_targets = codex
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
        for it in self.pet._last_items:  # every session, also in compact mode (one pet for all)
            st = it.get("state", "idle")
            counts[st] = counts.get(st, 0) + 1
        top = min(counts, key=lambda s: RANK.get(s, 9)) if counts else "idle"
        parts = [f"{counts[s]} {core.LABELS.get(s, s).rstrip('…!')}" for s in sorted(counts, key=lambda s: RANK.get(s, 9))]
        text = f"{APP_NAME} - " + ("AUTO APPROVE ON - " if self.c_auto else "") + (
            ", ".join(parts) if parts else "no active sessions")
        return top, text[:120]

    def update_dock(self):
        """macOS: the Dock is the taskbar - show the robot there, red while a session needs you, and bounce the icon
        once when that starts."""
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
            self.root.iconphoto(True, self._dock_ph)
            if key == "attention":
                self.root.attributes("-notify", True)  # Tk on macOS: bounce the Dock icon
        except Exception as e:
            core.log_error(f"dock icon: {e!r}")

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
                            core.log_error(f"menu bar icon: {e!r}")
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
                I("Claude usage status line...", act(self.confirm_usage, key), visible=not hi.is_codex(key)),
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
                      I("Check for old Claude Pet hooks...", act(self.check_legacy, True)),  # LEGACY
                      I("Re-detect / refresh status", lambda: threading.Thread(target=self.refresh_targets, daemon=True).start())]
            return items

        def codex_items():
            items = [target_menu(lab, key) for lab, key in self.codex_targets] or [
                I("Codex not found (running WSL distros are checked)" if self.probed else "Detecting…", None,
                  enabled=False)]
            return items + [M.SEPARATOR,
                            I("Run setup again...", act(self.show_setup)),
                            I("Re-detect / refresh status",
                              lambda: threading.Thread(target=self.refresh_targets, daemon=True).start())]

        def auto_items():
            items = [I("Approve permission prompts automatically for:", None, enabled=False)]
            for title, entries in self.auto_sections():
                items += [M.SEPARATOR, I(title, None, enabled=False)]
                if not entries:
                    items.append(I("   not found", None, enabled=False))
                items += [I("   " + self.auto_label(lab, key), act(self.toggle_auto, key, lab),
                            checked=lambda item, k=key: k in self.c_auto) for lab, key in entries]
            return items + [M.SEPARATOR, I("Turn all off", act(self.auto_all_off), enabled=lambda item: bool(self.c_auto))]

        return M(
            I(lambda item: f"Update available: {self.update_info['tag']}..." if self.update_info else "",
              act(self.open_update), visible=lambda item: bool(self.update_info)),
            I(lambda item: "Show pet" if self.hidden else "Hide pet", act(self.toggle), default=True),
            M.SEPARATOR,
            I("Claude Code hooks", M(hook_items)),
            I("Codex hooks", M(codex_items)),
            I(lambda item: "Auto approve (ON)" if self.c_auto else "Auto approve", M(auto_items)),
            I("Mute sounds", act(self.toggle_mute), checked=lambda item: self.c_muted),
            I("Windows notifications", act(self.toggle_notify), checked=lambda item: self.c_notify),
            I("Dark theme", act(self.toggle_theme), checked=lambda item: core.T.get("name") == "dark"),
            I("Pet style", M(*[I(label, act(self.set_style, key), checked=lambda item, k=key: core.STYLE["v"] == k,
                                 radio=True) for key, label in PET_STYLES])),
            I("Pet size...", act(self.pet.open_size_slider)),
            I("Reset pet size", act(self.pet.reset_scale)),
            I("Reset pet position (main screen)", act(self.reset_position)),
            I("Answer timeout...", act(self.pet.open_answer_slider)),
            I("Clear finished after...", act(self.pet.open_done_slider)),
            I("Health check every...", act(self.pet.open_health_slider)),
            I("Compact mode (one pet)", act(self.pet.toggle_compact), checked=lambda item: bool(self.pet.cfg.get("compact"))),
            I("Show on all desktops", act(lambda: self.pet.set_all_spaces(not self.pet.cfg.get("all_spaces", True))),
              checked=lambda item: bool(self.pet.cfg.get("all_spaces", True)), visible=os.name == "nt"),
            I("Click goes to the session's window",
              act(lambda: self.pet.set_click_to_focus(not self.pet.cfg.get("click_to_focus", True))),
              checked=lambda item: bool(self.pet.cfg.get("click_to_focus", True))),
            I("Session titles", M(*[I(text, act(self.pet.set_session_titles, value), radio=True,
                                      checked=lambda item, v=value: self.pet.cfg.get("session_titles", "name") == v)
                                    for value, text in (("name", "Session name"), ("prompt", "Last prompt"))])),
            I("Tooltips", M(*[I(label, act(lambda k=kind: self.pet.set_tooltip(k, not self.pet.cfg.get(k + "_tooltips", True))),
                                 checked=lambda item, k=kind: bool(self.pet.cfg.get(k + "_tooltips", True)))
                                for kind, label in (("session", "Session details"), ("usage", "Usage details"))])),
            I("Claude account usage (unofficial)", act(self.toggle_claude_oauth_usage),
              checked=lambda item: bool(self.pet.cfg.get("claude_oauth_usage", False))),
            I("Answer Codex prompts from the pet", act(lambda: self.pet.set_codex_answers(not self.pet.cfg.get("codex_answers"))),
              checked=lambda item: bool(self.pet.cfg.get("codex_answers"))),
            I("Log hook events (debug)", act(self.toggle_debug), checked=lambda item: os.path.exists(DEBUG_FLAG)),
            I("Start with Windows", act(self.toggle_autostart), checked=lambda item: self.c_autostart,
              visible=os.name == "nt"),
            I(lambda item: f"Workbench: {self.c_wb}", None, enabled=False),
            I("Open config folder", act(self.open_config)),
            M.SEPARATOR,
            I(lambda item: f"AIPet {self.version}", None, enabled=False),
            I("About AIPet...", act(self.show_about)),
            I("Check for updates...", act(self.check_updates, True)),
            I("Check for updates automatically", act(self.toggle_update_check),
              checked=lambda item: bool(self.pet.cfg.get("update_check", True))),
            M.SEPARATOR,
            I("Quit", act(self.quit)),
        )

    # ---- macOS menu bar: the same menu as the Windows tray, as a spec for mac_statusbar (rebuilt on every open)
    def _mac_menu_spec(self):
        def item(label, action=None, checked=False, enabled=True, submenu=None):
            return {"label": label, "action": action, "checked": checked, "enabled": enabled, "submenu": submenu}

        def target(name, key):
            st = self.status.get(key, "checking…")
            backups = hi.list_backups(key)[:15]
            restore = [item(f"{b['when']} - {b['reason']}" + ("  [original]" if b["original"] else ""),
                            lambda b=b: self.confirm_restore(key, b)) for b in backups] or [item("No backups yet", enabled=False)]
            return item(f"{'✓ ' if st.startswith('installed') else ''}{name}  ({st})", submenu=[
                item("Install / update hooks", lambda: self.confirm(key, True)),
                item("Remove hooks", lambda: self.confirm(key, False)),
                *([] if hi.is_codex(key) else [item("Claude usage status line...", lambda: self.confirm_usage(key))]),
                None,
                item("Restore backup", submenu=restore),
                item("Back up now", lambda: self.start_job(key, "backup")),
                item("Open backups folder", lambda: self.open_backups(key)),
            ])

        refresh = lambda: threading.Thread(target=self.refresh_targets, daemon=True).start()  # noqa: E731
        claude = [target("This Mac", hi.LOCAL), None,
                  item("Cowork (Claude desktop app)...", self.show_cowork),
                  item("Run setup again...", self.show_setup),
                  item("Check for old Claude Pet hooks...", lambda: self.check_legacy(True)),  # LEGACY
                  item("Re-detect / refresh status", refresh)]
        codex = [target(lab, key) for lab, key in self.codex_targets] or [item("Codex not found", enabled=False)]
        codex += [None, item("Run setup again...", self.show_setup), item("Re-detect / refresh status", refresh)]
        cfg = self.pet.cfg
        auto = [item("Approve permission prompts automatically for:", enabled=False)]
        for title, entries in self.auto_sections():
            auto += [None, item(title, enabled=False)] + (
                [item("   " + self.auto_label(lab, key), lambda k=key, lab=lab: self.toggle_auto(k, lab),
                      checked=key in self.c_auto) for lab, key in entries] or [item("   not found", enabled=False)])
        auto += [None, item("Turn all off", self.auto_all_off, enabled=bool(self.c_auto))]
        top = [item(f"Update available: {self.update_info['tag']}...", self.open_update), None] if self.update_info else []
        return top + [
            item("Show pet" if self.hidden else "Hide pet", self.toggle),
            None,
            item("Claude Code hooks", submenu=claude),
            item("Codex hooks", submenu=codex),
            item("Auto approve (ON)" if self.c_auto else "Auto approve", submenu=auto),
            None,
            item("Compact mode (one pet)", self.pet.toggle_compact, checked=bool(cfg.get("compact"))),
            item("Click goes to the session's window",
                 lambda: self.pet.set_click_to_focus(not cfg.get("click_to_focus", True)),
                 checked=bool(cfg.get("click_to_focus", True))),
            item("Show on all desktops", lambda: self.pet.set_all_spaces(not cfg.get("all_spaces", True)),
                 checked=bool(cfg.get("all_spaces", True))),
            item("Session titles", submenu=[item(text, lambda v=value: self.pet.set_session_titles(v),
                                                 checked=cfg.get("session_titles", "name") == value)
                                            for value, text in (("name", "Session name"), ("prompt", "Last prompt"))]),
            item("Tooltips", submenu=[item(label, lambda k=kind: self.pet.set_tooltip(k, not cfg.get(k + "_tooltips", True)),
                                           checked=bool(cfg.get(kind + "_tooltips", True)))
                                      for kind, label in (("session", "Session details"), ("usage", "Usage details"))]),
            item("Claude account usage (unofficial)", self.toggle_claude_oauth_usage,
                 checked=bool(cfg.get("claude_oauth_usage", False))),
            item("Answer Codex prompts from the pet", lambda: self.pet.set_codex_answers(not cfg.get("codex_answers")),
                 checked=bool(cfg.get("codex_answers"))),
            item("Mute sounds", self.toggle_mute, checked=self.c_muted),
            item("Notifications", self.toggle_notify, checked=self.c_notify),
            item("Dark theme", self.toggle_theme, checked=core.T.get("name") == "dark"),
            item("Pet style", submenu=[item(label, lambda k=key: self.set_style(k), checked=core.STYLE["v"] == key)
                                       for key, label in PET_STYLES]),
            item("Pet size...", self.pet.open_size_slider),
            item("Reset pet size", self.pet.reset_scale),
            item("Reset pet position (main screen)", self.reset_position),
            item("Answer timeout...", self.pet.open_answer_slider),
            item("Clear finished after...", self.pet.open_done_slider),
            item("Health check every...", self.pet.open_health_slider),
            item("Clear finished", self.pet.clear_finished),
            None,
            item("Log hook events (debug)", self.toggle_debug, checked=os.path.exists(DEBUG_FLAG)),
            item("Start at login", self.toggle_autostart, checked=self.c_autostart),
            item(f"Workbench: {self.c_wb}", enabled=False),
            item("Save diagnostics...", self.pet.save_diagnostics),
            item("Open config folder", self.open_config),
            None,
            item(f"AIPet {self.version}", enabled=False),
            item("About AIPet...", self.show_about),
            item("Check for updates...", lambda: self.check_updates(True)),
            item("Check for updates automatically", self.toggle_update_check,
                 checked=bool(cfg.get("update_check", True))),
            None,
            item("Quit AIPet", self.quit),
        ]

    # ---- actions (Tk thread)
    def show_about(self):
        existing = getattr(self, "about_win", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        win = self.about_win = tk.Toplevel(self.root)
        win.title("About AIPet")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        bg, fg = core.T["tag_bg"], core.T["tag_fg"]
        win.configure(bg=bg)
        tk.Label(win, text=f"AIPet {self.version}", font=("Segoe UI", 18, "bold"), bg=bg, fg=fg).pack(padx=28, pady=(24, 8))
        tk.Label(win, text="A little companion for your AI sessions.\nCreated by shreduck.", bg=bg, fg=fg).pack(padx=28, pady=(0, 16))
        for label, url in (("AIPet app page", "https://shreduck.github.io/duck-software/apps/aipet/"),
                           ("Duck Software · creator's page", "https://shreduck.github.io/duck-software/"),
                           ("AIPet on GitHub", "https://github.com/shreduck/AIPet")):
            link = tk.Label(win, text=label, fg="#5b8def", bg=bg, cursor="hand2", padx=12, pady=6)
            link.pack()
            link.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
        tk.Button(win, text="Close", command=win.destroy, **core.button_style("secondary")).pack(pady=(16, 24))
        win.bind("<Escape>", lambda e: win.destroy())
        core.theme_window(win)

    def _dialog_parent(self):
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.attributes("-topmost", True)
        return top

    def ask(self, text):
        """Yes / No question in the pet's style (follows the light / dark theme)."""
        try:
            return bool(core.themed_dialog(self.root, APP_NAME, text, kind="question",
                                           buttons=(("No", False, "secondary"), ("Yes", True, "primary")), cancel=False))
        except tk.TclError:
            p = self._dialog_parent()
            try:
                return messagebox.askyesno(APP_NAME, text, parent=p)
            finally:
                p.destroy()

    def info(self, text, error=False):
        try:
            core.themed_dialog(self.root, APP_NAME, text, kind="error" if error else "info")
        except tk.TclError:
            p = self._dialog_parent()
            try:
                (messagebox.showerror if error else messagebox.showinfo)(APP_NAME, text, parent=p)
            finally:
                p.destroy()

    def toggle_claude_oauth_usage(self):
        on = not self.pet.cfg.get("claude_oauth_usage", False)
        if on and not self.ask("Enable experimental Claude account usage?\n\n"
                               "This reads Claude Code's sign-in token and calls an unofficial endpoint. "
                               "It is not part of Anthropic's public API and might change or break in the future. "
                               "Anthropic restricts third-party use of subscription credentials; check that your use is permitted. "
                               "On macOS, accessing the Keychain may prompt for permission.\n\n"
                               "Requests run in the background, at most once every five minutes per login store. "
                               "AIPet saves usage figures, not your token. Disable this toggle at any time."):
            self.claude_oauth_var.set(False)
            return
        self.pet.cfg["claude_oauth_usage"] = bool(on)
        core.save_setting("claude_oauth_usage", bool(on))
        self.claude_oauth_var.set(bool(on))
        self.pet.hide_tip()

    def confirm_usage(self, key):
        on = not hi.usage_enabled(key)
        text = (f"Enable Claude's CLI usage collector for {hi.describe(key)}?\n\n"
                "This changes Claude Code's status line. If you have one, AIPet runs your existing command through Bash "
                "after recording Claude's usage data, adding a process on each refresh. If you have none, it adds a line "
                "showing usage percentages. The previous setting is saved in ~/.aipet and restored when disabled.\n\n"
                "This works in the CLI; VS Code and desktop sessions may not provide usage data. "
                "AIPet does not read sign-in credentials or the Keychain.") if on else (
                f"Disable Claude usage collection for {hi.describe(key)} and restore your previous status line?")
        if self.ask(text):
            self.start_job(key, "usage", on)

    def confirm(self, key, install):
        where = hi.describe(key)
        if install:
            text = (f"Add AIPet hooks to:\n{where}\n\nYour existing hooks and settings are kept, and the "
                    f"current file is backed up first (tray > Claude Code hooks > Restore backup).\n"
                    f"Only sessions started afterwards will show up."
                    + ("\n\nClaude usage collection is a separate, optional setting in this menu." if not hi.is_codex(key) else "")
                    + ("\n\nCodex then asks you to trust the new hooks once: run /hooks in Codex." if hi.is_codex(key) else ""))
        else:
            text = f"Remove AIPet hooks from:\n{where}\n\nOther hooks are left untouched and a backup is taken first."
        if self.ask(text):
            self.start_job(key, "install" if install else "remove")

    def confirm_restore(self, key, b):
        lines = [f"Restore {hi.describe(key)}", f"to the backup from {b['when']} ({b['reason']})?", ""]
        if b["absent"]:
            lines.append("settings.json did not exist at that point, so it will be DELETED.")
        else:
            lines.append(f"That version {'contains' if b['pet_hooks'] else 'does not contain'} AIPet hooks "
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
            elif action == "usage":
                msg = hi.set_usage(key, bool(arg))
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

    def reset_position(self):
        """Tray / menu bar: bring a lost pet back to the main screen's bottom-right corner (and show it if hidden)."""
        if self.hidden:
            self.show()
        self.pet.reset_position()

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
        """Start/stop writing ~/.aipet/events.log (metadata only; see the hook's debug_log)."""
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
                      "This usually means this copy of AIPet was started from inside another app (for example the Claude "
                      "desktop app), which keeps its registry changes in a private area that Windows ignores at login.\n\n"
                      "Quit AIPet, start AIPet.exe from File Explorer, and switch 'Start with Windows' on again.",
                      error=True)
        self.c_autostart = autostart_enabled()
        if hasattr(self, "autostart_var"):
            self.autostart_var.set(self.c_autostart)
        self.refresh_menu()

    def open_config(self):
        open_path(core.HOME_DIR)

    def quit(self):
        if getattr(self, "status_item", None):
            self.status_item.remove()
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
    if "--selftest-spaces" in sys.argv:
        if not IS_MAC:
            raise RuntimeError("The Spaces probe requires macOS")
        core.ensure_home()
        mark_setup_done()
        app = TrayApp()
        from aipet_selftest import start_spaces_probe
        output = sys.argv[sys.argv.index("--selftest-spaces") + 1]
        start_spaces_probe(app, os.path.abspath(output))
        app.root.mainloop()
        return
    if "--selftest" in sys.argv:  # CI (mac-selftest workflow): run without the setup window, save diagnostics, quit
        core.ensure_home()
        mark_setup_done()
        app = TrayApp()
        app.root.after(12000, app.pet.write_diagnostics)
        app.root.after(20000, app.quit)
        app.root.mainloop()
        return
    if not single_instance():
        return  # already running in the tray
    if legacy.needs_migration() and not legacy.migration_window(APP_NAME, set_autostart):  # LEGACY
        return
    TrayApp().root.mainloop()


if __name__ == "__main__":
    main()
