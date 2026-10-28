#!/usr/bin/env python3
"""
AIPet - tray application (entry point for AIPet.exe, in the AIPet folder).

* Floating pet window (aipet.PetApp) that can be hidden to the system tray.
* Tray icon changes colour with the most urgent session and shows a Windows
  notification when a session needs your input.
* Tray menu "Claude Code hooks" installs/removes hooks for Windows and for every
  auto-detected WSL distro.
* "Start with Windows" toggle (HKCU Run key).
"""
import aipetlib.ui.app_menus as app_menus
import ctypes
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
import aipetlib.ui.theme as theme
from aipetlib.ui import addin_messages as addin_message_ui
from aipetlib.runtime import diagnostics, paths, preferences
from aipetlib.runtime import settings as runtime_settings
from aipetlib.agents.tracker import NetworkTracker
import aipet_guide
import aipet_settings
from aipetlib import permissions
from aipetlib import geometry
from aipetlib.addins import AddinManager
from aipetlib import text as textfmt
from aipetlib import update as releases
from aipetlib.app.agent_windows import AgentWindows
from aipetlib.app.addin_intro import AddinIntro
from aipetlib.app.approvals import Approvals
from aipetlib.app.config_watch import ConfigWatcher
import aipetlib.app.diagnostics as app_diagnostics
from aipetlib.app.updates import UpdateFlow
from aipetlib.app.presentation import APP_NAME, TrayPresenter
from aipetlib.ui.tray_icons import set_window_icon
from aipetlib.integrations import targets as hook_targets
from aipetlib.integrations import legacy  # LEGACY: moving over from Claude Pet
from aipetlib.ui.legacy_migration import migration_window
from aipetlib.app.setup import SetupFlow, setup_is_done, mark_setup_done
from aipetlib.app.cowork import CoworkFlow
from aipetlib.app.legacy import LegacyFlow
from aipetlib.app.hooks import HookJobs

IS_MAC = sys.platform == "darwin"


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/com.aipet.app.plist")
_mutex = None
from aipetlib.runtime.paths import DEBUG_FLAG  # noqa: E402
from aipetlib.ui.pet_style import PET_STYLES  # noqa: E402


# --------------------------------------------------------------------------- helpers
def single_instance():
    global _mutex
    if os.name != "nt":
        try:
            import fcntl
            os.makedirs(paths.HOME_DIR, exist_ok=True)
            _mutex = open(os.path.join(paths.HOME_DIR, "app.lock"), "w")  # kept open for the process lifetime
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


def refresh_autostart_path():
    """Start with Windows / at login keeps pointing at the AIPet you actually run: a portable folder that was moved,
    or a new version unzipped somewhere else (or the old single-file exe replaced by the folder), would otherwise start
    the old copy - or nothing - at the next sign-in. Only touches an entry that is already on."""
    if not getattr(sys, "frozen", False) or not autostart_enabled():
        return
    try:
        if IS_MAC:
            import plistlib
            with open(LAUNCH_AGENT, "rb") as f:
                current = plistlib.load(f).get("ProgramArguments") or []
            if current[:1] != [sys.executable]:
                set_autostart(True)
        elif os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                current = winreg.QueryValueEx(k, "AIPet")[0]
            if os.path.normcase(str(current).strip('" ')) != os.path.normcase(sys.executable):
                set_autostart(True)
    except Exception as e:
        diagnostics.log_error(f"autostart path: {e!r}")


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


# --------------------------------------------------------------------------- app
class TrayApp:
    def __init__(self):
        runtime_settings.ensure_home()
        self.pet = core.PetApp()
        self.root = self.pet.root
        set_window_icon(self.root)
        self.q = queue.Queue()
        self.hidden = False
        self.distros = []          # [(name, state)]
        self.codex_targets = []    # [(label, key)] where Codex was found (this machine, running WSL distros)
        self.status = {hook_targets.LOCAL: "checking…"}
        # caches read by the tray thread (never touch Tk from there)
        self.c_muted = self.pet.muted.get()
        self.c_autostart = autostart_enabled()
        self.c_notify = bool(self.pet.cfg.get("notifications", False))
        self.c_wb = "off"
        self.agent_windows = AgentWindows(self)  # the agent network windows and their menu switches
        self._tk_thread = threading.current_thread()
        self.approvals = Approvals(self)  # auto approve rules per hook target, and which are on
        self.version = releases.current_version()
        self.updates = UpdateFlow(self)  # new releases: daily check, menu entries, the dialog
        self.setup = SetupFlow(self)
        self.cowork = CoworkFlow(self, open_path)
        self.legacy = LegacyFlow(self)
        self.hooks = HookJobs(self, open_path)
        self.presentation = TrayPresenter(self)
        self.addins = AddinManager(paths.HOME_DIR, self.pet.cfg.get("addins", {}))
        self.addins.start()
        self.pet.addin_snapshot = []
        self.pet.open_addin_messages = self.open_addin_messages
        self._addin_catalog_seen = self.addins.entries()
        self.addin_intro = AddinIntro(self)

        self.pet.on_alert = self.presentation.on_alert
        self.agent_tracker = NetworkTracker(lambda: list(self.pet._agent_items))
        self.agent_tracker.start()
        # One menu definition serves the pet, macOS menu bar and Windows tray.
        self.pet.context_menu_spec = self._context_menu_spec

        self.presentation.start()

        threading.Thread(target=self._startup_jobs, daemon=True).start()
        self.config_watch = ConfigWatcher(self)  # config.json edited by hand: applied live
        self.config_watch.start()
        self.pump()
        self.root.after(2000, self.addin_intro.maybe_show)

    # ---- threading
    def ui(self, fn):
        """Run fn on the Tk thread."""
        self.q.put(fn)

    def pump(self):
        # Schedule first: a queued confirmation may enter wait_window's nested event loop.
        # Other alerts and UI work must keep arriving while that confirmation waits.
        self.root.after(250, self.pump)
        self.pet.addin_snapshot = self.addins.messages.snapshot()
        catalog = self.addins.entries()
        if catalog != self._addin_catalog_seen:
            self._addin_catalog_seen = catalog
            self.refresh_menu()
        try:
            self.publish_addin_pet_locations()
            self.show_addin_alerts()
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        except Exception as e:
            print(f"[aipet] ui error: {e}", file=sys.stderr)
        self.c_muted = self.pet.muted.get()
        self.c_wb = self.pet.wb.status if self.pet.wb else "off"
        self.agent_windows.is_open()  # refresh the cached answer the tray menu reads
        self.presentation.update_tray()

    def _check_autostart(self):
        """At startup: show what Windows really sees, not an overlay (see windows_sees_autostart)."""
        seen = windows_sees_autostart()
        if seen is not None and seen != self.c_autostart:
            self.c_autostart = seen
            self.refresh_menu()

    def _startup_jobs(self):
        refresh_autostart_path()
        self._check_autostart()
        self.hooks.startup()


    # ---- new version check (GitHub releases; tells you and opens the page, never installs anything)
    # ---- config.json edited by hand: apply it live
    def config_handlers(self):
        """config.json key -> the setter that applies it now (the config watcher calls them with saving paused)."""
        pet = self.pet
        return {
            "addins": self.configure_addins,
            "addin_alert_topmost": lambda value: self.set_addin_alert_option('addin_alert_topmost', value),
            "addin_alert_no_focus": lambda value: self.set_addin_alert_option('addin_alert_no_focus', value),
            "theme": lambda v: (pet.preferences.apply_theme(v), hasattr(self, "theme_var") and self.theme_var.set(v == "dark")),
            "size": lambda v: pet.appearance.set_size(float(v)),
            "tooltip_text_scale": pet.tooltips.set_tooltip_text_scale,
            "answer_wait_seconds": pet.preferences.set_answer_wait,
            "done_timeout_minutes": pet.preferences.set_done_timeout,
            "health_check_seconds": pet.preferences.set_health_interval,
            "sounds": lambda v: (pet.muted.set(not v), setattr(self, "c_muted", not v)),
            "notifications": self._set_notify,
            "pet_style": self.set_style,
            "compact": lambda v: pet.preferences.toggle_compact(bool(v)),
            "session_titles": pet.preferences.set_session_titles,
            "click_to_focus": pet.preferences.set_click_to_focus,
            "context_banner": pet.preferences.set_context_banner,
            "all_spaces": pet.preferences.set_all_spaces,
            "claude_answers": pet.answers.set_claude_answers,
            "codex_answers": pet.answers.set_codex_answers,
            "session_tooltips": lambda v: pet.tooltips.set_tooltip("session", v),
            "usage_tooltips": lambda v: pet.tooltips.set_tooltip("usage", v),
            "card_scale": pet.preferences.set_card_scale,
        }

    def refresh_menu(self):
        if getattr(self, "settings_win", None) is not None:
            self.ui(self.settings_win.refresh)  # may be called from a worker thread: rebuild on the Tk thread
        self.presentation.refresh_menu()

    def _context_menu_spec(self, pet):
        return app_menus._context_menu_spec(self, pet)

    def _fast_cancel_item(self, pet, session):
        """Fix fast cancel: a prompt cancelled the instant it was sent leaves no trace Claude Code reports, so the pet
        keeps showing that session as working. This marks it finished (like This session > Mark as finished). In
        compact mode, one pet stands for every session: pick which one."""
        def busy(d):
            return bool(d and d.get("source") == "CC" and d.get("state") in ("working", "needs_input"))
        if pet and pet.data.get("members"):
            sessions = [d for d in self.pet._last_items if busy(d)]
            choices = [{"label": d.get("title") + (f" \u00b7 {d['conv']}" if d.get("conv") else ""),
                        "action": lambda d=d: self.pet.finish_session(d), "enabled": True} for d in sessions]
            return {"label": "Fix fast cancel", "enabled": True,
                    "submenu": choices or [{"label": "No session is working", "action": None, "enabled": False}]}
        return {"label": "Fix fast cancel", "action": lambda: self.pet.finish_session(session), "enabled": busy(session)}

    # Shared menu specification; all actions are executed on the Tk thread.
    def _mac_menu_spec(self, settings=False):
        return app_menus._mac_menu_spec(self, settings)

    def _live_spec(self, item, settings=False):
        return app_menus._live_spec(self, item, settings)

    def _start_timed(self, d):
        self.pet.answers.start_timed(d, on_result=lambda accepted: self.refresh_menu() if accepted else None)

    def _stop_timed(self, session_id):
        self.pet.answers.stop_timed(session_id)
        self.refresh_menu()

    def _timed_countdown_items(self):
        """Right-click: every running timed auto approve, with its countdown, one click to stop it."""
        grants, now = permissions.timed_grants(), time.time()
        return [{"label": f"⏱ Stop auto approve: {g.get('title') or 'session'} · "
                          f"{textfmt.fmt_left(g['until'] - now)} left",
                 "action": lambda sid=sid: self._stop_timed(sid), "enabled": True} for sid, g in grants.items()]

    def settings_item(self):
        return {"label": "AIPet menu...", "action": self.show_settings, "enabled": True, "submenu": None,
                "settings": True}

    # ---- agent network windows: one for all sessions, or one per session ("each")
    def startup_dialogs_open(self):
        """Wait with optional offers while setup, hook jobs, or confirmations are still open."""
        return (not setup_is_done() or self.setup.win is not None or self.hooks.busy
                or bool(theme.OPEN_DIALOGS) or self.root.grab_current() is not None)

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
        addins = self._addin_guide_entries()
        text = aipet_guide.build_guide(self.pet.cfg, paths.HOME_DIR, self.version, addins=addins)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
        except tk.TclError as e:
            self.info(f"Couldn't copy to the clipboard:\n{e}", error=True)
            return
        self.info("The AIPet settings guide is on your clipboard, including add-in settings and the script interface."
                  "\n\nPaste it into Claude, Codex or another assistant, then ask it to change settings or build an add-in.")

    def _addin_guide_entries(self):
        return [dict(id=key, name=spec['name'], enabled=self.pet.cfg.get('addins', {}).get(key) is True,
                     settings=self.addins.get_settings(key)) for key, spec in self.addins.entries()]

    def copy_addin_guide(self, notify=True):
        text = aipet_guide.build_addin_guide(paths.HOME_DIR, self.version)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
        except tk.TclError as error:
            if notify:
                self.info(f"Couldn't copy to the clipboard:\n{error}", error=True)
            return False
        if notify:
            self.info('The add-in Markdown guide is on your clipboard. Paste it into your AI assistant '
                      'and describe the add-in you want to build.')
        return True

    def settings_zoom(self):
        return preferences.clamp_zoom(self.pet.cfg.get("settings_zoom", 1.0))

    def set_settings_zoom(self, value):
        value = preferences.clamp_zoom(value)
        self.pet.cfg["settings_zoom"] = value
        runtime_settings.save_setting("settings_zoom", value)
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
        win.resizable(True, True)
        bg, fg = theme.T["tag_bg"], theme.T["tag_fg"]
        win.configure(bg=bg)
        tk.Label(win, text=f"AIPet {self.version}", font=(theme.UI_FONT, 18, "bold"), bg=bg, fg=fg).pack(padx=28, pady=(24, 8))
        tk.Label(win, text="A little companion for your AI sessions.\nCreated by shreduck.", bg=bg, fg=fg).pack(padx=28, pady=(0, 16))
        for label, url in (("AIPet app page", "https://shreduck.github.io/duck-software/apps/aipet/"),
                           ("Duck Software · creator's page", "https://shreduck.github.io/duck-software/"),
                           ("AIPet on GitHub", "https://github.com/shreduck/AIPet")):
            link = tk.Label(win, text=label, fg="#5b8def", bg=bg, cursor="hand2", padx=12, pady=6)
            link.pack()
            link.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
        tk.Button(win, text="Close", command=win.destroy, **theme.button_style("secondary")).pack(pady=(16, 24))
        win.bind("<Escape>", lambda e: win.destroy())
        theme.theme_window(win)

    def _dialog_parent(self):
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.attributes("-topmost", True)
        return top

    def ask(self, text):
        """Yes / No question in the pet's style (follows the light / dark theme)."""
        try:
            return bool(theme.themed_dialog(self.root, APP_NAME, text, kind="question",
                                           buttons=(("No", False, "secondary"), ("Yes", True, "primary")), cancel=False))
        except tk.TclError:
            p = self._dialog_parent()
            try:
                return messagebox.askyesno(APP_NAME, text, parent=p)
            finally:
                p.destroy()

    def info(self, text, error=False):
        try:
            theme.themed_dialog(self.root, APP_NAME, text, kind="error" if error else "info")
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
        runtime_settings.save_setting("claude_oauth_usage", bool(on))
        self.refresh_menu()
        self.pet.tooltips.hide_tip()

    def hide(self):
        self.pet.tooltips.hide_tip()
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
        self.pet.placement.reset_position()

    def toggle_mute(self):
        self.pet.muted.set(not self.pet.muted.get())
        self.c_muted = self.pet.muted.get()
        self.refresh_menu()

    def toggle_notify(self):
        self._set_notify(not self.c_notify)

    def _set_notify(self, value):
        self.c_notify = bool(value)
        self.pet.cfg["notifications"] = self.c_notify
        if not runtime_settings.save_setting("notifications", self.c_notify):
            self.info("Couldn't save this setting (config.json isn't valid JSON?). It applies until you quit.",
                      error=True)
        self.refresh_menu()

    def set_style(self, name):
        if name not in dict(PET_STYLES):
            return
        preferences.STYLE["v"] = name
        self.pet.cfg["pet_style"] = name
        runtime_settings.save_setting("pet_style", name)
        self.presentation.invalidate()  # redraw the tray icon in the new style
        if hasattr(self, "style_var"):
            self.style_var.set(name)
        self.refresh_menu()

    def toggle_theme(self):
        name = "light" if theme.T.get("name") == "dark" else "dark"
        self.pet.preferences.apply_theme(name)
        if not runtime_settings.save_setting("theme", name):
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
                os.makedirs(paths.HOME_DIR, exist_ok=True)
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

    def configure_addins(self, enabled):
        self.addins.request_configure(enabled)
        self.pet.cfg["addins"] = enabled

    def open_addin_messages(self, rows, parent=None):
        if not rows:
            return
        unread = [dict(row, messages=[m for m in row['messages'] if m['unread']])
                  for row in self.pet.addin_snapshot]
        self._show_addin_window([row for row in unread if row['messages']] or rows)

    def _show_addin_window(self, rows, screen=None):
        win = getattr(self, '_addin_alert_window', None)
        try:
            exists = win is not None and win.winfo_exists()
        except tk.TclError:
            exists = False
        if exists:
            win.append_addin_rows(rows)
            win.lift()
            win.focus_addin_report()
        else:
            win = self._addin_alert_window = addin_message_ui.show(self.root, rows,
                on_close=self.acknowledge_addin_messages, screen=screen,
                options={key: self.pet.cfg.get(key, True) for key in ('addin_alert_topmost', 'addin_alert_no_focus')},
                on_options=self.set_addin_alert_option)
        return win

    def set_addin_alert_option(self, key, value):
        if key not in ('addin_alert_topmost', 'addin_alert_no_focus'):
            raise ValueError('Unknown add-in alert option')
        value = bool(value)
        if not runtime_settings.save_setting(key, value):
            self.info('Could not save the add-in alert preference.', error=True)
            return False
        self.pet.cfg[key] = value
        win = getattr(self, '_addin_alert_window', None)
        if win is not None:
            try:
                if win.winfo_exists():
                    win.set_addin_options({key: value})
            except tk.TclError:
                pass
        self.refresh_menu()
        return True

    def acknowledge_addin_messages(self, rows):
        ids = {m['id'] for row in rows for m in row['messages']}
        self.addins.messages.acknowledge(ids)
        self.pet.addin_snapshot = self.addins.messages.snapshot()

    def publish_addin_pet_locations(self):
        session_context = {}
        for item in getattr(self.pet, '_last_items', []):
            if not item.get('session_id'):
                continue
            agent = 'codex' if item.get('agent') == 'codex' else 'claude'
            harness = 'Codex' if agent == 'codex' else 'Claude Code'
            if 'cowork' in str(item.get('env', '')).lower() or 'cowork' in str(item.get('entry', '')).lower():
                harness = 'Claude Cowork'
            session_context[(agent, str(item['session_id']))] = dict(
                conversation_title=item.get('conv') or item.get('title') or '',
                project=item.get('title') or '', harness=harness, cwd=item.get('cwd') or '',
                environment=item.get('where') or item.get('env') or '',
                source=item.get('source') or '', entry=item.get('entry') or '', model=item.get('model') or '')
        self.addins.set_session_context(session_context)
        default = geometry.widget_location(self.pet.root)
        sessions = {}
        for view in self.pet.pets.values():
            if not view.canvas.winfo_ismapped():
                continue
            location = geometry.widget_location(view.canvas)
            data = view.data
            for item in data.get('everyone') or data.get('members') or [data]:
                if item.get('session_id'):
                    identity = ('codex' if item.get('agent') == 'codex' else 'claude', str(item['session_id']))
                    sessions[identity] = location
        self.addins.set_pet_locations(default, sessions)

    def show_addin_alerts(self):
        """New alerts join one carousel without interrupting the alert being reviewed."""
        # Native file choosers can still own input. Never cover one with an unclickable alert.
        if self.root.grab_current() is not None:
            return
        rows = [dict(row, messages=[m for m in row['messages'] if m['unread']])
                for row in self.pet.addin_snapshot]
        rows = [row for row in rows if row['messages']]
        if not rows:
            return
        win = getattr(self, '_addin_alert_window', None)
        try:
            if win is not None and win.winfo_exists():
                win.append_addin_rows(rows)
                return
        except tk.TclError:
            pass
        first = next(((row, message) for row in rows for message in row['messages']
                      if message.get('window') in ('main', 'pet')), None)
        if first:
            row, message = first
            position = self.addins.pet_location(row) if message['window'] == 'pet' else None
            screen = position.get('screen') if position else None
            self._show_addin_window(rows, screen=screen or geometry.screen_info(0, 0, self.root))

    def toggle_addin(self, key):
        old = self.pet.cfg.get("addins")
        enabled = dict(old) if isinstance(old, dict) else {}
        enabled[key] = not bool(enabled.get(key))
        if not runtime_settings.save_setting("addins", enabled):
            self.info("Couldn't save add-in settings. Check that the AIPet data folder is writable.")
            return
        self.configure_addins(enabled)
        self.refresh_menu()

    def reload_addins(self):
        self.configure_addins(self.pet.cfg.get("addins", {}))
        self.root.after(500, self.refresh_menu)
        self.refresh_menu()

    def save_addin_settings(self, key, values, completed):
        self.addins.request_set_settings(key, values,
            lambda error: self.ui(lambda: completed(error)))

    def open_addins(self):
        folder = os.path.join(paths.HOME_DIR, "addins")
        os.makedirs(folder, exist_ok=True)
        open_path(folder)

    def open_config(self):
        open_path(paths.HOME_DIR)

    def quit(self):
        if getattr(self, "addins", None):
            self.addins.stop()
        if getattr(self, "agent_tracker", None):
            self.agent_tracker.stop()
        self.presentation.stop()
        self.root.destroy()


def main():
    if "--selftest-assets" in sys.argv:
        from aipet_selftest import check_assets
        output = sys.argv[sys.argv.index("--selftest-assets") + 1]
        sys.exit(0 if check_assets(output) else 1)
    if "--probe-workbench" in sys.argv:
        if sys.stdout is None:  # windowed exe: write the report to a file and open it
            runtime_settings.ensure_home()
            path = os.path.join(paths.HOME_DIR, "workbench-probe.txt")
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
        runtime_settings.ensure_home()
        mark_setup_done()
        app = TrayApp()
        from aipet_selftest import start_spaces_probe
        output = sys.argv[sys.argv.index("--selftest-spaces") + 1]
        start_spaces_probe(app, os.path.abspath(output))
        app.root.mainloop()
        return
    if "--selftest" in sys.argv:  # CI (mac-selftest workflow): run without the setup window, save diagnostics, quit
        runtime_settings.ensure_home()
        mark_setup_done()
        app = TrayApp()
        app.root.after(12000, lambda: app_diagnostics.write(app.pet))
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
    if legacy.needs_migration() and not migration_window(APP_NAME, set_autostart):  # LEGACY
        return
    TrayApp().root.mainloop()


if __name__ == "__main__":
    main()
