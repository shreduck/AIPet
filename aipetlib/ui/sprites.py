"""Sprite loading, pixel rendering and shared image/font caches.

Services are supplied by the compatibility entry point; this module does not import aipet.
"""
import json
import os
import tkinter as tk


def load_sprites(services, directory=None):
    """Load the robot sprites (needs Pillow + assets/sprites). Returns a dict, or False if unavailable."""
    if directory is None and services._SPR["state"] is not None:
        return services._SPR["state"]
    try:
        from PIL import Image
        d = directory or services.SPRITE_DIR
        with open(os.path.join(d, "sprites.json"), encoding="utf-8") as f:
            meta = json.load(f)
        img = {"robot": Image.open(os.path.join(d, meta["robot"]["file"])).convert("RGBA")}
        img["shadow"] = services._solid_shadow(Image.open(os.path.join(d, "shadow.png")).convert("RGBA"))
        res = {"meta": meta, "img": img, "faces": {}}
    except Exception as e:
        services.log_error(f"robot sprites unavailable in {directory or services.SPRITE_DIR!r} ({e!r}); using the mole")
        res = False
    if directory is None:
        services._SPR["state"] = res
    return res


def _rgb(services, hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def robot_image(services, face, lights, spr=None):
    """The robot at native sprite resolution with a face painted on its screen and its three lights coloured.
    face: a key of FACE_PARTS; lights: three keys of LIGHT_COLORS."""
    spr = spr or services.load_sprites()
    key = (face, tuple(lights))
    if key in spr["faces"]:
        return spr["faces"][key]
    m = spr["meta"]["robot"]
    im = spr["img"]["robot"].copy()
    px = im.load()
    x0, y0, x1, y1 = m["screen"]
    cyan = services._rgb(spr["meta"].get("cyan", "#6ee7f3"))

    def stamp(pattern, cx, cy):
        h, w = len(pattern), len(pattern[0])
        for j, row in enumerate(pattern):
            for i, ch in enumerate(row):
                x, y = cx - w // 2 + i, cy - h // 2 + j
                if ch != "." and x0 <= x <= x1 and y0 <= y <= y1:
                    px[x, y] = (services.CYAN_LIGHT if ch == "2" else cyan) + (255,)

    eye, mouth = services.FACE_PARTS[face]
    if face == "ask":  # a big "?" in the middle of the screen
        stamp(services.QUESTION_PIXELS, (x0 + x1) // 2, (y0 + y1) // 2)
    else:
        for side in ("left", "right"):
            stamp(services.EYE_PIXELS[eye], *m["eyes"][side])
        stamp(services.MOUTH_PIXELS[mouth], *m["mouth"])
    for (lx, ly, _name), colour in zip(m["lights"], lights):  # each light is a 2x2 dot; (lx, ly) is its top-left
        for dx in (0, 1):
            for dy in (0, 1):
                if 0 <= lx + dx < im.width and 0 <= ly + dy < im.height and px[lx + dx, ly + dy][3]:
                    px[lx + dx, ly + dy] = services.LIGHT_COLORS[colour] + (255,)
    spr["faces"][key] = im
    return im


def state_image(services, state, spr=None):
    """Native-resolution robot for a state (the tray icon uses this)."""
    spr = spr or services.load_sprites()
    return services.robot_image(services.STATE_FACE.get(state, "sleep"), services.light_cycle(state, 0), spr)


def light_cycle(services, state, t):
    if state == "working":
        i = int(t * 4) % 3
        return tuple("amber" if j == i else ("green" if j == (i + 1) % 3 else "off") for j in range(3))
    if state == "needs_input":  # flashing amber: attention
        return ("amber",) * 3 if int(t * 3) % 2 else ("off", "amber", "off")
    if state == "auto":  # steady green: something was just auto-approved
        return ("green",) * 3
    return {"done": ("green",) * 3, "error": ("red", "amber", "red"), "idle": ("off",) * 3}.get(state, ("off",) * 3)


def _to_photo(services, im):
    if not services._PNG_PHOTOS["on"]:
        try:
            from PIL import ImageTk
            return ImageTk.PhotoImage(im)
        except Exception as e:  # e.g. TclError: invalid command name "PyImagingPhoto"
            services.log_error(f"ImageTk unavailable, using PNG images: {e!r}")
            services._PNG_PHOTOS["on"] = True
    import base64
    import io
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"), format="png")  # Tk 8.6+ reads PNG


def sprite_photo(services, key, image, w, h):
    """Nearest-neighbour resize + PhotoImage, cached by (key, w, h)."""
    k = (key, w, h)
    ph = services._PHOTOS.get(k)
    if ph is None:
        if len(services._PHOTOS) > 400:
            services._PHOTOS.clear()
        from PIL import Image
        ph = services._PHOTOS[k] = services._to_photo(image.resize((max(1, w), max(1, h)), Image.NEAREST))
    return ph


def pixel_rects(services, key, image):
    """[(x0, y0, x1, y1, "#rrggbb")] covering the opaque pixels of a PIL RGBA image, in image pixels. Horizontal runs
    of one colour are merged, then identical runs on consecutive rows, so a sprite needs a few hundred rectangles."""
    rects = services._RUNS.get(key)
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
    if len(services._RUNS) > 600:
        services._RUNS.clear()
    services._RUNS[key] = rects
    return rects


def mono(services, size):
    """A console font at the pet's scale (Consolas on Windows, Menlo on macOS)."""
    return (services.MONO_FAMILY, max(4, int(round(size * services.SCALE["v"]))))


def bubble_image(services, w, h, fill, outline, tail_cx):
    """A w x h speech bubble plus a tail, as one shape: 1 px outline, round corners, and the tail grows out of the box
    (no seam), all at sprite resolution so it scales with the robot."""
    key = ("bubble", w, h, fill, outline, tail_cx)
    if key in services._IMG_CACHE:
        return services._IMG_CACHE[key]
    from PIL import Image
    H = h + (services.BUBBLE_TAIL if tail_cx is not None else 0)
    mask = [[False] * w for _ in range(H)]
    for y in range(h):
        inset = services._BUBBLE_INSET[y] if y < len(services._BUBBLE_INSET) else (
            services._BUBBLE_INSET[h - 1 - y] if h - 1 - y < len(services._BUBBLE_INSET) else 0)
        for x in range(inset, w - inset):
            mask[y][x] = True
    for r in range(services.BUBBLE_TAIL if tail_cx is not None else 0):  # 7, 5, 3, 1 px wide
        for x in range(tail_cx - (3 - r), tail_cx + (3 - r) + 1):
            if 0 <= x < w:
                mask[h + r][x] = True
    fill_c, out_c = services._rgb(fill) + (255,), services._rgb(outline) + (255,)
    im = Image.new("RGBA", (w, H), (0, 0, 0, 0))
    px = im.load()
    for y in range(H):
        for x in range(w):
            if mask[y][x]:
                edge = any(not (0 <= nx < w and 0 <= ny < H and mask[ny][nx])
                           for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
                px[x, y] = out_c if edge else fill_c
    services._IMG_CACHE[key] = im
    return im


def mark_image(services, kind, k=1):
    """A check / cross / question-mark glyph in pixel art, enlarged k times (nearest neighbour)."""
    key = ("mark", kind, k)
    if key not in services._IMG_CACHE:
        from PIL import Image
        rows = services.MARKS[kind]
        im = Image.new("RGBA", (len(rows[0]), len(rows)), (0, 0, 0, 0))
        px = im.load()
        colour = services._rgb(services.MARK_COLORS[kind]) + (255,)
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch == "#":
                    px[x, y] = colour
        services._IMG_CACHE[key] = im.resize((im.width * k, im.height * k), Image.NEAREST) if k > 1 else im
    return services._IMG_CACHE[key]


def _solid_shadow(services, im):
    """The pet window is colour-keyed transparent, so semi-transparent pixels would blend with the key colour (a magenta
    halo). Turn the soft shadow into two solid tones."""
    out = im.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            a = px[x, y][3]
            px[x, y] = (0, 0, 0, 0) if a < 40 else ((150, 150, 166, 255) if a < 100 else (118, 118, 136, 255))
    return out
