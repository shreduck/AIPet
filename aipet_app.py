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
import aipet_guide
import aipet_settings
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
# Remnants of earlier versions, kept under Appearance > Alpha
CONFIG_WATCH_MS = 1500  # how often config.json is checked for hand edits
RESTART_KEYS = {"workbench", "claude_code", "click_through", "max_pets"}  # read once at start


def config_stamp():
    try:
        st = os.stat(core.CONFIG_PATH)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


BETA_STYLES = {"mole": "The very first pet, rising from a dirt mound.", "cat": "An early experiment: a round cat."}
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


def set_window_icon(root):
    """The robot as the icon of every AIPet window (title bar and taskbar), in place of Tk's feather. Kept on the
    root so the images aren't garbage-collected. macOS shows the app's Dock icon instead."""
    if IS_MAC:
        return
    try:
        from PIL import ImageTk
        spr = core.load_sprites()
        if not spr:
            return
        robot = core.robot_image("happy", ("amber", "green", "off"), spr)
        photos = []
        for size in (256, 64, 32, 16):  # Windows picks the closest size for the taskbar, title bar and Alt+Tab
            k = min(size / robot.width, size / robot.height)
            im = robot.resize((max(1, int(robot.width * k)), max(1, int(robot.height * k))), Image.NEAREST)
            square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            square.paste(im, ((size - im.width) // 2, size - im.height), im)
            photos.append(ImageTk.PhotoImage(square, master=root))
        root.iconphoto(True, *photos)  # True: also every Toplevel created later
        root._aipet_icons = photos
    except Exception as e:
        core.log_error(f"window icon: {e!r}")


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
        set_window_icon(self.root)
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
        # One menu definition serves the pet, macOS menu bar and Windows tray.
        self.pet.context_menu_spec = self._context_menu_spec

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
        self._config_stamp = config_stamp()
        self.root.after(CONFIG_WATCH_MS, self.watch_config)
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

        tk.Label(win, text="Where should AIPet watch Claude Code?", font=(core.UI_FONT, 12, "bold")
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
                           font=(core.UI_FONT, 10)).pack(anchor="w")
            state_text, color = {
                "ready": ("not installed yet", grey),
                "installed": ("hooks installed and up to date", "#15803d"),
                "outdated": ("hooks installed but out of date - tick to update them", "#b45309"),
                "blocked": ("settings.json can't be read (invalid JSON?) - fix it first; nothing is changed", "#b91c1c"),
                "needs_python": (t["note"], "#b91c1c"),
                "missing": (t["note"], grey),
            }.get(t["kind"], (t["note"], grey))
            tk.Label(row, text=state_text, fg=color, font=(core.UI_FONT, 8, "bold"), wraplength=400, justify="left"
                     ).pack(anchor="w", padx=(26, 0))
            if t["kind"] in ("ready", "installed", "outdated"):
                tk.Label(row, text=t["note"], fg=grey, font=(core.UI_FONT, 8), wraplength=400, justify="left"
                         ).pack(anchor="w", padx=(26, 0))
            if t["key"] == "mac" and usable:
                self._mac_runtime_ui(row, t, runtime)
        if not targets and not stopped:
            tk.Label(body, text="No Claude Code installs found.", fg=grey).pack(anchor="w")
        if hidden:
            tk.Label(body, fg=grey, font=(core.UI_FONT, 8), wraplength=420, justify="left",
                     text=f"{hidden} WSL distro{'s' if hidden != 1 else ''} hidden: Claude Code isn't installed there."
                     ).pack(anchor="w", pady=(6, 0))

        if stopped:
            tk.Label(body, fg=grey, font=(core.UI_FONT, 8), wraplength=420, justify="left",
                     text="Not checked (not running): " + ", ".join(t["label"][5:] for t in stopped)
                     ).pack(anchor="w", pady=(6, 0))
            def check_stopped():
                if self.ask("Checking stopped WSL distros starts them. Continue?"):
                    self._close_setup()
                    self.show_setup(check_stopped=True)
            tk.Button(win, text="Check stopped WSL distros (starts them)...", command=check_stopped
                      ).pack(anchor="w", padx=16, pady=(0, 6))

        tk.Label(win, justify="left", wraplength=430, fg=grey, font=(core.UI_FONT, 8),
                 text="Each settings.json is backed up first, and your other hooks and settings are kept. "
                      "Only Claude Code sessions started afterwards show up. You can change this any time from "
                      "the tray menu: Claude Code hooks."
                 ).pack(anchor="w", padx=16, pady=(0, 8))

        cw = tk.LabelFrame(win, text=" Claude desktop app - Cowork ", font=(core.UI_FONT, 9, "bold"))
        cw.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(cw, justify="left", wraplength=410, font=(core.UI_FONT, 8),
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
        tk.Label(box, text="Hook runtime", font=(core.UI_FONT, 8, "bold")).pack(anchor="w")
        tk.Radiobutton(box, variable=var, value="python", state="normal" if py else "disabled", font=(core.UI_FONT, 9),
                       text=f"Your Python 3 (faster) - {py}" if py else "Your Python 3 (faster) - not found"
                       ).pack(anchor="w")
        tk.Radiobutton(box, variable=var, value="builtin", font=(core.UI_FONT, 9),
                       text="Built-in hook (nothing to install)").pack(anchor="w")
        warn = tk.Label(box, fg="#b45309", font=(core.UI_FONT, 8), wraplength=380, justify="left")
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
            tk.Label(win, text=text, font=(core.UI_FONT, 11, "bold")).pack(anchor="w", padx=16, pady=(12, 2))

        def p(text, **kw):
            tk.Label(win, text=text, justify="left", wraplength=wrap, font=(core.UI_FONT, 9), **kw
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
        tk.Label(win, text="Some hooks still use the old Claude Pet name", font=(core.UI_FONT, 12, "bold")
                 ).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, fg="#6b7280", justify="left", wraplength=480,
                 text="They keep working for now, but support for the old name will be removed in a future version. "
                      "This reminder shows once a day while anything is left.").pack(anchor="w", padx=16)
        body = tk.Frame(win)
        body.pack(fill="x", padx=16, pady=8)
        for what, how in found:
            tk.Label(body, text=what, font=(core.UI_FONT, 9, "bold"), justify="left", wraplength=480).pack(anchor="w", pady=(6, 0))
            tk.Label(body, text=how, font=(core.UI_FONT, 9), justify="left", wraplength=480).pack(anchor="w")
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
        agent_label = "Codex" if hi.is_codex(key) else "Claude Code"
        win.title(f"{APP_NAME} - auto approve - {agent_label} - {label}")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        accent = "#5b8def" if hi.is_codex(key) else "#d98960"
        frame = tk.Canvas(win, highlightthickness=0, bd=0)
        frame.pack()
        body = tk.Frame(frame)
        frame.create_window(14, 14, window=body, anchor="nw")

        def fit_card(_event=None):
            w, h = body.winfo_reqwidth() + 28, body.winfo_reqheight() + 28
            frame.configure(width=w, height=h)
            frame.delete("outline")
            frame.create_polygon(8, 2, w - 8, 2, w - 8, 5, w - 3, 5, w - 3, 8, w - 2, 8,
                                 w - 2, h - 8, w - 5, h - 8, w - 5, h - 3, w - 8, h - 3, w - 8, h - 2,
                                 8, h - 2, 8, h - 5, 3, h - 5, 3, h - 8, 2, h - 8, 2, 8, 5, 8, 5, 3, 8, 3,
                                 fill="", outline=accent, width=2, tags="outline")
        body.bind("<Configure>", fit_card)
        grey = "#6b7280"
        enabled = tk.BooleanVar(value=bool(old.get("enabled")))
        allow_all = tk.BooleanVar(value=bool(old.get("allow_all")))

        tk.Label(body, text="AUTO APPROVAL", fg=accent, font=(core.MONO_FAMILY, 9, "bold")).pack(anchor="w")
        tk.Label(body, text=label, font=(core.MONO_FAMILY, 17, "bold")).pack(anchor="w", pady=(3, 6))
        tk.Label(body, fg=grey, justify="left", wraplength=640, font=(core.MONO_FAMILY, 9),
                 text="Choose which requests can continue without asking.\nRequests outside your rules still need your approval."
                 ).pack(anchor="w", pady=(0, 14))
        mode = tk.LabelFrame(body, text=" Approval mode ", font=(core.MONO_FAMILY, 10, "bold"),
                             relief="solid", bd=1, padx=10, pady=8)
        mode.pack(fill="x")
        tk.Checkbutton(mode, text="Auto approve for this config", variable=enabled, font=(core.MONO_FAMILY, 10, "bold")
                       ).pack(anchor="w")
        tk.Checkbutton(mode, text="Allow all - approve every request (ignores both lists)",
                       variable=allow_all, font=(core.MONO_FAMILY, 9)).pack(anchor="w", pady=(4, 0))
        mode_hint = tk.Label(mode, fg=grey, justify="left", wraplength=620, font=(core.MONO_FAMILY, 8))
        mode_hint.pack(anchor="w", padx=4, pady=(6, 0))

        cols = tk.Frame(body)
        cols.pack(fill="x", pady=(16, 0))
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
            tk.Label(box, text=title, font=(core.MONO_FAMILY, 9, "bold")).pack(anchor="w", pady=(0, 5))
            tk.Label(box, text=hint, fg=grey, justify="left", anchor="nw", height=6,
                     wraplength=300, font=(core.MONO_FAMILY, 8)).pack(anchor="w")
            text = tk.Text(box, width=40, height=9, wrap="none", font=(core.MONO_FAMILY, 9), undo=True, padx=8, pady=8)
            text.insert("1.0", "\n".join(lines))
            text.pack(anchor="w", fill="x", pady=(4, 0))
            boxes[name] = text
        tk.Label(body, fg=grey, justify="left", wraplength=640, font=(core.MONO_FAMILY, 8),
                 text="Empty lines and lines starting with # are ignored. Only works while AIPet is running and its "
                      "hooks are installed for this config; the VS Code extension sends no permission events."
                 ).pack(anchor="w", pady=(10, 0))
        error = tk.Label(body, fg="#b91c1c", justify="left", wraplength=640, font=(core.MONO_FAMILY, 9))
        error.pack(anchor="w", padx=16)

        def sync(*_):  # Allow all switches the lists off
            state = "disabled" if allow_all.get() else "normal"
            for t in boxes.values():
                t.configure(state=state, fg=core.T["muted"] if allow_all.get() else core.T["entry_fg"])
            mode_hint.configure(text="Auto approval is off. Requests still need your approval." if not enabled.get()
                                else "All requests will be approved. The lists below are ignored." if allow_all.get()
                                else "Whitelist matches may proceed. Blacklist matches always ask you.")
        allow_all.trace_add("write", sync)
        enabled.trace_add("write", sync)

        def close():
            wins.pop(key, None)
            win.destroy()

        pending = {"value": False}

        def finish_save(rules, accepted):
            if not win.winfo_exists():
                return
            try:
                if not accepted:
                    return
                if not core.save_auto_approve(key, rules):
                    error.configure(text="Couldn't save these rules. Check that ~/.aipet is writable, then try again.")
                    return
                close()
                self._auto_changed()
            finally:
                pending["value"] = False
                if win.winfo_exists():
                    save_button.configure(state="normal")

        def save():
            if pending["value"]:
                return
            error.configure(text="")
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
            pending["value"] = True
            save_button.configure(state="disabled")
            if all_on or newly_on:
                # Return from Aqua's button callback before opening the warning;
                # continue saving asynchronously after the warning is destroyed.
                win.after_idle(lambda: self.warn_auto(label, rules, parent=win,
                               on_result=lambda accepted: finish_save(rules, accepted)))
                return
            finish_save(rules, True)

        buttons = tk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        save_button = tk.Button(buttons, text="Save", width=12, command=save, default="active")
        save_button.pack(side="right")
        tk.Button(buttons, text="Cancel", width=10, command=close).pack(side="right", padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: close())
        core.theme_window(win)
        sync()
        win.update_idletasks()
        win.geometry(f"+{max(0, (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2)}"
                     f"+{max(0, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)}")
        if IS_MAC:
            import mac_statusbar
            win._mouse_bridge = mac_statusbar.PanelMouseBridge(win)
            win.bind("<Destroy>", lambda event: win._mouse_bridge.close() if event.widget is win else None, add="+")

    toggle_auto = open_auto_rules  # the menus' entry point

    def warn_auto(self, label, rules, parent=None, on_result=None):
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
            parent if parent is not None else self.root, f"{APP_NAME} - auto approve", kind="warning", heading=heading,
            text=(what + "\n\nIt takes effect immediately for running sessions and only while AIPet is running.\n\n"
                  "Turn it off any time: pet or menu-bar / tray menu > Permissions > Auto approve."),
            buttons=(("Cancel", False, "secondary"), ("Auto approve", True, "danger")),
            cancel=False, enter_confirms=False, on_result=on_result)

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

    # ---- config.json edited by hand: apply it live
    def watch_config(self):
        try:
            stamp = config_stamp()
            if stamp != self._config_stamp:
                self._config_stamp = stamp
                self.apply_config_file()
        except Exception as e:
            core.log_error(f"config watch: {e!r}")
        self.root.after(CONFIG_WATCH_MS, self.watch_config)

    def apply_config_file(self):
        """Apply what changed in config.json since the app last saw it. A file that isn't valid JSON (an editor
        mid-save, a typo) is skipped until it is. Returns the keys that only apply after a restart."""
        try:
            with open(core.CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        cfg, pet = self.pet.cfg, self.pet
        merged = core.deep_merge(core.DEFAULT_CONFIG, data)  # nested sections (workbench...) as the app holds them
        changed = {k: merged[k] for k in data if cfg.get(k) != merged[k]}
        if not changed:
            return []
        handlers = {
            "theme": lambda v: (pet.apply_theme(v), hasattr(self, "theme_var") and self.theme_var.set(v == "dark")),
            "size": lambda v: pet.set_size(float(v)),
            "answer_wait_seconds": pet.set_answer_wait,
            "done_timeout_minutes": pet.set_done_timeout,
            "health_check_seconds": pet.set_health_interval,
            "sounds": lambda v: (pet.muted.set(not v), setattr(self, "c_muted", not v)),
            "notifications": self._set_notify,
            "pet_style": self.set_style,
            "compact": lambda v: pet.toggle_compact(bool(v)),
            "session_titles": pet.set_session_titles,
            "click_to_focus": pet.set_click_to_focus,
            "all_spaces": pet.set_all_spaces,
            "claude_answers": pet.set_claude_answers,
            "codex_answers": pet.set_codex_answers,
            "session_tooltips": lambda v: pet.set_tooltip("session", v),
            "usage_tooltips": lambda v: pet.set_tooltip("usage", v),
            "card_scale": pet.set_card_scale,
        }
        restart = []
        core.SAVE_PAUSED["v"] = True  # the setters save; the file already says so
        try:
            for key, value in changed.items():
                if key in RESTART_KEYS:
                    restart.append(key)
                    continue
                try:
                    if key in handlers:
                        handlers[key](value)
                    cfg[key] = value  # everything else is read where it's used (sound_style, update_check...)
                except Exception as e:
                    core.log_error(f"config.json {key}={value!r}: {e!r}")
        finally:
            core.SAVE_PAUSED["v"] = False
        self.refresh_menu()  # menus and the settings window show the new values
        if restart:
            self.info("config.json changed. Applied what could be applied now; these take effect after AIPet "
                      "restarts: " + ", ".join(sorted(restart)))
        return restart

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
        if getattr(self, "settings_win", None) is not None:
            self.ui(self.settings_win.refresh)  # may be called from a worker thread: rebuild on the Tk thread
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
        def dispatch(fn):
            return lambda: self.ui(fn)

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
            return convert(self._mac_menu_spec())
        return pystray.Menu(entries)

    def _context_menu_spec(self, pet):
        def item(label, action, enabled=True):
            return {"label": label, "action": action, "enabled": enabled}
        session = self.pet._menu_session(pet)
        real_pet = bool(pet and pet.key != "_none")
        session_actions = [
            item("Session details...", lambda: self.pet.open_detail(pet.data.get("focus", pet.key)), real_pet),
            item("Go to session window", lambda: self.pet.focus_session(pet), real_pet),
            item("Open in VS Code", lambda: self.pet.open_in_vscode(pet),
                 bool(pet and pet.data.get("source") == "CC" and pet.data.get("cwd"))),
            None,
            item("Mark as finished", self.pet.finish_menu_pet,
                 bool(session and session.get("source") == "CC" and session.get("state") in
                      ("working", "needs_input", "error"))),
            item("Dismiss this pet", self.pet.dismiss_menu_pet, real_pet),
        ]
        shared = self._mac_menu_spec()  # starts with Settings... and a separator
        return shared[:1] + [{"label": "This session", "submenu": session_actions}, None] + shared[2:]

    # Shared menu specification; all actions are executed on the Tk thread.
    def _mac_menu_spec(self, settings=False):
        """settings=True: the settings window's version, with a few entries that only make sense there."""
        def item(label, action=None, checked=None, enabled=True, submenu=None, default=False, help=None, icon=None,
                 choice=False, slider=None):
            # help / icon / choice / slider are only read by the settings window; the menus ignore them
            entry = {"label": label, "action": action, "enabled": enabled, "submenu": submenu}
            if default:
                entry["default"] = True
            if checked is not None:
                entry["checked"] = checked
            if help:
                entry["help"] = help
            if icon:
                entry["icon"] = icon
            if choice:
                entry["choice"] = True  # the submenu is a pick-one list
            if slider:
                entry["slider"] = slider  # the settings window shows a slider; the menus show the choice list
            return entry

        def target(name, key):
            st = self.status.get(key, "checking…")
            backups = hi.list_backups(key)[:15]
            restore = [item(f"{b['when']} - {b['reason']}" + ("  [original]" if b["original"] else ""),
                            lambda b=b: self.confirm_restore(key, b)) for b in backups] or [item("No backups yet", enabled=False)]
            return item(f"{'✓ ' if st.startswith('installed') else ''}{name}  ({st})", submenu=[
                item("Install / update hooks", lambda: self.confirm(key, True),
                     help="Adds AIPet's hooks to this install's settings, or brings them up to date. Your own hooks stay."),
                item("Remove hooks", lambda: self.confirm(key, False),
                     help="Takes AIPet's hooks out again. The pet stops seeing sessions from here."),
                *([] if hi.is_codex(key) else [item(
                    "Claude usage status line...", lambda: self.confirm_usage(key),
                    help="Lets the pet show Claude's usage limits by recording them from Claude Code's status line.")]),
                None,
                item("Restore backup", submenu=restore,
                     help="Puts back a settings file saved before AIPet changed it."),
                item("Back up now", lambda: self.start_job(key, "backup"),
                     help="Saves a copy of the current settings file."),
                item("Open backups folder", lambda: self.open_backups(key)),
            ], help=f"Hook status: {st}.")

        refresh = lambda: threading.Thread(target=self.refresh_targets, daemon=True).start()  # noqa: E731
        claude = [target("This Mac" if IS_MAC else "This PC (Windows)", hi.LOCAL)]
        claude += [target(f"WSL: {name}", "wsl:" + name) for name, _ in self.distros]
        claude += [None, item("Cowork (Claude desktop app)...", self.show_cowork,
                              help="How to let the pet follow Cowork sessions in the Claude desktop app."),
                   item("Check for old Claude Pet hooks...", lambda: self.check_legacy(True),
                        help="Finds hooks left behind by Claude Pet, this app's old name, and offers to remove them.")]
        codex = [target(lab, key) for lab, key in self.codex_targets] or [item("Codex not found", enabled=False)]
        cfg = self.pet.cfg
        auto = []
        for title, entries in self.auto_sections():
            auto.append(item(title, submenu=[
                item(self.auto_label(lab, key), lambda k=key, lab=lab: self.toggle_auto(k, lab),
                     checked=key in self.c_auto) for lab, key in entries
            ] or [item("Not found", enabled=False)]))
        auto += [None, item("Turn all off", self.auto_all_off, enabled=bool(self.c_auto),
                            help="Switches auto approve off everywhere at once.")]
        top = [item(f"Update available: {self.update_info['tag']}...", self.open_update, icon="refresh",
                    help="A newer AIPet is out. See what changed and update."), None] if self.update_info else []
        return [self.settings_item(), None] + top + [
            item("Show pet" if self.hidden else "Hide pet", self.toggle, default=True,
                 help="Hides the pet from the screen. AIPet keeps watching your sessions in the background."),
            item("Clear finished sessions", self.pet.clear_finished,
                 help="Removes the pets of sessions that are done."),
            item("Open config and data folder", self.open_config, icon="home",
                 help=f"{core.HOME_DIR}: settings (config.json, auto-approve.json), sessions, logs and backups."),
            *([item("Copy settings guide for AI", self.copy_ai_guide,
                    help="Copies a Markdown guide to AIPet's settings files (where they are, every option and its "
                         "values) to the clipboard. Paste it into Claude, Codex or another assistant and ask it to "
                         "change AIPet's settings for you.")] if settings else []),
            None,
            item("Appearance", icon="palette", help="How the pet and its windows look.", submenu=[
                item("Pet style", choice=True, help="Which creature sits on your screen.",
                     submenu=[item(label, lambda k=key: self.set_style(k), checked=core.STYLE["v"] == key)
                              for key, label in PET_STYLES if key not in BETA_STYLES]),
                item("Pet size...", self.pet.open_size_slider, help="Make the pet bigger or smaller.",
                     slider={"value": core.SCALE["v"] / core.SCALE_UNIT, "min": 0.3, "max": 3.0, "step": 0.05,
                             "set": self.pet.set_size, "format": lambda v: f"{int(round(v * 100))}%"}),
                item("Reset pet size", self.pet.reset_scale, help="Back to the standard size."),
                item("Compact mode (one pet)", self.pet.toggle_compact, checked=bool(cfg.get("compact")),
                     help="One pet stands in for every session, showing the one that needs you most."),
                None,
                item("Dark theme", self.toggle_theme, checked=core.T.get("name") == "dark",
                     help="Dark name tags, bubbles and windows."),
                item("Window scale", choice=True,
                     help="Text and icons in this window. Also Ctrl + plus / minus / 0, or Ctrl + mouse wheel here.",
                     slider={"value": self.settings_zoom(), "min": aipet_settings.ZOOM_MIN,
                             "max": aipet_settings.ZOOM_MAX, "step": aipet_settings.ZOOM_STEP,
                             "set": self.set_settings_zoom, "format": lambda v: f"{int(round(v * 100))}%"},
                     submenu=[item(f"{int(v * 100)}%", lambda v=v: self.set_settings_zoom(v),
                                   checked=abs(self.settings_zoom() - v) < 0.01)
                              for v in (0.5, 0.75, 1.0, 1.25, 1.5)]),
                item("Session titles", choice=True, help="What the name tag under each pet shows.",
                     submenu=[item(text, lambda v=value: self.pet.set_session_titles(v),
                                   checked=cfg.get("session_titles", "name") == value)
                              for value, text in (("name", "Session name"), ("prompt", "Last prompt"))]),
                item("Tooltips", help="What appears when you hover over a pet.",
                     submenu=[item(label, lambda k=kind: self.pet.set_tooltip(k, not cfg.get(k + "_tooltips", True)),
                                   checked=bool(cfg.get(kind + "_tooltips", True)), help=text)
                              for kind, label, text in (
                                  ("session", "Session details", "Title, state, last message and how long ago."),
                                  ("usage", "Usage details", "Usage limits when you hover over a usage badge."))]),
                None,
                item("Alpha", icon="wrench",
                     help="Pet styles left over from earlier AIPet versions, no longer looked after. "
                          "Switch one off to go back to the robot.",
                     submenu=[item(f"{label} style", lambda k=key: self.set_style("robot" if core.STYLE["v"] == k else k),
                                   checked=core.STYLE["v"] == key, help=BETA_STYLES[key])
                              for key, label in PET_STYLES if key in BETA_STYLES]),
            ]),
            item("Behavior", icon="gear", help="What the pet does and when.", submenu=[
                *([item("Show on all desktops", lambda: self.pet.set_all_spaces(not cfg.get("all_spaces", True)),
                        checked=bool(cfg.get("all_spaces", True)),
                        help="Keep the pet visible when you switch virtual desktops" +
                             (" or Spaces." if IS_MAC else "."))] if IS_MAC or os.name == "nt" else []),
                item("Reset pet position (main screen)", self.reset_position,
                     help="Moves the pet back to the main screen, in case it ended up out of sight."),
                item("Click goes to the session's window",
                     lambda: self.pet.set_click_to_focus(not cfg.get("click_to_focus", True)),
                     checked=bool(cfg.get("click_to_focus", True)),
                     help="Clicking a pet brings its terminal or editor window to the front."),
                None,
                item("Sounds and notifications", icon="bell", help="How the pet gets your attention.", submenu=[
                    item("Mute sounds", self.toggle_mute, checked=self.c_muted,
                         help="No sound when a session needs you or finishes."),
                    item("Notifications", self.toggle_notify, checked=self.c_notify,
                         help="System notifications when a session needs you, hits an error or finishes."),
                    *([item("Sound style", choice=True,
                            help="Choose AIPet's custom chimes (the default) or your operating system's sounds.",
                            submenu=[item(text, lambda v=value: self.pet.set_sound_style(v),
                                          checked=cfg.get("sound_style", "chimes") == value)
                                     for value, text in (("chimes", "AIPet chimes (default)"),
                                                         ("system", "macOS sounds" if IS_MAC else "Windows sounds"))])]
                      if IS_MAC or os.name == "nt" else []),
                    item("Test sounds", icon="speaker", help="Hear each sound the pet makes, and when it plays it.",
                         submenu=[
                             item("Needs you: a permission prompt or a question", lambda: self.pet.beep(True, force=True),
                                  help="Also plays as a reminder while the session keeps waiting for you."),
                             item("Error: a session hit an error", lambda: self.pet.beep(True, force=True, kind="error"),
                                  help="Two low falling notes: something went wrong and needs a look."),
                             item("Done: a session finished its work", lambda: self.pet.beep(False, force=True),
                                  help="Plays when a working session finishes (switch off with sound_on_done in config)."),
                         ]),
                ]),
                item("Session timing", icon="clock", help="How long the pet waits for things.", submenu=[
                    item("Answer timeout...", self.pet.open_answer_slider,
                         help="How long a permission prompt waits for your answer from the pet. 0 means no limit.",
                         slider={"value": core.ANSWER_WAIT["v"], "min": 0, "max": core.MAX_WAIT, "step": 15,
                                 "set": self.pet.set_answer_wait, "format": core.fmt_wait}),
                    item("Clear finished after...", self.pet.open_done_slider,
                         help="How long a finished session's pet stays before it leaves. 0 keeps it until you dismiss it.",
                         slider={"value": core.DONE_TIMEOUT["v"], "min": 0, "max": core.MAX_WAIT // 60, "step": 1,
                                 "set": self.pet.set_done_timeout,
                                 "format": lambda v: "never" if v <= 0 else f"{int(v)} min"}),
                    item("Health check every...", self.pet.open_health_slider,
                         help="How often the pet checks that sessions are still running. 0 turns the check off.",
                         slider={"value": core.HEALTH["v"], "min": 0, "max": core.MAX_HEALTH, "step": 5,
                                 "set": self.pet.set_health_interval,
                                 "format": lambda v: "off" if v <= 0 else core.fmt_wait(v)}),
                ]),
                None,
                *([item("Start at login" if IS_MAC else "Start with Windows", self.toggle_autostart,
                        checked=self.c_autostart, help="Start AIPet automatically when you sign in.")]
                  if IS_MAC or os.name == "nt" else []),
            ]),
            item("Integrations", icon="plug", help="Where the pet gets its sessions from.", submenu=[
                item("Claude Code hooks", icon="link", submenu=claude,
                     help="Hooks let Claude Code tell the pet what each session is doing."),
                item("Codex hooks", icon="link", submenu=codex,
                     help="Hooks let Codex tell the pet what each session is doing."),
                item("Extra", icon="wrench", submenu=[
                    item("Claude account usage (unofficial)", self.toggle_claude_oauth_usage,
                         checked=bool(cfg.get("claude_oauth_usage", False)),
                         help="Reads your Claude plan's usage from an unofficial endpoint. Experimental; asks before turning on."),
                    None,
                    item("Run setup again...", self.show_setup, help="The first-run setup: pick which installs to hook up."),
                    item("Re-detect / refresh status", refresh, help="Look again for Claude Code, Codex and WSL installs."),
                    item(f"Workbench: {self.c_wb}", enabled=False),
                ]),
            ]),
            item("Permissions" + (" (auto approve ON)" if self.c_auto else ""), icon="shield",
                 help="Answering permission prompts.", submenu=[
                item("Auto approve", icon="check", submenu=auto,
                     help="Approve every permission prompt from these installs without asking. Use with care."),
                item("Answer Codex prompts from the pet", lambda: self.pet.set_codex_answers(not cfg.get("codex_answers")),
                     checked=bool(cfg.get("codex_answers")),
                     help="Codex permission prompts get Allow / Deny buttons in the pet's popup."),
                item("Answer Claude Code prompts from the pet", lambda: self.pet.set_claude_answers(not cfg.get("claude_answers", True)),
                     checked=bool(cfg.get("claude_answers", True)),
                     help="Claude Code permission prompts get Allow / Deny buttons in the pet's popup."),
            ]),
            None,
            item("Help", icon="help", help="About AIPet, updates and troubleshooting.", submenu=[
                item("About AIPet...", self.show_about),
                item(f"Version: {self.version}", enabled=False),
                item("Updates", icon="refresh", submenu=[
                    item("Check for updates...", lambda: self.check_updates(True)),
                    item("Check for updates automatically", self.toggle_update_check,
                         checked=bool(cfg.get("update_check", True)),
                         help="Look for a new AIPet now and then. Nothing is installed without asking."),
                ]),
                None,
                item("Diagnostics", icon="wrench", help="For when something isn't working.", submenu=[
                    item("Save diagnostics...", self.pet.save_diagnostics,
                         help="Saves a report you can attach to a bug report."),
                    item("Open config folder", self.open_config, help="Where AIPet keeps its settings and logs."),
                    item("Log hook events (debug)", self.toggle_debug, checked=os.path.exists(DEBUG_FLAG),
                         help="Writes what the hooks report to events.log (no prompt text)."),
                ]),
            ]),
            None,
            item("Quit AIPet", self.quit),
        ]

    def settings_item(self):
        return {"label": "Settings...", "action": self.show_settings, "enabled": True, "submenu": None,
                "settings": True}

    def show_settings(self):
        if getattr(self, "settings_win", None) is not None and self.settings_win.alive():
            self.settings_win.raise_()
            return
        self.settings_win = aipet_settings.SettingsWindow(self.root, lambda: self._mac_menu_spec(settings=True),
                                                          version=self.version,
                                                          zoom_fn=self.settings_zoom, set_zoom=self.set_settings_zoom)

    def copy_ai_guide(self):
        """Settings > General: the settings guide for an AI assistant, on the clipboard (Tk's clipboard is the
        system one on Windows and macOS)."""
        text = aipet_guide.build_guide(self.pet.cfg, core.HOME_DIR, self.version)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
        except tk.TclError as e:
            self.info(f"Couldn't copy to the clipboard:\n{e}", error=True)
            return
        self.info("The AIPet settings guide is on your clipboard.\n\nPaste it into Claude, Codex or another assistant, "
                  "then ask it to change what you want (for example: \"switch AIPet to the dark theme and make the "
                  "pets bigger\").")

    def settings_zoom(self):
        return aipet_settings.clamp_zoom(self.pet.cfg.get("settings_zoom", 1.0))

    def set_settings_zoom(self, value):
        value = aipet_settings.clamp_zoom(value)
        self.pet.cfg["settings_zoom"] = value
        core.save_setting("settings_zoom", value)
        self.refresh_menu()

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
        tk.Label(win, text=f"AIPet {self.version}", font=(core.UI_FONT, 18, "bold"), bg=bg, fg=fg).pack(padx=28, pady=(24, 8))
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
            return
        self.pet.cfg["claude_oauth_usage"] = bool(on)
        core.save_setting("claude_oauth_usage", bool(on))
        self.refresh_menu()
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
    if os.name == "nt":
        try:  # AIPet's own taskbar identity: its windows show the robot there, not python.exe's or Tk's icon
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AIPet.AIPet")
        except Exception:
            pass
    if legacy.needs_migration() and not legacy.migration_window(APP_NAME, set_autostart):  # LEGACY
        return
    TrayApp().root.mainloop()


if __name__ == "__main__":
    main()
