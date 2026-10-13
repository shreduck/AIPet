"""Sprite loading, pixel rendering and the shared image/font caches.

This module owns the robot's pixel data (faces, eyes, mouths, lights, marks, bubbles) and the image caches. It
imports no application entry point: the sprite folder comes from aipetlib.runtime.paths, the drawing scale from
aipetlib.runtime.preferences and the console font from aipetlib.ui.theme.
"""
import json
import math
import os
import sys
import tkinter as tk

from ..runtime import diagnostics, preferences
from ..runtime.paths import SPRITE_DIR
from .theme import UI_FONT

IS_MAC = sys.platform == "darwin"

_SPR = {"state": None}  # None = not tried yet, False = unavailable (no Pillow / no files) -> the mole is used instead
_PHOTOS = {}  # (key, w, h) -> ImageTk.PhotoImage, so a frame never re-resizes the same sprite
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


_PNG_PHOTOS = {"on": IS_MAC}  # macOS: Pillow's ImageTk bridge often can't reach the app's Tk, so hand Tk a PNG instead
_RUNS = {}
MONO_FAMILY = UI_FONT

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
        diagnostics.log_error(f"robot sprites unavailable in {directory or SPRITE_DIR!r} ({e!r}); using the mole")
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


def _to_photo(im):
    if not _PNG_PHOTOS["on"]:
        try:
            from PIL import ImageTk
            return ImageTk.PhotoImage(im)
        except Exception as e:  # e.g. TclError: invalid command name "PyImagingPhoto"
            diagnostics.log_error(f"ImageTk unavailable, using PNG images: {e!r}")
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


def mono(size):
    """A console font at the pet's scale (Consolas on Windows, Menlo on macOS)."""
    return (MONO_FAMILY, max(4, int(round(size * preferences.SCALE["v"]))))


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
