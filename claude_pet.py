#!/usr/bin/env python3
"""
Claude Pet - a floating, always-on-top companion that shows one little creature
per active conversation:

    blue, bobbing, orbiting dots  -> working
    orange, jumping, "!" bubble   -> needs your input (beeps, re-reminds)
    green, sleeping, "z"          -> done
    red, shaking                  -> error (Workbench)

Sources:
    * Claude Code  - via claude_pet_hook.py (hooks write session files)
    * mcp-workbench Agents chats - polled over MCP streamable HTTP (listAgentChats)

Run:   pythonw claude_pet.py              (no console window)
Probe: python  claude_pet.py --probe-workbench   (prints what Workbench returns)

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

try:
    import winsound
except ImportError:  # not Windows
    winsound = None

HOME_DIR = os.path.join(os.path.expanduser("~"), ".claude-pet")
SESSIONS_DIR = os.path.join(HOME_DIR, "sessions")
CONFIG_PATH = os.path.join(HOME_DIR, "config.json")

DEFAULT_CONFIG = {
    "poll_ms": 700,
    "max_pets": 10,
    "sounds": True,
    "sound_on_done": True,
    "notifications": False,  # Windows toast notifications; off by default (toggle in the tray menu)
    "theme": "light",  # "light" (default) or "dark"; toggle in the tray / right-click menu
    "answer_wait_seconds": 180,  # how long a permission prompt can be answered from the pet: 0 (off) - 300
    "pet_style": "robot",  # "robot" (default), "mole" or "cat"
    # "size": 1.0 is written when you use the Size slider (1.0 = 100%, range 0.3 - 3.0); deliberately not a default
    # here, so an old absolute "scale" value in config.json can still be migrated once.
    "remind_seconds": 90,
    "hide_done_after_minutes": 30,
    "stale_hours": 12,
    "claude_code": {
        "enabled": True,
        # Extra folders to watch, e.g. a WSL distro if the hook can't reach Windows:
        # (in config.json: "\\\\wsl.localhost\\Ubuntu\\home\\<you>\\.claude-pet\\sessions")
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
SOURCE_NAMES = {"CC": "Claude Code", "WB": "Workbench"}
BADGE_COLORS = {"CC": "#6b7280", "WSL": "#7c3aed", "VS": "#007acc", "WB": "#0f766e"}
STATE_ORDER = ("needs_input", "error", "done", "working")
PET_W, PET_H = 92, 122
INK = "#1f2937"
MOUND, MOUND_DARK = "#a16207", "#713f12"
EMERGE_SECONDS, EMERGE_DEPTH, EMERGE_STAGGER = 0.7, 62, 0.25
BUBBLE_W = 270
# Colours of what the pet draws around the creatures (name tags, bubbles, tooltips). Light is the default.
THEMES = {
    "light": {"tag_bg": "#ffffff", "tag_fg": "#111827", "label_dark": True,
              "bubble_bg": "#ffffff", "bubble_fg": "#111827", "bubble_msg": "#374151", "bubble_muted": "#6b7280",
              "tip_bg": "#ffffff", "tip_fg": "#111827", "tip_border": "#cbd5e1", "tag_outline": "#111827",
              "code_bg": "#f3f4f6", "code_fg": "#111827"},
    "dark": {"tag_bg": INK, "tag_fg": "#ffffff", "label_dark": False,
             "bubble_bg": "#111827", "bubble_fg": "#ffffff", "bubble_msg": "#e5e7eb", "bubble_muted": "#9ca3af",
             "tip_bg": "#111827", "tip_fg": "#f9fafb", "tip_border": "#111827", "tag_outline": "#0b1220",
             "code_bg": "#0b1220", "code_fg": "#e5e7eb"},
}
T = {}  # the active theme; set_theme() mutates it in place so every module sees the change


def set_theme(name):
    name = name if name in THEMES else "light"
    T.clear()
    T.update(THEMES[name], name=name)


set_theme("light")

STYLE = {"v": "robot"}  # "robot", "mole" or "cat"
SCALE_UNIT = 2.0  # the absolute drawing scale that counts as 100% (it was the old 200%, the size people settled on)
SCALE = {"v": SCALE_UNIT}  # absolute drawing scale: 92x122 px per pet at 1.0; the slider shows SCALE / SCALE_UNIT


ANSWER_WAIT = {"v": 180}  # seconds; mirrored into <pet dir>/answer-wait for the hook (0 = answering from the pet is off)


def answers_dir():
    return os.path.join(HOME_DIR, "answers")


def clamp_wait(v):
    try:
        return int(max(0, min(300, round(float(v)))))
    except (TypeError, ValueError):
        return 180


def fmt_wait(sec):
    sec = int(sec)
    if sec <= 0:
        return "off"
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


def answers_enabled():
    return ANSWER_WAIT["v"] > 0 and not os.path.exists(os.path.join(HOME_DIR, "no-answers"))


def clamp_scale(v):
    try:
        return max(0.6, min(6.0, float(v)))  # 30% - 300% of SCALE_UNIT
    except (TypeError, ValueError):
        return 1.0


def px(n):
    return int(round(n * SCALE["v"]))


def fnt(size, weight=""):
    f = ("Segoe UI", max(5, int(round(size * SCALE["v"]))))
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
        print(f"[claude-pet] config.json unreadable, using defaults: {e}", file=sys.stderr)
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
        print(f"[claude-pet] couldn't save {key}: {e}", file=sys.stderr)
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
    dirs = [SESSIONS_DIR] + list(cfg["claude_code"].get("extra_session_dirs") or [])
    paths = []
    for d in dirs:
        try:
            paths += glob.glob(os.path.join(d, "*.json"))
        except OSError:
            continue  # e.g. WSL distro not running
    seen_ids = set()
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            continue  # mid-write or corrupt; next poll will catch it
        if rec.get("updated", 0) < cutoff:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        sid = str(rec.get("id"))
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        env, ide = rec.get("env", ""), rec.get("ide", "")
        badges = ["WSL"] if env == "wsl" else []
        if ide == "vscode":
            badges.append("VS")
        where = ["Claude Code"]
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
            "cwd": rec.get("cwd", ""),
            "title": rec.get("title") or "session",
            "state": rec.get("state", "idle"),
            "message": rec.get("message", ""),
            "detail": rec.get("cwd", ""),
            "changed": rec.get("changed", rec.get("updated", 0)),
            "path": path,
            "hwnd": rec.get("hwnd"),
            "subagents": _active_agents(rec),
            "request": rec.get("request") or {},
            "pid": rec.get("pid"),
            "sid": str(rec.get("id")),
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
            "clientInfo": {"name": "claude-pet", "version": "1.0"},
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
        "badges": ["WB"],
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
        print(f"[claude-pet] robot sprites unavailable ({e}); using the mole", file=sys.stderr)
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
    return {"done": ("green",) * 3, "error": ("red", "amber", "red"), "idle": ("off",) * 3}.get(state, ("off",) * 3)


def sprite_photo(key, image, w, h):
    """Nearest-neighbour resize + PhotoImage, cached by (key, w, h)."""
    from PIL import ImageTk
    k = (key, w, h)
    ph = _PHOTOS.get(k)
    if ph is None:
        if len(_PHOTOS) > 400:
            _PHOTOS.clear()
        from PIL import Image
        ph = _PHOTOS[k] = ImageTk.PhotoImage(image.resize((max(1, w), max(1, h)), Image.NEAREST))
    return ph


def mono(size):
    return ("Consolas", max(4, int(round(size * SCALE["v"]))))


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
        c = self.canvas = tk.Canvas(app.frame, width=px(PET_W), height=px(PET_H), bg=TRANSPARENT,
                                    highlightthickness=0, bd=0)
        c.bind("<ButtonPress-1>", app.on_press)
        c.bind("<B1-Motion>", app.on_drag)
        c.bind("<ButtonRelease-1>", lambda e: app.on_release(e, self))
        c.bind("<Button-3>", lambda e: app.on_menu(e, self))
        if IS_MAC:  # macOS Tk: right button is Button-2; ctrl-click is the trackpad equivalent
            c.bind("<Button-2>", lambda e: app.on_menu(e, self))
            c.bind("<Control-Button-1>", lambda e: app.on_menu(e, self))
        c.bind("<Enter>", lambda e: app.show_tip(self))
        c.bind("<Leave>", lambda e: app.hide_tip())
        self._on_bubble = False  # the check/cross/? bubble ("ans" items) opens the details popup when clicked
        c.tag_bind("ans", "<Enter>", lambda e: c.config(cursor="hand2"))
        c.tag_bind("ans", "<Leave>", lambda e: c.config(cursor=""))
        c.tag_bind("ans", "<ButtonPress-1>", lambda e: setattr(self, "_on_bubble", True))

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
        self._finish()

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
        tw_, th_ = PET_W - 6, 26
        try:
            s_ = SCALE["v"]
            im = bubble_image(tw_, th_, T["tag_bg"], T["tag_outline"], None)
            ph = sprite_photo(("tag", tw_, th_, T["tag_bg"], T["tag_outline"]), im, int(round(tw_ * s_)), int(round(th_ * s_)))
            c.create_image(3, PET_H - 28, image=ph, anchor="nw")
        except Exception:  # no Pillow: a plain rectangle
            c.create_rectangle(3, PET_H - 28, PET_W - 3, PET_H - 2, fill=T["tag_bg"], outline=T["tag_outline"], width=1)
        bx = 3  # environment badges, top-left
        for b in self.data.get("badges", []):
            w = 7 + 6 * len(b)
            c.create_rectangle(bx, 2, bx + w, 14, fill=BADGE_COLORS.get(b, "#6b7280"), outline="")
            c.create_text(bx + w / 2, 8, text=b, fill="white", font=fnt(6, "bold"))
            bx += w + 2
        name = self.data.get("title", "")
        if len(name) > 15:
            name = name[:14] + "\u2026"
        c.create_text(PET_W / 2, PET_H - 20, text=name, fill=T["tag_fg"], font=fnt(8))
        label = LABELS.get(st, st)
        if self.data.get("subagents"):
            label += f" +{self.data['subagents']}"
        font = fnt(7, "bold")
        x0 = int(PET_W / 2 - (6 + self._text_w(font, label)) / 2)
        c.create_rectangle(x0, PET_H - 10, x0 + 3, PET_H - 7, fill=col, outline="")  # the state light
        c.create_text(x0 + 6, PET_H - 9, text=label, anchor="w", fill=dark if T["label_dark"] else col, font=font)

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
            elif st in ("done", "idle"):  # rising z's
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

    def _bubble(self, x1, y1, x2, y2, tail_x, fill, outline, tags=()):
        """Pixel-art speech bubble (see bubble_image) with its top-left at (x1, y1); the tail points down at tail_x."""
        w, h = max(14, int(round(x2 - x1))), max(9, int(round(y2 - y1)))
        tcx = max(5, min(w - 6, int(round(tail_x - x1))))
        im = bubble_image(w, h, fill, outline, tcx)
        s = SCALE["v"]
        ph = sprite_photo(("bubble", w, h, fill, outline, tcx), im, int(round(w * s)), int(round(im.height * s)))
        self.canvas.create_image(int(round(x1)), int(round(y1)), image=ph, anchor="nw", tags=tags)

    def _mark(self, kind, cx, cy, k=1, tags=()):
        im = mark_image(kind, k)
        s = SCALE["v"]
        ph = sprite_photo(("mark", kind, k), im, int(round(im.width * s)), int(round(im.height * s)))
        self.canvas.create_image(int(round(cx)), int(round(cy)), image=ph, anchor="center", tags=tags)

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
        heads = HEAD_LAYOUTS[min(3, int(self.data.get("subagents", 0) or 0)) + 1]
        top_main = ground
        for i, (dx, k) in enumerate(heads):
            main = i == len(heads) - 1
            ph = t + i * 1.7
            cx, dy, squash = cx0 + dx, 0.0, 1.0
            face = STATE_FACE.get(st, "sleep")
            if st == "working":
                dy = -abs(math.sin(ph * (5 if main else 4.2))) * 3 * k
                if (ph % 4) < 0.15:
                    face = "blink"
            elif st == "needs_input":  # hops while it waits for you
                dy = (-abs(math.sin(ph * 7)) * 8 if not self.acked else -1.5 * math.sin(ph * 3)) * (1 if main else 0.6)
                squash = 1.0 if dy < -1.2 else 0.94
            elif st == "error" and not self.acked:
                cx += 2 * math.sin(ph * 25)
            elif st == "idle":
                squash = 1 + 0.025 * math.sin(ph * 2)  # slow breathing
            w, h = int(round(rw0 * k * s)), int(round(rh0 * k * squash * s))
            sw = int(round(rw0 * k * 1.1 * s))
            c.create_image(cx, ground + 2, image=sprite_photo("shadow", shadow, sw, max(2, int(sw * 0.25))), anchor="center")
            im = robot_image(face, light_cycle(st, ph))
            c.create_image(cx, ground + dy + rise, image=sprite_photo(("robot", face, light_cycle(st, ph)), im, w, h), anchor="s")
            if main:
                top_main = ground + dy - rh0 * k * squash

        if not emerging:
            bx1, bx2 = cx0 - 34, cx0 + 36
            by1 = 15.0  # below the badge row
            by2 = 36.0 if st == "needs_input" else min(top_main + 2, 40.0)  # the hop must not squash the bubble
            tail = cx0 - 6  # off the antenna
            if st == "working":  # hacker-screen bubble
                self._bubble(bx1, by1, bx2, by2, tail, "#0b1220", "#34d399")
                font = mono(5)
                cw = self._char_w(font)  # real width of one character, in drawing units
                rows = self._terminal_rows(cols=max(6, int((bx2 - bx1 - 12) / cw)))
                for i, row in enumerate(rows):
                    y = by1 + 6 + i * (by2 - by1 - 8) / 2.6
                    c.create_text(bx1 + 5, y, text=row[:-1], anchor="w", fill="#22c55e", font=font)
                    c.create_text(bx1 + 5 + cw * (len(row) - 1), y, text=row[-1], anchor="w", fill="#d1fae5", font=font)
            elif st in ("done", "error"):
                top = by2 - 19  # a small bubble, centred on its tail
                self._bubble(cx0 - 22, top, cx0 + 10, by2, tail, "#fafafa", "#111827")
                bob = 1 if int(t * 3) % 2 else 0  # a one-pixel bob
                self._mark("ok" if st == "done" else "no", cx0 - 6, (top + by2) / 2 - bob)
            elif st == "needs_input":  # the answer bubble: check / cross / question mark (click it for the popup)
                self._bubble(bx1 + 4, by1, bx2 - 6, by2, tail, "#fafafa", "#111827", tags=("ans",))
                y, pulse = (by1 + by2) / 2, int(t * 2) % 3
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
        elif st == "done":  # rising z's
            for i in range(2):
                ph = (t * 0.6 + i * 0.5) % 1
                zx, zy, s = cx + 20 + ph * 8, top - 4 - ph * 18, 3 + ph * 3
                c.create_line(zx - s, zy - s, zx + s, zy - s, zx - s, zy + s, zx + s, zy + s, width=2, fill="#e5e7eb")

        self._draw_tag()


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


def focus_hwnd(hwnd):
    """Bring a window to the front. True if the window exists (and we asked for it)."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        u = _user32()
        if not u.IsWindow(int(hwnd)):
            return False
        if u.IsIconic(int(hwnd)):
            u.ShowWindow(int(hwnd), 9)  # SW_RESTORE
        u.SetForegroundWindow(int(hwnd))
        if u.GetForegroundWindow() != int(hwnd):  # foreground lock: a tap of ALT lets the call through
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x12, 0, 2, 0)
            u.SetForegroundWindow(int(hwnd))
        return True
    except Exception:
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


def focus_wsl_terminal(d):
    """Best effort for WSL sessions (the hook can't see Windows process ids): pick the Windows Terminal window
    whose title mentions the distro or project, else the only/frontmost one."""
    wins = terminal_windows()
    if not wins:
        return False
    needles = [n.lower() for n in (d.get("distro"), d.get("title")) if n]
    for hwnd, title in wins:
        if any(n in title.lower() for n in needles):
            return focus_hwnd(hwnd)
    return focus_hwnd(wins[0][0])


# --------------------------------------------------------------------------- app
class Detail:
    """A styled card, centred on the screen, with the full context of a session that needs the user. The robot
    beside it asks with a "?" and reacts when you answer."""

    ACCENT = COLORS["needs_input"]

    def __init__(self, app, key, item):
        self.app, self.key, self._shown, self.sent = app, key, None, None
        self._after, self._t0, self._moved, self._size = None, time.time(), False, None
        bg, fg, msg_fg, muted = T["bubble_bg"], T["bubble_fg"], T["bubble_msg"], T["bubble_muted"]
        w = self.win = tk.Toplevel(app.root)
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
        self.head = tk.Label(top, bg=bg, fg=fg, anchor="w", font=("Segoe UI", 15, "bold"))
        self.head.pack(side="left", fill="x", expand=True)
        close = tk.Label(top, text="\u2715", bg=bg, fg=muted, cursor="hand2", font=("Segoe UI", 12), padx=4)
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        tk.Label(right, text="needs your permission", bg=bg, fg=self.ACCENT, anchor="w",
                 font=("Segoe UI", 9, "bold")).pack(fill="x")
        self.where = tk.Label(right, bg=bg, fg=muted, anchor="w", justify="left", wraplength=430, font=("Segoe UI", 8))
        self.where.pack(fill="x", pady=(2, 0))
        tk.Frame(right, bg=T["tip_border"], height=1).pack(fill="x", pady=8)
        for widget in (top, self.head):  # drag the card by its header
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        self.body = tk.Frame(right, bg=bg)
        self.body.pack(fill="x")
        self.ask = tk.Label(self.body, bg=bg, fg=fg, anchor="w", justify="left", wraplength=430, font=("Segoe UI", 11, "bold"))
        self.desc = tk.Label(self.body, bg=bg, fg=msg_fg, anchor="w", justify="left", wraplength=430, font=("Segoe UI", 10))
        self.code = tk.Text(self.body, height=1, width=60, wrap="word", bg="#0b1220", fg="#d1fae5", relief="flat",
                            borderwidth=0, highlightthickness=0, padx=10, pady=8, font=("Consolas", 9), state="disabled",
                            insertbackground="#d1fae5")
        wait_row = tk.Frame(right, bg=bg)
        wait_row.pack(fill="x", pady=(8, 0))
        self.wait = tk.Label(wait_row, bg=bg, fg=muted, anchor="w", font=("Segoe UI", 9))
        self.wait.pack(side="left")
        self.golink = tk.Label(wait_row, text="Go to window ›", bg=bg, fg=self.ACCENT, cursor="hand2",
                               font=("Segoe UI", 9, "underline"))
        self.golink.bind("<Button-1>", lambda e: self.go())
        self.note = tk.Label(right, bg=bg, fg=muted, anchor="w", justify="left", wraplength=430, font=("Segoe UI", 8))
        self.note.pack(fill="x", pady=(2, 8))
        self.answer_row = tk.Frame(right, bg=bg)
        self.btn_deny = tk.Button(self.answer_row, text="Deny", width=12, bg=bg, fg="#dc2626", activebackground="#fee2e2",
                                  activeforeground="#b91c1c", relief="solid", bd=1, cursor="hand2",
                                  font=("Segoe UI", 10, "bold"), command=lambda: self.answer("deny"))
        self.btn_allow = tk.Button(self.answer_row, text="Allow once", width=14, bg="#16a34a", fg="white",
                                   activebackground="#15803d", activeforeground="white", relief="flat", cursor="hand2",
                                   font=("Segoe UI", 10, "bold"), command=lambda: self.answer("allow"))
        self.btn_deny.pack(side="left", ipady=3)
        self.btn_allow.pack(side="right", ipady=4)
        self.answer_row.pack(fill="x")
        self.go_row = tk.Frame(right, bg=bg)
        self.btn_go = tk.Button(self.go_row, text="Go to window", bg=self.ACCENT, fg="#111827", activebackground="#d97706",
                                activeforeground="#111827", relief="flat", cursor="hand2", font=("Segoe UI", 10, "bold"),
                                command=self.go)
        self.btn_go.pack(side="left", ipady=4, ipadx=14)
        self.update(item)
        self._place(first=True)
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
        x = (w.winfo_screenwidth() - size[0]) // 2 + 28 * n
        y = max(20, (w.winfo_screenheight() - size[1]) // 2 - 30 + 28 * n)
        w.geometry(f"+{x}+{y}")

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
        if self.sent == "Allow once":
            face, state, dy = "joy", "done", -abs(math.sin(t * 6)) * 4
        elif self.sent == "Deny":
            face, state, dy = "worried", "error", 0.0
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
                              font=("Segoe UI", int(9 + 7 * (1 - ph)), "bold"))

    def update(self, item):
        req = item.get("request") or {}
        self.head.config(text=item.get("title", "session"))
        self.where.config(text=" \u00b7 ".join(x for x in (item.get("where"), item.get("detail")) if x))
        for widget in (self.ask, self.desc, self.code):
            widget.pack_forget()
        if req:
            self.ask.config(text=f"Allow Claude to use {req.get('tool') or 'this tool'}?")
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
        can_answer = bool(req.get("answerable")) and not self.sent
        if self.sent:
            note = f"Sent: {self.sent}. Claude Code will carry on in a moment."
        elif can_answer:
            note = ("Allow once or Deny answers this prompt right from here, or answer in the session window "
                    "(Go to window). If you do neither, the normal prompt appears.")
        elif req.get("source") == "transcript":
            note = ("This session type (the VS Code extension) sends no permission events, so the pet can't answer for it. "
                    "This is what Claude is asking, so you know what to approve there. Press Go to window to jump there.")
        elif req:
            if ANSWER_WAIT["v"] <= 0:
                note = ("Answering from the pet is switched off (Answer timeout = off). "
                        "Answer in the session window: press Go to window.")
            else:
                note = (f"The pet's {fmt_wait(ANSWER_WAIT['v'])} answer window has passed. "
                        "Answer in the session window: press Go to window.")
        else:
            note = ("No command details were reported for this prompt, so there is nothing to answer here. "
                    "Press Go to window to jump to the session.")
        self.note.config(text=note)
        if can_answer:
            self.answer_row.pack(fill="x", after=self.note)
        else:
            self.answer_row.pack_forget()
        if can_answer or self.sent:  # small link while the answer buttons (or the "sent" note) are showing
            self.golink.pack(side="right")
            self.go_row.pack_forget()
        else:  # nothing to answer from here: the big button
            self.golink.pack_forget()
            self.go_row.pack(fill="x", after=self.note)
        self._place()

    def go(self):
        """Bring the session's window to the front and dismiss this popup."""
        self.app.focus_key(self.key)
        self.close()

    def answer(self, behavior):
        if self.app.send_answer(self.key, behavior):
            self.sent = "Allow once" if behavior == "allow" else "Deny"
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
        write_answer_wait(ANSWER_WAIT["v"])
        root = self.root = tk.Tk()
        root.title("Claude Pet")
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
        self.frame.pack(side="top", anchor="e")

        self.pets, self.order, self.prev_states = {}, [], {}
        self.details = {}
        self._slots, self._slot_n = {}, 0
        self._last_items, self._last_size = [], None
        self.first_refresh = True
        self.muted = tk.BooleanVar(value=not self.cfg["sounds"])
        self.tip = None
        self.drag = None
        self.menu_pet = None
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
        self.dismiss_menu_index = m.index("end")
        m.add_command(label="Size...", command=self.open_size_slider)
        self.size_menu_index = m.index("end")
        m.add_command(label="Reset size", command=self.reset_scale)
        m.add_command(label="Answer timeout...", command=self.open_answer_slider)
        m.add_separator()
        m.add_checkbutton(label="Mute sounds", variable=self.muted)
        m.add_command(label="Clear finished", command=self.clear_finished)
        m.add_separator()
        m.add_command(label="Workbench: " + ("starting…" if self.wb else "off"), state="disabled")
        self.wb_menu_index = m.index("end")
        m.add_separator()
        m.add_command(label="Quit", command=root.destroy)

        self.anchor = [root.winfo_screenwidth() - 24, root.winfo_screenheight() - 60]  # bottom-right
        self.size_win, self._menu_xy = None, None
        self.refresh()
        self.animate()

    # ---- data
    def collect(self):
        items = read_claude_code_sessions(self.cfg)
        if self.wb:
            items += self.wb.items()
        hide = self.cfg["hide_done_after_minutes"] * 60
        now = time.time()
        items = [i for i in items
                 if not (i["state"] in ("done", "idle") and i.get("changed") and now - i["changed"] > hide)]
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
            items = self.collect() or [{
                "key": "_none", "source": "", "title": "no sessions", "state": "idle",
                "message": "Waiting for Claude Code / Workbench activity", "detail": "", "changed": 0}]
            keys = [i["key"] for i in items]
            self._last_items = items
            self._heartbeat()
            for k in list(self.pets):
                if k not in keys:
                    self.pets.pop(k).destroy()
                    self.prev_states.pop(k, None)

            now = time.time()
            spawned = 0
            for it in items:
                pet = self.pets.get(it["key"])
                if pet is None:
                    pet = self.pets[it["key"]] = Pet(self, it["key"])
                    if it["key"] != "_none":  # new conversation: pop up, staggered if several arrive together
                        pet.born = now + EMERGE_STAGGER * spawned
                        spawned += 1
                old = self.prev_states.get(it["key"])
                pet.data = it
                if old != it["state"]:
                    pet.acked = False
                    pet.last_remind = now
                    if not self.first_refresh and it["key"] != "_none":
                        self.alert(it["state"], old, it)
                    self.prev_states[it["key"]] = it["state"]
                elif (it["state"] == "needs_input" and not pet.acked and self.cfg["remind_seconds"] > 0
                      and now - pet.last_remind > self.cfg["remind_seconds"]):
                    self.alert("needs_input", old, it)
                    pet.last_remind = now

            if keys != self.order:
                for k in self.order:
                    if k in self.pets:
                        self.pets[k].canvas.pack_forget()
                for k in keys:
                    self.pets[k].canvas.pack(side="left", padx=2)
                self.order = keys
                self.reposition()

            self.sync_bubbles(items)
            if self.wb:
                self.menu.entryconfigure(self.wb_menu_index, label="Workbench: " + self.wb.status)
            self.first_refresh = False
        except Exception as e:
            print(f"[claude-pet] refresh error: {e}", file=sys.stderr)
        finally:
            self.root.after(self.cfg["poll_ms"], self.refresh)

    # ---- bubbles
    def sync_bubbles(self, items):
        """The popups follow their sessions: refreshed while they need you, closed when they no longer do."""
        needing = {i["key"] for i in items if i["state"] == "needs_input" and i["key"] != "_none"}
        by_key = {i["key"]: i for i in items}
        for k in list(self.details):
            if k in needing:
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

    def send_answer(self, key, behavior):
        """Hand the user's click to the waiting PermissionRequest hook. Returns True if it was written."""
        item = next((i for i in self._last_items if i["key"] == key), None)
        req = (item or {}).get("request") or {}
        if not req.get("answerable") or behavior not in ("allow", "deny"):
            return False
        name = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in str(req.get("id") or item.get("sid") or "request"))[:120]
        try:
            os.makedirs(answers_dir(), exist_ok=True)
            path = os.path.join(answers_dir(), name + ".json")
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"behavior": behavior, "t": time.time()}, f)
            os.replace(path + ".tmp", path)
            return True
        except OSError:
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
        for d in list(self.details.values()):  # their colours are fixed at creation
            d.destroy()

    # ---- resizing (the Size... slider in the right-click menu)
    def set_scale(self, v):
        v = clamp_scale(v)
        if abs(v - SCALE["v"]) < 0.005:
            return
        SCALE["v"] = v
        for pet in self.pets.values():
            pet.canvas.config(width=px(PET_W), height=px(PET_H))

    def _place_above_pet(self, w):
        """Put a small window directly above the pet overlay, centred on it (just below it if there is no room)."""
        w.update_idletasks()
        self.root.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
        ox, oy, ow, oh = self.root.winfo_x(), self.root.winfo_y(), self.root.winfo_width(), self.root.winfo_height()
        x = max(0, min(ox + ow // 2 - ww // 2, sw - ww - 10))
        y = oy - wh - 10
        if y < 0:
            y = max(0, min(oy + oh + 10, sh - wh - 60))
        w.geometry(f"+{x}+{y}")

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
        pct = tk.Label(w, width=6, font=("Segoe UI", 12, "bold"))

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

        tk.Label(w, text="Pet size", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
        scale = ttk.Scale(w, from_=0.3, to=3.0, orient="horizontal", length=260, variable=var, command=apply)
        scale.grid(row=1, column=0, padx=(14, 6), pady=8)
        scale.bind("<ButtonRelease-1>", commit)
        pct.grid(row=1, column=1, padx=(0, 14))
        row = tk.Frame(w)
        row.grid(row=2, column=0, columnspan=2, sticky="e", padx=14, pady=(0, 12))
        tk.Button(row, text="100%", width=8, command=reset).pack(side="left", padx=(0, 6))
        tk.Button(row, text="Done", width=8, command=close).pack(side="left")
        w.protocol("WM_DELETE_WINDOW", close)
        w.bind("<Escape>", lambda e: close())
        apply()
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
        val = tk.Label(w, width=10, font=("Segoe UI", 12, "bold"))

        def apply(_v=None):
            ANSWER_WAIT["v"] = clamp_wait(round(var.get() / 5) * 5)  # steps of 5 s
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

        tk.Label(w, text="Answer permission prompts from the pet for up to:", font=("Segoe UI", 10, "bold")
                 ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 0))
        scale = ttk.Scale(w, from_=0, to=300, orient="horizontal", length=260, variable=var, command=apply)
        scale.grid(row=1, column=0, padx=(14, 6), pady=8)
        scale.bind("<ButtonRelease-1>", commit)
        val.grid(row=1, column=1, padx=(0, 14))
        tk.Label(w, justify="left", wraplength=360, fg="#6b7280", font=("Segoe UI", 8),
                 text="0 turns it off: the pet only shows the question and you answer in the session window. "
                      "While the pet waits, Claude Code's own prompt is still shown and the first answer wins, "
                      "except for background subagents, where Claude Code may hold its prompt until this time is up."
                 ).grid(row=2, column=0, columnspan=2, sticky="w", padx=14)
        row = tk.Frame(w)
        row.grid(row=3, column=0, columnspan=2, sticky="e", padx=14, pady=(8, 12))
        tk.Button(row, text="Default (3 min)", command=reset).pack(side="left", padx=(0, 6))
        tk.Button(row, text="Done", width=8, command=close).pack(side="left")
        w.protocol("WM_DELETE_WINDOW", close)
        w.bind("<Escape>", lambda e: close())
        apply()
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
            self.open_in_vscode(pet)  # `code <folder>` raises the right VS Code window
        elif os.name == "nt":
            if not focus_hwnd(d.get("hwnd")) and d.get("env") == "wsl":
                focus_wsl_terminal(d)

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
            self.beep(True)
            self.root.lift()
            self.root.attributes("-topmost", True)
        elif state == "done" and old in ("working", "needs_input") and self.cfg["sound_on_done"]:
            self.beep(False)

    def beep(self, urgent):
        if self.muted.get():
            return
        if winsound:
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION if urgent else winsound.MB_ICONASTERISK)
            except RuntimeError:
                pass
        elif IS_MAC:
            try:
                sound = "Funk" if urgent else "Glass"
                subprocess.Popen(["afplay", f"/System/Library/Sounds/{sound}.aiff"])
            except OSError:
                self.root.bell()
        else:
            self.root.bell()

    def dismiss(self, pet):
        d = pet.data
        if d.get("source") == "CC" and d.get("path"):
            try:
                os.remove(d["path"])
            except OSError:
                pass
        elif d.get("source") == "WB" and self.wb:
            self.wb.dismiss(d["key"])

    def clear_finished(self):
        for pet in list(self.pets.values()):
            if pet.data.get("state") in ("done", "idle"):
                self.dismiss(pet)

    # ---- window
    def reposition(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        self.root.geometry(f"+{max(0, int(self.anchor[0] - w))}+{max(0, int(self.anchor[1] - h))}")

    def animate(self):
        t = time.time() - self.t0
        for p in self.pets.values():
            p.draw(t)
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
        on_bubble, pet._on_bubble = pet._on_bubble, False
        if self.drag and self.drag[4]:
            self.root.update_idletasks()
            self.anchor = [self.root.winfo_x() + self.root.winfo_width(),
                           self.root.winfo_y() + self.root.winfo_height()]
        elif on_bubble and pet.key != "_none":
            pet.acked = True
            self.open_detail(pet.key)  # clicking the check/cross/? bubble: the full question and the answer buttons
        else:
            pet.acked = True  # click: acknowledge (stops jumping / reminders) and go to the session's window
            if pet.key != "_none":
                self.focus_session(pet)
        self.drag = None

    def on_menu(self, e, pet=None):
        self.hide_tip()
        self.menu_pet = pet
        self._menu_xy = (e.x_root, e.y_root)
        self.menu.entryconfigure(self.size_menu_index, label=f"Size...  ({int(round(SCALE['v'] / SCALE_UNIT * 100))}%)")
        ok = bool(pet and pet.data.get("source") == "CC" and pet.data.get("cwd"))
        self.menu.entryconfigure(self.vscode_menu_index, state="normal" if ok else "disabled")
        self.menu.entryconfigure(self.dismiss_menu_index,
                                 state="normal" if pet and pet.key != "_none" else "disabled")
        self.menu.tk_popup(e.x_root, e.y_root)

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
        args = [code]
        if d.get("env") == "wsl":
            args += ["--remote", f"wsl+{d.get('distro') or 'Ubuntu'}"]
        args.append(d["cwd"])
        try:
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            self.root.bell()

    def show_tip(self, pet):
        self.hide_tip()
        d = pet.data
        lines = [d.get("title", ""), f"{d.get('where') or SOURCE_NAMES.get(d.get('source'), '')} · {LABELS.get(d.get('state'), d.get('state'))}".strip(" ·")]
        if d.get("message"):
            lines.append(d["message"][:200])
        if d.get("detail"):
            lines.append(d["detail"])
        if d.get("changed"):
            lines.append("since " + ago(d["changed"]))
        if d.get("pid"):
            lines.append(f"PID {d['pid']}" + (" (WSL)" if d.get("env") == "wsl" else ""))
        tip = self.tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tip.configure(bg=T["tip_border"], padx=1, pady=1)
        tk.Label(tip, text="\n".join(lines), justify="left", bg=T["tip_bg"], fg=T["tip_fg"],
                 font=("Segoe UI", 9), padx=8, pady=6, wraplength=340).pack()
        tip.update_idletasks()
        x = pet.canvas.winfo_rootx()
        y = pet.canvas.winfo_rooty() - tip.winfo_reqheight() - 6
        tip.geometry(f"+{max(0, x)}+{max(0, y)}")

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
