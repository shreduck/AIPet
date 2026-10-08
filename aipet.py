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
import copy
import glob
import json
import math
import random
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

# Every AIPet window uses a console font, like the pet's own name tags and tooltips
UI_FONT = "Consolas" if os.name == "nt" else ("Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono")

try:
    import winsound
except ImportError:  # not Windows
    winsound = None

HOME_DIR = os.path.join(os.path.expanduser("~"), ".aipet")
SESSIONS_DIR = os.path.join(HOME_DIR, "sessions")
# Claude Pet (the old name) kept its files in ~/.claude-pet. Hooks not yet updated to AIPet still write their sessions
# there, so the pet keeps reading that folder while it exists.
LEGACY_SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".claude-pet", "sessions")  # LEGACY
CONFIG_PATH = os.path.join(HOME_DIR, "config.json")

DEFAULT_CONFIG = {
    "poll_ms": 700,
    "max_pets": 10,
    "sounds": True,
    "sound_on_done": True,
    "sound_style": "chimes",  # Windows: "chimes" (AIPet's own, close to the macOS sounds) or "system" (Windows beeps)
    "notifications": False,  # Windows toast notifications; off by default (toggle in the tray menu)
    "theme": "light",  # "light" (default) or "dark"; toggle in the tray / right-click menu
    "answer_wait_seconds": 180,  # how long a permission prompt can be answered from the pet: 0 (no limit) - 1800
    "pet_style": "robot",  # "robot" (default), "mole" or "cat"
    # "size": 1.0 is written when you use the Size slider (1.0 = 100%, range 0.3 - 3.0); deliberately not a default
    # here, so an old absolute "scale" value in config.json can still be migrated once.
    "remind_seconds": 90,
    "compact": False,
    "update_check": True,  # look for a new AIPet release on GitHub once a day (tells you; never installs anything)
    "click_to_focus": True,  # clicking a pet (or a badge) brings its session's window to the front
    "session_tooltips": True,
    "usage_tooltips": True,
    "claude_oauth_usage": False,  # experimental credential-based quota requests require explicit consent
    "all_spaces": True,  # show the pet on every virtual desktop / macOS Space
    "session_titles": "name",  # extra title for same-folder sessions: "name" (the harness's name) or "prompt" (latest prompt)
    "codex_answers": True,  # answer Codex permission prompts in the pet's prompt window
    "claude_answers": True,  # preserve existing Claude Code answering behavior
    "click_through": True,  # only the robot and its "needs you" bubble take clicks; the rest of the window lets them through
    # clicking a session of the VS Code extension also opens its conversation tab (vscode://anthropic.claude-code/open)
    "vscode_open_conversation": True,
    "done_timeout_minutes": 3,
    "health_check_seconds": 15,  # how often a working session's process is checked (0 = off); "Health check every..."  # finished sessions disappear after this: 0 (never) - 30; "Clear finished after..." slider
    "stale_hours": 12,
    "claude_code": {
        "enabled": True,
        # Extra folders to watch, e.g. a WSL distro if the hook can't reach Windows:
        # (in config.json: "\\\\wsl.localhost\\Ubuntu\\home\\<you>\\.aipet\\sessions")
        "extra_session_dirs": [],
    },
    "workbench": {
        "enabled": False,
        "mcp_url": "http://localhost:8080/mcp",
        "tool_name": "listAgentChats",
        "tool_arguments": {"limit": 50},
        "api_key_env": "WORKBENCH_MCP_KEY",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
        "poll_seconds": 10,
        "timeout_seconds": 20,
        "max_chats": 8,
        "state_fields": ["state", "status", "runState", "runStatus", "agentState", "lastRunState", "phase"],
        "state_map": {
            "needs_input": ["waiting", "awaiting", "awaiting_input", "needs_input", "input_required",
                            "pending_approval", "approval", "paused", "blocked", "waiting_for_user"],
            "error": ["error", "failed", "failure", "cancelled", "canceled", "timeout", "timed_out"],
            "done": ["completed", "complete", "done", "finished", "succeeded", "success", "idle",
                     "stopped", "ended", "inactive", "closed"],
            "working": ["running", "processing", "in_progress", "streaming", "active", "busy",
                        "executing", "thinking", "started", "queued"],
        },
    },
}

IS_MAC = sys.platform == "darwin"
TRANSPARENT = "systemTransparent" if IS_MAC else "#ff00fe"
COLORS = {"working": "#5b8def", "needs_input": "#f59e0b", "done": "#22c55e", "error": "#ef4444", "idle": "#9ca3af"}
DARK = {"working": "#2f5bb7", "needs_input": "#b45309", "done": "#15803d", "error": "#991b1b", "idle": "#4b5563"}
LABELS = {"working": "working…", "needs_input": "needs you!", "done": "done", "error": "error", "idle": "idle"}
BADGE_DOTS = {"Claude": "#d97757", "Codex": "#0f8a6a", "Workbench": "#0f766e"}  # badge dot colour by its first word
BADGE_DOT_COWORK = "#c2410c"
BADGE_ATTENTION_BG = "#fde4e4"  # a very faint red: this session needs you


def session_badge(agent="Claude", cowork=False, wsl=False, vscode=False, entry=""):
    """One badge per session with all its tags: 'Claude CLI', 'Claude VS', 'Claude WSL VS', 'Codex WSL',
    'Claude Cowork'..."""
    if cowork:
        return "Claude Cowork"
    parts = [agent] + (["WSL"] if wsl else []) + (["VS"] if vscode else [])
    if len(parts) == 1:
        parts.append("App" if "desktop" in (entry or "").lower() else "CLI")
    return " ".join(parts)


def merge_badges(labels):
    """Compact mode's collapsed badge for several sessions: different agents are listed by name ("Claude + Codex"),
    one agent gets all its tags ("Claude Cowork+WSL+VS")."""
    agents, tags = [], {}
    for label in labels:
        words = label.split(" ")
        if not words or words[0].startswith("+"):
            continue
        a = words[0]
        if a not in agents:
            agents.append(a)
        for q in words[1:]:
            if q not in tags.setdefault(a, []):
                tags[a].append(q)
    if len(agents) != 1:
        return " + ".join(agents)
    a = agents[0]
    return a + (" " + "+".join(tags.get(a, [])) if tags.get(a) else "")


def badge_dot(text):
    return BADGE_DOT_COWORK if text.endswith("Cowork") else BADGE_DOTS.get(text.split(" ")[0], "#6b7280")


BADGE_NAMES = {"CC": "Claude", "CW": "Cowork", "CX": "Codex", "WSL": "WSL", "VS": "VS", "WB": "Workbench"}
SOURCE_NAMES = {"CC": "Claude Code", "CW": "Cowork", "CX": "Codex", "WB": "Workbench"}
BADGE_COLORS = {"CC": "#6b7280", "CW": "#c2410c", "CX": "#0f8a6a", "WSL": "#7c3aed", "VS": "#007acc", "WB": "#0f766e"}
STATE_ORDER = ("needs_input", "error", "done", "working")
PET_W, PET_H = 92, 122  # the drawing area of one pet, before the badge column and top trim below
BADGE_GUTTER = 0  # extra width on the pet's left (badges now sit on the name tag's top edge, so none is needed)
TAG_H = 19  # name tag height (one unit taller than it was, for the name + conversation title lines)
TOP_TRIM = 14  # the empty strip the badges used to take above the bubble
CANVAS_W, CANVAS_H = PET_W + BADGE_GUTTER, PET_H - TOP_TRIM
USAGE_GUTTER = 0  # individual pets reserve quota space only when they show readings
USAGE_EDGE_ROOM = 18  # one shared margin keeps the rightmost pet stable as usage appears
INK = "#1f2937"
MOUND, MOUND_DARK = "#a16207", "#713f12"
EMERGE_SECONDS, EMERGE_DEPTH, EMERGE_STAGGER = 0.7, 62, 0.25
DONE_FLASH_SECONDS = 1.6  # a working pet shows the done check this long when a subagent or another session finishes
BUBBLE_W = 270
# Colours of what the pet draws around the creatures (name tags, bubbles, tooltips). Light is the default.
THEMES = {
    "light": {"tag_bg": "#ffffff", "tag_fg": "#111827", "label_dark": True,
              "bubble_bg": "#ffffff", "bubble_fg": "#111827", "bubble_msg": "#374151", "bubble_muted": "#6b7280",
              "tip_bg": "#ffffff", "tip_fg": "#111827", "tip_border": "#cbd5e1", "tag_outline": "#111827",
              "code_bg": "#f3f4f6", "code_fg": "#111827",
              # windows, dialogs and menus
              "win_bg": "#ffffff", "win_fg": "#111827", "muted": "#6b7280", "border": "#cbd5e1",
              "btn_bg": "#f3f4f6", "btn_fg": "#111827", "btn_active": "#e5e7eb",
              "entry_bg": "#f3f4f6", "entry_fg": "#111827", "select_bg": "#dbeafe",
              "primary": "#2563eb", "primary_active": "#1d4ed8", "primary_fg": "#ffffff",
              "danger": "#dc2626", "danger_active": "#b91c1c",
              "menu_bg": "#ffffff", "menu_fg": "#111827", "menu_active_bg": "#e5e7eb", "menu_active_fg": "#111827",
              "menu_disabled": "#9ca3af"},
    "dark": {"tag_bg": INK, "tag_fg": "#ffffff", "label_dark": False,
             "bubble_bg": "#111827", "bubble_fg": "#ffffff", "bubble_msg": "#e5e7eb", "bubble_muted": "#9ca3af",
             "tip_bg": "#111827", "tip_fg": "#f9fafb", "tip_border": "#111827", "tag_outline": "#0b1220",
             "code_bg": "#0b1220", "code_fg": "#e5e7eb",
             "win_bg": "#111827", "win_fg": "#f9fafb", "muted": "#9ca3af", "border": "#374151",
             "btn_bg": "#1f2937", "btn_fg": "#f9fafb", "btn_active": "#374151",
             "entry_bg": "#0b1220", "entry_fg": "#e5e7eb", "select_bg": "#1e3a8a",
             "primary": "#3b82f6", "primary_active": "#2563eb", "primary_fg": "#ffffff",
             "danger": "#ef4444", "danger_active": "#dc2626",
             "menu_bg": "#1f2937", "menu_fg": "#f9fafb", "menu_active_bg": "#374151", "menu_active_fg": "#ffffff",
             "menu_disabled": "#6b7280"},
}
# Fixed status colours used in the windows (green / amber / red notes) and their readable versions on a dark background
DARK_TEXT = {"#15803d": "#4ade80", "#b45309": "#fbbf24", "#b91c1c": "#f87171", "#dc2626": "#f87171",
             "#d97706": "#fbbf24"}
T = {}  # the active theme; set_theme() mutates it in place so every module sees the change


def set_theme(name):
    name = name if name in THEMES else "light"
    T.clear()
    T.update(THEMES[name], name=name)


set_theme("light")


# --------------------------------------------------------------------------- themed windows, dialogs and menus
# The pet's own drawings use T directly. Ordinary Tk windows (setup, sliders, Cowork help...) are styled after they are
# built by theme_window(), which also remembers them so a theme switch restyles them live. Colours a window set on
# purpose (green "installed", amber "out of date"...) are kept, brightened on the dark background.
THEMED = []
_DEFAULT_FG = {"", "black", "#000000", "systembuttontext", "systemwindowtext", "systemmenutext", "#111827"}
MUTED_FG = {"#6b7280", "grey", "gray"}


def _fg_role(w, opt="fg"):
    roles = w.__dict__.setdefault("_pet_roles", {})
    if opt not in roles:
        try:
            v = str(w.cget(opt))
        except tk.TclError:
            return T["win_fg"]
        low = v.lower()
        roles[opt] = "fg" if low in _DEFAULT_FG else ("muted" if low in MUTED_FG else "keep:" + v)
    role = roles[opt]
    if role == "fg":
        return T["win_fg"]
    if role == "muted":
        return T["muted"]
    c = role[5:]
    return DARK_TEXT.get(c.lower(), c) if T.get("name") == "dark" else c


def themed_color(c):
    """A status colour readable on the current theme's window background."""
    return DARK_TEXT.get(c.lower(), c) if T.get("name") == "dark" else c


def button_style(kind="secondary"):
    """Options for a tk.Button in the pet's style: 'primary' (filled), 'danger' or 'secondary' (outlined)."""
    if IS_MAC:
        # Aqua draws the button face itself and ignores our filled background.
        # Its matching system text stays readable in both macOS appearances.
        return dict(fg="systemButtonText", activeforeground="systemButtonText",
                    bg=T["win_bg"], highlightbackground=T["win_bg"],
                    cursor="hand2", padx=14, pady=4,
                    font=(UI_FONT, 9, "bold") if kind in ("primary", "danger") else (UI_FONT, 9))
    if kind == "primary":
        return dict(bg=T["primary"], fg=T["primary_fg"], activebackground=T["primary_active"],
                    activeforeground=T["primary_fg"], relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                    padx=14, pady=4, font=(UI_FONT, 9, "bold"))
    if kind == "danger":
        return dict(bg=T["danger"], fg="#ffffff", activebackground=T["danger_active"], activeforeground="#ffffff",
                    relief="flat", bd=0, highlightthickness=0, cursor="hand2", padx=14, pady=4,
                    font=(UI_FONT, 9, "bold"))
    return dict(bg=T["btn_bg"], fg=T["btn_fg"], activebackground=T["btn_active"], activeforeground=T["btn_fg"],
                relief="flat", bd=0, highlightthickness=1, highlightbackground=T["border"], highlightcolor=T["border"],
                cursor="hand2", padx=12, pady=3, disabledforeground=T["menu_disabled"], font=(UI_FONT, 9))


def style_menu(menu):
    """Colour a Tk menu and its submenus like the theme (Windows and Linux; macOS draws menus natively)."""
    try:
        menu.configure(bg=T["menu_bg"], fg=T["menu_fg"], activebackground=T["menu_active_bg"],
                       activeforeground=T["menu_active_fg"], disabledforeground=T["menu_disabled"],
                       selectcolor=T["menu_fg"], relief="flat", bd=1, activeborderwidth=0)
    except tk.TclError:
        return
    for child in menu.winfo_children():
        if isinstance(child, tk.Menu):
            style_menu(child)


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
    bounds = work_area(x, y, menu)
    if bounds and y + height + 8 > bounds[3]:
        y -= height + 8
    x, y = fit_on_screen(x, y, menu.winfo_reqwidth(), height, ref, menu, margin=8)
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


# --------------------------------------------------------------------------- chimes (Windows)
# macOS plays its system sounds (Glass when a session is done, Funk when one needs you). Those files are Apple's and only
# exist on a Mac, so on Windows AIPet plays two sounds of its own, synthesized here as two xylophone notes each: "ting"
# rises when a session is done, "knock" falls when one needs you. They are written once to ~/.aipet/sounds as WAV files.
CHIME_VERSION = 4  # bump when the sounds change, so the new WAV files get written
CHIME_RATE = 44100


# Two xylophone notes each: (frequency, start in seconds, loudness, ring time)
CHIMES = {
    # Mid register and mellow on purpose: noticeable without being shrill while you're concentrating
    "ting": ((440.0, 0.0, 0.9, 0.17), (587.3, 0.22, 1.0, 0.22)),  # done: A4 then D5, rising
    "knock": ((392.0, 0.0, 1.0, 0.14), (329.6, 0.24, 0.95, 0.17)),  # needs you: G4 then E4, like a doorbell
    # error: a low, falling tritone (F4 then B3) - clearly "something's off", still soft, never a buzzer
    "thud": ((349.2, 0.0, 1.0, 0.13), (246.9, 0.24, 1.0, 0.2)),
}


def xylophone_note(out, f0, start, amp, ring):
    """Add one soft-mallet xylophone note: the bar's fundamental, its tuned 3rd harmonic and a faint high overtone
    that die away quickly, with a tiny downward bend at the strike."""
    rate, phase = CHIME_RATE, 0.0
    first, n = int(start * rate), int(ring * 6 * rate)
    attack, release = int(0.008 * rate), int(n * 0.3)
    for i in range(n):
        t = i / rate
        phase += 2 * math.pi * f0 * (1 + 0.02 * math.exp(-t / 0.006)) / rate
        v = (math.exp(-t / ring) * math.sin(phase)
             + 0.12 * math.exp(-t / (ring * 0.3)) * math.sin(3 * phase)
             + 0.02 * math.exp(-t / (ring * 0.12)) * math.sin(6.27 * phase))
        # Smooth (raised-cosine) 8 ms attack and a release that lands exactly on zero: any corner in the envelope,
        # at the start or where a note stops, is heard as a tick
        env = 0.5 - 0.5 * math.cos(math.pi * i / attack) if i < attack else 1.0
        if i > n - release:
            env *= 0.5 - 0.5 * math.cos(math.pi * (n - i) / release)
        if first + i < len(out):
            out[first + i] += amp * v * env


CHIME_PAD = (0.06, 0.2)  # seconds of silence before / after, so opening and closing the audio device is not heard


def chime_samples(kind):
    """The chime as floats in -1..1, padded with silence."""
    notes = CHIMES.get(kind, CHIMES["ting"])
    out = [0.0] * int(CHIME_RATE * max(start + ring * 6 for _, start, _, ring in notes))
    for f0, start, amp, ring in notes:
        xylophone_note(out, f0, start, amp, ring)
    peak = max(abs(v) for v in out) or 1.0
    lead, tail = (int(CHIME_RATE * s) for s in CHIME_PAD)
    return [0.0] * lead + [v / peak * 0.5 for v in out] + [0.0] * tail


def chime_path(kind):
    """A WAV file for the chime, created on first use."""
    import struct
    import wave
    folder = os.path.join(HOME_DIR, "sounds")
    path = os.path.join(folder, f"{kind}-v{CHIME_VERSION}.wav")
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        data = b"".join(struct.pack("<h", int(v * 32767)) for v in chime_samples(kind))
        with wave.open(path + ".tmp", "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(CHIME_RATE)
            f.writeframes(data)
        os.replace(path + ".tmp", path)
    return path


def _ttk_scale_style(widget):
    try:
        from tkinter import ttk
        s = ttk.Style(widget)
        s.configure("Pet.Horizontal.TScale", background=T["win_bg"], troughcolor=T["entry_bg"])
        return "Pet.Horizontal.TScale"
    except Exception:
        return None


def theme_widget(w):
    """Style one widget and everything inside it."""
    cls, bg = w.winfo_class(), T["win_bg"]
    try:
        if cls in ("Frame", "Toplevel", "Tk", "Canvas"):
            w.configure(bg=bg)
        elif cls == "Labelframe":
            w.configure(bg=bg, fg=_fg_role(w), highlightbackground=T["border"])
        elif cls == "Label":
            w.configure(bg=bg, fg=_fg_role(w))
        elif cls == "Button":
            kind = w.__dict__.setdefault("_pet_kind", "primary" if str(w.cget("default")) == "active" else "secondary")
            opts = button_style(kind)
            if str(w.cget("font")) != "TkDefaultFont":
                opts.pop("font")  # keep a font the window chose itself (or the one set on an earlier pass)
            w.configure(**opts)
        elif cls in ("Checkbutton", "Radiobutton"):
            fg = _fg_role(w)
            w.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, selectcolor=T["entry_bg"],
                        disabledforeground=T["menu_disabled"], highlightthickness=0)
        elif cls == "Entry":
            w.configure(bg=T["entry_bg"], readonlybackground=T["entry_bg"], fg=T["entry_fg"],
                        insertbackground=T["entry_fg"], relief="flat", highlightthickness=1,
                        highlightbackground=T["border"], highlightcolor=T["primary"])
        elif cls == "Text":
            w.configure(bg=T["entry_bg"], fg=T["entry_fg"], insertbackground=T["entry_fg"], relief="flat",
                        highlightthickness=1, highlightbackground=T["border"], highlightcolor=T["primary"])
        elif cls == "Scale":
            w.configure(bg=bg, fg=T["win_fg"], troughcolor=T["entry_bg"], activebackground=T["primary"],
                        highlightthickness=0)
        elif cls == "TScale":
            style = _ttk_scale_style(w)
            if style:
                w.configure(style=style)
    except tk.TclError:
        pass
    for child in w.winfo_children():
        if isinstance(child, tk.Menu):
            style_menu(child)
        else:
            theme_widget(child)


def titlebar_theme(win):
    """Windows 10/11: a dark title bar for a window while the dark theme is on."""
    if os.name != "nt":
        return
    try:
        import ctypes
        win.update_idletasks()
        u32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
        u32.GetParent.argtypes, u32.GetParent.restype = [ctypes.c_void_p], ctypes.c_void_p
        hwnd = u32.GetParent(win.winfo_id()) or win.winfo_id()
        val = ctypes.c_int(1 if T.get("name") == "dark" else 0)
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer builds, then 1809 - 1909)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
        u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
        u32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)  # redraw the frame now (NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED)
    except Exception:
        pass


def native_menu_theme():
    """Windows 10 1903+: menus the system draws (the tray icon's menu, menu borders) follow the theme. Uses uxtheme's
    SetPreferredAppMode / FlushMenuThemes (by ordinal, as they are not exported by name); harmless if unavailable."""
    if os.name != "nt":
        return
    try:
        import ctypes
        if sys.getwindowsversion().build < 18362:
            return
        ux = ctypes.WinDLL("uxtheme")
        set_mode = ux[135]
        set_mode.argtypes, set_mode.restype = [ctypes.c_int], ctypes.c_int
        set_mode(2 if T.get("name") == "dark" else 3)  # ForceDark / ForceLight
        ux[136]()  # FlushMenuThemes
    except Exception:
        pass


def use_console_font(root):
    """Widgets that don't name a font (buttons, check boxes, entries, Tk menus...) get the console font too."""
    try:
        from tkinter import font as tkfont
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
                     "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
            tkfont.Font(root=root, name=name, exists=True).configure(family=UI_FONT)
    except Exception:
        pass


def theme_window(win):
    """Style a finished Toplevel like the pet and keep it in step with later theme switches."""
    theme_widget(win)
    titlebar_theme(win)
    if win not in THEMED:
        THEMED.append(win)
        win.bind("<Destroy>", lambda e, w=win: THEMED.remove(w) if e.widget is w and w in THEMED else None, add="+")
    return win


def retheme_all():
    for w in list(THEMED):
        try:
            if w.winfo_exists():
                theme_widget(w)
                titlebar_theme(w)
        except tk.TclError:
            pass


DIALOG_ACCENTS = {"info": "#5b8def", "question": "#5b8def", "warning": "#f59e0b", "error": "#ef4444"}


def themed_dialog(root, title, text, buttons=(("OK", True, "primary"),), kind="info", heading=None, cancel=None,
                  enter_confirms=True, on_result=None):
    """A modal message card in the pet's style (accent border, bold heading, flat buttons), in place of the native
    message boxes. buttons: (label, value, 'primary' | 'danger' | 'secondary'), left to right; the first primary or
    danger one is the default (Enter, unless enter_confirms is False: then Enter cancels, for risky choices).
    Escape or closing returns `cancel`. With on_result, deliver the result after
    closing without nesting a wait loop inside a native button callback."""
    previous_focus, previous_grab = root.focus_get(), root.grab_current()
    previous_grab_status = previous_grab.grab_status() if previous_grab is not None else None
    accent = DIALOG_ACCENTS.get(kind, DIALOG_ACCENTS["info"])
    bg, fg = T["win_bg"], T["win_fg"]
    w = tk.Toplevel(root)
    # A modal card belongs to the window that opened it. Topmost alone does
    # not keep it above another topmost window on macOS.
    if root.winfo_viewable():
        w.transient(root.winfo_toplevel())
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    w.configure(bg=accent)
    w.title(title)
    result = {"v": cancel, "closing": False, "restored": False}
    card = tk.Frame(w, bg=bg)
    card.pack(padx=2, pady=2)
    tk.Frame(card, bg=accent, height=5).pack(fill="x")
    body = tk.Frame(card, bg=bg)
    body.pack(fill="both", padx=18, pady=(12, 14))
    top = tk.Frame(body, bg=bg)
    top.pack(fill="x")
    head = tk.Label(top, text=heading or title, bg=bg, fg=fg, anchor="w", font=(UI_FONT, 12, "bold"))
    head.pack(side="left", fill="x", expand=True)
    x = tk.Label(top, text="✕", bg=bg, fg=T["muted"], cursor="hand2", font=(UI_FONT, 11), padx=4)
    x.pack(side="right")
    if kind in ("warning", "error"):
        tk.Label(body, text="warning" if kind == "warning" else "something went wrong", bg=bg, fg=accent, anchor="w",
                 font=(UI_FONT, 9, "bold")).pack(fill="x")
    tk.Frame(body, bg=T["border"], height=1).pack(fill="x", pady=8)
    tk.Label(body, text=text, bg=bg, fg=T["bubble_msg"], justify="left", anchor="w", wraplength=440,
             font=(UI_FONT, 10)).pack(fill="x")
    row = tk.Frame(body, bg=bg)
    row.pack(fill="x", pady=(14, 0))

    def restore_input():
        if result["restored"]:
            return
        result["restored"] = True
        try:
            w.grab_release()
        except tk.TclError:
            pass
        try:
            if previous_grab is not None and previous_grab.winfo_exists():
                previous_grab.grab_set_global() if previous_grab_status == "global" else previous_grab.grab_set()
            if previous_focus is not None and previous_focus.winfo_exists():
                previous_focus.focus_set()
        except tk.TclError:
            pass

    def finish():
        restore_input()
        w.destroy()
        if on_result is not None and root.winfo_exists():
            root.after_idle(lambda: on_result(result["v"]))

    def done(value):
        if result["closing"]:
            return
        result.update(v=value, closing=True)
        # Let Aqua finish tracking the clicked button before destroying it.
        w.after_idle(finish)

    def destroyed(event):
        if event.widget is w and not result["closing"]:
            result["closing"] = True
            restore_input()
            if on_result is not None and root.winfo_exists():
                root.after_idle(lambda: on_result(cancel))

    w.bind("<Destroy>", destroyed, add="+")
    w.protocol("WM_DELETE_WINDOW", lambda: done(cancel))

    default = None
    for label, value, style in reversed(list(buttons)):  # packed from the right, so the last one ends up rightmost
        b = tk.Button(row, text=label, command=lambda v=value: done(v), **button_style(style))
        b.pack(side="right", padx=(8, 0))
        if style in ("primary", "danger") and default is None:
            default = value
    if default is None and buttons:
        default = list(buttons)[-1][1]
    x.bind("<Button-1>", lambda e: done(cancel))
    w.bind("<Escape>", lambda e: done(cancel))
    w.bind("<Return>", lambda e: done(default if enter_confirms else cancel))
    drag = {}
    for widget in (top, head):
        widget.bind("<ButtonPress-1>", lambda e: drag.update(d=(e.x_root - w.winfo_x(), e.y_root - w.winfo_y())))
        widget.bind("<B1-Motion>", lambda e: w.geometry(f"+{e.x_root - drag['d'][0]}+{e.y_root - drag['d'][1]}")
                    if "d" in drag else None)
    w.update_idletasks()
    ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
    try:
        ref = (root.winfo_x() + root.winfo_width() // 2, root.winfo_y() + root.winfo_height() // 2)
        left, top_, right, bottom = work_area(*ref, w) or (0, 0, w.winfo_screenwidth(), w.winfo_screenheight())
    except Exception:
        left, top_, right, bottom = 0, 0, w.winfo_screenwidth(), w.winfo_screenheight()
    w.geometry(geo(left + (right - left - ww) // 2, max(top_ + 20, top_ + (bottom - top_ - wh) // 3)))
    w.deiconify()
    w.lift()
    w.focus_force()
    if IS_MAC:
        import mac_statusbar
        mac_statusbar.raise_modal(w.title(), root.winfo_toplevel().title())
        w._mouse_bridge = mac_statusbar.PanelMouseBridge(w)
        w.bind("<Destroy>", lambda event: w._mouse_bridge.close() if event.widget is w else None, add="+")
    try:
        w.grab_set()
    except tk.TclError:
        pass
    if on_result is not None:
        return w
    try:
        root.wait_window(w)
    finally:
        restore_input()
    return result["v"]


STYLE ={"v": "robot"}  # "robot", "mole" or "cat"
SCALE_UNIT = 2.0  # the absolute drawing scale that counts as 100% (it was the old 200%, the size people settled on)
SCALE = {"v": SCALE_UNIT}  # absolute drawing scale: 92x122 px per pet at 1.0; the slider shows SCALE / SCALE_UNIT


ANSWER_WAIT = {"v": 180}  # seconds; mirrored into <pet dir>/answer-wait for the hook (0 = wait until answered)
MAX_WAIT = 1800  # every timer: 0 (no limit / never) to 30 minutes
DONE_TIMEOUT = {"v": 3}  # minutes until a finished session's pet is cleared (0 = never)
HEALTH = {"v": 15}  # seconds between checks that a working session's process still runs (0 = off)
MAX_HEALTH = 300


def clamp_health(v):
    try:
        return int(max(0, min(MAX_HEALTH, round(float(v)))))
    except (TypeError, ValueError):
        return 15


PROMPT_CONFIRM_SECONDS = 12  # Claude Code's permission_prompt Notification comes ~6 s after the prompt appears


def unconfirmed_prompts(rec, now=None):
    """True when a session shows "needs you" only for permission prompts Claude Code never put on screen: none got
    its permission_prompt Notification in time. Only for Claude Code sessions known to send those notifications."""
    if rec.get("state") != "needs_input" or not rec.get("notifies") or rec.get("agent") == "codex":
        return False
    reqs = [r for r in (rec.get("requests") or []) if isinstance(r, dict) and r.get("source") != "transcript"]
    now = time.time() if now is None else now
    return bool(reqs) and all(not r.get("shown") and now - float(r.get("t") or now) > PROMPT_CONFIRM_SECONDS
                              for r in reqs)


def answer_name(item):
    req = (item or {}).get("request") or {}
    return "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in str(req.get("id") or (item or {}).get("sid") or "request"))[:120]


def hook_waiting(item):
    """True while a PermissionRequest hook is really waiting for this prompt: it touches answers/<id>.waiting every
    second. A hook stopped by its agent (e.g. Codex's own hook timeout) leaves 'answerable' set but stops touching."""
    req = (item or {}).get("request") or {}
    if not req.get("answerable"):
        return False
    try:
        return time.time() - os.path.getmtime(os.path.join(answers_dir(), answer_name(item) + ".waiting")) < 4
    except OSError:
        return False


def answers_dir():
    return os.path.join(HOME_DIR, "answers")


def clamp_wait(v):
    try:
        return int(max(0, min(MAX_WAIT, round(float(v)))))
    except (TypeError, ValueError):
        return 180


def clamp_done(v):
    try:
        return int(max(0, min(MAX_WAIT // 60, round(float(v)))))
    except (TypeError, ValueError):
        return 3


def fmt_wait(sec):
    sec = int(sec)
    if sec <= 0:
        return "no limit"
    m, s = divmod(sec, 60)
    return f"{m} min {s} s" if m and s else (f"{m} min" if m else f"{s} s")


def write_answer_wait(sec):
    """Tell the hooks (including WSL ones, through the shared folder) how long to wait for a click."""
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        with open(os.path.join(HOME_DIR, "answer-wait"), "w", encoding="utf-8") as f:
            f.write(str(int(sec)))
    except OSError:
        pass


AUTO_APPROVE_PATH = os.path.join(HOME_DIR, "auto-approve.json")  # read by every hook (WSL ones through /mnt/c)
DEFAULT_BLACKLIST = [".*"]  # by default every request still needs you
AUTO_FLASH_SECONDS = 10  # how long a pet celebrates a whitelist auto-approval


def default_auto_rules():
    return {"enabled": False, "allow_all": False, "whitelist": [], "blacklist": list(DEFAULT_BLACKLIST)}


def auto_approve_rules():
    """{hook config key: rules} as saved by the Auto approve windows. A missing or unreadable file means all off.
    The first version stored a plain list of keys: those meant "allow all"."""
    try:
        with open(AUTO_APPROVE_PATH, encoding="utf-8") as f:
            targets = json.load(f).get("targets")
    except Exception:
        return {}
    if isinstance(targets, list):
        return {k: dict(default_auto_rules(), enabled=True, allow_all=True) for k in targets if isinstance(k, str)}
    if not isinstance(targets, dict):
        return {}
    out = {}
    for k, r in targets.items():
        if isinstance(r, dict):
            rules = default_auto_rules()
            rules.update({x: r[x] for x in ("enabled", "allow_all", "whitelist", "blacklist") if x in r})
            out[k] = rules
    return out


def auto_approve_targets():
    """Hook configs with auto-approval switched on (empty = all off, the default)."""
    return sorted(k for k, r in auto_approve_rules().items() if r.get("enabled"))


def save_auto_approve(key, rules):
    """Save one hook config's rules (key None = switch every config off, keeping their lists). True if saved."""
    targets = auto_approve_rules()
    if key is None:
        for r in targets.values():
            r["enabled"] = False
    else:
        targets[key] = rules
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        tmp = AUTO_APPROVE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"targets": targets}, f, indent=2)
        os.replace(tmp, AUTO_APPROVE_PATH)
        return True
    except OSError as e:
        log_error(f"auto-approve: {e!r}")
        return False


def bad_patterns(lines):
    """[(line number, pattern, error)] for the regexes that don't compile (blank and # lines are skipped)."""
    import re
    out = []
    for n, ln in enumerate(lines, 1):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        try:
            re.compile(ln)
        except re.error as e:
            out.append((n, ln, str(e)))
    return out


def answers_enabled():
    return not os.path.exists(os.path.join(HOME_DIR, "no-answers"))


def clamp_scale(v):
    try:
        return max(0.6, min(6.0, float(v)))  # 30% - 300% of SCALE_UNIT
    except (TypeError, ValueError):
        return 1.0


def px(n):
    return int(round(n * SCALE["v"]))


def fnt(size, weight=""):
    f = (UI_FONT, max(5, int(round(size * SCALE["v"]))))
    return f + (weight,) if weight else f


MOLE_BODY, MOLE_EDGE, MOLE_LIGHT = "#a5703e", "#6b4423", "#c18f5a"
MOLE_NOSE, MOLE_NOSE_EDGE, MOLE_HILL, MOLE_HILL_EDGE = "#f4a6b8", "#d97a94", "#8a5a33", "#5a3a1c"
# (x offset, size) of each head, left to right; the LAST entry is the main agent, drawn last and in front
HEAD_LAYOUTS = {
    1: [(0, 1.0)],
    2: [(-15, 0.78), (13, 0.92)],
    3: [(-27, 0.7), (27, 0.7), (0, 0.92)],
    4: [(-31, 0.62), (31, 0.62), (-11, 0.78), (11, 0.92)],
}


# --------------------------------------------------------------------------- config
def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return deep_merge(DEFAULT_CONFIG, json.load(f))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULT_CONFIG)
    except Exception as e:
        print(f"[aipet] config.json unreadable, using defaults: {e}", file=sys.stderr)
        return copy.deepcopy(DEFAULT_CONFIG)


def save_setting(key, value):
    """Persist one top-level key in config.json, leaving everything else as the user wrote it."""
    try:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        data[key] = value
        os.makedirs(HOME_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
        return True
    except Exception as e:  # unreadable/hand-edited config: don't clobber it
        print(f"[aipet] couldn't save {key}: {e}", file=sys.stderr)
        return False


def first(d, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def ago(ts):
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h {s % 3600 // 60}m ago"


def place_name(rec):
    """The name tag's top line: the session's folder. A session with no folder to name it after (Cowork without a
    connected folder, a session in the home folder) used to be titled by its first prompt; the conversation title now
    sits right below the name, so show where it runs instead - its badge tags: "Cowork", "WSL", "WSL VS Code", "CLI"."""
    title = rec.get("title") or "session"
    if not (rec.get("topic") and title == rec.get("topic")) and title not in ("~", "Cowork"):
        return title
    badge = session_badge("Codex" if rec.get("agent") == "codex" else "Claude", rec.get("app") == "cowork",
                          rec.get("env") == "wsl", rec.get("ide") == "vscode", rec.get("entry", ""))
    tags = [{"VS": "VS Code"}.get(t, t) for t in badge.split(" ")[1:]]
    return " ".join(tags) or title


_PID_CACHE = {}


def pid_alive(pid, max_age=5.0):
    """Is the process running (cached for a few seconds)? None when it can't be told. Only for processes on this
    machine (not WSL ones)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    hit = _PID_CACHE.get(pid)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    alive = None
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                alive = ctypes.get_last_error() == 5  # access denied: it exists; otherwise it is gone
            else:
                code = wintypes.DWORD()
                ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
                k32.CloseHandle(h)
                alive = bool(ok) and code.value == 259  # STILL_ACTIVE
        else:
            os.kill(pid, 0)
            alive = True
    except PermissionError:
        alive = True
    except ProcessLookupError:
        alive = False
    except Exception:
        alive = None
    _PID_CACHE[pid] = (time.time(), alive)
    if len(_PID_CACHE) > 200:
        _PID_CACHE.clear()
    return alive


def session_title(rec, mode="name"):
    """The extra title of a session: "name" = the harness's session name (Claude Code's /rename or its own name for
    the session, Codex's thread name), else the latest prompt; "prompt" = the latest prompt, else the name."""
    prompt = next((p for p in (rec.get("last_prompt"), rec.get("first_prompt"))  # first_prompt: older hooks
                   if p and not str(p).startswith("<")), "")  # "<...>": injected by the harness, not typed
    name = rec.get("conv") or ""
    return (prompt or name) if mode == "prompt" else (name or prompt)


def disambiguate_titles(items):
    """Sessions named after the same folder ("esign-online", "esign-online") get their conversation's title added
    ("esign-online · Fix login bug"), or a short session id when there is none yet."""
    counts = {}
    for i in items:
        k = (i.get("title") or "").casefold()
        counts[k] = counts.get(k, 0) + 1
    for i in items:
        if counts.get((i.get("title") or "").casefold(), 0) > 1:
            extra = i.get("conv") or ("#" + str(i.get("session_id") or i.get("key") or "")[-4:])
            i["raw_title"] = i.get("title") or ""
            i["title"] = f"{i.get('title') or 'session'} \u00b7 {extra}"
            i["dup_title"] = True


# --------------------------------------------------------------------------- Claude Code source
def _active_agents(rec, now=None):
    """How many subagents are running: started (or seen) and not yet stopped, within 15 min (while the session is busy)."""
    if rec.get("state") not in ("working", "needs_input"):
        return 0
    now = now or time.time()
    return sum(1 for ts in (rec.get("agents") or {}).values() if now - ts < 900)


def read_claude_code_sessions(cfg):
    items = []
    if not cfg["claude_code"]["enabled"]:
        return items
    cutoff = time.time() - cfg["stale_hours"] * 3600
    dirs = [SESSIONS_DIR] + ([LEGACY_SESSIONS_DIR] if os.path.isdir(LEGACY_SESSIONS_DIR) else [])  # LEGACY
    dirs += list(cfg["claude_code"].get("extra_session_dirs") or [])
    paths = []
    for d in dirs:
        try:
            paths += glob.glob(os.path.join(d, "*.json"))
        except OSError:
            continue  # e.g. WSL distro not running
    newest = {}  # session id -> (path, record): the same session can have a file in two folders (old and new hooks)
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            continue  # mid-write or corrupt; next poll will catch it
        if rec.get("updated", 0) < cutoff and not (DONE_TIMEOUT["v"] == 0 and rec.get("state") in ("done", "idle")):
            try:
                os.remove(path)
                usage.remove_for(path)
            except OSError:
                pass
            continue
        sid = str(rec.get("id"))
        old = newest.get(sid)
        if old and old[1].get("updated", 0) >= rec.get("updated", 0):
            stale_path = path
        else:
            stale_path = old[0] if old else None
            newest[sid] = (path, rec)
        if stale_path:  # an older copy of a session that is written elsewhere now: drop it
            try:
                os.remove(stale_path)
            except OSError:
                pass
    for path, rec in sorted(newest.values(), key=lambda pr: pr[0]):
        sid = str(rec.get("id"))
        local = rec.get("env") == ("windows" if os.name == "nt" else sys.platform)
        quiet = time.time() - rec.get("updated", 0) > max(60, HEALTH["v"])  # an active session isn't judged by its pid
        if (local and HEALTH["v"] > 0 and quiet and rec.get("state") in ("working", "needs_input") and rec.get("pid")
                and pid_alive(rec["pid"], HEALTH["v"]) is False):
            # its Claude Code / Codex process is gone without a Stop or SessionEnd (the app was closed, or Cowork moved
            # the conversation to a new session): show it as done, so the done timeout clears it
            rec = dict(rec, state="done", message="", request={}, changed=rec.get("updated", 0))
        if unconfirmed_prompts(rec):
            # Claude Code announces every prompt it really shows (a Notification ~6 s in). These never were: settled
            # without the user (auto mode, a rule...), so the session is working, not waiting for you.
            rec = dict(rec, state="working", message="", request={}, requests=[])
        env, ide = rec.get("env", ""), rec.get("ide", "")
        cowork = rec.get("app") == "cowork"
        codex = rec.get("agent") == "codex"
        badges = [session_badge("Codex" if codex else "Claude", cowork, env == "wsl", ide == "vscode",
                                rec.get("entry", ""))]
        where = ["Claude app · Cowork" if cowork else ("Codex" if codex else "Claude Code")]
        if env == "wsl":
            where.append(f"WSL ({rec['distro']})" if rec.get("distro") else "WSL")
        if ide == "vscode":
            where.append("VS Code")
        items.append({
            "key": "cc:" + sid,
            "source": "CC",
            "badges": badges or ["CC"],
            "where": " · ".join(where),
            "env": env,
            "distro": rec.get("distro", ""),
            "ide": ide,
            "session_id": sid,
            "agent": rec.get("agent", "claude"),
            "entry": rec.get("entry", ""),
            "cwd": rec.get("cwd", ""),
            "title": place_name(rec),
            "state": rec.get("state", "idle"),
            "message": rec.get("message", ""),
            "detail": rec.get("cwd", ""),
            "changed": rec.get("changed", rec.get("updated", 0)),
            "path": path,
            "legacy": os.path.dirname(path) == LEGACY_SESSIONS_DIR,  # LEGACY: written by a hook on the old name
            "app": rec.get("app", ""),
            "hwnd": rec.get("hwnd"),
            "subagents": _active_agents(rec),
            "request": rec.get("request") or {},
            "pending": len(rec.get("requests") or []) or (1 if rec.get("request") else 0),  # prompts queued
            "pid": rec.get("pid"),
            "sid": str(rec.get("id")),
            "auto_approved": rec.get("auto_approved", 0),
            "auto_t": rec.get("auto_t", 0),
            "auto_kind": rec.get("auto_kind", ""),
            "auto_what": rec.get("auto_what", ""),
            "conv": session_title(rec, cfg.get("session_titles", "name")),
            "usage": [r for r in usage.load(path) if cfg.get("claude_oauth_usage", False) or not r.get("provider", "").startswith("Claude Code account")],
            "updated": rec.get("updated", 0),
        })
    return items


# --------------------------------------------------------------------------- Workbench source
class McpHttpClient:
    """Minimal MCP streamable-HTTP client: initialize + tools/call."""

    def __init__(self, wcfg):
        self.cfg = wcfg
        self.session_id = None
        self.protocol = None
        self._next_id = 0

    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        key = os.environ.get(self.cfg.get("api_key_env") or "", "")
        if key:  # key is read from the environment, never stored in config
            scheme = self.cfg.get("auth_scheme") or ""
            h[self.cfg.get("auth_header") or "Authorization"] = f"{scheme} {key}".strip()
        return h

    def _post(self, method, params=None, notify=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            self._next_id += 1
            payload["id"] = self._next_id
        req = urllib.request.Request(self.cfg["mcp_url"], data=json.dumps(payload).encode("utf-8"),
                                     headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=self.cfg.get("timeout_seconds", 20)) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            if notify:
                return None
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8", "replace")
        msg = self._parse(raw, ctype, payload["id"])
        if "error" in msg:
            raise RuntimeError(f"MCP error: {msg['error']}")
        return msg.get("result", {})

    @staticmethod
    def _parse(raw, ctype, want_id):
        if "text/event-stream" in ctype:
            for block in raw.replace("\r\n", "\n").split("\n\n"):
                data = "\n".join(l[5:].lstrip() for l in block.split("\n") if l.startswith("data:"))
                if not data:
                    continue
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == want_id:
                    return msg
            raise RuntimeError("no matching response in event stream")
        return json.loads(raw)

    def _ensure_session(self):
        if self.protocol:
            return
        res = self._post("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "aipet", "version": "1.0"},
        })
        self.protocol = res.get("protocolVersion", "2025-06-18")
        try:
            self._post("notifications/initialized", notify=True)
        except Exception:
            pass

    def call_tool(self, name, args):
        self._ensure_session()
        try:
            return self._post("tools/call", {"name": name, "arguments": args})
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):  # session expired -> re-initialize once
                self.session_id = self.protocol = None
                self._ensure_session()
                return self._post("tools/call", {"name": name, "arguments": args})
            raise


def tool_result_to_obj(result):
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    joined = "\n".join(texts)
    if result.get("isError"):
        raise RuntimeError("tool error: " + joined[:200])
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    try:
        return json.loads(joined)
    except ValueError:
        return {"text": joined}


ID_KEYS = ("chatId", "id", "uuid")


def find_chat_list(obj, depth=0):
    """Find the first list of chat-like dicts anywhere in the response."""
    if depth > 5:
        return None
    if isinstance(obj, list):
        if not obj:
            return []
        if all(isinstance(x, dict) for x in obj) and any(k in obj[0] for k in ID_KEYS):
            return obj
        for x in obj:
            r = find_chat_list(x, depth + 1)
            if r:
                return r
        return None
    if isinstance(obj, dict):
        for k in ("items", "chats", "content", "results", "data", "rows"):
            if k in obj:
                r = find_chat_list(obj[k], depth + 1)
                if r is not None:
                    return r
        for v in obj.values():
            if isinstance(v, (list, dict)):
                r = find_chat_list(v, depth + 1)
                if r:
                    return r
    return None


def normalize_state(raw, smap):
    if isinstance(raw, dict):
        raw = raw.get("state") or raw.get("status") or ""
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not s:
        return "idle"
    for st in STATE_ORDER:
        if s in smap.get(st, []):
            return st
    for st in STATE_ORDER:
        if any(term in s for term in smap.get(st, [])):
            return st
    return "idle"


def normalize_chat(c, wcfg):
    cid = first(c, ID_KEYS)
    raw_state = first(c, wcfg["state_fields"])
    state = normalize_state(raw_state, wcfg["state_map"])
    if c.get("needsInput") or c.get("awaitingInput") or c.get("pendingApproval"):
        state = "needs_input"
    return {
        "key": f"wb:{cid}",
        "source": "WB",
        "badges": ["Workbench"],
        "where": "Workbench",
        "title": str(first(c, ("title", "name", "label", "summary")) or f"chat {cid}"),
        "state": state,
        "message": str(first(c, ("statusMessage", "lastMessagePreview", "preview", "message")) or ""),
        "detail": f"chat {cid} · raw state: {raw_state}",
        "changed": 0,
    }


class WorkbenchPoller(threading.Thread):
    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.wcfg = cfg["workbench"]
        self.client = McpHttpClient(self.wcfg)
        self.lock = threading.Lock()
        self._items = []
        self.seen = {}
        self.dismissed = set()
        self.first = True
        self.status = "starting…"

    def fetch(self):
        res = self.client.call_tool(self.wcfg["tool_name"], self.wcfg.get("tool_arguments") or {})
        obj = tool_result_to_obj(res)
        chats = find_chat_list(obj)
        if chats is None:
            raise RuntimeError("no chat list in response (run --probe-workbench)")
        return [normalize_chat(c, self.wcfg) for c in chats], obj

    def run(self):
        while True:
            try:
                chats, _ = self.fetch()
                self._update(chats)
                self.status = f"ok · {len(chats)} chats"
            except Exception as e:
                self.status = f"error: {e}"[:110]
            time.sleep(max(2, self.wcfg.get("poll_seconds", 10)))

    def _update(self, chats):
        now = time.time()
        out = []
        with self.lock:
            for c in chats:
                key = c["key"]
                prev = self.seen.get(key)
                if prev is None:
                    # Chats already finished when the pet started stay hidden.
                    self.seen[key] = {"state": c["state"], "changed": 0 if self.first else now}
                elif prev["state"] != c["state"]:
                    prev["state"], prev["changed"] = c["state"], now
                    self.dismissed.discard(key)
                c["changed"] = self.seen[key]["changed"]
                if key in self.dismissed:
                    continue
                if c["state"] in ("working", "needs_input", "error") or (c["state"] == "done" and c["changed"]):
                    out.append(c)
            self.first = False
            self._items = out[: self.wcfg.get("max_chats", 8)]

    def items(self):
        with self.lock:
            return [dict(i) for i in self._items]

    def dismiss(self, key):
        with self.lock:
            self.dismissed.add(key)
            self._items = [i for i in self._items if i["key"] != key]


# --------------------------------------------------------------------------- the pet
# --------------------------------------------------------------------------- robot sprites
SPRITE_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets", "sprites")
_SPR = {"state": None}  # None = not tried yet, False = unavailable (no Pillow / no files) -> the mole is used instead
_PHOTOS = {}  # (key, w, h) -> ImageTk.PhotoImage, so a frame never re-resizes the same sprite
TERMINAL_CHARS = "0123456789ABCDEF$#%&@<>/=+*;:"
CYAN_LIGHT = (214, 251, 255)
LIGHT_COLORS = {"amber": (245, 165, 36), "green": (34, 197, 94), "red": (239, 68, 68), "off": (55, 65, 81)}
# state -> (face, light colours). "working" cycles its lights in light_cycle().
STATE_FACE = {"working": "happy", "done": "joy", "error": "worried", "idle": "sleep", "needs_input": "ask"}
FACE_PARTS = {"ask": (None, None), "happy": ("open", "smile"), "blink": ("line", "smile"), "joy": ("arc", "smile"),
              "sleep": ("line", "flat"), "worried": ("open", "wavy")}
EYE_PIXELS = {"open": [".2.", "111", "111", ".1."], "arc": [".1.", "1.1"], "line": ["111"],
              "x": ["1.1", ".1.", "1.1"]}
QUESTION_PIXELS = [".111.", "1...1", "....1", "...1.", "..1..", "..1..", ".....", "..1.."]
MOUTH_PIXELS = {"smile": ["1.....1", ".1...1.", "..111.."], "flat": [".111."], "wavy": [".1.1.1.", "1.1.1.1"]}
# Working robots "talk": a sine wave that travels along the mouth, so they stand out from the idle / done ones
TALK_FRAMES = 4  # one pixel of travel per frame; the wave is 4 pixels long, so 4 frames loop it
# Working robots blink at 0.075x the mouth wave's 2 Hz: every 6.7 s (3/40, so blink and wave line up every 20 s).
# The threshold keeps each blink about 0.13 s long whatever the ratio.
BLINK_RATIO, BLINK_SECONDS = 0.075, 0.13
BLINK_THRESHOLD = math.cos(math.pi * BLINK_SECONDS * BLINK_RATIO * 2)


def _sine_mouth(frame, width=7, rows=2):
    """A low, short squiggle (two humps across the mouth): a full-width wave would read as smile / frown."""
    grid = [["."] * width for _ in range(rows)]
    for i in range(width):
        y = 0.5 - 0.5 * math.sin(2 * math.pi * (i + frame) / 4.0 + 0.3)  # 0..1, top to bottom
        grid[int(round(y))][i] = "1"
    return ["".join(r) for r in grid]


for _f in range(TALK_FRAMES):
    MOUTH_PIXELS[f"sine{_f}"] = _sine_mouth(_f)
    FACE_PARTS[f"talk{_f}"] = ("open", f"sine{_f}")
    FACE_PARTS[f"talkblink{_f}"] = ("line", f"sine{_f}")


def load_sprites(directory=None):
    """Load the robot sprites (needs Pillow + assets/sprites). Returns a dict, or False if unavailable."""
    if directory is None and _SPR["state"] is not None:
        return _SPR["state"]
    try:
        from PIL import Image
        d = directory or SPRITE_DIR
        with open(os.path.join(d, "sprites.json"), encoding="utf-8") as f:
            meta = json.load(f)
        img = {"robot": Image.open(os.path.join(d, meta["robot"]["file"])).convert("RGBA")}
        img["shadow"] = _solid_shadow(Image.open(os.path.join(d, "shadow.png")).convert("RGBA"))
        res = {"meta": meta, "img": img, "faces": {}}
    except Exception as e:
        log_error(f"robot sprites unavailable in {directory or SPRITE_DIR!r} ({e!r}); using the mole")
        res = False
    if directory is None:
        _SPR["state"] = res
    return res


def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def robot_image(face, lights, spr=None):
    """The robot at native sprite resolution with a face painted on its screen and its three lights coloured.
    face: a key of FACE_PARTS; lights: three keys of LIGHT_COLORS."""
    spr = spr or load_sprites()
    key = (face, tuple(lights))
    if key in spr["faces"]:
        return spr["faces"][key]
    m = spr["meta"]["robot"]
    im = spr["img"]["robot"].copy()
    px = im.load()
    x0, y0, x1, y1 = m["screen"]
    cyan = _rgb(spr["meta"].get("cyan", "#6ee7f3"))

    def stamp(pattern, cx, cy):
        h, w = len(pattern), len(pattern[0])
        for j, row in enumerate(pattern):
            for i, ch in enumerate(row):
                x, y = cx - w // 2 + i, cy - h // 2 + j
                if ch != "." and x0 <= x <= x1 and y0 <= y <= y1:
                    px[x, y] = (CYAN_LIGHT if ch == "2" else cyan) + (255,)

    eye, mouth = FACE_PARTS[face]
    if face == "ask":  # a big "?" in the middle of the screen
        stamp(QUESTION_PIXELS, (x0 + x1) // 2, (y0 + y1) // 2)
    else:
        for side in ("left", "right"):
            stamp(EYE_PIXELS[eye], *m["eyes"][side])
        stamp(MOUTH_PIXELS[mouth], *m["mouth"])
    for (lx, ly, _name), colour in zip(m["lights"], lights):  # each light is a 2x2 dot; (lx, ly) is its top-left
        for dx in (0, 1):
            for dy in (0, 1):
                if 0 <= lx + dx < im.width and 0 <= ly + dy < im.height and px[lx + dx, ly + dy][3]:
                    px[lx + dx, ly + dy] = LIGHT_COLORS[colour] + (255,)
    spr["faces"][key] = im
    return im


def state_image(state, spr=None):
    """Native-resolution robot for a state (the tray icon uses this)."""
    spr = spr or load_sprites()
    return robot_image(STATE_FACE.get(state, "sleep"), light_cycle(state, 0), spr)


def light_cycle(state, t):
    if state == "working":
        i = int(t * 4) % 3
        return tuple("amber" if j == i else ("green" if j == (i + 1) % 3 else "off") for j in range(3))
    if state == "needs_input":  # flashing amber: attention
        return ("amber",) * 3 if int(t * 3) % 2 else ("off", "amber", "off")
    if state == "auto":  # steady green: something was just auto-approved
        return ("green",) * 3
    return {"done": ("green",) * 3, "error": ("red", "amber", "red"), "idle": ("off",) * 3}.get(state, ("off",) * 3)


_PNG_PHOTOS = {"on": IS_MAC}  # macOS: Pillow's ImageTk bridge often can't reach the app's Tk, so hand Tk a PNG instead


def _to_photo(im):
    if not _PNG_PHOTOS["on"]:
        try:
            from PIL import ImageTk
            return ImageTk.PhotoImage(im)
        except Exception as e:  # e.g. TclError: invalid command name "PyImagingPhoto"
            log_error(f"ImageTk unavailable, using PNG images: {e!r}")
            _PNG_PHOTOS["on"] = True
    import base64
    import io
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"), format="png")  # Tk 8.6+ reads PNG


def sprite_photo(key, image, w, h):
    """Nearest-neighbour resize + PhotoImage, cached by (key, w, h)."""
    k = (key, w, h)
    ph = _PHOTOS.get(k)
    if ph is None:
        if len(_PHOTOS) > 400:
            _PHOTOS.clear()
        from PIL import Image
        ph = _PHOTOS[k] = _to_photo(image.resize((max(1, w), max(1, h)), Image.NEAREST))
    return ph


# macOS Tk paints no images at all on a transparent canvas (systemTransparent), whatever the image format, while
# rectangles and text do render (found with tools/mac_image_probe.py on CI). So on macOS the pet window draws its pixel
# art as rectangles: each image becomes merged runs of same-coloured pixels, computed once per image.
RECT_SPRITES = IS_MAC
_RUNS = {}


def pixel_rects(key, image):
    """[(x0, y0, x1, y1, "#rrggbb")] covering the opaque pixels of a PIL RGBA image, in image pixels. Horizontal runs
    of one colour are merged, then identical runs on consecutive rows, so a sprite needs a few hundred rectangles."""
    rects = _RUNS.get(key)
    if rects is not None:
        return rects
    im = image.convert("RGBA")
    px = im.load()
    w, h = im.size
    open_runs, rects = {}, []  # (x0, x1, colour) -> y where the run started
    for y in range(h + 1):
        row = set()
        if y < h:
            x = 0
            while x < w:
                r, g, b, a = px[x, y]
                if a < 128:
                    x += 1
                    continue
                col = f"#{r:02x}{g:02x}{b:02x}"
                x1 = x + 1
                while x1 < w and px[x1, y][3] >= 128 and px[x1, y][:3] == (r, g, b):
                    x1 += 1
                row.add((x, x1, col))
                x = x1
        for run in list(open_runs):
            if run not in row:  # the run ends above this row: close its rectangle
                rects.append((run[0], open_runs.pop(run), run[1], y, run[2]))
        for run in row:
            open_runs.setdefault(run, y)
    if len(_RUNS) > 600:
        _RUNS.clear()
    _RUNS[key] = rects
    return rects


_LOGGED = set()


def log_error(msg):
    """Append a message (once per run) to ~/.aipet/error.log: a windowed app has no console to print to."""
    if msg in _LOGGED:
        return
    _LOGGED.add(msg)
    print(f"[aipet] {msg}", file=sys.stderr)
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        path = os.path.join(HOME_DIR, "error.log")
        if os.path.exists(path) and os.path.getsize(path) > 200_000:
            os.remove(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


MONO_FAMILY = UI_FONT


def mono(size):
    """A console font at the pet's scale (Consolas on Windows, Menlo on macOS)."""
    return (MONO_FAMILY, max(4, int(round(size * SCALE["v"]))))


# ---- pixel-art speech bubbles and glyphs, drawn at sprite resolution so they match the robot
BUBBLE_TAIL = 4  # rows the tail adds below the box
_BUBBLE_INSET = (3, 1, 1)  # stair-stepped round corners: pixels cut from the first three rows (and last three)
_IMG_CACHE = {}
MARKS = {  # drawn at sprite resolution (no enlargement), about 45% of the answer bubble's height
    "ok": ["..........##", ".........###", "........###.", ".##....###..", ".###..###...", "..######....", "...####.....",
           "....##......", ".....#......"],
    "no": ["##.....##", "###...###", ".###.###.", "..#####..", "...###...", "..#####..", ".###.###.", "###...###", "##.....##"],
    "?": ["..####.", ".##..##", ".....##", "....##.", "...##..", "...##..", ".......", "...##..", "...##.."],
}
MARK_COLORS = {"ok": "#22a447", "no": "#e0301e", "?": "#111827"}


def bubble_image(w, h, fill, outline, tail_cx):
    """A w x h speech bubble plus a tail, as one shape: 1 px outline, round corners, and the tail grows out of the box
    (no seam), all at sprite resolution so it scales with the robot."""
    key = ("bubble", w, h, fill, outline, tail_cx)
    if key in _IMG_CACHE:
        return _IMG_CACHE[key]
    from PIL import Image
    H = h + (BUBBLE_TAIL if tail_cx is not None else 0)
    mask = [[False] * w for _ in range(H)]
    for y in range(h):
        inset = _BUBBLE_INSET[y] if y < len(_BUBBLE_INSET) else (
            _BUBBLE_INSET[h - 1 - y] if h - 1 - y < len(_BUBBLE_INSET) else 0)
        for x in range(inset, w - inset):
            mask[y][x] = True
    for r in range(BUBBLE_TAIL if tail_cx is not None else 0):  # 7, 5, 3, 1 px wide
        for x in range(tail_cx - (3 - r), tail_cx + (3 - r) + 1):
            if 0 <= x < w:
                mask[h + r][x] = True
    fill_c, out_c = _rgb(fill) + (255,), _rgb(outline) + (255,)
    im = Image.new("RGBA", (w, H), (0, 0, 0, 0))
    px = im.load()
    for y in range(H):
        for x in range(w):
            if mask[y][x]:
                edge = any(not (0 <= nx < w and 0 <= ny < H and mask[ny][nx])
                           for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
                px[x, y] = out_c if edge else fill_c
    _IMG_CACHE[key] = im
    return im


def mark_image(kind, k=1):
    """A check / cross / question-mark glyph in pixel art, enlarged k times (nearest neighbour)."""
    key = ("mark", kind, k)
    if key not in _IMG_CACHE:
        from PIL import Image
        rows = MARKS[kind]
        im = Image.new("RGBA", (len(rows[0]), len(rows)), (0, 0, 0, 0))
        px = im.load()
        colour = _rgb(MARK_COLORS[kind]) + (255,)
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch == "#":
                    px[x, y] = colour
        _IMG_CACHE[key] = im.resize((im.width * k, im.height * k), Image.NEAREST) if k > 1 else im
    return _IMG_CACHE[key]


def _solid_shadow(im):
    """The pet window is colour-keyed transparent, so semi-transparent pixels would blend with the key colour (a magenta
    halo). Turn the soft shadow into two solid tones."""
    out = im.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            a = px[x, y][3]
            px[x, y] = (0, 0, 0, 0) if a < 40 else ((150, 150, 166, 255) if a < 100 else (118, 118, 136, 255))
    return out


class Pet:
    def __init__(self, app, key):
        self.app = app
        self.key = key
        self.data = {}
        self.acked = False
        self.last_remind = 0.0
        self.born = 0.0  # when this pet starts popping out of the ground (0 = already out)
        self.seed = (sum(map(ord, key)) % 97) / 13.0  # desync animations
        self.usage_extra = USAGE_GUTTER
        self._usage_hover = None
        self._usage_details = {}
        c = self.canvas = tk.Canvas(app.frame, width=px(CANVAS_W + USAGE_GUTTER), height=px(CANVAS_H), bg=TRANSPARENT,
                                    highlightthickness=0, bd=0)
        c.bind("<ButtonPress-1>", app.on_press)
        c.bind("<B1-Motion>", app.on_drag)
        c.bind("<ButtonRelease-1>", lambda e: app.on_release(e, self))
        c.bind("<Button-3>", lambda e: app.on_menu(e, self))
        if IS_MAC:  # macOS Tk: right button is Button-2; ctrl-click is the trackpad equivalent
            c.bind("<Button-2>", lambda e: app.on_menu(e, self))
            c.bind("<Control-Button-1>", lambda e: app.on_menu(e, self))
            c.bind("<Control-ButtonRelease-1>", lambda e: "break")
        c.bind("<Enter>", lambda e: app.show_tip(self))
        c.bind("<Leave>", lambda e: app.hide_tip())
        self._on_bubble = False  # the check/cross/? bubble ("ans" items) opens the details popup when clicked
        c.tag_bind("ans", "<Enter>", lambda e: c.config(cursor="hand2"))
        c.tag_bind("ans", "<Leave>", lambda e: c.config(cursor=""))
        c.tag_bind("ans", "<ButtonPress-1>", lambda e: setattr(self, "_on_bubble", True))
        self._on_badge = None  # a badge's session key, when a badge was pressed (it opens that session's prompt)
        c.tag_bind("badge", "<Enter>", lambda e: c.config(cursor="hand2"))
        c.tag_bind("badge", "<Leave>", lambda e: c.config(cursor=""))
        c.tag_bind("badge", "<ButtonPress-1>", lambda e: setattr(self, "_on_badge", self._badge_under_pointer()))

    def _badge_under_pointer(self):
        c = self.canvas
        for tag in c.gettags("current"):
            if tag.startswith("badge:"):
                return tag[6:] or None
        return None

    def destroy(self):
        self.canvas.destroy()

    def draw(self, t):
        c = self.canvas
        c.delete("all")
        if STYLE["v"] == "cat":
            self._draw_cat(t)
        elif STYLE["v"] == "mole":
            self._draw_mole(t)
        else:
            self._draw_robot(t)
        c.move("all", BADGE_GUTTER, -TOP_TRIM)  # drop the empty top strip
        min_x = self._draw_badges()
        extra = self._draw_usage()
        # compact mode, expanded: the badges stand in a column snapped to the pet's left; widen the canvas to the left
        # for it (the overlay is anchored bottom-right, so the pet itself doesn't move)
        gutter = int(math.ceil(-min_x)) + 1 if min_x is not None and min_x < 0 else 0
        if gutter:
            c.move("all", gutter, 0)
        if gutter != getattr(self, "gutter", 0) or extra != getattr(self, "usage_extra", 0):
            delta = px(CANVAS_W + gutter + extra) - px(CANVAS_W + getattr(self, "gutter", 0) + getattr(self, "usage_extra", 0))
            self.gutter = gutter
            self.usage_extra = extra
            c.config(width=px(CANVAS_W + gutter + extra))
            delta += self.app._sync_usage_padding()
            self.app.shift_for_width(delta)  # update the canvas and shared margin in the same redraw
        self._finish()
        self._update_usage_hover()

    def _draw_usage(self):
        details = usage.badge_details(self.data.get("everyone") or [self.data])
        rows = [(r["agent"], r["window"], r["percent"]) for r in details]
        self._usage_details = {}
        if not rows:
            return USAGE_GUTTER
        c, left = self.canvas, PET_W - 26
        # Align a short column with the lower body, ending above the name tag.
        top = 81 - min(6, len(rows)) * 9
        font = mono(4.5)
        extra = 0
        # Start beside the robot, below its speech bubble. Never cover the name tag.
        for start in range(0, len(rows), 6):
            column = rows[start:start + 6]
            labels = [("CX" if ai == "codex" else "CL") + f" {window} {percent}%" for ai, window, percent in column]
            width = max(self._char_w(font) * len(label) + 6 for label in labels)
            for i, ((ai, window, percent), label) in enumerate(zip(column, labels)):
                colour = "#5b8def" if ai == "codex" else "#d98960"
                y = top + i * 9
                tag = f"usage:{ai}:{window}:{percent}"
                self._usage_details[tag] = details[start + i]
                c.create_rectangle(left, y, left + width, y + 8, fill=T["tag_bg"], outline=colour, width=1, tags=("usage", tag))
                c.create_text(left + 3, y + 4, text=label, font=font, fill=colour, anchor="w", tags=("usage", tag))
            extra = max(extra, int(math.ceil(left + width + 2 - CANVAS_W)))
            left += width + 3
        return max(USAGE_GUTTER, extra)

    def _update_usage_hover(self):
        # Animation recreates canvas items. Track the reading under the pointer
        # instead of item Enter/Leave events, which would flicker every frame.
        c = self.canvas
        if not c.winfo_ismapped():
            self._usage_hover = None
            return
        x, y = c.winfo_pointerx() - c.winfo_rootx(), c.winfo_pointery() - c.winfo_rooty()
        tag = next((tag for item in c.find_overlapping(x, y, x, y) for tag in c.gettags(item)
                    if tag in self._usage_details), None)
        if tag == self._usage_hover:
            return
        previous, self._usage_hover = self._usage_hover, tag
        if tag:
            self.app.show_usage_tip(self, self._usage_details[tag])
        elif previous:
            if 0 <= x < c.winfo_width() and 0 <= y < c.winfo_height():
                self.app.show_tip(self)
            else:
                self.app.hide_tip()

    def _completion_seal(self, x, y):
        self.canvas.create_oval(x - 9, y - 9, x + 9, y + 9, fill="#dcfce7", outline="#15803d", width=1)
        self._mark("ok", x, y)

    def _finish(self):
        """Apply the overall size: everything is drawn at 92x122 and scaled here (line widths too)."""
        s = SCALE["v"]
        if abs(s - 1) < 0.01:
            return
        c = self.canvas
        c.scale("all", 0, 0, s, s)
        for it in c.find_all():
            if c.type(it) in ("line", "oval", "polygon", "arc", "rectangle"):
                try:
                    c.itemconfigure(it, width=max(1.0, float(c.itemcget(it, "width")) * s))
                except tk.TclError:
                    pass

    def _draw_tag(self):
        c = self.canvas
        st = self.data.get("state", "idle")
        col, dark = COLORS.get(st, COLORS["idle"]), DARK.get(st, DARK["idle"])
        # name label: a pixel-art rounded box like the speech bubbles (solid pixels: no colour fringing on the
        # transparent window), with a small "light" in the state colour like the ones on the robot
        tw_, th_ = PET_W - 6, TAG_H
        try:
            s_ = SCALE["v"]
            im = bubble_image(tw_, th_, T["tag_bg"], T["tag_outline"], None)
            self._put(("tag", tw_, th_, T["tag_bg"], T["tag_outline"]), im, int(round(tw_ * s_)), int(round(th_ * s_)),
                      3, PET_H - 2 - TAG_H, "nw")
        except Exception:  # no Pillow: a plain rectangle
            c.create_rectangle(3, PET_H - 2 - TAG_H, PET_W - 3, PET_H - 2, fill=T["tag_bg"], outline=T["tag_outline"], width=1)
        font, sub_font = mono(5.0), mono(4.0)  # console-style text, like the robot's screen
        room, left = PET_W - 14, 7  # inside the tag's rounded border
        everyone = self.data.get("everyone") or []
        entries = [(m.get("raw_title") or m.get("title") or "session", m.get("conv") or "", m.get("state"))
                   for m in (everyone if len(everyone) > 1 else [self.data])]
        two = any(e[1] for e in entries)  # a conversation title to show: the name moves up, the title goes below it
        y_top0 = PET_H - 2 - TAG_H
        y1 = y_top0 + 6.4 if two else PET_H - 2 - TAG_H / 2 + 1.5  # name near the top; one line: a bit low (badges)
        y2 = y_top0 + 13.9  # clear of the tag's bottom border
        sub_fg = "#d1d5db" if T.get("name") == "dark" else "#374151"
        if len(everyone) > 1:  # compact mode: every session scrolls past like a banner, its title right below it
            self._title_banner(entries, font, sub_font, left, room, y1, y2 if two else None, sub_fg)
            return
        name, title, _ = entries[0]  # one pet: never cut, a line too long for the tag scrolls round instead
        self._marquee(name, font, y1, T["tag_fg"], left, room)
        if title:
            self._marquee(title, sub_font, y2, sub_fg, left, room)

    def _marquee(self, text, font, y, fill, left, room):
        """One line of the name tag: centred if it fits, else scrolling round like a ticker (characters drawn one by one
        and only while wholly inside the tag, since a Tk canvas can't clip text)."""
        c = self.canvas
        if self._text_w(font, text) <= room:
            c.create_text(PET_W / 2, y, text=text, fill=fill, font=font)
            return
        gap = self.BANNER_GAP
        loop_text = text + gap
        loop = self._text_w(font, loop_text)
        offset = (time.time() * self.BANNER_PX + self.seed * 37) % loop
        right = left + room
        for k in (0, 1):
            cx = left - offset + k * loop
            for ch in loop_text:
                key = ("cw", font, ch)
                cw = _IMG_CACHE[key] if key in _IMG_CACHE else _IMG_CACHE.setdefault(key, self._text_w(font, ch))
                if cx >= right:
                    break
                if cx >= left and cx + cw <= right and not ch.isspace():
                    c.create_text(cx, y, text=ch, anchor="w", fill=fill, font=font)
                cx += cw

    def _cut(self, font, text, room):
        """text cut to fit room drawing units, with an ellipsis (real font widths, not a character count)."""
        if self._text_w(font, text) <= room:
            return text
        while text and self._text_w(font, text + "\u2026") > room:
            text = text[:-1]
        return text.rstrip() + "\u2026"

    EXPAND, COLLAPSE = "\u25b4", "\u25be"  # compact mode's badges: up to expand, down to collapse
    # compact mode's banner: each title after its state symbol, both in a faded state colour
    BANNER_STATES = {"working": ("\u2743", "#6a9fd8"), "needs_input": ("\u2749", "#e07b74"),
                     "error": ("\u2749", "#e07b74"), "done": ("\u2713", "#6fb88a"), "idle": ("\u273a", "#9aa3ad")}
    BANNER_GAP = "    "

    BANNER_PX = 14  # scroll speed, drawing units per second
    BANNER_TITLE_MAX = 64  # a title is cut (with an ellipsis) to the width of its name or this, whichever is wider

    def _title_banner(self, entries, font, sub_font, left, room, y1, y2, sub_fg):
        """Compact mode: every session's name after its state symbol (working, waiting, done) in a faded state colour,
        and - when y2 is given - its conversation title in small text right below, both scrolling together through
        the name tag. Shown whole and centred when it all fits. Tk canvases can't clip text, so while scrolling each
        character is drawn on its own and only those wholly inside the tag are shown."""
        c, cols = self.canvas, []  # cols: (top text, colour, bottom text, width)
        gap = self._text_w(font, self.BANNER_GAP)
        for name, title, state in entries:
            sym, colour = self.BANNER_STATES.get(state, ("\u2743", "#9aa3ad"))
            top = f"{sym} {name}"
            tw = self._text_w(font, top)
            sub = self._cut(sub_font, title, max(tw, self.BANNER_TITLE_MAX)) if (title and y2 is not None) else ""
            cols.append((top, colour, sub, max(tw, self._text_w(sub_font, sub))))
        total = sum(w for *_, w in cols) + gap * (len(cols) - 1)
        if total <= room:  # it all fits: centred, no scrolling
            x = PET_W / 2 - total / 2
            for top, colour, sub, w in cols:
                c.create_text(x, y1, text=top, anchor="w", fill=colour, font=font)
                if sub:
                    c.create_text(x, y2, text=sub, anchor="w", fill=sub_fg, font=sub_font)
                x += w + gap
            return
        loop = total + gap
        offset = (time.time() * self.BANNER_PX) % loop
        right = left + room
        for k in (0, 1):  # the strip twice, so the start follows the end seamlessly
            x = left - offset + k * loop
            for top, colour, sub, w in cols:
                if x < right and x + w > left:
                    for text, f, y, fill in ((top, font, y1, colour), (sub, sub_font, y2, sub_fg)):
                        cx = x
                        for ch in text:
                            key = ("cw", f, ch)  # per-character widths, measured once
                            cw = _IMG_CACHE[key] if key in _IMG_CACHE else _IMG_CACHE.setdefault(key, self._text_w(f, ch))
                            if cx >= left and cx + cw <= right and not ch.isspace():
                                c.create_text(cx, y, text=ch, anchor="w", fill=fill, font=f)
                            cx += cw
                x += w + gap

    def _draw_badges(self):
        """Where each session runs: one small pixel box per session ("Claude CLI", "Codex WSL VS"...), styled like the
        name tag and sitting like tabs on its top edge, faint red while that session needs you. In compact mode there is
        one per session shown. Badges wrap onto rows above when a row is full. Called after the drawing has been moved
        (see draw())."""
        c = self.canvas
        texts = [BADGE_NAMES.get(b, b) for b in self.data.get("badges", [])]
        if not texts:
            return
        flags = self.data.get("badge_attention") or [self.data.get("state") == "needs_input"] * len(texts)
        keys = self.data.get("badge_keys") or [self.data.get("focus", self.key)] * len(texts)  # whose prompt a click opens
        everyone = self.data.get("everyone") or []
        collapse = False
        if len(everyone) > 1:  # compact mode: one merged badge, or every session's badge plus a collapse badge
            if not self.app.badges_expanded:
                texts = [merge_badges([(m.get("badges") or ["?"])[0] for m in everyone]) + " " + self.EXPAND]
                flags = [any(m.get("state") == "needs_input" for m in everyone)]
                keys = ["__expand"]
            else:  # the collapse badge goes on a row of its own, above all the others (see below)
                collapse = True
        font = fnt(4.5)
        arrow_font = fnt(4.5 * 1.2)  # the expand / collapse arrows, 20% bigger than the badge text
        h, gap, s_ = 11, 2, SCALE["v"]
        left, right = BADGE_GUTTER + 6, BADGE_GUTTER + PET_W - 6

        def split(text):  # (label, arrow): the merged badge ends with the expand arrow, the collapse badge is one
            if text and text[-1] in (self.EXPAND, self.COLLAPSE):
                return text[:-1].rstrip(), text[-1]
            return text, ""

        def width(text):
            label, arrow = split(text)
            dot = 0 if not label else 8  # the arrow-only collapse badge has no dot
            return int(round(4 + dot + self._text_w(font, label) + (2 if label and arrow else 0)
                             + (self._text_w(arrow_font, arrow) if arrow else 0)))
        # expanded compact badges: each session's conversation title on a second, smaller line
        sub_font, sub_h = fnt(4.5 * 0.85), 7
        sub_fg = "#d1d5db" if T.get("name") == "dark" else "#374151"  # almost black grey (light grey on dark tags)
        titles = list(self.data.get("badge_titles") or []) if collapse else []
        titles += [""] * (len(texts) - len(titles))

        def fit(text, room):
            if self._text_w(sub_font, text) <= room:
                return text
            while text and self._text_w(sub_font, text + "\u2026") > room:
                text = text[:-1]
            return text.rstrip() + "\u2026" if text else ""
        col_max = 112  # expanded compact mode: the widest a badge in the side column gets (titles are cut to fit)
        rows, x = [[]], left
        for text, hot, key, sub in zip(texts, flags, keys, titles):
            w = width(text)
            if sub:
                sub = fit(sub, (col_max if collapse else right - left) - 6)
                w = max(w, int(round(6 + self._text_w(sub_font, sub))))
            if rows[-1] and x + w > right:  # no more room on this row: wrap to a new one above
                rows.append([])
                x = left
            rows[-1].append((text, hot, x, w, key, sub))
            x += w + gap
        if collapse:  # always the topmost row
            rows.append([(self.COLLAPSE, False, left, width(self.COLLAPSE), "__collapse", "")])
        placed = []  # (text, hot, x, y, w, height, key, title)
        if collapse:
            # expanded compact mode: one column to the LEFT of the pet, right-aligned against it, from the name tag's
            # bottom upwards (the collapse badge on top); a further column to the left when a column is full
            entries = [e for row in rows for e in row]
            col_right, bottom0 = -2, PET_H - 2 - TOP_TRIM
            bottom, col_w = bottom0, 0
            for text, hot, _, w, key, sub in entries:
                bh = h + (sub_h if sub else 0)
                if bottom - bh < 2 and bottom != bottom0:  # column full: start the next one further left
                    col_right -= col_w + 3
                    bottom, col_w = bottom0, 0
                placed.append((text, hot, col_right - w, bottom - bh, w, bh, key, sub))
                bottom -= bh + gap
                col_w = max(col_w, w)
        else:
            bottom = PET_H - 2 - TAG_H - TOP_TRIM + 4  # the first row overlaps the tag's top border by 4 units
            for row in rows:
                row_h = h + (sub_h if any(e[5] for e in row) else 0)
                for text, hot, bx, w, key, sub in row:
                    bh = h + (sub_h if sub else 0)
                    placed.append((text, hot, bx, bottom - bh, w, bh, key, sub))  # a row's badges share one line
                bottom -= row_h + gap
        for text, hot, bx, y, w, bh, key, sub in placed:
            bg = BADGE_ATTENTION_BG if hot else T["tag_bg"]
            tags = ("badge", "badge:" + (key or ""))
            try:
                im = bubble_image(w, bh, bg, T["tag_outline"], None)
                self._put(("badge", w, bh, bg, T["tag_outline"]), im, int(round(w * s_)), int(round(bh * s_)), bx, y,
                          "nw", tags=tags)
            except Exception:  # no Pillow
                c.create_rectangle(bx, y, bx + w, y + bh, fill=bg, outline=T["tag_outline"], tags=tags)
            if sub:
                c.create_text(bx + 3, y + h + sub_h / 2 - 1.5, text=sub, anchor="w",
                              fill="#374151" if hot else sub_fg, font=sub_font, tags=tags)
            label, arrow = split(text)
            fg = "#111827" if hot else T["tag_fg"]
            tx = bx + 2
            if label:
                dot = COLORS["needs_input"] if text.startswith("+") else badge_dot(text)
                # the dot at the badge's middle; the text a unit higher than its anchor box would put it, because
                # its visual middle sits below the box's middle (room for descenders)
                c.create_rectangle(bx + 3, y + 4, bx + 6, y + 7, fill=dot, outline="", tags=tags)
                c.create_text(bx + 8, y + h / 2 - 0.5, text=label, anchor="w", fill=fg, font=font, tags=tags)
                tx = bx + 8 + self._text_w(font, label) + 2
            if arrow:
                c.create_text(tx, y + h / 2 - 0.5, text=arrow, anchor="w", fill=fg, font=arrow_font, tags=tags)
        return min((p[2] for p in placed), default=None)

    @staticmethod
    def _text_w(font, text):
        """Width of text in drawing units (real font metrics, so the label stays centred at every size)."""
        key = ("font", font)
        if key not in _IMG_CACHE:
            import tkinter.font as tkfont
            _IMG_CACHE[key] = tkfont.Font(family=font[0], size=font[1], weight=font[2] if len(font) > 2 else "normal")
        return _IMG_CACHE[key].measure(text) / SCALE["v"]

    def _mole_head(self, cx, bc, k, st, ph):
        c = self.canvas
        rx, ry = 21 * k, 29 * k
        c.create_oval(cx - rx, bc - ry, cx + rx, bc + ry, fill=MOLE_BODY, outline=MOLE_EDGE, width=2)
        c.create_oval(cx - rx * 0.62, bc - ry * 0.85, cx - rx * 0.2, bc - ry * 0.5, fill=MOLE_LIGHT, outline="")
        ey, ex = bc - 9 * k, 8 * k
        for side in (-1, 1):  # eyes
            x = cx + side * ex
            if st in ("done", "idle"):
                c.create_arc(x - 4 * k, ey - 3 * k, x + 4 * k, ey + 3 * k, start=180, extent=180, style="arc",
                             width=2, outline=INK)
            elif st == "error":
                c.create_line(x - 3 * k, ey - 3 * k, x + 3 * k, ey + 3 * k, width=2, fill=INK)
                c.create_line(x - 3 * k, ey + 3 * k, x + 3 * k, ey - 3 * k, width=2, fill=INK)
            elif st == "working" and (ph % 4) < 0.15:  # blink
                c.create_line(x - 3 * k, ey, x + 3 * k, ey, width=2, fill=INK)
            else:
                r = (4.6 if st == "needs_input" else 3.4) * k
                c.create_oval(x - r, ey - r * 1.25, x + r, ey + r * 1.25, fill=INK, outline="")
                c.create_oval(x - r * 0.15, ey - r * 0.95, x + r * 0.45, ey - r * 0.25, fill="white", outline="")
        wig = math.sin(ph * 10) * 1.2 * k if st == "working" else 0  # nose wiggles while digging
        ny = bc + 5 * k
        c.create_oval(cx - 9 * k + wig, ny - 6 * k, cx + 9 * k + wig, ny + 7 * k, fill=MOLE_NOSE,
                      outline=MOLE_NOSE_EDGE, width=2)
        c.create_oval(cx - 5 * k + wig, ny - 4 * k, cx - 1.5 * k + wig, ny - 0.5 * k, fill="white", outline="")

    def _draw_mole(self, t):
        c = self.canvas
        st = self.data.get("state", "idle")
        col = COLORS.get(st, COLORS["idle"])
        t += self.seed
        cx0, ground = PET_W / 2, 90
        p = max(0.0, min(1.0, (time.time() - self.born) / EMERGE_SECONDS)) if self.born else 1.0
        e = 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2 if p > 0 else 0.0
        emerging = p < 1.0
        heads = HEAD_LAYOUTS[min(3, int(self.data.get("subagents", 0) or 0)) + 1]

        # the ground disc: its rim shows the state colour
        c.create_oval(cx0 - 40, ground - 9, cx0 + 40, ground + 9, fill=MOLE_EDGE, outline=col, width=3)
        top = 99
        for i, (dx, k) in enumerate(heads):
            main = i == len(heads) - 1
            ph = t + i * 1.7
            cx, dy = cx0 + dx, 0.0
            if st == "working":  # popping up and down while digging
                dy = -abs(math.sin(ph * (5 if main else 4.2))) * 8 * k + 2
            elif st == "needs_input":
                dy = (-abs(math.sin(ph * 7)) * 15 if not self.acked else -2 * math.sin(ph * 3)) * (1 if main else 0.6)
            elif st == "error" and not self.acked:
                cx += 2 * math.sin(ph * 25)
            elif st in ("done", "idle"):  # asleep, mostly underground
                dy = 8 + 1.5 * math.sin(ph * 2)
            bc = ground - 34 * k + dy + EMERGE_DEPTH * (1 - e)
            top = min(top, bc - 29 * k)
            self._mole_head(cx, bc, k, st, ph)

        # the front hill hides the buried part of the heads
        c.create_oval(cx0 - 31, ground - 15, cx0 + 31, ground + 1, fill=MOLE_HILL, outline=MOLE_HILL_EDGE, width=2)
        c.create_oval(cx0 - 16, ground - 11, cx0 + 8, ground - 4, fill="#7a4e2b", outline="")
        if not emerging:
            if st == "working":  # flying dirt
                for i in range(5):
                    ph = (t * 1.5 + i * 0.2) % 1
                    x = cx0 + (1 if i % 2 else -1) * (26 + ph * 14)
                    y = ground - 8 - 26 * math.sin(math.pi * ph)
                    r = 2.6 * (1 - ph * 0.5)
                    c.create_oval(x - r, y - r, x + r, y + r, fill=MOLE_EDGE, outline="")
            elif st == "needs_input":
                bx, by = cx0 + 28, max(14.0, top - 4)
                r = 10 + (0 if self.acked else 1.5 * math.sin(t * 8))
                c.create_oval(bx - r, by - r, bx + r, by + r, fill="white", outline=DARK["needs_input"], width=2)
                c.create_text(bx, by, text="!", fill="#dc2626", font=fnt(12, "bold"))
            elif st == "done":
                self._completion_seal(cx0 - 28, top + 8)
            elif st == "idle":  # rising z's
                for i in range(2):
                    ph = (t * 0.6 + i * 0.5) % 1
                    zx, zy, s = cx0 + 22 + ph * 8, 40 - ph * 18, 3 + ph * 3
                    c.create_line(zx - s, zy - s, zx + s, zy - s, zx - s, zy + s, zx + s, zy + s, width=2, fill="#9ca3af")
        self._draw_tag()

    @staticmethod
    def _char_w(font):
        key = ("charw", font)
        if key not in _IMG_CACHE:
            import tkinter.font as tkfont
            _IMG_CACHE[key] = tkfont.Font(family=font[0], size=font[1]).measure("0") / SCALE["v"]
        return _IMG_CACHE[key]

    def _terminal_rows(self, rows=3, cols=13):
        """Scrolling hacker text for the working bubble; each row advances at its own speed."""
        tx = self.__dict__.setdefault("_tx", {"rows": None, "t": 0.0})
        now = time.time()
        if tx["rows"] is None or len(tx["rows"][0]) != cols:
            tx["rows"] = ["".join(random.choice(TERMINAL_CHARS) for _ in range(cols)) for _ in range(rows)]
        if now - tx["t"] > 0.09:
            tx["t"] = now
            for i in range(rows):
                if random.random() < (0.55, 0.85, 0.4)[i % 3]:
                    tx["rows"][i] = tx["rows"][i][1:] + random.choice(TERMINAL_CHARS)
        return tx["rows"]

    def _put(self, key, image, w, h, x, y, anchor="nw", tags=()):
        """Draw a PIL image scaled to w x h (final pixels) at canvas point (x, y). Canvas coordinates are at base size
        and _finish() scales them by SCALE, which is why rectangles use w / SCALE. See RECT_SPRITES."""
        c = self.canvas
        if not RECT_SPRITES:
            c.create_image(x, y, image=sprite_photo(key, image, w, h), anchor=anchor, tags=tags)
            return
        s = SCALE["v"]
        bw, bh = w / s, h / s  # size in base coordinates
        left = x - (bw / 2 if anchor in ("center", "n", "s") else (bw if "e" in anchor else 0))
        top = y - (bh / 2 if anchor in ("center", "e", "w") else (bh if "s" in anchor else 0))
        kx, ky = bw / image.width, bh / image.height
        for x0, y0, x1, y1, col in pixel_rects(key, image):
            c.create_rectangle(left + x0 * kx, top + y0 * ky, left + x1 * kx, top + y1 * ky,
                               fill=col, outline="", width=0, tags=tags)

    def _bubble(self, x1, y1, x2, y2, tail_x, fill, outline, tags=()):
        """Pixel-art speech bubble (see bubble_image) with its top-left at (x1, y1); the tail points down at tail_x."""
        w, h = max(14, int(round(x2 - x1))), max(9, int(round(y2 - y1)))
        tcx = max(5, min(w - 6, int(round(tail_x - x1))))
        im = bubble_image(w, h, fill, outline, tcx)
        s = SCALE["v"]
        self._put(("bubble", w, h, fill, outline, tcx), im, int(round(w * s)), int(round(im.height * s)),
                  int(round(x1)), int(round(y1)), "nw", tags)

    def _mark(self, kind, cx, cy, k=1, tags=()):
        im = mark_image(kind, k)
        s = SCALE["v"]
        self._put(("mark", kind, k), im, int(round(im.width * s)), int(round(im.height * s)),
                  int(round(cx)), int(round(cy)), "center", tags)

    def _draw_robot(self, t):
        spr = load_sprites()
        if not spr:
            return self._draw_mole(t)
        c = self.canvas
        st = self.data.get("state", "idle")
        t += self.seed
        s = SCALE["v"]
        cx0, ground = PET_W / 2, 90
        p = max(0.0, min(1.0, (time.time() - self.born) / EMERGE_SECONDS)) if self.born else 1.0
        e = 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2 if p > 0 else 0.0
        rise = EMERGE_DEPTH * (1 - e)  # new pets rise up from behind the name tag
        emerging = p < 1.0
        shadow = spr["img"]["shadow"]

        # the robot (one per active agent: the main one in front, subagents smaller beside it)
        rm = spr["meta"]["robot"]
        rw0, rh0 = rm["size"]
        members = self.data.get("members")
        if members:  # compact mode: a robot per session, each in its own state; the front one is the focus
            heads = HEAD_LAYOUTS[len(members)]
            states = [(m["state"], self.app.ack.get(m["key"], False)) for m in members[1:]] + [(st, self.acked)]
        else:  # one session: the main robot in front, its subagents smaller beside it, all in the session's state
            heads = HEAD_LAYOUTS[min(3, int(self.data.get("subagents", 0) or 0)) + 1]
            states = [(st, self.acked)] * len(heads)
        top_main = ground
        flash = bool(self.data.get("auto_flash")) and st not in ("needs_input", "error")
        # a subagent or another session just finished: the done check briefly takes over the working bubble
        done_flash = st == "working" and time.time() < self.app.done_flash.get(self.data.get("focus", self.key), 0)
        for i, (dx, k) in enumerate(heads):
            main = i == len(heads) - 1
            hst, hacked = states[i]
            ph = t + i * 1.7
            cx, dy, squash = cx0 + dx, 0.0, 1.0
            face = STATE_FACE.get(hst, "sleep")
            if main and flash:  # just auto-approved something: a calm, content robot with green lights
                hst, face = "auto", "joy"
                dy = -abs(math.sin(ph * 2.5)) * 1.5
            elif hst == "working":
                dy = -abs(math.sin(ph * (5 if main else 4.2))) * 3 * k
                frame = int(ph * 8) % TALK_FRAMES  # the wave travels along the mouth (one loop = 0.5 s, 2 Hz)
                # Blink on a cosine at BLINK_RATIO x the mouth's frequency: a short blink near each peak. The two
                # rhythms drift apart and line up again now and then.
                blink = math.cos(2 * math.pi * BLINK_RATIO * (8 / TALK_FRAMES) * ph) > BLINK_THRESHOLD
                face = f"talkblink{frame}" if blink else f"talk{frame}"
                if main and done_flash:
                    face = "joy"
            elif hst == "needs_input":  # hops while it waits for you
                dy = (-abs(math.sin(ph * 7)) * 8 if not hacked else -1.5 * math.sin(ph * 3)) * (1 if main else 0.6)
                squash = 1.0 if dy < -1.2 else 0.94
            elif hst == "error" and not hacked:
                cx += 2 * math.sin(ph * 25)
            elif hst == "idle":
                squash = 1 + 0.025 * math.sin(ph * 2)  # slow breathing
            w, h = int(round(rw0 * k * s)), int(round(rh0 * k * squash * s))
            sw = int(round(rw0 * k * 1.1 * s))
            self._put("shadow", shadow, sw, max(2, int(sw * 0.25)), cx, ground + 2, "center")
            im = robot_image(face, light_cycle(hst, ph))
            self._put(("robot", face, light_cycle(hst, ph)), im, w, h, cx, ground + dy + rise, "s", tags=("hit",))
            if main:
                top_main = ground + dy - rh0 * k * squash

        if not emerging:
            bx1, bx2 = cx0 - 34, cx0 + 36
            by1 = 15.0  # the top of the drawing (the strip above is trimmed off: badges live in a left column)
            by2 = 36.0 if st == "needs_input" else min(top_main + 2, 40.0)  # the hop must not squash the bubble
            tail = cx0 - 6  # off the antenna
            if flash:  # a small pale-green "✓ auto" bubble: approved by the whitelist, nothing to do
                top = by2 - 19
                self._bubble(cx0 - 26, top, cx0 + 16, by2, tail, "#f0fdf4", "#4b8f63")
                y = (top + by2) / 2
                self._mark("ok", cx0 - 15, y)
                c.create_text(cx0 + 3, y, text="auto", fill="#4b8f63", font=fnt(5))
            elif done_flash:
                self._completion_seal(cx0 - 27, top_main + 8)
            elif st == "working":  # hacker-screen bubble
                self._bubble(bx1, by1, bx2, by2, tail, "#0b1220", "#34d399")
                font = mono(5)
                cw = self._char_w(font)  # real width of one character, in drawing units
                rows = self._terminal_rows(cols=max(6, int((bx2 - bx1 - 12) / cw)))
                for i, row in enumerate(rows):
                    y = by1 + 6 + i * (by2 - by1 - 8) / 2.6
                    c.create_text(bx1 + 5, y, text=row[:-1], anchor="w", fill="#22c55e", font=font)
                    c.create_text(bx1 + 5 + cw * (len(row) - 1), y, text=row[-1], anchor="w", fill="#d1fae5", font=font)
            elif st == "done":
                # A completion seal, away from the interactive speech bubble.
                x, y = cx0 - 27, top_main + 8
                self._completion_seal(x, y)
            elif st == "error":
                top = by2 - 19  # a small bubble, centred on its tail
                self._bubble(cx0 - 22, top, cx0 + 10, by2, tail, "#fafafa", "#111827")
                bob = 1 if int(t * 3) % 2 else 0  # a one-pixel bob
                self._mark("ok" if st == "done" else "no", cx0 - 6, (top + by2) / 2 - bob)
            elif st == "needs_input":  # the answer bubble: check / cross / question mark (click it for the popup)
                self._bubble(bx1 + 4, by1, bx2 - 6, by2, tail, "#fafafa", "#111827", tags=("ans",))
                y, pulse = (by1 + by2) / 2, int(t * 2) % 3
                if (self.data.get("request") or {}).get("kind") == "question":  # a question: just a bobbing "?"
                    self._mark("?", cx0 - 1, y - (1 if int(t * 3) % 2 else 0), tags=("ans",))
                else:
                    for i, (x, kind) in enumerate(((cx0 - 16, "ok"), (cx0 - 1, "no"), (cx0 + 14, "?"))):
                        self._mark(kind, x, y - (1 if i == pulse else 0), tags=("ans",))
            else:  # idle: sleeping, rising z's
                for i in range(3):
                    ph = (t * 0.5 + i / 3) % 1
                    zx, zy = cx0 + 14 + ph * 14, top_main + 4 - ph * 22
                    c.create_text(zx, zy, text="z", fill="#9ca3af", font=fnt(8 + 5 * ph, "bold"))
        self._draw_tag()

    def _draw_cat(self, t):
        c = self.canvas
        st = self.data.get("state", "idle")
        col, dark = COLORS.get(st, COLORS["idle"]), DARK.get(st, DARK["idle"])
        t += self.seed
        cx, base, rx, ry, dy = PET_W / 2, 66, 26, 22, 0.0

        # Diglett-style entrance: rise out of a dirt mound with a little overshoot.
        p = max(0.0, min(1.0, (time.time() - self.born) / EMERGE_SECONDS)) if self.born else 1.0
        e = 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2 if p > 0 else 0.0
        emerging = p < 1.0

        if st == "working":
            dy = -3 * math.sin(t * 6)
        elif st == "needs_input":
            dy = -abs(math.sin(t * 7)) * 12 if not self.acked else -2 * math.sin(t * 3)
        elif st == "error" and not self.acked:
            cx += 2 * math.sin(t * 25)
        else:
            ry *= 1 + 0.04 * math.sin(t * 2)  # breathing

        by = base + dy + EMERGE_DEPTH * (1 - e)
        ground = base + 26
        sw = rx * (1 + dy / 40)
        c.create_oval(cx - sw, ground - 3, cx + sw, ground + 4, fill="#2b2b2b", outline="")

        top = by - ry
        for side in (-1, 1):  # ears
            c.create_polygon(cx + side * 18, top + 7, cx + side * 13, top - 9, cx + side * 4, top + 2,
                             fill=col, outline=dark, width=2)
        c.create_oval(cx - rx, by - ry, cx + rx, by + 22, fill=col, outline=dark, width=2)

        ey = by - 3
        for side in (-1, 1):  # eyes
            ex = cx + side * 9
            if st in ("done", "idle"):
                c.create_arc(ex - 5, ey - 3, ex + 5, ey + 4, start=180, extent=180, style="arc", width=2, outline=INK)
            elif st == "error":
                c.create_line(ex - 4, ey - 4, ex + 4, ey + 4, width=2, fill=INK)
                c.create_line(ex - 4, ey + 4, ex + 4, ey - 4, width=2, fill=INK)
            elif st == "working" and (t % 4) < 0.15:  # blink
                c.create_line(ex - 4, ey, ex + 4, ey, width=2, fill=INK)
            else:
                r = 6 if st == "needs_input" else 5
                px = 2 * math.sin(t * 1.7) if st == "working" else 0
                c.create_oval(ex - r, ey - r, ex + r, ey + r, fill="white", outline=INK)
                c.create_oval(ex + px - 2.5, ey - 2.5, ex + px + 2.5, ey + 2.5, fill=INK, outline="")

        my = by + 9  # mouth
        if st == "needs_input":
            c.create_oval(cx - 3, my - 3, cx + 3, my + 3, fill=INK, outline="")
        elif st == "done":
            c.create_arc(cx - 6, my - 6, cx + 6, my + 3, start=200, extent=140, style="arc", width=2, outline=INK)
            for side in (-1, 1):
                c.create_oval(cx + side * 17 - 4, my - 4, cx + side * 17 + 4, my + 1, fill="#f9a8d4", outline="")
        elif st == "error":
            c.create_line(cx - 6, my, cx - 2, my - 2, cx + 2, my, cx + 6, my - 2, width=2, fill=INK)
        else:
            c.create_line(cx - 4, my, cx + 4, my, width=2, fill=INK)

        if emerging:  # dirt mound hides the part of the body still underground
            mw = 34 * (1 - 0.5 * p)
            c.create_oval(cx - mw, ground - 12, cx + mw, ground + 3, fill=MOUND, outline=MOUND_DARK, width=2)
            c.create_oval(cx - mw * 0.55, ground - 8, cx + mw * 0.55, ground - 1, fill=MOUND_DARK, outline="")

        if emerging:
            pass
        elif st == "working":  # orbiting dots
            for i in range(3):
                a = t * 3 + i * 2.094
                x, y = cx + 20 * math.cos(a), top - 14 + 5 * math.sin(a)
                c.create_oval(x - 3, y - 3, x + 3, y + 3, fill="white", outline=dark)
        elif st == "needs_input":
            bx, byy = cx + 24, top - 14
            pulse = 0 if self.acked else 1.5 * math.sin(t * 8)
            r = 10 + pulse
            c.create_oval(bx - r, byy - r, bx + r, byy + r, fill="white", outline=dark, width=2)
            c.create_text(bx, byy, text="!", fill="#dc2626", font=fnt(12, "bold"))
        elif st == "done":
            self._completion_seal(cx - 28, top + 8)

        self._draw_tag()


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
        vscode_open_folders(report=rep)
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
def work_area(x, y, widget):
    """(left, top, right, bottom) of the monitor nearest to screen point (x, y) (the whole monitor, taskbar included,
    so a pet parked over the taskbar stays there).
    Windows asks the OS (monitors left of / above the main one have negative coordinates). macOS returns None: Tk
    gives no per-monitor geometry there, so callers don't clamp and trust positions that came from a drag.
    Elsewhere (X11) the root window spans every monitor."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
            u = ctypes.windll.user32
            u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            u.MonitorFromPoint.restype = wintypes.HMONITOR
            u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
            mon = u.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)  # MONITOR_DEFAULTTONEAREST
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if mon and u.GetMonitorInfoW(mon, ctypes.byref(mi)):
                r = mi.rcMonitor
                return r.left, r.top, r.right, r.bottom
        except Exception:
            pass
    if IS_MAC:
        return mac_screen_at(x, y)
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


_MAC_SCREENS = {"t": 0.0, "v": []}


def mac_screen_at(x, y):
    """macOS: (left, top, right, bottom) in Tk coordinates of the visible part of the screen holding (x, y) (without
    the menu bar and the Dock, which covers windows), else the nearest one; None if the screens can't be read
    (callers then don't clamp). Read from NSScreen (cached for 3 s)."""
    now = time.time()
    if now - _MAC_SCREENS["t"] > 3:
        try:
            import mac_statusbar
            _MAC_SCREENS["v"] = mac_statusbar.screen_rects()
        except Exception as e:
            _MAC_SCREENS["v"] = []
            log_error(f"mac screens: {e!r}")
        _MAC_SCREENS["t"] = now
    rects = _MAC_SCREENS["v"]
    if not rects:
        return None

    def dist(r):
        dx = max(r[0] - x, 0, x - r[2])
        dy = max(r[1] - y, 0, y - r[3])
        return dx * dx + dy * dy
    return min(rects, key=dist)


def fit_on_screen(x, y, w, h, ref, widget, margin=0):
    """Clamp a w x h window at (x, y) into the monitor holding screen point ref (on macOS: unchanged)."""
    r = work_area(ref[0], ref[1], widget)
    if not r:
        return int(x), int(y)
    left, top, right, bottom = r
    return (int(max(left, min(x, right - w - margin))), int(max(top, min(y, bottom - h - margin))))


def geo(x, y):
    """Tk geometry offset that also works for negative coordinates (monitors left of / above the main one)."""
    return f"+{int(x)}+{int(y)}"


# --------------------------------------------------------------------------- VS Code: which open window holds a folder
VSCODE_FLAVOURS = ("Code", "Code - Insiders", "VSCodium", "Cursor")


def vscode_user_dirs():
    if os.name == "nt":
        base = os.environ.get("APPDATA", "")
    elif IS_MAC:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return [os.path.join(base, f) for f in VSCODE_FLAVOURS if base and os.path.isdir(os.path.join(base, f))]


def _uri_parts(uri):
    """file:///c%3A/x -> ("", "c:/x"); vscode-remote://wsl%2Bubuntu/home/x -> ("wsl+ubuntu", "/home/x")."""
    import urllib.parse
    u = urllib.parse.urlparse(uri)
    path = urllib.parse.unquote(u.path)
    if u.scheme == "file":
        if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return "", path
    if u.scheme == "vscode-remote":
        return urllib.parse.unquote(u.netloc).lower(), path
    return None, None


def _backup_uris(b):
    """(uri, is_workspace_file) from VS Code's backup-workspaces record, in any of its formats."""
    if not isinstance(b, dict):
        return []
    uris = [(x.get("folderUri"), False) for x in b.get("folders") or [] if isinstance(x, dict)]
    uris += [(x, False) for x in b.get("folderURIWorkspaces") or [] if isinstance(x, str)]  # older format
    for key in ("workspaces", "rootURIWorkspaces"):  # current format / older one
        for x in b.get(key) or []:
            if isinstance(x, dict):
                ws = x.get("workspace") if isinstance(x.get("workspace"), dict) else x
                uris.append((ws.get("configURIPath") or ws.get("configPath"), True))
    return uris


def vscode_open_folders(user_dirs=None, report=None):
    """Folders and .code-workspace files open in VS Code windows: [(authority, path, is_workspace_file)].

    Sources, freshest first: globalStorage/storage.json "backupWorkspaces" (current VS Code keeps the open windows
    there, updated as they open and close), Backups/workspaces.json (older VS Code), then the window state saved in
    storage.json (only written when VS Code quits or a window closes, so it can be stale: macOS keeps VS Code running
    with its windows closed). report, if a list, receives one line per source (counts only, for diagnostics)."""
    found = []
    for d in user_dirs if user_dirs is not None else vscode_user_dirs():
        uris, storage = [], {}
        try:
            with open(os.path.join(d, "User", "globalStorage", "storage.json"), encoding="utf-8") as f:
                storage = json.load(f) or {}
        except (OSError, ValueError):
            pass
        live = _backup_uris(storage.get("backupWorkspaces"))
        legacy_file = []
        try:
            with open(os.path.join(d, "Backups", "workspaces.json"), encoding="utf-8") as f:
                legacy_file = _backup_uris(json.load(f))
        except (OSError, ValueError):
            pass
        uris = live + legacy_file
        saved = []
        try:
            ws = storage.get("windowsState") or {}
            for w in [ws.get("lastActiveWindow") or {}] + list(ws.get("openedWindows") or []):
                if not isinstance(w, dict):
                    continue
                if w.get("folder"):
                    saved.append((w["folder"], False))
                elif isinstance(w.get("workspace"), dict) and w["workspace"].get("configPath"):
                    saved.append((w["workspace"]["configPath"], True))
        except AttributeError:
            pass
        if not uris:
            uris = saved
        if isinstance(report, list):
            report.append(f"{os.path.basename(d)}: backupWorkspaces={len(live)} workspaces.json={len(legacy_file)} "
                          f"windowsState={len(saved)}")
        for uri, is_ws in uris:
            if isinstance(uri, str):
                auth, path = _uri_parts(uri)
                if path:
                    found.append((auth, path, is_ws))
    return found


def _workspace_roots(ws_path):
    """Folder paths listed in a local .code-workspace file (relative ones resolved against the file)."""
    try:
        with open(ws_path, encoding="utf-8") as f:
            text = f.read()
        import re
        text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda m: m.group(1) or "", text,
                      flags=re.S)  # .code-workspace allows // and /* */ comments (kept inside strings)
        text = re.sub(r",(\s*[}\]])", r"\1", text)  # ... and trailing commas
        data = json.loads(text)
    except (OSError, ValueError):
        return []
    roots = []
    for fo in data.get("folders") or []:
        p = fo.get("path") if isinstance(fo, dict) else None
        if p:
            roots.append(os.path.normpath(os.path.join(os.path.dirname(ws_path), p)))
    return roots


def vscode_target(cwd, env="", distro="", user_dirs=None):
    """What to pass to `code` so it raises the window that already holds cwd: the deepest open folder that is cwd or
    one of its parents (so a session in a subfolder of an open workspace doesn't open a new window), or the
    .code-workspace file whose folders contain it. Falls back to cwd itself. Returns (args, path)."""
    wsl = env == "wsl"
    want_auth = f"wsl+{(distro or 'ubuntu').lower()}" if wsl else ""
    if wsl:
        norm = lambda p: p.rstrip("/") or "/"  # noqa: E731
    elif IS_MAC or sys.platform == "darwin":
        # macOS: the default file system ignores case, and folders are often reached through symlinks (/var ->
        # /private/var, a renamed or linked ~/Code); compare resolved, case-folded paths
        norm = lambda p: os.path.normpath(os.path.realpath(p)).casefold()  # noqa: E731
    else:
        norm = lambda p: os.path.normcase(os.path.normpath(p))  # noqa: E731
    target = norm(cwd)

    def inside(root):
        r = norm(root)
        sep = "/" if wsl else os.sep
        return target == r or target.startswith(r.rstrip(sep) + sep)
    best = None  # (depth, path, is_workspace_file)
    for auth, path, is_ws in vscode_open_folders(user_dirs):
        if (auth or "") != want_auth:
            continue
        if is_ws:
            if wsl:
                continue
            roots = [r for r in _workspace_roots(path) if inside(r)]
            if roots:
                depth = max(len(norm(r)) for r in roots)
                if not best or depth > best[0]:
                    best = (depth, path, True)
        elif inside(path):
            if not best or len(norm(path)) > best[0]:
                best = (len(norm(path)), path, False)
    path = best[1] if best else cwd
    return (["--remote", f"wsl+{distro or 'Ubuntu'}", path] if wsl else [path]), path


def vscode_conversation_uri(d, code_path=""):
    """vscode://anthropic.claude-code/open?session=<id>: the Claude Code extension (v2.1.72+) opens that conversation's
    tab. Only for sessions that run in the extension (entry point claude-vscode), not the CLI in VS Code's terminal,
    where it would open a second copy of the conversation in the extension."""
    sid = d.get("session_id") or ""
    if not sid or sid == "unknown" or "vscode" not in (d.get("entry") or "").lower():
        return None
    import urllib.parse
    scheme = "vscode-insiders" if "insiders" in (code_path or "").lower() else "vscode"
    return f"{scheme}://anthropic.claude-code/open?session={urllib.parse.quote(sid, safe='')}"


def open_uri(uri):
    try:
        if os.name == "nt":
            os.startfile(uri)
        else:
            subprocess.Popen(["open" if IS_MAC else "xdg-open", uri])
    except (AttributeError, OSError) as e:
        log_error(f"couldn't open {uri.split('?')[0]}: {e!r}")


class ClickThrough:
    """Lets mouse clicks pass through the pet window to whatever is behind it, or not. Windows: the WS_EX_TRANSPARENT
    style on the (already layered) window. macOS: NSWindow.ignoresMouseEvents through the Objective-C runtime (no
    extra packages). Elsewhere: does nothing."""

    def __init__(self, root):
        self.root = root
        self._win = None

    def set(self, through):
        if os.name == "nt":
            self._set_windows(through)
        elif IS_MAC:
            self._set_mac(through)

    def _set_windows(self, through):
        import ctypes
        u = ctypes.windll.user32
        hwnd = int(self.root.wm_frame(), 16)
        u.GetWindowLongW.restype = ctypes.c_long
        ex = u.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        new = (ex | 0x20 | 0x80000) if through else (ex & ~0x20)  # WS_EX_TRANSPARENT (+ WS_EX_LAYERED)
        if new != ex:
            u.SetWindowLongW(hwnd, -20, ctypes.c_long(new))

    def _objc(self):
        import ctypes
        import ctypes.util
        if not hasattr(self, "_lib"):
            self._lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
            self._lib.objc_getClass.restype = ctypes.c_void_p
            self._lib.sel_registerName.restype = ctypes.c_void_p
        return self._lib

    def _send(self, obj, sel, *args, restype=None, argtypes=()):
        import ctypes
        lib = self._objc()
        f = lib.objc_msgSend
        f.restype = restype
        f.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + list(argtypes)
        return f(obj, lib.sel_registerName(sel.encode()), *args)

    def _set_mac(self, through):
        import ctypes
        if not self._win:
            app = self._send(self._objc().objc_getClass(b"NSApplication"), "sharedApplication", restype=ctypes.c_void_p)
            wins = self._send(app, "windows", restype=ctypes.c_void_p)
            n = self._send(wins, "count", restype=ctypes.c_ulong)
            title = self.root.title()
            for i in range(n):
                w = self._send(wins, "objectAtIndex:", i, restype=ctypes.c_void_p, argtypes=[ctypes.c_ulong])
                t = self._send(w, "title", restype=ctypes.c_void_p)
                name = self._send(t, "UTF8String", restype=ctypes.c_char_p) if t else None
                if name and name.decode("utf-8", "replace") == title:
                    self._win = w
                    break
            if not self._win:
                raise RuntimeError("pet window not found")
        self._send(self._win, "setIgnoresMouseEvents:", bool(through), argtypes=[ctypes.c_bool])


# --------------------------------------------------------------------------- window focus (Windows)
def _user32():
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.IsWindow.argtypes = u.IsIconic.argtypes = u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    return u


def _go_to_desktop_of(hwnd):
    """Windows 10/11 virtual desktops: switch to the desktop the window is on. SetForegroundWindow alone often
    leaves you where you are when the window lives on another desktop (the pet is on every desktop, so this happens)."""
    try:
        from pyvda import AppView, VirtualDesktop
        view = AppView(hwnd=int(hwnd))
        if view.is_pinned():
            return
        target = view.desktop
        if target.number != VirtualDesktop.current().number:
            target.go()
            time.sleep(0.15)  # let the switch animation hand over before activating the window
    except Exception:
        pass  # pyvda missing or this Windows build unsupported: focusing still works on the current desktop


def focus_hwnd(hwnd):
    """Bring a window to the front, on whatever virtual desktop it is. True if it really came to the front."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u, k32 = _user32(), ctypes.windll.kernel32
        h = int(hwnd)
        if not u.IsWindow(h):
            return False
        _go_to_desktop_of(h)
        if u.IsIconic(h):
            u.ShowWindow(h, 9)  # SW_RESTORE
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        u.BringWindowToTop.argtypes = [wintypes.HWND]

        def front():
            return u.GetForegroundWindow() == h

        u.SetForegroundWindow(h)
        if not front():
            # Windows only lets the foreground app hand the foreground on. Borrow it: attach to the thread that
            # owns the current foreground window for the call, then detach again.
            mine = k32.GetCurrentThreadId()
            theirs = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
            attached = bool(theirs and theirs != mine and u.AttachThreadInput(mine, theirs, True))
            try:
                u.BringWindowToTop(h)
                u.SetForegroundWindow(h)
            finally:
                if attached:
                    u.AttachThreadInput(mine, theirs, False)
        if not front():  # last resort: a tap of ALT unlocks SetForegroundWindow
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x12, 0, 2, 0)
            u.SetForegroundWindow(h)
        if not front():
            log_error(f"go to window: couldn't bring {h:#x} to the front")
        return front()
    except Exception as e:
        log_error(f"go to window: {e!r}")
        return False


def terminal_windows():
    """Visible Windows Terminal windows as [(hwnd, title)], frontmost first."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    u, out = _user32(), []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if u.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(128)
            u.GetClassNameW(hwnd, cls, 128)
            if cls.value == "CASCADIA_HOSTING_WINDOW_CLASS":
                title = ctypes.create_unicode_buffer(512)
                u.GetWindowTextW(hwnd, title, 512)
                out.append((int(hwnd), title.value))
        return True

    u.EnumWindows(visit, 0)
    return out


def claude_app_windows(exe_name="claude.exe"):
    """Visible top-level windows of a desktop app (the Claude app: claude.exe; the Codex app: codex.exe), frontmost
    first. The CLIs of the same names own no window (their terminal does), so they never show up here."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    u, k32, out = _user32(), ctypes.windll.kernel32, []
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    names = {}

    def exe(pid):
        if pid not in names:
            names[pid] = ""
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                buf, n = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                    names[pid] = os.path.basename(buf.value).lower()
                k32.CloseHandle(h)
        return names[pid]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if u.IsWindowVisible(hwnd) and not u.GetWindow(hwnd, 4) and u.GetWindowTextLengthW(hwnd) > 0:
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if exe(pid.value) == exe_name:
                out.append(int(hwnd))
        return True

    u.EnumWindows(visit, 0)
    return out


def _folder(d):
    return os.path.basename(str(d.get("cwd") or "").replace("\\", "/").rstrip("/")).lower()


def terminal_agent(title):
    """Which agent a terminal title looks like: "claude" (Claude Code puts ✳ or a braille spinner in front of its
    title), "codex", or "" when it doesn't say."""
    t = (title or "").lstrip()
    low = t.lower()
    if "codex" in low:
        return "codex"
    if "claude" in low or (t and (t[0] in "\u2733\u2736\u273b\u273d\u2722" or 0x2800 <= ord(t[0]) <= 0x28ff)):
        return "claude"
    return ""


def focus_wsl_terminal(d, others=()):
    """Best effort for sessions without a recorded window (WSL hooks can't see Windows process ids). Never picks a
    window another session recorded as its own (a Codex terminal, say), and keeps Claude and Codex apart: a terminal
    whose title says it runs the other agent is skipped, one that says it runs this agent is preferred. In order:
    a terminal whose title mentions the distro or project folder; for sessions run by the desktop app, that app's
    window; a terminal of this agent, else one that doesn't say; the agent's desktop app."""
    agent = "codex" if d.get("agent") == "codex" else "claude"
    if agent == "claude" and ("desktop" in (d.get("entry") or "").lower() or d.get("app") == "cowork"):
        # Run by the Claude desktop app (its Code tab, here in WSL): that app's window, always. Not a terminal that
        # happens to mention the distro or folder, and not "claimed": one app window hosts all of its sessions, so
        # a Cowork session having recorded it doesn't make it someone else's.
        app = claude_app_windows("claude.exe")
        if app:
            return focus_hwnd(app[0])
    claimed = {int(o["hwnd"]) for o in others if o.get("hwnd")}
    folder = _folder(d)
    needles = [n.lower() for n in (d.get("distro"), folder, d.get("raw_title")) if n]
    foreign = {f for f in (_folder(o) for o in others) if f and f != folder}
    wins = [(h, t, terminal_agent(t)) for h, t in terminal_windows() if h not in claimed]
    wins = [w for w in wins if w[2] in ("", agent)]  # never the other agent's terminal
    named = [w for w in wins if any(n in w[1].lower() for n in needles)]
    if named:  # this agent's own terminal first, then one that doesn't say
        return focus_hwnd(sorted(named, key=lambda w: w[2] != agent)[0][0])
    wins = [w for w in wins if not any(f in w[1].lower() for f in foreign)]
    app = [h for h in claude_app_windows(agent + ".exe") if h not in claimed]
    if app and (agent == "claude" and "desktop" in (d.get("entry") or "").lower() or not wins):
        return focus_hwnd(app[0])
    if wins:
        return focus_hwnd(sorted(wins, key=lambda w: w[2] != agent)[0][0])
    return bool(app) and focus_hwnd(app[0])


# --------------------------------------------------------------------------- app
class Detail:
    """A styled card, centred on the screen, with the full context of a session that needs the user. The robot
    beside it asks with a "?" and reacts when you answer."""

    ACCENT = COLORS["needs_input"]

    def __init__(self, app, key, item):
        self.app, self.key, self._shown, self.sent = app, key, None, None
        self.pinned = item.get("state") != "needs_input"  # opened from the menu: not a prompt that closes when answered
        self._after, self._t0, self._moved, self._size = None, time.time(), False, None
        bg, fg, msg_fg, muted = T["bubble_bg"], T["bubble_fg"], T["bubble_msg"], T["bubble_muted"]
        w = self.win = MacOverlayWindow(app.root) if IS_MAC else tk.Toplevel(app.root)
        w.withdraw()
        w.title(f"AIPet permission — {key}")
        if IS_MAC:
            w.attributes("-alpha", 0.0)
        w.overrideredirect(True)  # our own frame: accent border, draggable header, close button
        w.attributes("-topmost", True)
        w.configure(bg=self.ACCENT)
        w.bind("<Escape>", lambda e: self.close())
        card = tk.Frame(w, bg=bg)
        card.pack(padx=2, pady=2)
        tk.Frame(card, bg=self.ACCENT, height=5).pack(fill="x")
        cols = tk.Frame(card, bg=bg)
        cols.pack(fill="both", padx=16, pady=(12, 14))

        spr = load_sprites()
        self.robot = None
        if spr:  # left column: the robot
            self.robot = tk.Canvas(cols, width=124, height=176, bg=bg, highlightthickness=0, bd=0)
            self.robot.pack(side="left", anchor="n", padx=(0, 16))
        right = tk.Frame(cols, bg=bg, width=430)
        right.pack(side="left", fill="both", expand=True, anchor="n")

        top = tk.Frame(right, bg=bg)
        top.pack(fill="x")
        self.head = tk.Label(top, bg=bg, fg=fg, anchor="w", font=(UI_FONT, 15, "bold"))
        self.head.pack(side="left", fill="x", expand=True)
        close = tk.Label(top, text="\u2715", bg=bg, fg=muted, cursor="hand2", font=(UI_FONT, 12), padx=4)
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        self.status = tk.Label(right, text="needs your permission", bg=bg, fg=self.ACCENT, anchor="w",
                               font=(UI_FONT, 9, "bold"))
        self.status.pack(fill="x")
        self.where = tk.Label(right, bg=bg, fg=muted, anchor="w", justify="left", wraplength=430, font=(UI_FONT, 8))
        self.where.pack(fill="x", pady=(2, 0))
        tk.Frame(right, bg=T["border"], height=1).pack(fill="x", pady=8)
        for widget in (top, self.head):  # drag the card by its header
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        self.body = tk.Frame(right, bg=bg)
        self.body.pack(fill="x")
        self.ask = tk.Label(self.body, bg=bg, fg=fg, anchor="w", justify="left", wraplength=430, font=(UI_FONT, 11, "bold"))
        self.desc = tk.Label(self.body, bg=bg, fg=msg_fg, anchor="w", justify="left", wraplength=430, font=(UI_FONT, 10))
        self.code = tk.Text(self.body, height=1, width=60, wrap="word", bg="#0b1220", fg="#d1fae5", relief="flat",
                            borderwidth=0, highlightthickness=0, padx=10, pady=8, font=("Consolas", 9), state="disabled",
                            insertbackground="#d1fae5")
        wait_row = tk.Frame(right, bg=bg)
        wait_row.pack(fill="x", pady=(8, 0))
        self.wait = tk.Label(wait_row, bg=bg, fg=muted, anchor="w", font=(UI_FONT, 9))
        self.wait.pack(side="left")
        self.golink = tk.Label(wait_row, text="Go to window ›", bg=bg, fg=self.ACCENT, cursor="hand2",
                               font=(UI_FONT, 9, "underline"))
        self.golink.bind("<Button-1>", lambda e: self.go())
        self.note = tk.Label(right, bg=bg, fg=muted, anchor="w", justify="left", wraplength=430, font=(UI_FONT, 8))
        self.note.pack(fill="x", pady=(2, 8))
        # Escape hatch for a prompt that is gone (its agent died, it was settled elsewhere) but still shows here
        self.clearlink = tk.Label(right, text="Not asking anymore? Clear this prompt", bg=bg, fg=muted, cursor="hand2",
                                  anchor="w", font=(UI_FONT, 8, "underline"))
        self.clearlink.bind("<Button-1>", lambda e: self.clear_prompt())
        # Questions (Claude's AskUserQuestion): one block per question, options as toggles, plus an own-answer field
        self.qframe = tk.Frame(self.body, bg=bg)
        self._qid, self._qvars = None, []
        self.q_row = tk.Frame(right, bg=bg)
        self.btn_send = tk.Button(self.q_row, text="Send answer", command=self.send_answers, **button_style("primary"))
        self.btn_send.pack(side="right", ipady=2)
        self.answer_row = tk.Frame(right, bg=bg)
        self.btn_deny = tk.Button(self.answer_row, text="Deny", width=12, bg=bg, fg=themed_color("#dc2626"),
                                  activebackground=T["btn_active"], activeforeground=themed_color("#b91c1c"),
                                  relief="flat", bd=0, highlightthickness=1, highlightbackground=themed_color("#dc2626"),
                                  cursor="hand2",
                                  font=(UI_FONT, 10, "bold"), command=lambda: self.answer("deny"))
        dark = T.get("name") == "dark"  # dark theme: black text on a brighter green
        allow_fg, allow_bg, allow_active = ("#111827", "#22c55e", "#16a34a") if dark else ("white", "#16a34a", "#15803d")
        self.btn_allow = tk.Button(self.answer_row, text="Allow once", width=14, bg=allow_bg, fg=allow_fg,
                                   activebackground=allow_active, activeforeground=allow_fg, relief="flat", cursor="hand2",
                                   font=(UI_FONT, 10, "bold"), command=lambda: self.answer("allow"))
        if IS_MAC:
            self.btn_allow.configure(**button_style("primary"))
            self.btn_deny.configure(**button_style("danger"))
        self.btn_deny.pack(side="left", ipady=3)
        self.btn_allow.pack(side="right", ipady=4)
        self.answer_row.pack(fill="x")
        self.go_row = tk.Frame(right, bg=bg)
        self.btn_go = tk.Button(self.go_row, text="Go to window", bg=self.ACCENT, fg="#111827", activebackground="#d97706",
                                activeforeground="#111827", relief="flat", cursor="hand2", font=(UI_FONT, 10, "bold"),
                                command=self.go)
        self.btn_go.pack(side="left", ipady=4, ipadx=14)
        self.update(item)
        self._place(first=True)
        w.deiconify()
        if IS_MAC:
            import mac_statusbar as mac
            w.update_idletasks()
            mac.set_all_spaces(w.title(), app.cfg.get("all_spaces", True))
            # Tk 8.6 skips native panel mouse events. Forward this card's events
            # so buttons, text selection, and dragging still work without activation.
            self._mouse_bridge = mac.PanelMouseBridge(w)
            w.bind("<Destroy>", lambda event: self._mouse_bridge.close()
                   if event.widget is w else None, add="+")
            w.attributes("-alpha", 1.0)
        else:
            w.focus_force()
        self._tick()

    # ---- placement: centred on the screen, cascading a little when several are open
    def _place(self, first=False):
        w = self.win
        w.update_idletasks()
        size = (w.winfo_reqwidth(), w.winfo_reqheight())
        if size == self._size and not first:
            return
        self._size = size
        if self._moved:
            return
        n = len(self.app.details) - (0 if self.key in self.app.details else 0)
        n = max(0, n - (1 if self.key in self.app.details else 0))
        root = self.app.root  # centre on the monitor the pets are on
        ref = (root.winfo_x() + root.winfo_width() // 2, root.winfo_y() + root.winfo_height() // 2)
        left, top, right, bottom = work_area(*ref, w) or (0, 0, w.winfo_screenwidth(), w.winfo_screenheight())
        x = left + (right - left - size[0]) // 2 + 28 * n
        y = max(top + 20, top + (bottom - top - size[1]) // 2 - 30 + 28 * n)
        w.geometry(geo(x, y))

    def _fit_code(self):
        """Size the command box to its real wrapped line count (long paths wrap mid-word, so estimates clip)."""
        try:
            if not self.code.winfo_ismapped():
                return
            self.code.config(state="normal", height=40)
            self.code.update_idletasks()
            n = self.code.count("1.0", "end-1c", "displaylines")  # number of line BREAKS, so rows = breaks + 1
            rows = (int(n[0]) if n else 0) + 1
            self.code.config(height=max(1, min(16, rows)))
            self.code.config(state="disabled")
            self._place()
        except tk.TclError:
            pass

    def _drag_start(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())
        self._moved = True

    def _drag_move(self, e):
        dx, dy = self._drag
        self.win.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    # ---- the robot: asks while waiting, reacts to your answer
    def _tick(self):
        try:
            if not self.win.winfo_exists():
                return
            if self.robot is not None:
                self._draw_robot(time.time() - self._t0)
        except tk.TclError:
            return
        self._after = self.win.after(60, self._tick)

    def _draw_robot(self, t):
        spr = load_sprites()
        if not spr:
            return
        c = self.robot
        c.delete("all")
        if self.sent in ("Allow once", "Answer"):
            face, state, dy = "joy", "done", -abs(math.sin(t * 6)) * 4
        elif self.sent == "Deny":
            face, state, dy = "worried", "error", 0.0
        elif getattr(self, "_state", "needs_input") != "needs_input" and self._state in STATE_FACE:
            face, state, dy = STATE_FACE[self._state], self._state, -abs(math.sin(t * 2)) * 3
        else:
            face, state, dy = "ask", "needs_input", -abs(math.sin(t * 4)) * 10
        rw, rh = spr["meta"]["robot"]["size"]
        z = 3
        base = 166
        shadow = spr["img"]["shadow"]
        sw = int(rw * z * 1.1 * (1 - min(0.35, abs(dy) / 40)))
        c.create_image(62, base + 4, image=sprite_photo("popup-shadow", shadow, sw, max(2, sw // 4)), anchor="center")
        lights = light_cycle(state, t)
        im = robot_image(face, lights, spr)
        c.create_image(62, base + dy, image=sprite_photo(("popup", face, lights), im, rw * z, rh * z), anchor="s")
        if state == "needs_input":  # floating question marks
            for i in range(3):
                ph = (t * 0.6 + i / 3) % 1
                c.create_text(22 + i * 40 + 8 * math.sin(t * 3 + i), 40 - ph * 34, text="?", fill=self.ACCENT,
                              font=(UI_FONT, int(9 + 7 * (1 - ph)), "bold"))

    def update(self, item):
        self._state = item.get("state", "needs_input")
        req = item.get("request") or {}
        if self.sent and self._state == "needs_input" and req.get("id") and req.get("id") != getattr(self, "_sent_id", None):
            self.sent = None  # that prompt is settled and the next queued one is showing: answer it too
        if self._state != "needs_input" and not self.sent:
            return self._update_status(item)
        pending = item.get("pending", 0)
        question = req.get("kind") == "question"
        self.status.config(text=("has a question for you" if question else "needs your permission")
                           + (f"  ·  {pending} prompts waiting" if pending > 1 else ""), fg=self.ACCENT)
        self.head.config(text=item.get("title", "session"))
        self.where.config(text=" \u00b7 ".join(x for x in (item.get("where"), item.get("detail")) if x))
        for widget in (self.ask, self.desc, self.code, self.qframe):
            widget.pack_forget()
        if question:
            self._show_questions(req)
        elif req:
            who = "Codex" if item.get("agent") == "codex" else "Claude"
            self.ask.config(text=f"Allow {who} to use {req.get('tool') or 'this tool'}?")
            self.ask.pack(fill="x")
            if req.get("description"):
                self.desc.config(text=req["description"])
                self.desc.pack(fill="x", pady=(4, 0))
            detail = req.get("detail", "")
            if detail:
                if detail != self._shown:
                    self.code.config(state="normal")
                    self.code.delete("1.0", "end")
                    self.code.insert("1.0", detail)
                    lines = sum(max(1, -(-len(ln) // 50)) for ln in detail.splitlines() or [""]) + 1  # provisional
                    self.code.config(height=max(1, min(16, lines)), state="disabled")
                    self.win.after(80, self._fit_code)  # then measure the real wrapped height once it is on screen
                    self._shown = detail
                self.code.pack(fill="x", pady=(8, 0))
        else:
            self.ask.config(text=item.get("message") or "Waiting for your input")
            self.ask.pack(fill="x")
        self.wait.config(text=f"waiting {ago(item['changed'])}" if item.get("changed") else "")
        can_answer = self.app.answers_enabled(item) and hook_waiting(item) and not self.sent
        who = "Codex" if item.get("agent") == "codex" else "Claude Code"
        if self.sent:
            note = f"Sent: {self.sent}. {who} will carry on in a moment."
        elif can_answer:
            note = ("Allow once or Deny answers this prompt right from here, or answer in the session window "
                    "(Go to window). If you do neither, the normal prompt appears.")
        elif req and not self.app.answers_enabled(item):
            note = (f"Answer this one in {who} (Go to window). To answer prompts here instead, switch on "
                    f"Answer {who} prompts from the pet under Permissions.")
        elif item.get("agent") == "codex" and req and self.app.cfg.get("codex_answers", True):
            note = ("Codex is no longer waiting for this window, so answer in Codex (Go to window). If this happens "
                    "right away, your Codex hooks are from an older AIPet with a short timeout: update them in the "
                    "Codex hooks menu and trust them again with /hooks in Codex.")
        elif req.get("source") == "transcript":
            note = ("This session type (the VS Code extension) sends no permission events, so the pet can't answer for it. "
                    "This is what Claude is asking, so you know what to approve there. Press Go to window to jump there.")
        elif req:
            if ANSWER_WAIT["v"] <= 0:
                note = ("The pet stopped waiting for this prompt (it was closed or restarted meanwhile). "
                        "Answer in the session window: press Go to window.")
            else:
                note = (f"The pet's {fmt_wait(ANSWER_WAIT['v'])} answer window has passed. "
                        "Answer in the session window: press Go to window.")
        else:
            note = ("No command details were reported for this prompt, so there is nothing to answer here. "
                    "Press Go to window to jump to the session.")
        if question and can_answer:
            note = ("Pick an answer (or type your own) and press Send answer, or answer in the session window "
                    "(Go to window). If you do neither, Claude Code asks you itself.")
        elif question and self.sent:
            note = f"Sent your answer. {who} will carry on in a moment."
        self.note.config(text=note)
        self.answer_row.pack_forget()
        self.q_row.pack_forget()
        if can_answer:
            (self.q_row if question else self.answer_row).pack(fill="x", after=self.note)
        self._req_id = req.get("id")
        if req and not self.sent and req.get("source") != "transcript":
            self.clearlink.pack(fill="x", pady=(0, 6), after=self.note)
        else:
            self.clearlink.pack_forget()
        if can_answer or self.sent:  # small link while the answer buttons (or the "sent" note) are showing
            self.golink.pack(side="right")
            self.go_row.pack_forget()
        else:  # nothing to answer from here: the big button
            self.golink.pack_forget()
            self.go_row.pack(fill="x", after=self.note)
        self._place()

    def _show_questions(self, req):
        """Build the question blocks once per prompt (the card refreshes every poll: keep what was picked)."""
        self.qframe.pack(fill="x")
        if self._qid == req.get("id"):
            return
        self._qid, self._qvars = req.get("id"), []
        for child in self.qframe.winfo_children():
            child.destroy()
        bg, fg, muted = T["bubble_bg"], T["bubble_fg"], T["bubble_muted"]
        for i, q in enumerate(req.get("questions") or []):
            block = tk.Frame(self.qframe, bg=bg)
            block.pack(fill="x", pady=(0 if i == 0 else 12, 0))
            if q.get("header"):
                tk.Label(block, text=q["header"].upper(), bg=bg, fg=self.ACCENT, anchor="w",
                         font=(UI_FONT, 8, "bold")).pack(fill="x")
            tk.Label(block, text=q["question"], bg=bg, fg=fg, anchor="w", justify="left", wraplength=430,
                     font=(UI_FONT, 11, "bold")).pack(fill="x", pady=(2, 4))
            multi = bool(q.get("multiSelect"))
            picks = {}
            choice = tk.StringVar(master=self.win, value="")
            for opt in q.get("options") or []:
                row = tk.Frame(block, bg=bg)
                row.pack(fill="x", pady=1)
                if multi:
                    var = picks[opt["label"]] = tk.BooleanVar(master=self.win, value=False)
                    box = tk.Checkbutton(row, text=opt["label"], variable=var, anchor="w")
                else:
                    box = tk.Radiobutton(row, text=opt["label"], variable=choice, value=opt["label"], anchor="w")
                box.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, selectcolor=T["entry_bg"],
                              highlightthickness=0, font=(UI_FONT, 10, "bold"), cursor="hand2")
                box.pack(fill="x")
                if opt.get("description"):
                    tk.Label(row, text=opt["description"], bg=bg, fg=muted, anchor="w", justify="left",
                             wraplength=400, font=(UI_FONT, 8)).pack(fill="x", padx=(24, 0))
            own = tk.StringVar(master=self.win, value="")
            other = tk.Frame(block, bg=bg)
            other.pack(fill="x", pady=(4, 0))
            tk.Label(other, text="Or your own answer:", bg=bg, fg=muted, font=(UI_FONT, 8)).pack(side="left")
            entry = tk.Entry(other, textvariable=own, bg=T["entry_bg"], fg=T["entry_fg"], relief="flat",
                             insertbackground=T["entry_fg"], highlightthickness=1, highlightbackground=T["border"],
                             highlightcolor=T["primary"], font=(UI_FONT, 9))
            entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
            if not multi:  # typing replaces the picked option
                own.trace_add("write", lambda *_, c=choice, o=own: c.set("") if o.get().strip() else None)
            self._qvars.append((q, multi, choice, picks, own))
        self.win.after(60, self._place)

    def question_answers(self):
        """{question text: label} (single choice) or {question text: [labels]} (multi-select); typed answers count
        as a label. None until every question has an answer."""
        answers = {}
        for q, multi, choice, picks, own in self._qvars:
            typed = own.get().strip()
            if multi:
                value = [label for label, var in picks.items() if var.get()] + ([typed] if typed else [])
            else:
                value = typed or choice.get()
            if not value:
                return None
            answers[q["question"]] = value
        return answers or None

    def send_answers(self):
        answers = self.question_answers()
        if not answers:
            self.note.config(text="Answer every question first (pick an option or type your own).")
            return
        item = next((i for i in self.app._last_items if i["key"] == self.key), {})
        if self.app.send_answer(self.key, "allow", answers=answers):
            self.sent = "Answer"
            self._sent_id = (item.get("request") or {}).get("id")
            self.update(item or {"request": {}})

    def _update_status(self, item):
        """A session that isn't asking anything: show what it's doing, with no answer buttons."""
        state = item.get("state")
        self.status.config(text=LABELS.get(state, state or ""), fg=COLORS.get(state, self.ACCENT))
        self.head.config(text=item.get("title", "session"))
        self.where.config(text=" · ".join(x for x in (item.get("where"), item.get("detail")) if x))
        for widget in (self.ask, self.desc, self.code):
            widget.pack_forget()
        if item.get("message"):
            self.ask.config(text=item["message"])
            self.ask.pack(fill="x")
        self.wait.config(text=f"since {ago(item['changed'])}" if item.get("changed") else "")
        self.note.config(text="Press Go to window to jump to the session.")
        self.answer_row.pack_forget()
        self.q_row.pack_forget()
        self.qframe.pack_forget()
        self.golink.pack_forget()
        self.go_row.pack(fill="x", after=self.note)
        self._place()

    def go(self):
        """Bring the session's window to the front and dismiss this popup."""
        self.app.focus_key(self.key)
        self.close()

    def clear_prompt(self):
        self.app.clear_prompt(self.key, getattr(self, "_req_id", None))  # the next poll moves the card on / closes it

    def answer(self, behavior):
        item = next((i for i in self.app._last_items if i["key"] == self.key), {})
        if self.app.send_answer(self.key, behavior):
            self.sent = "Allow once" if behavior == "allow" else "Deny"
            self._sent_id = (item.get("request") or {}).get("id")
            self.update(next((i for i in self.app._last_items if i["key"] == self.key), {"request": {}}))

    def close(self):
        self.app.details.pop(self.key, None)
        try:
            if self._after:
                self.win.after_cancel(self._after)
            self.win.destroy()
        except tk.TclError:
            pass

    def destroy(self):
        self.close()


# --------------------------------------------------------------------------- app
class MacOverlayWindow(tk.Toplevel):
    """Create passive overlays as panels before Tk realizes their native windows."""
    def __init__(self, master):
        super().__init__(master, takefocus=False)
        # Tk's main window is already an NSWindow by the time Tk() returns.
        # Changing MacWindowStyle later cannot turn it into an NSPanel. A fresh
        # toplevel lets Tk allocate a non-activating panel for full-screen Spaces.
        self.tk.call("::tk::unsupported::MacWindowStyle", "style", self._w, "utility",
                     ("noTitleBar", "nonActivating", "doesNotHide", "doesNotCycle"))


class MacPetWindow(MacOverlayWindow):
    """The pet panel owns the hidden Tk root that runs its event loop."""
    def __init__(self):
        self._owner = tk.Tk()
        self._owner.withdraw()
        self._destroying = False
        super().__init__(self._owner)

    def destroy(self):
        if self._destroying:
            return
        self._destroying = True
        super().destroy()
        self._owner.destroy()  # also end mainloop; never leave the hidden root alive


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

    def send_answer(self, key, behavior, answers=None):
        """Hand the user's click to the waiting PermissionRequest hook. Returns True if it was written.
        answers: for a question, {question text: label, or [labels] for multi-select}."""
        item = next((i for i in self._last_items if i["key"] == key), None)
        req = (item or {}).get("request") or {}
        if not self.answers_enabled(item) or not hook_waiting(item) or behavior not in ("allow", "deny"):
            return False
        name = answer_name(item)
        try:
            os.makedirs(answers_dir(), exist_ok=True)
            path = os.path.join(answers_dir(), name + ".json")
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(dict({"behavior": behavior, "t": time.time()}, **({"answers": answers} if answers else {})), f)
            os.replace(path + ".tmp", path)
            return True
        except OSError:
            return False

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
    def set_scale(self, v):
        v = clamp_scale(v)
        if abs(v - SCALE["v"]) < 0.005:
            return
        SCALE["v"] = v
        for pet in self.pets.values():
            pet.canvas.config(width=px(CANVAS_W + getattr(pet, "gutter", 0) + getattr(pet, "usage_extra", 0)), height=px(CANVAS_H))
        self._sync_usage_padding()

    def _place_above_pet(self, w):
        """Put a small window directly above the pet overlay, centred on it (just below it if there is no room)."""
        w.update_idletasks()
        self.root.update_idletasks()
        ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
        ox, oy, ow, oh = self.root.winfo_x(), self.root.winfo_y(), self.root.winfo_width(), self.root.winfo_height()
        ref = (ox + ow // 2, oy + oh // 2)  # the pet's monitor
        area = work_area(*ref, w)
        x, y = ox + ow // 2 - ww // 2, oy - wh - 10
        if area and y < area[1]:
            y = oy + oh + 10  # no room above: just below
        w.geometry(geo(*fit_on_screen(x, y, ww, wh, ref, w, margin=10)))

    def open_size_slider(self):
        """A small window with a slider (30% - 300%; 100% = SCALE_UNIT). The pet follows it live, saved on release."""
        if self.size_win is not None:
            try:
                self.size_win.lift()
                return
            except tk.TclError:
                self.size_win = None
        from tkinter import ttk
        w = self.size_win = tk.Toplevel(self.root)
        w.title("Pet size")
        w.attributes("-topmost", True)
        w.resizable(False, False)
        var = tk.DoubleVar(value=SCALE["v"] / SCALE_UNIT)
        pct = tk.Label(w, width=6, font=(UI_FONT, 12, "bold"))

        def apply(_v=None):
            self.set_scale(var.get() * SCALE_UNIT)
            self.reposition()
            pct.config(text=f"{int(round(SCALE['v'] / SCALE_UNIT * 100))}%")

        def commit(_e=None):
            self.cfg["size"] = round(SCALE["v"] / SCALE_UNIT, 2)
            save_setting("size", self.cfg["size"])
            if self.size_win is not None:
                self._place_above_pet(w)  # the pet grew/shrank: stay directly above it

        def reset():
            var.set(1.0)
            apply()
            commit()

        def close():
            commit()
            self.size_win = None
            w.destroy()

        tk.Label(w, text="Pet size", font=(UI_FONT, 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
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
        theme_window(w)
        self._place_above_pet(w)

    def open_answer_slider(self):
        """Slider for how long a permission prompt can be answered from the pet: 0 (off) to 5 minutes."""
        if getattr(self, "answer_win", None) is not None:
            try:
                self.answer_win.lift()
                return
            except tk.TclError:
                self.answer_win = None
        from tkinter import ttk
        w = self.answer_win = tk.Toplevel(self.root)
        w.title("Answer timeout")
        w.attributes("-topmost", True)
        w.resizable(False, False)
        var = tk.DoubleVar(value=ANSWER_WAIT["v"])
        val = tk.Label(w, width=10, font=(UI_FONT, 12, "bold"))

        def apply(_v=None):
            ANSWER_WAIT["v"] = clamp_wait(round(var.get() / 15) * 15)  # steps of 15 s
            val.config(text=fmt_wait(ANSWER_WAIT["v"]))

        def commit(_e=None):
            apply()
            self.cfg["answer_wait_seconds"] = ANSWER_WAIT["v"]
            save_setting("answer_wait_seconds", ANSWER_WAIT["v"])
            write_answer_wait(ANSWER_WAIT["v"])

        def reset():
            var.set(180)
            commit()

        def close():
            commit()
            self.answer_win = None
            w.destroy()

        tk.Label(w, text="Answer permission prompts from the pet for up to:", font=(UI_FONT, 10, "bold")
                 ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
        scale = ttk.Scale(w, from_=0, to=MAX_WAIT, orient="horizontal", length=260, variable=var, command=apply)
        scale.grid(row=1, column=0, padx=(14, 6), pady=8)
        scale.bind("<ButtonRelease-1>", commit)
        val.grid(row=1, column=1, padx=(0, 14))
        tk.Label(w, justify="left", wraplength=360, fg="#6b7280", font=(UI_FONT, 8),
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
        theme_window(w)
        self._place_above_pet(w)

    def open_done_slider(self):
        """Slider for how long a finished session's pet stays: 0 (never cleared) to 30 minutes."""
        if getattr(self, "done_win", None) is not None:
            try:
                self.done_win.lift()
                return
            except tk.TclError:
                self.done_win = None
        from tkinter import ttk
        w = self.done_win = tk.Toplevel(self.root)
        w.title("Clear finished sessions")
        w.attributes("-topmost", True)
        w.resizable(False, False)
        var = tk.DoubleVar(value=DONE_TIMEOUT["v"])
        val = tk.Label(w, width=10, font=(UI_FONT, 12, "bold"))

        def apply(_v=None):
            DONE_TIMEOUT["v"] = clamp_done(var.get())  # whole minutes
            val.config(text="never" if DONE_TIMEOUT["v"] <= 0 else f"{DONE_TIMEOUT['v']} min")

        def commit(_e=None):
            apply()
            self.cfg["done_timeout_minutes"] = DONE_TIMEOUT["v"]
            save_setting("done_timeout_minutes", DONE_TIMEOUT["v"])

        def reset():
            var.set(3)
            commit()

        def close():
            commit()
            self.done_win = None
            w.destroy()

        tk.Label(w, text="Clear a finished session's pet after:", font=(UI_FONT, 10, "bold")
                 ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
        scale = ttk.Scale(w, from_=0, to=MAX_WAIT // 60, orient="horizontal", length=260, variable=var, command=apply)
        scale.grid(row=1, column=0, padx=(14, 6), pady=8)
        scale.bind("<ButtonRelease-1>", commit)
        val.grid(row=1, column=1, padx=(0, 14))
        tk.Label(w, justify="left", wraplength=360, fg="#6b7280", font=(UI_FONT, 8),
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
        theme_window(w)
        self._place_above_pet(w)

    def reset_scale(self):
        self.set_scale(SCALE_UNIT)
        self.reposition()
        self.cfg["size"] = 1.0
        save_setting("size", 1.0)

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
            if focus_hwnd(d.get("hwnd")):
                return
            if d.get("env") == "wsl" or d.get("agent") == "codex" or d.get("app") == "cowork" or \
                    "desktop" in (d.get("entry") or "").lower():
                own = d.get("focus") or pet.key
                others = [i for i in self._last_items if i.get("key") != own]
                focus_wsl_terminal(d, others)  # no recorded window: match by distro / project, never another's

    def _menu_session(self, pet):
        """The session a right-click is about: the pet's own, or in compact mode the one in front."""
        if not pet or pet.key == "_none":
            return None
        key = pet.data.get("focus") or pet.key
        return next((i for i in self._last_items if i["key"] == key), None)

    def finish_menu_pet(self):
        """Right-click > Mark as finished: for a session that is stuck (its app closed without telling the pet)."""
        d = self._menu_session(self.menu_pet)
        if not d or d.get("source") != "CC" or not d.get("path"):
            return
        try:
            with open(d["path"], encoding="utf-8") as f:
                rec = json.load(f)
            now = time.time()
            rec.update(state="done", message="", request={}, wait_agent="", agents={}, main_stopped=False,
                       updated=now, changed=now)
            tmp = d["path"] + ".pet.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            os.replace(tmp, d["path"])
        except (OSError, ValueError) as e:
            log_error(f"mark as finished: {e!r}")

    def open_health_slider(self):
        """Slider for how often a working session's process is checked: 0 (off) to 5 minutes."""
        if getattr(self, "health_win", None) is not None:
            try:
                self.health_win.lift()
                return
            except tk.TclError:
                self.health_win = None
        from tkinter import ttk
        w = self.health_win = tk.Toplevel(self.root)
        w.title("Health check")
        w.attributes("-topmost", True)
        w.resizable(False, False)
        var = tk.DoubleVar(value=HEALTH["v"])
        val = tk.Label(w, width=10, font=(UI_FONT, 12, "bold"))

        def apply(_v=None):
            HEALTH["v"] = clamp_health(round(var.get() / 5) * 5)  # steps of 5 s
            val.config(text="off" if HEALTH["v"] <= 0 else fmt_wait(HEALTH["v"]))

        def commit(_e=None):
            apply()
            self.cfg["health_check_seconds"] = HEALTH["v"]
            save_setting("health_check_seconds", HEALTH["v"])

        def reset():
            var.set(15)
            commit()

        def close():
            commit()
            self.health_win = None
            w.destroy()

        tk.Label(w, text="Check that working sessions are still running every:", font=(UI_FONT, 10, "bold")
                 ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
        scale = ttk.Scale(w, from_=0, to=MAX_HEALTH, orient="horizontal", length=260, variable=var, command=apply)
        scale.grid(row=1, column=0, padx=(14, 6), pady=8)
        scale.bind("<ButtonRelease-1>", commit)
        val.grid(row=1, column=1, padx=(0, 14))
        tk.Label(w, justify="left", wraplength=360, fg="#6b7280", font=(UI_FONT, 8),
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
        theme_window(w)
        self._place_above_pet(w)

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
        error = kind == "error"
        if winsound:
            if self.cfg.get("sound_style", "chimes") == "chimes":
                try:
                    winsound.PlaySound(chime_path("thud" if error else "knock" if urgent else "ting"),
                                       winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                    return
                except (RuntimeError, OSError):
                    pass  # fall back to the system sound
            try:
                winsound.MessageBeep(winsound.MB_ICONHAND if error else
                                     winsound.MB_ICONEXCLAMATION if urgent else winsound.MB_ICONASTERISK)
            except RuntimeError:
                pass
        elif IS_MAC:
            try:
                sound = "Basso" if error else "Funk" if urgent else "Glass"
                subprocess.Popen(["afplay", f"/System/Library/Sounds/{sound}.aiff"])
            except OSError:
                self.root.bell()
        else:
            self.root.bell()

    def set_sound_style(self, style):
        if style not in ("chimes", "system"):
            return
        self.cfg["sound_style"] = style
        save_setting("sound_style", style)
        self.beep(False, force=True)  # let the user hear the choice

    def dismiss(self, pet):
        self.dismiss_item(pet.data)

    def _sweep_usage(self):
        """A minute after start, then hourly: clear usage files of sessions that are long gone."""
        try:
            usage.sweep(HOME_DIR)
        except Exception as e:
            log_error(f"usage sweep: {e!r}")
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
        x, y = fit_on_screen(ax - w, ay - h, w, h, ref, r)
        r.geometry(geo(x, y))
        self.anchor = [x + w, y + h]

    def default_anchor(self):
        """Where the pet's bottom-right corner starts: the main screen's bottom-right corner, just above the Windows
        taskbar; on macOS just above the Dock (the visible area of the main screen)."""
        r = self.root
        if IS_MAC:
            area = mac_screen_at(1, 1)  # the main screen holds Tk's origin
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
            x, y = fit_on_screen(self.anchor[0] - w, self.anchor[1] - h, w, h, ref, r)
            r.geometry(geo(x, y))
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
            extra, _ = vscode_target(d["cwd"], d.get("env", ""), d.get("distro", ""))
        except Exception as e:
            log_error(f"vscode_target failed: {e!r}")
            extra = (["--remote", f"wsl+{d.get('distro') or 'Ubuntu'}"] if d.get("env") == "wsl" else []) + [d["cwd"]]
        args = [code] + extra
        try:
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            self.root.bell()
            return
        uri = vscode_conversation_uri(d, code)
        if uri and self.cfg.get("vscode_open_conversation", True):
            # VS Code hands a vscode:// link to its active window, so give `code` a moment to raise the right one
            self.root.after(1200, lambda: open_uri(uri))

    def show_tip(self, pet):
        if getattr(self, "_menu_open", False):
            return
        self.hide_tip()
        if not self.cfg.get("session_tooltips", True):
            return
        d = pet.data
        lines = [d.get("title", ""), f"{d.get('where') or SOURCE_NAMES.get(d.get('source'), '')} · {LABELS.get(d.get('state'), d.get('state'))}".strip(" ·")]
        if d.get("everyone"):  # compact mode: every session, the one in front first
            lines += [""] + [f"{'> ' if m['key'] == d.get('focus') else '   '}{m.get('title', '')} · "
                             f"{LABELS.get(m.get('state'), m.get('state'))}" for m in d["everyone"][:12]]
        if d.get("message"):
            lines.append(d["message"][:200])
        if d.get("detail"):
            lines.append(d["detail"])
        if d.get("changed"):
            lines.append("since " + ago(d["changed"]))
        if d.get("pid"):
            lines.append(f"PID {d['pid']}" + (" (WSL)" if d.get("env") == "wsl" else ""))
        if d.get("auto_what") and time.time() - (d.get("auto_t") or 0) < 120:
            lines.append(f"Auto-approved {ago(d['auto_t'])}: {d['auto_what'][:90]}")
        if d.get("auto_approved"):
            lines.append(f"Auto-approved {d['auto_approved']} permission prompt{'s' if d['auto_approved'] != 1 else ''}")
        self._show_tooltip(pet, "\n".join(lines))

    def show_usage_tip(self, pet, detail):
        if getattr(self, "_menu_open", False):
            return
        self.hide_tip()
        if not self.cfg.get("usage_tooltips", True):
            return
        self._show_tooltip(pet, usage.tooltip(detail), usage_detail=detail)

    def set_tooltip(self, kind, on):
        if kind not in ("session", "usage"):
            return
        self.cfg[kind + "_tooltips"] = bool(on)
        save_setting(kind + "_tooltips", bool(on))
        var = getattr(self, "tooltip_vars", {}).get(kind)
        if var is not None:
            var.set(bool(on))
        self.hide_tip()
        for pet in self.pets.values():
            pet._usage_hover = None

    def _show_tooltip(self, pet, text, usage_detail=None):
        if getattr(self, "_menu_open", False):
            return
        tip = self.tip = MacOverlayWindow(self.root) if IS_MAC else tk.Toplevel(self.root)
        # Configure the native panel and position it before it becomes visible.
        tip.withdraw()
        tip.title("AIPet tooltip")
        if IS_MAC:
            tip.attributes("-alpha", 0.0)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tip.configure(bg=TRANSPARENT)
        try:
            tip.attributes("-transparent", True) if IS_MAC else tip.attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass
        c = tk.Canvas(tip, bg=TRANSPARENT, highlightthickness=0, bd=0)
        c.pack()
        title, _, body = text.partition("\n")
        title = title.strip() or "Session details"
        data = usage_detail if usage_detail is not None else getattr(pet, "data", {})
        agent = data.get("agent")
        accent = "#5b8def" if agent == "codex" else "#d98960" if agent == "claude" or data.get("source") == "CC" else T["tag_fg"]
        head = c.create_text(12, 10, text=title, font=(MONO_FAMILY, 9, "bold"), fill=accent, anchor="nw", width=320)
        header_box = c.bbox(head)
        w, h = header_box[2] + 12, header_box[3] + 10
        if body.strip():
            content = c.create_text(12, header_box[3] + 6, text=body, font=(MONO_FAMILY, 8), fill=T["tag_fg"], anchor="nw", width=320)
            box = c.bbox(content)
            w, h = max(header_box[2], box[2]) + 12, box[3] + 10
        border = c.create_polygon(6, 1, w - 6, 1, w - 6, 3, w - 3, 3, w - 3, 6, w - 1, 6,
                                 w - 1, h - 6, w - 3, h - 6, w - 3, h - 3, w - 6, h - 3, w - 6, h - 1,
                                 6, h - 1, 6, h - 3, 3, h - 3, 3, h - 6, 1, h - 6, 1, 6, 3, 6, 3, 3, 6, 3,
                                 fill=T["tag_bg"], outline=T["tag_outline"], width=2)
        c.tag_lower(border)
        c.config(width=w, height=h)
        tip.update_idletasks()
        # Both session and usage details belong above the pet, clear of its body
        # and badges. Centre on the pet itself, excluding the usage badge gutter.
        x = pet.canvas.winfo_rootx() + px(BADGE_GUTTER + getattr(pet, "gutter", 0) + PET_W / 2) - tip.winfo_reqwidth() // 2
        y = pet.canvas.winfo_rooty() - tip.winfo_reqheight() - 6
        tip.geometry(geo(*fit_on_screen(x, y, tip.winfo_reqwidth(), tip.winfo_reqheight(),
                                        (pet.canvas.winfo_rootx() + 10, pet.canvas.winfo_rooty() + 10), tip)))
        tip.deiconify()
        if IS_MAC:
            import mac_statusbar as mac
            # Tk creates/resets the native window on mapping. Keep it invisible
            # until its Space and mouse behavior have been applied.
            tip.update_idletasks()
            mac.set_all_spaces(tip.title(), self.cfg.get("all_spaces", True))
            window = mac.window_titled(tip.title())
            if window:
                parent = mac.window_titled(self.root.title())
                if parent:
                    level = mac.send(parent, "level", restype=mac.c_long)
                    mac.send(window, "setLevel:", level + 1, restype=None, argtypes=[mac.c_long])
                # Hover text must never intercept clicks or become the key window.
                mac.send(window, "setIgnoresMouseEvents:", True, restype=None, argtypes=[mac.c_bool])
            tip.attributes("-alpha", 1.0)

    def hide_tip(self):
        if self.tip:
            self.tip.destroy()
            self.tip = None


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


def ensure_home():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)


def main():
    ensure_home()
    if "--probe-workbench" in sys.argv:
        probe_workbench()
        return
    PetApp().root.mainloop()


if __name__ == "__main__":
    main()
