#!/usr/bin/env python3
"""
AIPet - a floating, always-on-top companion that shows one little creature
per active conversation:

    blue, bobbing, orbiting dots  -> working
    orange, jumping, "!" bubble   -> needs your input (beeps, re-reminds)
    green, sleeping, "z"          -> done
    red, shaking                  -> error (Workbench)

Sources:
    * Claude Code  - via aipet_hook.py (hooks write session files)
    * mcp-workbench Agents chats - polled over MCP streamable HTTP (listAgentChats)

Run:   pythonw aipet.py              (no console window)
Probe: python  aipet.py --probe-workbench   (prints what Workbench returns)

Standard library only (tkinter, urllib, winsound).
"""
import aipetlib.vscode as vscode
import aipetlib.housekeeping as housekeeping
import aipetlib.ui.sounds as sounds
import aipetlib.ui.appearance_controls as appearance_controls
import aipetlib.ui.tooltips as tooltips
import copy
import glob
import json
import random
import re
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import tkinter as tk
import aipet_usage as usage
from aipetlib.ui.permission_card import PermissionCard
from aipetlib.ui.pet_view import PetView
from aipetlib.ui import font_metrics
from aipetlib.mac_panels import MacOverlayWindow, MacPetWindow
from aipetlib.click_through import ClickThroughController
import aipetlib.windows as windows
import aipetlib.geometry as geometry
import aipetlib.permissions as permissions
from aipetlib.permissions import (  # noqa: E402,F401
    DEFAULT_BLACKLIST, PROMPT_CONFIRM_SECONDS, TIMED_DEFAULT, TIMED_MAX, TIMED_MIN, _search, _write_auto_approve,
    answer_name, answers_dir, answers_enabled, auto_approve_key_for, auto_approve_rules, auto_approve_targets,
    bad_patterns, default_auto_rules, hook_waiting, save_auto_approve, set_timed_grant, stop_timed_grant,
    timed_grants, unconfirmed_prompts, write_answer_wait,
)
import aipetlib.sessions as sessions
from aipetlib.sessions import (  # noqa: E402,F401
    _active_agents, COWORK_QUIET_SECONDS, INTERRUPT_MARKERS, _PID_CACHE, _TRANSCRIPT_TAILS, _phrase, _scan_tail,
    _transcript_message, disambiguate_titles, last_said, pid_alive, place_name, read_claude_code_sessions,
    session_badge, session_title, transcript_file, transcript_tail, turn_interrupted, visible_hook_session,
)
import aipetlib.agents.context as agent_context
from aipetlib.records import STATE_ORDER, first
from aipetlib.ui.theme import (
    INK, THEMES, DARK_TEXT, T, THEMED, _DEFAULT_FG, MUTED_FG, DIALOG_ACCENTS, UI_FONT, set_theme, _fg_role,
    themed_color, button_style, style_menu, _ttk_scale_style, theme_widget, titlebar_theme, native_menu_theme,
    use_console_font, theme_window, retheme_all, themed_dialog,
)
from aipetlib.ui.sprites import (
    SPRITE_DIR, _SPR, _PHOTOS, CYAN_LIGHT, LIGHT_COLORS, STATE_FACE, FACE_PARTS, EYE_PIXELS, QUESTION_PIXELS,
    MOUTH_PIXELS, TALK_FRAMES, _PNG_PHOTOS, _RUNS, MONO_FAMILY, BUBBLE_TAIL, _BUBBLE_INSET, _IMG_CACHE, MARKS,
    MARK_COLORS, load_sprites, _rgb, robot_image, state_image, light_cycle, _to_photo, sprite_photo, pixel_rects,
    mono, bubble_image, mark_image, _solid_shadow, _sine_mouth,
)

from aipetlib.runtime.paths import AUTO_APPROVE_PATH, CONFIG_PATH, HOME_DIR, LEGACY_SESSIONS_DIR, SESSIONS_DIR
from aipetlib.runtime.preferences import (
    ANSWER_WAIT, CARD_SCALE, CARD_SCALE_MAX, CARD_SCALE_MIN, CARD_SCALE_STEP, DONE_TIMEOUT, HEALTH, MAX_HEALTH, MAX_WAIT,
    SCALE, SCALE_UNIT, STYLE, clamp_card_scale, clamp_done, clamp_health, clamp_scale, clamp_wait,
)
from aipetlib.runtime import diagnostics
from aipetlib.runtime.settings import SAVE_PAUSED, ensure_home, load_config, save_setting
from aipetlib.text import ago, fmt_left, fmt_wait

from aipetlib.config import DEFAULT_CONFIG, ConfigStore, deep_merge

IS_MAC = sys.platform == "darwin"
from aipetlib.ui.pet_style import (  # noqa: E402,F401
    TRANSPARENT, COLORS, DARK, LABELS, BADGE_DOTS, BADGE_DOT_COWORK, BADGE_ATTENTION_BG, merge_badges, badge_dot,
    BADGE_NAMES, SOURCE_NAMES, BADGE_COLORS, PET_W, PET_H, BADGE_GUTTER, TAG_H, TOP_TRIM, CANVAS_W, CANVAS_H,
    USAGE_GUTTER, USAGE_EDGE_ROOM, MOUND, MOUND_DARK, EMERGE_SECONDS, EMERGE_DEPTH, EMERGE_STAGGER,
    DONE_FLASH_SECONDS, BUBBLE_W, px, fnt, MOLE_BODY, MOLE_EDGE, MOLE_LIGHT, MOLE_NOSE, MOLE_NOSE_EDGE, MOLE_HILL,
    MOLE_HILL_EDGE, HEAD_LAYOUTS, TERMINAL_CHARS, BLINK_RATIO, BLINK_SECONDS, BLINK_THRESHOLD, RECT_SPRITES,
    _CARD_FONTS, card_font, rescale_card_fonts, NO_CHOICE, OWN_CHOICE,
)


def fill_menu(menu, entries):
    """Render the shared app menu spec in Tk, including native macOS menus."""
    menu.delete(0, "end")
    for child in menu.winfo_children():
        child.destroy()
    menu._check_vars = []
    for entry in entries:
        if entry is None:
            menu.add_separator()
            continue
        options = dict(label=entry["label"], state="normal" if entry.get("enabled", True) else "disabled")
        if entry.get("submenu") is not None:
            sub = tk.Menu(menu, tearoff=0)
            fill_menu(sub, entry["submenu"])
            menu.add_cascade(menu=sub, **options)
        elif "checked" in entry:
            var = tk.BooleanVar(master=menu, value=entry["checked"])
            menu._check_vars.append(var)
            menu.add_checkbutton(variable=var, command=entry.get("action"), **options)
        else:
            menu.add_command(command=entry.get("action"), **options)
    style_menu(menu)


def popup_menu(menu, x, y):
    """Move a context menu up when it would otherwise scroll at the screen edge."""
    menu.update_idletasks()
    ref = (x, y)
    height = menu.winfo_reqheight()
    bounds = geometry.work_area(x, y, menu)
    if bounds and y + height + 8 > bounds[3]:
        y -= height + 8
    x, y = geometry.fit_on_screen(x, y, menu.winfo_reqwidth(), height, ref, menu, margin=8)
    menu.tk_popup(x, y)


# --------------------------------------------------------------------------- native context menu (Windows)
# Tk draws its Windows menu items itself (classic look). Building a real Win32 menu instead lets Windows draw it, so the
# pet's right-click menu looks like the tray menu and every other Windows 11 menu (rounded corners, shadow, spacing).
NATIVE_WIN_MENU = os.name == "nt"
MF_GRAYED, MF_CHECKED, MF_POPUP, MF_SEPARATOR = 0x1, 0x8, 0x10, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD, GA_ROOT, WM_NULL = 0x2, 0x100, 2, 0
_WIN_MENU_API = None


def _win_menu_api():
    """A private user32 handle, so these prototypes never clash with pystray's or _user32()'s."""
    global _WIN_MENU_API
    if _WIN_MENU_API is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.CreatePopupMenu.argtypes = []
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        u.AppendMenuW.restype = wintypes.BOOL
        u.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
        u.TrackPopupMenuEx.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       ctypes.c_void_p]
        u.TrackPopupMenuEx.restype = ctypes.c_int  # with TPM_RETURNCMD: the chosen id, 0 when dismissed
        u.DestroyMenu.argtypes = [wintypes.HMENU]
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        _WIN_MENU_API = u
    return _WIN_MENU_API


def build_win_menu(u, entries, actions):
    """Turn the shared menu spec into an HMENU. Command id N runs actions[N - 1]."""
    hmenu = u.CreatePopupMenu()
    if not hmenu:
        raise OSError("CreatePopupMenu failed")
    try:
        for entry in entries:
            if entry is None:
                u.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                continue
            label = str(entry["label"]).replace("&", "&&")  # a single & would underline the next letter
            flags = 0 if entry.get("enabled", True) else MF_GRAYED
            if entry.get("submenu") is not None:
                sub = build_win_menu(u, entry["submenu"], actions)
                if not u.AppendMenuW(hmenu, flags | MF_POPUP, sub, label):
                    u.DestroyMenu(sub)
                    raise OSError("AppendMenuW failed")
                continue
            if entry.get("checked"):
                flags |= MF_CHECKED
            actions.append(entry.get("action"))
            if not u.AppendMenuW(hmenu, flags, len(actions), label):
                raise OSError("AppendMenuW failed")
            if entry.get("default"):
                u.SetMenuDefaultItem(hmenu, len(actions), 0)
    except Exception:
        u.DestroyMenu(hmenu)  # also frees the submenus already attached
        raise
    return hmenu


def popup_native_menu(widget, entries, x, y, api=None):
    """Show the menu spec as a Win32 menu at (x, y). Returns the chosen action, or None when dismissed."""
    u = api or _win_menu_api()
    actions = []
    hmenu = build_win_menu(u, entries, actions)
    try:
        hwnd = u.GetAncestor(widget.winfo_id(), GA_ROOT) or widget.winfo_id()
        u.SetForegroundWindow(hwnd)  # without this, clicking elsewhere does not close the menu
        chosen = u.TrackPopupMenuEx(hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON, int(x), int(y), hwnd, None)
        u.PostMessageW(hwnd, WM_NULL, 0, 0)  # documented fix: lets the next menu open on the first click
    finally:
        u.DestroyMenu(hmenu)
    return actions[chosen - 1] if 0 < chosen <= len(actions) else None


AUTO_FLASH_SECONDS = 10  # how long a pet celebrates a whitelist auto-approval


# ---- timed auto approve: one session approves every permission prompt by itself for N minutes


# --------------------------------------------------------------------------- Workbench source
from aipetlib.workbench import ID_KEYS, McpHttpClient, WorkbenchPoller, tool_result_to_obj, find_chat_list, normalize_state, normalize_chat


# --------------------------------------------------------------------------- the pet
# --------------------------------------------------------------------------- robot sprites


def log_error(msg):
    """Append a message (once per run) to ~/.aipet/error.log: a windowed app has no console to print to."""
    diagnostics.log_error(msg, HOME_DIR)


class Pet(PetView):
    """The pet view, with the text metrics the tests and the drawing code reach on the class."""

    @staticmethod
    def _text_w(font, text):
        return font_metrics._text_w(font, text)

    @staticmethod
    def _char_w(font):
        return font_metrics._char_w(font)


# --------------------------------------------------------------------------- diagnostics
def diagnostics_report(app=None):
    """Plain-text report for debugging a machine we can't test (no prompts, commands or session contents)."""
    import platform
    import traceback
    lines = []

    def add(k, v):
        lines.append(f"{k}: {v}")

    def attempt(name, fn):
        try:
            add(name, fn())
        except Exception:
            add(name, "FAILED\n    " + traceback.format_exc(limit=3).strip().replace("\n", "\n    "))

    add("time", time.strftime("%Y-%m-%d %H:%M:%S"))
    attempt("version", lambda: __import__("aipet_update").current_version())
    add("platform", f"{platform.platform()} ({platform.machine()})")
    add("python", f"{sys.version.split()[0]} frozen={getattr(sys, 'frozen', False)} exe={sys.executable}")
    add("meipass", getattr(sys, "_MEIPASS", "-"))
    attempt("tk", lambda: f"TkVersion={tk.TkVersion} patchlevel={app.root.tk.call('info', 'patchlevel')} "
                          f"windowing={app.root.tk.call('tk', 'windowingsystem')}" if app else tk.TkVersion)
    attempt("pillow", lambda: __import__("PIL").__version__)
    add("style", STYLE["v"])
    add("scale", SCALE["v"])
    add("png_photos", _PNG_PHOTOS["on"])
    add("sprite_dir", f"{SPRITE_DIR} exists={os.path.isdir(SPRITE_DIR)}")
    attempt("sprite_files", lambda: sorted(os.listdir(SPRITE_DIR)))
    attempt("load_sprites", lambda: "ok " + str(load_sprites(SPRITE_DIR)["img"]["robot"].size)
            if load_sprites(SPRITE_DIR) else "False (see error.log)")

    def test_imagetk():
        from PIL import Image, ImageTk
        ph = ImageTk.PhotoImage(Image.new("RGBA", (4, 4), (255, 0, 0, 255)))
        return f"ok {ph.width()}x{ph.height()}"

    def test_png():
        import base64
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(buf, "PNG")
        ph = tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"), format="png")
        return f"ok {ph.width()}x{ph.height()} red-pixel={ph.get(1, 1)}"

    def test_robot():
        spr = load_sprites()
        if not spr:
            return "no sprites"
        im = robot_image("happy", ("amber", "green", "off"), spr)
        ph = sprite_photo(("diag",), im, im.width * 2, im.height * 2)
        return f"ok {ph.width()}x{ph.height()} centre-pixel={ph.get(im.width, im.height)}"
    attempt("imagetk_photo", test_imagetk)
    attempt("png_photo", test_png)
    attempt("robot_photo", test_robot)

    def test_vscode():  # where the open VS Code folders come from (counts only, no paths)
        rep = []
        vscode.vscode_open_folders(report=rep)
        return "; ".join(rep) or "no VS Code user folder found"
    attempt("vscode_open_folders", test_vscode)
    if IS_MAC:
        def test_screens():
            import mac_statusbar
            return f"full={mac_statusbar.screen_rects(False)} visible={mac_statusbar.screen_rects(True)}"
        attempt("screens", test_screens)
    if app:
        r = app.root
        attempt("window", lambda: f"geometry={r.winfo_geometry()} viewable={r.winfo_viewable()} "
                                  f"state={r.state()} screen={r.winfo_screenwidth()}x{r.winfo_screenheight()} "
                                  f"anchor={app.anchor}")
        for key, pet in list(app.pets.items())[:6]:
            c = pet.canvas
            attempt(f"pet {key[:14]}", lambda c=c, pet=pet: (
                f"state={pet.data.get('state')} mapped={c.winfo_ismapped()} size={c.winfo_width()}x{c.winfo_height()} "
                f"items={len(c.find_all())} images={sum(1 for i in c.find_all() if c.type(i) == 'image')} "
                f"rects={sum(1 for i in c.find_all() if c.type(i) == 'rectangle')} rect_sprites={RECT_SPRITES}"))
    try:
        with open(os.path.join(HOME_DIR, "error.log"), encoding="utf-8") as f:
            tail = f.read()[-4000:]
    except OSError:
        tail = "(none)"
    lines += ["", "---- error.log (tail)", tail]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- multi-monitor placement
from aipetlib.geometry import _MAC_SCREENS, fit_on_screen, geo, mac_screen_at, work_area  # noqa: E402,F401


# --------------------------------------------------------------------------- VS Code: which open window holds a folder
from aipetlib.vscode import (  # noqa: E402,F401
    VSCODE_FLAVOURS, _backup_uris, _uri_parts, _workspace_roots, open_uri, vscode_conversation_uri, vscode_open_folders,
    vscode_target, vscode_user_dirs,
)


ClickThrough = ClickThroughController  # the name the app and tests use


# --------------------------------------------------------------------------- window focus (Windows)
from aipetlib.windows import (  # noqa: E402,F401
    _folder, _go_to_desktop_of, _user32, claude_app_windows, focus_hwnd, focus_wsl_terminal, terminal_agent,
    terminal_windows,
)


# --------------------------------------------------------------------------- app


class Detail(PermissionCard):
    """The permission / question card, in the needs-you colour."""
    ACCENT = COLORS["needs_input"]


# --------------------------------------------------------------------------- app


class PetApp:
    def __init__(self):
        self.cfg = load_config()
        set_theme(self.cfg.get("theme", "light"))
        style = {"duckbot": "robot"}.get(self.cfg.get("pet_style"), self.cfg.get("pet_style"))  # old name
        STYLE["v"] = style if style in ("robot", "mole", "cat") else "robot"
        size = self.cfg.get("size")
        if size is None and self.cfg.get("scale") is not None:  # older config: an absolute scale -> relative to the new 100%
            try:
                size = round(float(self.cfg["scale"]) / SCALE_UNIT, 2)
                self.cfg["size"] = size
                save_setting("size", size)
            except (TypeError, ValueError):
                size = None
        SCALE["v"] = clamp_scale((size if size is not None else 1.0) * SCALE_UNIT)
        ANSWER_WAIT["v"] = clamp_wait(self.cfg.get("answer_wait_seconds", 180))
        CARD_SCALE["v"] = clamp_card_scale(self.cfg.get("card_scale", 1.0))
        legacy = self.cfg.get("hide_done_after_minutes")  # the old config key (30 min default, no slider)
        if legacy is not None:
            if legacy != 30 and self.cfg.get("done_timeout_minutes") == 3:  # a value the user chose: keep it
                self.cfg["done_timeout_minutes"] = clamp_done(legacy)
                save_setting("done_timeout_minutes", self.cfg["done_timeout_minutes"])
            save_setting("hide_done_after_minutes", None)
        DONE_TIMEOUT["v"] = clamp_done(self.cfg.get("done_timeout_minutes", 3))
        HEALTH["v"] = clamp_health(self.cfg.get("health_check_seconds", 15))
        write_answer_wait(ANSWER_WAIT["v"])
        root = self.root = MacPetWindow() if IS_MAC else tk.Tk()
        use_console_font(root)
        root.title("AIPet")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=TRANSPARENT)
        try:
            if IS_MAC:
                root.attributes("-transparent", True)
            else:
                root.attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass
        self.stack = tk.Frame(root, bg=TRANSPARENT)  # notification bubbles, stacked above the pets
        self.stack.pack(side="top", anchor="e")
        self.frame = tk.Frame(root, bg=TRANSPARENT)
        self._usage_frame_pad = px(USAGE_EDGE_ROOM)
        self.frame.pack(side="top", anchor="e", padx=(0, self._usage_frame_pad))

        self.pets, self.order, self.prev_states = {}, [], {}
        self.prev_agents, self.done_flash = {}, {}  # subagent counts; session key -> time its brief "done" check ends
        self.details = {}
        self._slots, self._slot_n = {}, 0
        self._last_items, self._last_size = [], None
        self._agent_items = []  # all tracked Claude/Codex sessions, including ones beyond the visible pet limit
        self.ack, self.reminded = {}, {}  # per session: acknowledged (clicked) / last reminder time
        self.badges_expanded = False  # compact mode: the merged badge is expanded into one badge per session
        self.first_refresh = True
        self.muted = tk.BooleanVar(value=not self.cfg["sounds"])
        self.tip = None
        self.drag = None
        self.menu_pet = None
        self.context_menu_spec = None
        self._menu_open = False
        self.on_alert = None  # optional callback(state, old_state, item), used by the tray app
        self.t0 = time.time()

        self.wb = None
        if self.cfg["workbench"]["enabled"]:
            self.wb = WorkbenchPoller(self.cfg)
            self.wb.start()

        m = self.menu = tk.Menu(root, tearoff=0)
        m.add_command(label="Open in VS Code", command=self.open_in_vscode)
        self.vscode_menu_index = m.index("end")
        m.add_command(label="Dismiss this pet", command=self.dismiss_menu_pet)
        m.add_command(label="Mark as finished", command=self.finish_menu_pet)
        self.finish_menu_index = m.index("end")
        self.dismiss_menu_index = m.index("Dismiss this pet")
        m.add_command(label="Size...", command=self.open_size_slider)
        self.size_menu_index = m.index("end")
        m.add_command(label="Tooltip text size...", command=self.open_tooltip_text_slider)
        m.add_command(label="Reset size", command=self.reset_scale)
        m.add_command(label="Answer timeout...", command=self.open_answer_slider)
        m.add_command(label="Clear finished after...", command=self.open_done_slider)
        m.add_command(label="Health check every...", command=self.open_health_slider)
        self.codex_answer_var = tk.BooleanVar(value=bool(self.cfg.get("codex_answers")))
        self._write_codex_flag()
        m.add_checkbutton(label="Answer Codex prompts from the pet", variable=self.codex_answer_var,
                          command=lambda: self.set_codex_answers(self.codex_answer_var.get()))
        self.claude_answer_var = tk.BooleanVar(value=bool(self.cfg.get("claude_answers", True)))
        self._write_claude_flag()
        m.add_checkbutton(label="Answer Claude Code prompts from the pet", variable=self.claude_answer_var,
                          command=lambda: self.set_claude_answers(self.claude_answer_var.get()))
        self.compact_var = tk.BooleanVar(value=bool(self.cfg.get("compact")))
        m.add_checkbutton(label="Compact mode (one pet)", variable=self.compact_var, command=self.toggle_compact)
        self.context_banner_var = tk.BooleanVar(value=bool(self.cfg.get("context_banner", True)))
        m.add_checkbutton(label="Context beside the pet", variable=self.context_banner_var,
                          command=lambda: self.set_context_banner(self.context_banner_var.get()))
        self.click_focus_var = tk.BooleanVar(value=bool(self.cfg.get("click_to_focus", True)))
        m.add_checkbutton(label="Click goes to the session's window", variable=self.click_focus_var,
                          command=lambda: self.set_click_to_focus(self.click_focus_var.get()))
        if IS_MAC or os.name == "nt":
            self.all_spaces_var = tk.BooleanVar(value=bool(self.cfg.get("all_spaces", True)))
            m.add_checkbutton(label="Show on all desktops", variable=self.all_spaces_var,
                              command=lambda: self.set_all_spaces(self.all_spaces_var.get()))
        self.titles_var = tk.StringVar(value=self.cfg.get("session_titles", "name"))
        titles = tk.Menu(m, tearoff=0)
        for value, text in (("name", "Session name"), ("prompt", "Last prompt")):
            titles.add_radiobutton(label=text, variable=self.titles_var, value=value,
                                   command=lambda v=value: self.set_session_titles(v))
        m.add_cascade(label="Session titles", menu=titles)
        tips = tk.Menu(m, tearoff=0)
        self.tooltip_vars = {}
        for kind, label in (("session", "Session details"), ("usage", "Usage details")):
            var = self.tooltip_vars[kind] = tk.BooleanVar(value=bool(self.cfg.get(kind + "_tooltips", True)))
            tips.add_checkbutton(label=label, variable=var,
                                 command=lambda k=kind: self.set_tooltip(k, self.tooltip_vars[k].get()))
        m.add_cascade(label="Tooltips", menu=tips)
        m.add_separator()
        m.add_checkbutton(label="Mute sounds", variable=self.muted)
        m.add_command(label="Clear finished", command=self.clear_finished)
        m.add_command(label="Save diagnostics...", command=self.save_diagnostics)
        m.add_separator()
        m.add_command(label="Workbench: " + ("starting…" if self.wb else "off"), state="disabled")
        self.wb_menu_index = m.index("end")
        m.add_separator()
        m.add_command(label="Quit", command=root.destroy)
        style_menu(m)
        native_menu_theme()

        self.anchor = self.default_anchor()  # bottom-right of the main screen
        self.size_win, self._menu_xy = None, None
        self.clickthru = ClickThrough(root) if self.cfg.get("click_through", True) else None
        self._pass = None
        self.refresh()
        self.animate()
        if IS_MAC:
            import mac_statusbar
            root.update_idletasks()
            self._mouse_bridge = mac_statusbar.PanelMouseBridge(root)
            root.bind("<Destroy>", lambda event: self._mouse_bridge.close()
                      if event.widget is root else None, add="+")
        if self.clickthru:
            root.after(500, self._pass_tick)
        root.after(60 * 1000, self._sweep_usage)
        if IS_MAC or os.name == "nt":
            root.after(700, self._apply_all_spaces)
            root.bind("<Map>", lambda e: root.after(100, self._apply_all_spaces) if e.widget is root else None, add="+")

    # ---- data
    def collect(self):
        items = read_claude_code_sessions(self.cfg)
        if self.wb:
            items += self.wb.items()
        hide = DONE_TIMEOUT["v"] * 60  # 0 = never
        now = time.time()
        items = [i for i in items
                 if not (hide and i["state"] in ("done", "idle") and i.get("changed") and now - i["changed"] > hide)]
        navigation = getattr(self, "_prompt_navigation", None)
        if navigation:
            items = navigation.project(items)
        self._agent_items = [i for i in items if i.get("source") == "CC"]
        for i in self._agent_items:  # context in use, published by the agent tracker (None until it has read the session)
            reading = agent_context.get(i["key"])
            if reading:
                i["context"] = reading
            else:
                i.pop("context", None)
        disambiguate_titles(items)
        # Stable slots: a pet keeps its place for as long as it exists, whatever its state does. New sessions join
        # on the left, so the pets already on screen don't move (the overlay is anchored bottom-right).
        for it in sorted((i for i in items if i["key"] not in self._slots), key=lambda i: i.get("changed") or 0):
            self._slot_n += 1
            self._slots[it["key"]] = self._slot_n
        self._slots = {i["key"]: self._slots[i["key"]] for i in items}  # forget sessions that are gone
        if len(items) > self.cfg["max_pets"]:  # too many to show: drop the least urgent, not the oldest slot
            rank = {"needs_input": 0, "error": 1, "working": 2, "done": 3, "idle": 4}
            items = sorted(items, key=lambda i: (rank.get(i["state"], 5), -(i.get("changed") or 0)))[: self.cfg["max_pets"]]
        items.sort(key=lambda i: -self._slots[i["key"]])
        return items

    def refresh(self):
        try:
            real = self.collect()  # one item per session
            self._mark_auto_flash(real)
            items = (self._compact_items(real) if self.cfg.get("compact") and real else real) or [{
                "key": "_none", "source": "", "title": "no sessions", "state": "idle",
                "message": "Waiting for Claude Code / Workbench activity", "detail": "", "changed": 0}]
            keys = [i["key"] for i in items]
            self._last_items = real
            self._heartbeat()
            for k in list(self.pets):
                if k not in keys:
                    self.pets.pop(k).destroy()

            now = time.time()
            # alerts and acknowledgements are per session, whatever the pets show (compact mode: one pet for all)
            real_keys = {it["key"] for it in real}
            for k in list(self.prev_states):
                if k not in real_keys:
                    self.prev_states.pop(k, None)
                    self.ack.pop(k, None)
                    self.reminded.pop(k, None)
                    self.prev_agents.pop(k, None)
                    self.done_flash.pop(k, None)
            finished = False
            for it in real:
                k, old = it["key"], self.prev_states.get(it["key"])
                agents = int(it.get("subagents") or 0)
                if (not self.first_refresh and it["state"] == "working" and old == "working"
                        and agents < self.prev_agents.get(k, 0)):
                    self.done_flash[k] = now + DONE_FLASH_SECONDS  # one of its subagents just finished
                self.prev_agents[k] = agents
                if old != it["state"] and it["state"] == "done" and old in ("working", "needs_input"):
                    finished = finished or not self.first_refresh
                if old != it["state"]:
                    self.ack[k] = False
                    self.reminded[k] = now
                    if not self.first_refresh:
                        self.alert(it["state"], old, it)
                    self.prev_states[k] = it["state"]
                elif (it["state"] == "needs_input" and not self.ack.get(k) and self.cfg["remind_seconds"] > 0
                      and now - self.reminded.get(k, 0) > self.cfg["remind_seconds"]):
                    self.alert("needs_input", old, it)
                    self.reminded[k] = now
            if finished:  # another session just finished: every working pet briefly shows the done check too
                for it in real:
                    if it["state"] == "working":
                        self.done_flash[it["key"]] = now + DONE_FLASH_SECONDS

            spawned = 0
            for it in items:
                pet = self.pets.get(it["key"])
                if pet is None:
                    pet = self.pets[it["key"]] = Pet(self, it["key"])
                    if it["key"] != "_none":  # new conversation: pop up, staggered if several arrive together
                        pet.born = now + EMERGE_STAGGER * spawned
                        spawned += 1
                pet.data = it
                pet.acked = self.ack.get(it.get("focus", it["key"]), False)

            if keys != self.order:
                for k in self.order:
                    if k in self.pets:
                        self.pets[k].canvas.pack_forget()
                for k in keys:
                    self.pets[k].canvas.pack(side="left", padx=2)
                self.order = keys
                self._sync_usage_padding()
                self.reposition()

            self.sync_bubbles(real)
            if self.wb and not self.context_menu_spec:
                self.menu.entryconfigure(self.wb_menu_index, label="Workbench: " + self.wb.status)
            self.first_refresh = False
        except Exception as e:
            print(f"[aipet] refresh error: {e}", file=sys.stderr)
        finally:
            self.root.after(self.cfg["poll_ms"], self.refresh)

    def _mark_auto_flash(self, real):
        """A session whose last permission prompt the whitelist approved celebrates for AUTO_FLASH_SECONDS, unless
        (or until) any session needs the user: then that celebration is over for good."""
        now = time.time()
        cut = self.__dict__.setdefault("_flash_cut", {})
        attention = any(i.get("state") in ("needs_input", "error") for i in real)
        for it in real:
            t = it.get("auto_t") or 0
            fresh = it.get("auto_kind") == "whitelist" and 0 <= now - t < AUTO_FLASH_SECONDS
            if fresh and attention:
                cut[(it["key"], t)] = now
            it["auto_flash"] = fresh and not attention and (it["key"], t) not in cut
        for k in [k for k, v in cut.items() if now - v > 120]:
            del cut[k]

    # ---- bubbles
    def sync_bubbles(self, items):
        """The popups follow their sessions: refreshed while they need you, closed when they no longer do."""
        needing = {i["key"] for i in items if i["state"] == "needs_input" and i["key"] != "_none"}
        by_key = {i["key"]: i for i in items}
        for k in list(self.details):
            # A card opened for a session that wasn't asking anything stays until closed or the session goes away
            if k in needing or (getattr(self.details[k], "pinned", False) and k in by_key):
                self.details[k].update(by_key[k])
            else:
                self.details[k].destroy()

    def _heartbeat(self):
        """Tell the hooks the pet is running (they only wait for an answer while this is fresh)."""
        try:
            os.makedirs(HOME_DIR, exist_ok=True)
            with open(os.path.join(HOME_DIR, "alive"), "w") as f:
                f.write(str(time.time()))
        except OSError:
            pass

    def send_answer(self, key, behavior, answers=None, permissions=None):
        """Hand the user's click to the waiting PermissionRequest hook. Returns True if it was written.
        answers: for a question, {question text: label, or [labels] for multi-select}.
        permissions: Claude Code's own suggestions to apply with the allow (its "don't ask again" choices)."""
        item = next((i for i in self._last_items if i["key"] == key), None)
        req = (item or {}).get("request") or {}
        if not self.answers_enabled(item) or not hook_waiting(item) or behavior not in ("allow", "deny", "release"):
            return False
        name = answer_name(item)
        try:
            os.makedirs(answers_dir(), exist_ok=True)
            path = os.path.join(answers_dir(), name + ".json")
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(dict({"behavior": behavior, "t": time.time()}, **({"answers": answers} if answers else {}),
                               **({"permissions": permissions} if permissions else {})), f)
            os.replace(path + ".tmp", path)
            return True
        except OSError:
            return False

    def go_to_prompt(self, key):
        """Go to window for a session that is asking: Codex shows its own prompt only once AIPet's hook lets go of
        it, so let go first (its own prompt has choices the pet doesn't, like always allowing a tool). Claude Code
        shows its prompt alongside anyway, so the pet's buttons stay."""
        item = next((i for i in self._last_items if i["key"] == key), None)
        if item and item.get("agent") == "codex" and item.get("state") == "needs_input":
            self.send_answer(key, "release")
        self.focus_key(key)
        return self.step_prompt(key)

    def step_prompt(self, key, step=1):
        """Select another waiting prompt without answering it or opening its session."""
        item = next((i for i in self._last_items if i["key"] == key), None)
        if item:
            from aipetlib.prompt_navigation import PromptNavigation
            if not hasattr(self, "_prompt_navigation"):
                self._prompt_navigation = PromptNavigation()
            if self._prompt_navigation.advance(item, step):
                # Update presentation immediately without starting a second poll loop.
                self._last_items = self._prompt_navigation.project(self._last_items)
                by_key = {i["key"]: i for i in self._last_items}
                if hasattr(self, "_agent_items"):
                    self._agent_items = [by_key.get(i["key"], i) for i in self._agent_items]
                self.sync_bubbles(self._last_items)
                return True
        return False

    def timed_minutes(self):
        try:
            return max(TIMED_MIN, min(TIMED_MAX, int(self.cfg.get("timed_approve_minutes", TIMED_DEFAULT))))
        except (TypeError, ValueError):
            return TIMED_DEFAULT

    def set_timed_minutes(self, minutes):
        self.cfg["timed_approve_minutes"] = max(TIMED_MIN, min(TIMED_MAX, int(round(float(minutes)))))
        save_setting("timed_approve_minutes", self.cfg["timed_approve_minutes"])

    def start_timed_approve(self, item, minutes=None):
        """Auto approve every permission prompt from this session for N minutes, after a safety confirmation.
        Returns True once the grant is running."""
        if not item or not item.get("session_id"):
            return False
        minutes = minutes or self.timed_minutes()
        name = item.get("title") or "this session"
        if item.get("conv"):
            name += f" \u00b7 {item['conv']}"
        ok = themed_dialog(
            self.root, "AIPet", kind="warning", heading=f"Auto approve {name} for {minutes} min?",
            text=(f"Every permission prompt from this session (commands, file edits, tool calls) will be approved "
                  f"without asking for the next {minutes} minutes. Other sessions keep asking, and questions from the "
                  "AI still wait for you.\n\nLive Management in the right-click menu shows the countdown and can "
                  "stop it early. It also stops when AIPet quits."),
            buttons=(("Cancel", False, "secondary"), (f"Auto approve {minutes} min", True, "danger")),
            cancel=False, enter_confirms=False)
        if not ok:
            return False
        return set_timed_grant(item["session_id"], minutes, name)

    def stop_timed_approve(self, session_id):
        stop_timed_grant(session_id)

    def clear_prompt(self, key, req_id):
        """Drop one permission prompt from a session that still shows it although nobody is asking anymore (its
        subagent died on an API error, say). A hook still waiting for it notices and lets go."""
        item = next((i for i in self._last_items if i["key"] == key), None)
        path = (item or {}).get("path")
        if not path or not req_id:
            return False
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            reqs = [r for r in (rec.get("requests") or ([rec["request"]] if rec.get("request") else []))
                    if isinstance(r, dict) and r.get("id") != req_id]
            now = time.time()
            rec.update(requests=reqs, request=reqs[0] if reqs else {}, updated=now)
            if not reqs and rec.get("state") == "needs_input":
                rec.update(state="working", message="", wait_agent="", changed=now)
            elif reqs:
                rec["message"] = f"Claude needs your permission to use {reqs[0].get('tool') or 'a tool'}"
            tmp = path + ".pet.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            os.replace(tmp, path)
            return True
        except (OSError, ValueError) as e:
            log_error(f"clear prompt: {e!r}")
            return False

    def open_detail(self, key):
        d = self.details.get(key)
        if d:
            d.win.lift()
            return
        item = next((i for i in self._last_items if i["key"] == key), None)
        if item:
            self.details[key] = Detail(self, key, item)

    def apply_theme(self, name):
        """Switch theme live: name tags and tooltips pick it up on their own; bubbles are rebuilt."""
        set_theme(name)
        self.cfg["theme"] = T["name"]
        reopen = list(self.details)
        for d in list(self.details.values()):  # their colours are fixed at creation: rebuild them in the new theme
            d.destroy()
        for key in reopen:
            self.open_detail(key)
        retheme_all()
        style_menu(self.menu)
        native_menu_theme()

    # ---- resizing (the Size... slider in the right-click menu)
    def set_size(self, size):
        return appearance_controls.set_size(self, size)

    def tooltip_text_scale(self):
        return tooltips.tooltip_text_scale(self)

    def set_tooltip_text_scale(self, scale):
        return tooltips.set_tooltip_text_scale(self, scale)

    def set_answer_wait(self, seconds):
        ANSWER_WAIT["v"] = clamp_wait(round(float(seconds) / 15) * 15)
        self.cfg["answer_wait_seconds"] = ANSWER_WAIT["v"]
        save_setting("answer_wait_seconds", ANSWER_WAIT["v"])
        write_answer_wait(ANSWER_WAIT["v"])

    def set_done_timeout(self, minutes):
        DONE_TIMEOUT["v"] = clamp_done(minutes)
        self.cfg["done_timeout_minutes"] = DONE_TIMEOUT["v"]
        save_setting("done_timeout_minutes", DONE_TIMEOUT["v"])

    def set_health_interval(self, seconds):
        HEALTH["v"] = clamp_health(round(float(seconds) / 5) * 5)
        self.cfg["health_check_seconds"] = HEALTH["v"]
        save_setting("health_check_seconds", HEALTH["v"])

    def set_scale(self, v):
        return appearance_controls.set_scale(self, v)

    def _place_above_pet(self, w):
        return appearance_controls._place_above_pet(self, w)

    def open_size_slider(self):
        return appearance_controls.open_size_slider(self)

    def open_tooltip_text_slider(self):
        return appearance_controls.open_tooltip_text_slider(self)

    def open_answer_slider(self):
        return appearance_controls.open_answer_slider(self)

    def open_done_slider(self):
        return appearance_controls.open_done_slider(self)

    def reset_scale(self):
        return appearance_controls.reset_scale(self)

    def focus_key(self, key):
        pet = self.pets.get(key)
        if pet:
            self.focus_session(pet)

    def focus_session(self, pet):
        """Bring the window that hosts this session to the front (best effort)."""
        d = pet.data
        if d.get("source") != "CC":
            return
        if d.get("ide") == "vscode":
            self.open_in_vscode(pet)  # `code <folder>` raises the right VS Code window, then the conversation tab
        elif os.name == "nt":
            if windows.focus_hwnd(d.get("hwnd")):
                return
            if d.get("env") == "wsl" or d.get("agent") == "codex" or d.get("app") == "cowork" or \
                    "desktop" in (d.get("entry") or "").lower():
                own = d.get("focus") or pet.key
                others = [i for i in self._last_items if i.get("key") != own]
                windows.focus_wsl_terminal(d, others)  # no recorded window: match by distro / project, never another's

    def _menu_session(self, pet):
        """The session a right-click is about: the pet's own, or in compact mode the one in front."""
        if not pet or pet.key == "_none":
            return None
        key = pet.data.get("focus") or pet.key
        return next((i for i in self._last_items if i["key"] == key), None)

    def finish_menu_pet(self):
        """Right-click > Mark as finished: for a session that is stuck (its app closed without telling the pet)."""
        self.finish_session(self._menu_session(self.menu_pet))

    def finish_session(self, d):
        """Mark one session finished by hand (Mark as finished, Fix fast cancel): its pet stops showing it busy."""
        if not d or d.get("source") != "CC" or not d.get("path"):
            return
        try:
            with open(d["path"], encoding="utf-8") as f:
                rec = json.load(f)
            now = time.time()
            rec.update(state="done", message="", request={}, requests=[], wait_agent="", agents={},
                       main_stopped=False, updated=now, changed=now)
            tmp = d["path"] + ".pet.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            os.replace(tmp, d["path"])
        except (OSError, ValueError) as e:
            log_error(f"mark as finished: {e!r}")

    def open_health_slider(self):
        return appearance_controls.open_health_slider(self)

    def dismiss_menu_pet(self):
        if self.menu_pet and self.menu_pet.key != "_none":
            self.dismiss(self.menu_pet)

    def alert(self, state, old, item=None):
        if self.on_alert:
            try:
                self.on_alert(state, old, item)
            except Exception:
                pass
        if state in ("needs_input", "error"):
            self.beep(True, kind="error" if state == "error" else None)
            self.root.lift()
            self.root.attributes("-topmost", True)
        elif state == "done" and old in ("working", "needs_input") and self.cfg["sound_on_done"]:
            self.beep(False)

    def beep(self, urgent, force=False, kind=None):
        """kind: "error" for its own sound; otherwise urgent = needs you, not urgent = done."""
        if self.muted.get() and not force:
            return
        sounds.play(urgent, error=kind == "error", style=self.cfg.get("sound_style", "chimes"), bell=lambda: self.root.bell())

    def set_sound_style(self, style):
        if style not in ("chimes", "system"):
            return
        self.cfg["sound_style"] = style
        save_setting("sound_style", style)
        self.beep(False, force=True)  # let the user hear the choice

    def dismiss(self, pet):
        self.dismiss_item(pet.data)

    def _sweep_usage(self):
        """A minute after start, then hourly: clear usage files of sessions that are long gone, and leftovers
        (half-written files, answers and waits of hooks that died, old debug logs)."""
        try:
            usage.sweep(HOME_DIR)
        except Exception as e:
            log_error(f"usage sweep: {e!r}")
        try:
            housekeeping.sweep_leftovers()
        except Exception as e:
            log_error(f"leftover sweep: {e!r}")
        self.root.after(3600 * 1000, self._sweep_usage)

    def dismiss_item(self, d):
        if d.get("source") == "CC" and d.get("path"):
            try:
                os.remove(d["path"])
                usage.remove_for(d["path"])
            except OSError:
                pass
        elif d.get("source") == "WB" and self.wb:
            self.wb.dismiss(d["key"])

    def write_diagnostics(self):
        """Write ~/.aipet/diagnostics.txt (versions, sprite loading, image tests, window state, recent errors).
        Returns the path, or None if it failed."""
        path = os.path.join(HOME_DIR, "diagnostics.txt")
        try:
            text = diagnostics_report(self)
            os.makedirs(HOME_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return path
        except Exception as e:
            log_error(f"diagnostics failed: {e!r}")
            return None

    def save_diagnostics(self):
        """Write the diagnostics and open them, so someone on a machine we can't test can send them."""
        path = self.write_diagnostics()
        if not path:
            return
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if IS_MAC else "xdg-open", path])
        except (AttributeError, OSError):
            pass

    def clear_finished(self):
        """Dismiss every finished session - all of them, also when compact mode shows them in one pet."""
        for it in list(self._last_items):
            if it.get("state") in ("done", "idle"):
                self.dismiss_item(it)

    # ---- window
    def _sync_usage_padding(self):
        """Share one right margin across the overlay instead of padding every pet."""
        last = self.pets.get(self.order[-1]) if self.order else None
        extra = getattr(last, "usage_extra", 0) if last else 0
        padding = max(0, px(USAGE_EDGE_ROOM - extra))
        delta = padding - self._usage_frame_pad
        if delta:
            self._usage_frame_pad = padding
            self.frame.pack_configure(padx=(0, padding))
        return delta

    def reposition(self, ref=None):
        """Keep the bottom-right corner (the anchor) where it is while the pets change size, but inside the monitor the
        pet is on. That monitor is found from the middle of the window as it is now, not from the anchor: an anchor
        just across a monitor edge used to pull a growing pet onto the neighbouring screen. The anchor follows any
        clamping, so the next resize starts from where the pet really is."""
        r = self.root
        r.update_idletasks()
        w, h = r.winfo_reqwidth(), r.winfo_reqheight()
        ax, ay = self.anchor
        if ref is None:
            try:
                cw, ch = r.winfo_width(), r.winfo_height()
                ref = (r.winfo_x() + cw // 2, r.winfo_y() + ch // 2) if cw > 1 and ch > 1 else (ax - 1, ay - 1)
            except tk.TclError:
                ref = (ax - 1, ay - 1)
        x, y = geometry.fit_on_screen(ax - w, ay - h, w, h, ref, r)
        r.geometry(geometry.geo(x, y))
        self.anchor = [x + w, y + h]

    def default_anchor(self):
        """Where the pet's bottom-right corner starts: the main screen's bottom-right corner, just above the Windows
        taskbar; on macOS just above the Dock (the visible area of the main screen)."""
        r = self.root
        if IS_MAC:
            area = geometry.mac_screen_at(1, 1)  # the main screen holds Tk's origin
            if area:
                return [area[2] - 24, area[3] - 8]
        return [r.winfo_screenwidth() - 24, r.winfo_screenheight() - 60]

    def shift_for_width(self, delta):
        """The overlay is about to become delta px wider (narrower if negative): move it now, before Tk redraws, so the
        new size and position land together and the pet stays put (reposition() would redraw at the old position
        first, flashing the pet sideways). Falls back to reposition() before the window is shown."""
        r = self.root
        try:
            w, h = r.winfo_width(), r.winfo_height()
            if w <= 1 or h <= 1:
                r.after_idle(self.reposition)
                return
            w += delta
            ref = (r.winfo_x() + r.winfo_width() // 2, r.winfo_y() + h // 2)
            x, y = geometry.fit_on_screen(self.anchor[0] - w, self.anchor[1] - h, w, h, ref, r)
            r.geometry(geometry.geo(x, y))
            self.anchor = [x + w, y + h]
        except tk.TclError:
            r.after_idle(self.reposition)

    def reset_position(self):
        """Last resort for a pet lost off screen: back to the main screen's bottom-right corner."""
        self.anchor = self.default_anchor()
        self.reposition(ref=(self.anchor[0] - 1, self.anchor[1] - 1))
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

    # ---- click-through: the window is one rectangle around all pets, but only the robots and the "needs you" bubbles
    # should catch clicks. The pointer is polled, and the window passes clicks through whenever it isn't over those.
    def _over_hit(self, x, y, margin=3):
        for pet in list(self.pets.values()):
            c = pet.canvas
            try:
                if not c.winfo_ismapped():
                    continue
                ox, oy = c.winfo_rootx(), c.winfo_rooty()
                boxes = [c.bbox(t) for t in ("hit", "ans", "badge", "usage") if c.find_withtag(t)] or [c.bbox("all")]  # mole/cat: all
            except tk.TclError:
                continue
            for bb in boxes:
                if bb and ox + bb[0] - margin <= x <= ox + bb[2] + margin and oy + bb[1] - margin <= y <= oy + bb[3] + margin:
                    return True
        return False

    def _pass_tick(self):
        try:
            x, y = self.root.winfo_pointerxy()
            want = not (self._menu_open or self.drag is not None or self._over_hit(x, y))
            if want != self._pass:
                self._pass = want
                self.clickthru.set(want)
                if want:
                    self.hide_tip()
        except Exception as e:
            log_error(f"click-through: {e!r}")
        self.root.after(50, self._pass_tick)

    def animate(self):
        t = time.time() - self.t0
        try:
            for p in self.pets.values():
                try:
                    p.draw(t)
                except Exception:  # one bad frame must not stop every pet's animation
                    import traceback
                    log_error("draw failed:\n" + traceback.format_exc(limit=4))
        finally:
            self.root.after(50, self.animate)

    def on_press(self, e):
        self.drag = [e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y(), False]

    def on_drag(self, e):
        if not self.drag:
            return
        sx, sy, wx, wy, _ = self.drag
        dx, dy = e.x_root - sx, e.y_root - sy
        if abs(dx) + abs(dy) > 4:
            self.drag[4] = True
        if self.drag[4]:
            self.hide_tip()
            self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def on_release(self, e, pet):
        if IS_MAC and (e.state & 0x4):  # Control-click is a context menu, never a session click.
            self.drag = None
            pet._on_bubble, pet._on_badge = False, None
            return "break"
        on_bubble, pet._on_bubble = pet._on_bubble, False
        on_badge, pet._on_badge = pet._on_badge, None
        if self.drag and self.drag[4]:
            self.root.update_idletasks()
            self.anchor = [self.root.winfo_x() + self.root.winfo_width(),
                           self.root.winfo_y() + self.root.winfo_height()]
        elif on_badge in ("__expand", "__collapse"):  # compact mode: show every session's badge, or the merged one
            self.badges_expanded = on_badge == "__expand"
        elif on_badge and on_badge != "_none":  # a badge: that session's prompt window, or its window if it isn't waiting
            item = next((i for i in self._last_items if i["key"] == on_badge), None)
            if item:
                self.ack[on_badge] = True
                if item.get("state") == "needs_input":
                    self.open_detail(on_badge)
                elif self.cfg.get("click_to_focus", True):
                    import types
                    self.focus_session(types.SimpleNamespace(key=on_badge, data=item))
        elif on_bubble and pet.key != "_none":
            self._acknowledge(pet)
            self.open_detail(pet.data.get("focus", pet.key))  # the bubble: the full question and the answer buttons
        else:
            self._acknowledge(pet)  # click: acknowledge (stops jumping / reminders) and go to the session's window
            if pet.key != "_none" and self.cfg.get("click_to_focus", True):
                self.focus_session(pet)
        self.drag = None

    def _acknowledge(self, pet):
        pet.acked = True
        self.ack[pet.data.get("focus", pet.key)] = True

    # ---- compact mode: one pet for every session
    COMPACT_HEADS = 4

    def _compact_items(self, real):
        """One pet item standing for all sessions. In front: the first session that asked for you (a queue - answer it
        and the next one steps forward), else the most urgent / most recently changed one. Its name, badges, bubble and
        click target are the pet's; the other sessions are the smaller robots around it."""
        rank = {"needs_input": 0, "error": 1, "working": 2, "done": 3, "idle": 4}
        queue = sorted((i for i in real if i["state"] == "needs_input"), key=lambda i: i.get("changed") or 0)
        by_urgency = sorted(real, key=lambda i: (rank.get(i["state"], 5), -(i.get("changed") or 0)))
        focus = queue[0] if queue else by_urgency[0]
        members = [focus] + [i for i in by_urgency if i is not focus]
        shown = members[:self.COMPACT_HEADS]
        g = dict(focus)
        # one badge per session shown (faint red while it needs you), plus a count of the ones without a robot
        badges = [(m.get("badges") or ["?"])[0] for m in shown]
        attention = [m["state"] == "needs_input" for m in shown]
        if len(members) > len(shown):
            badges.append(f"+{len(members) - len(shown)}")
            attention.append(any(m["state"] == "needs_input" for m in members[len(shown):]))
        keys = [m["key"] for m in shown] + ([None] if len(members) > len(shown) else [])
        g.update(key="_group", focus=focus["key"], members=shown, everyone=members, badges=badges,
                 badge_attention=attention, badge_keys=keys, subagents=0,
                 badge_titles=[m.get("conv") or "" for m in shown],
                 auto_flash=any(m.get("auto_flash") for m in members))
        return [g]

    def answers_enabled(self, item):
        agent = "codex" if (item or {}).get("agent") == "codex" else "claude"
        return bool(self.cfg.get(agent + "_answers", True))

    def set_claude_answers(self, on):
        """Choose whether Claude hooks wait for an answer from the pet."""
        self.cfg["claude_answers"] = bool(on)
        save_setting("claude_answers", self.cfg["claude_answers"])
        self.claude_answer_var.set(self.cfg["claude_answers"])
        self._write_claude_flag()
        self.sync_bubbles(self._last_items)

    def _write_claude_flag(self):
        # An opt-out preserves Claude answering for existing installations.
        flag = os.path.join(HOME_DIR, "no-claude-answers")
        try:
            if not self.cfg.get("claude_answers", True):
                os.makedirs(HOME_DIR, exist_ok=True)
                open(flag, "w").close()
            elif os.path.exists(flag):
                os.remove(flag)
        except OSError:
            pass

    def set_codex_answers(self, on):
        """Codex asks its hooks before showing its own prompt: with this on, the pet waits for your click first (up to
        the answer timeout), so Codex's prompt only appears if you don't answer on the pet."""
        self.cfg["codex_answers"] = bool(on)
        save_setting("codex_answers", self.cfg["codex_answers"])
        if self.codex_answer_var.get() != self.cfg["codex_answers"]:
            self.codex_answer_var.set(self.cfg["codex_answers"])
        self._write_codex_flag()
        self.sync_bubbles(self._last_items)

    def _write_codex_flag(self):
        flag = os.path.join(HOME_DIR, "codex-answers")  # read by the hooks, including WSL ones (shared folder)
        try:
            if self.cfg.get("codex_answers"):
                os.makedirs(HOME_DIR, exist_ok=True)
                open(flag, "w").close()
            elif os.path.exists(flag):
                os.remove(flag)
        except OSError:
            pass

    def set_session_titles(self, mode):
        """Session name (default) or latest prompt as the extra title (same-folder sessions, expanded badges)."""
        mode = "prompt" if mode == "prompt" else "name"
        self.cfg["session_titles"] = mode
        save_setting("session_titles", mode)
        if hasattr(self, "titles_var") and self.titles_var.get() != mode:
            self.titles_var.set(mode)

    def set_click_to_focus(self, on):
        """Clicking a pet / badge goes to the session's window (on by default). Off: a click only acknowledges it."""
        self.cfg["click_to_focus"] = bool(on)
        save_setting("click_to_focus", self.cfg["click_to_focus"])
        if hasattr(self, "click_focus_var") and self.click_focus_var.get() != self.cfg["click_to_focus"]:
            self.click_focus_var.set(self.cfg["click_to_focus"])

    def set_context_banner(self, on):
        """Each AI's context in use beside the pet (on by default); the pets redraw with or without it."""
        self.cfg["context_banner"] = bool(on)
        save_setting("context_banner", self.cfg["context_banner"])
        if hasattr(self, "context_banner_var") and self.context_banner_var.get() != self.cfg["context_banner"]:
            self.context_banner_var.set(self.cfg["context_banner"])

    def set_all_spaces(self, on):
        """Show the pet on every virtual desktop, or only its own."""
        self.cfg["all_spaces"] = bool(on)
        save_setting("all_spaces", self.cfg["all_spaces"])
        if hasattr(self, "all_spaces_var") and self.all_spaces_var.get() != self.cfg["all_spaces"]:
            self.all_spaces_var.set(self.cfg["all_spaces"])
        self._apply_all_spaces()

    def _apply_all_spaces(self):
        try:
            on = bool(self.cfg.get("all_spaces", True))
            self.root.update_idletasks()
            if IS_MAC:
                # Keep Tk's attributes in sync so remapping the overlay does not
                # replace the native Space behavior.
                try:
                    kind, attrs = self.root.tk.call("::tk::unsupported::MacWindowStyle", "style", self.root._w)
                    attrs = [a for a in self.root.tk.splitlist(attrs)
                             if a not in ("canJoinAllSpaces", "moveToActiveSpace", "hideOnFullScreen", "hideOnSuspend")]
                    if on:
                        attrs.append("canJoinAllSpaces")
                    if "doesNotHide" not in attrs:
                        attrs.append("doesNotHide")
                    self.root.tk.call("::tk::unsupported::MacWindowStyle", "style", self.root._w, kind, tuple(attrs))
                except tk.TclError as e:
                    log_error(f"all spaces: Tk window style: {e!r}")
                import mac_statusbar
                if not mac_statusbar.set_all_spaces(self.root.title(), on):
                    log_error("all spaces: pet window not found")
            elif os.name == "nt":
                from pyvda import AppView
                view = AppView(hwnd=int(self.root.wm_frame(), 16))
                view.pin() if on else view.unpin()
        except Exception as e:
            log_error(f"all spaces: {e!r}")

    def toggle_compact(self, value=None):
        self.cfg["compact"] = (not self.cfg.get("compact", False)) if value is None else bool(value)
        save_setting("compact", self.cfg["compact"])
        if hasattr(self, "compact_var") and self.compact_var.get() != self.cfg["compact"]:
            self.compact_var.set(self.cfg["compact"])

    def on_menu(self, e, pet=None):
        if self._menu_open:
            return "break"
        self.hide_tip()
        self.drag = None
        if pet:
            pet._on_bubble, pet._on_badge = False, None
        self.menu_pet = pet
        self._menu_xy = (e.x_root, e.y_root)
        if self.context_menu_spec:
            # Keep mouse handling enabled for the entire menu tracking session.
            self._menu_open = True
            chosen = None
            try:
                if self.clickthru:
                    self.clickthru.set(False)
                    self._pass = False
                entries = self.context_menu_spec(pet)
                native = False
                if NATIVE_WIN_MENU:
                    try:
                        widget = e.widget if isinstance(getattr(e, "widget", None), tk.Misc) else self.root
                        self._context_menu_shown = True
                        chosen = popup_native_menu(widget, entries, e.x_root, e.y_root)
                        native = True
                    except Exception:
                        globals()["NATIVE_WIN_MENU"] = False  # Win32 menu unavailable: use the Tk menu from now on
                if not native:
                    fill_menu(self.menu, entries)
                    self._context_menu_shown = True
                    try:
                        # Tk posts an AppKit menu on macOS and safely manages its
                        # nested event loop; no focus/Space switch is needed.
                        popup_menu(self.menu, e.x_root, e.y_root)
                    finally:
                        self.menu.grab_release()
            finally:
                self._menu_open = False
            if chosen:
                chosen()  # run after the menu has closed, like a Tk menu command
            return "break"
        self.menu.entryconfigure(self.size_menu_index, label=f"Size...  ({int(round(SCALE['v'] / SCALE_UNIT * 100))}%)")
        ok = bool(pet and pet.data.get("source") == "CC" and pet.data.get("cwd"))
        self.menu.entryconfigure(self.vscode_menu_index, state="normal" if ok else "disabled")
        self.menu.entryconfigure(self.dismiss_menu_index,
                                 state="normal" if pet and pet.key != "_none" else "disabled")
        target = self._menu_session(pet)
        self.menu.entryconfigure(self.finish_menu_index, state="normal" if target and target.get("source") == "CC" and target.get("state") in (
            "working", "needs_input", "error") else "disabled")
        x, y = e.x_root, e.y_root
        if IS_MAC:
            # A menu only opens in the active app. The pet window never activates AIPet by itself, so a right-click while
            # another app is in front used to need a left click first. Activate AIPet, then open the menu. Some Tk
            # versions report a right-click as both Button-2 and Button-3: open it once.
            now = time.time()
            if now - getattr(self, "_menu_t", 0) < 0.4:
                return
            self._menu_t = now
            try:
                self.root.focus_force()  # Tk on macOS: also activates the application
            except tk.TclError:
                pass
            self.root.after(60, lambda: popup_menu(self.menu, x, y))
            return
        style_menu(self.menu)  # submenus added since (hooks, auto approve) pick up the theme too
        popup_menu(self.menu, x, y)

    def open_in_vscode(self, pet=None):
        """Open/focus the session's folder in VS Code (Remote-WSL for WSL sessions)."""
        pet = pet or self.menu_pet
        d = pet.data if pet else {}
        code = shutil.which("code") or next(
            (p for p in ("/usr/local/bin/code", "/opt/homebrew/bin/code",
                         "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")
             if os.path.exists(p)), None)
        if not code or not d.get("cwd"):
            self.root.bell()
            return
        try:  # the open window that holds this folder (it may have a parent folder open), not a new one
            extra, _ = vscode.vscode_target(d["cwd"], d.get("env", ""), d.get("distro", ""))
        except Exception as e:
            log_error(f"vscode_target failed: {e!r}")
            extra = (["--remote", f"wsl+{d.get('distro') or 'Ubuntu'}"] if d.get("env") == "wsl" else []) + [d["cwd"]]
        args = [code] + extra
        try:
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            self.root.bell()
            return
        uri = vscode.vscode_conversation_uri(d, code)
        if uri and self.cfg.get("vscode_open_conversation", True):
            # VS Code hands a vscode:// link to its active window, so give `code` a moment to raise the right one
            self.root.after(1200, lambda: vscode.open_uri(uri))

    def show_tip(self, pet):
        return tooltips.show_tip(self, pet)

    def show_usage_tip(self, pet, detail):
        return tooltips.show_usage_tip(self, pet, detail)

    def set_card_scale(self, value):
        """Size of the permission / question cards (all open ones follow at once); saved as card_scale."""
        CARD_SCALE["v"] = clamp_card_scale(value)
        self.cfg["card_scale"] = CARD_SCALE["v"]
        save_setting("card_scale", CARD_SCALE["v"])
        rescale_card_fonts()
        for d in list(self.details.values()):
            d.apply_scale()

    def whitelist_request(self, item):
        """Add the prompt on this session's card to its hook config's auto-approve whitelist, as an exact match:
        the whole command (or path, URL...), or the tool itself when it has no such argument (MCP tools). Returns a
        line for the card, or None if nothing was saved."""
        req = (item or {}).get("request") or {}
        tool = req.get("tool") or ""
        if not tool:
            return None
        subject = req.get("subject") if req.get("primary") else ""
        pattern = re.escape(subject) if subject else re.escape(tool)
        key = auto_approve_key_for(item)
        rules = auto_approve_rules().get(key) or default_auto_rules()
        white = [x for x in rules.get("whitelist") or [] if str(x).strip()]
        if pattern not in white:
            white.append(pattern)
        black = rules.get("blacklist")
        if black == DEFAULT_BLACKLIST and len(white) == 1:
            # the default "ask for everything" would keep the whitelist from ever applying; with a fresh list of exact
            # matches only, nothing broader gets through
            black = []
        rules = dict(rules, enabled=True, whitelist=white, blacklist=black)
        if not save_auto_approve(key, rules):
            return None
        blocked = any(_search(p, subject or tool) for p in (black or []))
        what = "this exact command" if subject else f"every {tool} request"
        return (f"Allowed, and {what} is now on the auto-approve whitelist ({key})."
                + (" Your blacklist still asks for it, though." if blocked else ""))

    def set_tooltip(self, kind, on):
        return tooltips.set_tooltip(self, kind, on)

    def _show_tooltip(self, pet, text, usage_detail=None):
        return tooltips._show_tooltip(self, pet, text, usage_detail)

    def _build_tooltip(self, pet, text, usage_detail):
        return tooltips._build_tooltip(self, pet, text, usage_detail)

    def hide_tip(self):
        return tooltips.hide_tip(self)


# --------------------------------------------------------------------------- entry points
def probe_workbench():
    w = load_config()["workbench"]
    print(f"URL:  {w['mcp_url']}\nTool: {w['tool_name']}  args={w.get('tool_arguments')}")
    print(f"Key env var {w['api_key_env']!r}: {'set' if os.environ.get(w['api_key_env'] or '') else 'NOT set'}\n")
    obj = tool_result_to_obj(McpHttpClient(w).call_tool(w["tool_name"], w.get("tool_arguments") or {}))
    print("Raw response (first 4000 chars):")
    print(json.dumps(obj, indent=2, ensure_ascii=False)[:4000])
    chats = find_chat_list(obj)
    if chats is None:
        print("\nNo chat list found. Check the raw response and adjust tool_name/tool_arguments.")
        return
    print(f"\n{len(chats)} chats. How the pet reads them:")
    for raw in chats[:25]:
        n = normalize_chat(raw, w)
        print(f"  {n['state']:12} raw={str(first(raw, w['state_fields'])):22} {n['title']}")
    print("\nIf states look wrong, edit state_fields / state_map in", CONFIG_PATH)


def main():
    ensure_home()
    if "--probe-workbench" in sys.argv:
        probe_workbench()
        return
    PetApp().root.mainloop()


if __name__ == "__main__":
    main()
