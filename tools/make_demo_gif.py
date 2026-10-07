#!/usr/bin/env python3
"""Render an animated GIF of AIPet pets for the website, straight from the app's sprites (crisp pixel art instead of
a resized screenshot). Four pets in a 2x2 grid: working, needs you, done and idle, each with its name tag and badges.

    python tools/make_demo_gif.py [out.gif] [--bg "#eef3f4"] [--unit 2]

Mirrors the drawing in aipet.py (PetApp robot style, light theme): same layout units, sprites, bubbles, marks and
motion formulas. Text uses DejaVu fonts (or Pillow's default) instead of Segoe UI / Consolas.
"""
import math
import os
import random
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    import tkinter  # noqa: F401
except ImportError:  # aipet imports tkinter at module level; the sprite code doesn't need it
    stub = types.ModuleType("tkinter")
    stub.Tk = stub.Frame = stub.Toplevel = stub.Canvas = object
    sys.modules["tkinter"] = stub

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import aipet as core  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = args[0] if args else os.path.join(ROOT, "demo.gif")
BG = sys.argv[sys.argv.index("--bg") + 1] if "--bg" in sys.argv else "#eef3f4"
U = int(sys.argv[sys.argv.index("--unit") + 1]) if "--unit" in sys.argv else 2  # pixels per drawing unit
FRAMES, FPS = 72, 12  # 6 s: the compact pet's queue moves on halfway

core.set_theme("light")
T = core.T
SPR = core.load_sprites()
W, H = core.CANVAS_W, core.CANVAS_H
G, TRIM = core.BADGE_GUTTER, core.TOP_TRIM

PETS = [  # (title, state, badges)
    ("duck-software", "working", ["Claude CLI"]),
    ("ai-pet", "needs_input", ["Codex WSL"]),
    ("Cowork", "done", ["Claude Cowork"]),
    ("notes", "idle", ["Claude VS"]),
]


def font(path_names, px):
    for name in path_names:
        for d in ("/usr/share/fonts/truetype/dejavu", "C:/Windows/Fonts", "/Library/Fonts", "/System/Library/Fonts"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, px)
    return ImageFont.load_default()


SANS = font(["DejaVuSans.ttf", "segoeui.ttf", "Arial.ttf"], int(6.8 * U))
SANS_SMALL = font(["DejaVuSans.ttf", "segoeui.ttf", "Arial.ttf"], int(5.0 * U))
SANS_Z = font(["DejaVuSans-Bold.ttf", "segoeuib.ttf", "Arial Bold.ttf"], int(8 * U))
MONO = font(["DejaVuSansMono.ttf", "consola.ttf", "Menlo.ttc"], int(5.6 * U))


def paste(canvas, im, w, h, x, y, anchor="nw"):
    """Nearest-neighbour scale im to w x h px and paste with its top-left derived from anchor, all in px."""
    im = im.resize((max(1, int(round(w))), max(1, int(round(h)))), Image.NEAREST)
    left = x - (im.width / 2 if anchor in ("center", "n", "s") else 0)
    top = y - (im.height / 2 if anchor == "center" else (im.height if anchor == "s" else 0))
    canvas.alpha_composite(im, (int(round(left)), int(round(top))))


def text_w(f, s):
    return f.getlength(s)


def draw_pet(title, st, badges, t, rnd, heads=None, hot=None, collapse=False):
    """One pet at drawing time t (seconds), in px, coordinates as in aipet.py after the gutter/trim move.
    Compact mode: heads = the sessions' states, the one in front first (as in PetApp._compact_items); hot = per badge,
    faint red while that session needs you; collapse = the expanded badges' collapse tab on top."""
    im = Image.new("RGBA", (W * U, H * U), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    X = lambda v: (v + G) * U  # noqa: E731  - drawing units -> px (x shifted right by the badge column)
    Y = lambda v: (v - TRIM) * U  # noqa: E731
    cx0, ground = core.PET_W / 2, 90
    rw0, rh0 = SPR["meta"]["robot"]["size"]
    states = heads[1:] + heads[:1] if heads else [st]  # drawn back to front: the session in front last
    layout = core.HEAD_LAYOUTS[len(states)]
    top_main = ground
    for i, ((dx, k), hst) in enumerate(zip(layout, states)):
        main = i == len(states) - 1
        ph = t + i * 1.7
        dy, squash, face = 0.0, 1.0, core.STATE_FACE.get(hst, "sleep")
        if hst == "working":
            dy = -abs(math.sin(ph * (5 if main else 4.2))) * 3 * k
            if (ph % 4) < 0.15:
                face = "blink"
        elif hst == "needs_input":
            dy = -abs(math.sin(ph * 7)) * 8 * (1 if main else 0.6)
            squash = 1.0 if dy < -1.2 else 0.94
        elif hst == "idle":
            squash = 1 + 0.025 * math.sin(ph * 2)
        sw = rw0 * k * 1.1
        paste(im, SPR["img"]["shadow"], sw * U, max(2, int(sw * 0.25)) * U, X(cx0 + dx), Y(ground + 2), "center")
        paste(im, core.robot_image(face, core.light_cycle(hst, ph)), rw0 * k * U, rh0 * k * squash * U,
              X(cx0 + dx), Y(ground + dy), "s")
        if main:
            top_main = ground + dy - rh0 * k * squash

    bx1, bx2, by1 = cx0 - 34, cx0 + 36, 15.0
    by2 = 36.0 if st == "needs_input" else min(top_main + 2, 40.0)
    tail = cx0 - 6

    def bubble(x1, y1, x2, y2, fill, outline):
        w, h = max(14, int(round(x2 - x1))), max(9, int(round(y2 - y1)))
        tcx = max(5, min(w - 6, int(round(tail - x1))))
        b = core.bubble_image(w, h, fill, outline, tcx)
        paste(im, b, b.width * U, b.height * U, X(int(round(x1))), Y(int(round(y1))))

    def mark(kind, x, y):
        m = core.mark_image(kind)
        paste(im, m, m.width * U, m.height * U, X(round(x)), Y(round(y)), "center")

    if st == "working":
        bubble(bx1, by1, bx2, by2, "#0b1220", "#34d399")
        for i, row in enumerate(rnd):
            y = by1 + 6 + i * (by2 - by1 - 8) / 2.6
            d.text((X(bx1 + 5), Y(y)), row[:-1], font=MONO, fill="#22c55e", anchor="lm")
            d.text((X(bx1 + 5) + text_w(MONO, row[:-1]), Y(y)), row[-1], font=MONO, fill="#d1fae5", anchor="lm")
    elif st == "done":
        top = by2 - 19
        bubble(cx0 - 22, top, cx0 + 10, by2, "#fafafa", "#111827")
        mark("ok", cx0 - 6, (top + by2) / 2 - (1 if int(t * 3) % 2 else 0))
    elif st == "needs_input":
        bubble(bx1 + 4, by1, bx2 - 6, by2, "#fafafa", "#111827")
        y, pulse = (by1 + by2) / 2, int(t * 2) % 3
        for i, (x, kind) in enumerate(((cx0 - 16, "ok"), (cx0 - 1, "no"), (cx0 + 14, "?"))):
            mark(kind, x, y - (1 if i == pulse else 0))
    else:  # idle: rising z's
        for i in range(3):
            ph = (t * 0.5 + i / 3) % 1
            zf = font(["DejaVuSans-Bold.ttf", "segoeuib.ttf"], int((6 + 4 * ph) * U))
            d.text((X(cx0 + 14 + ph * 14), Y(top_main + 4 - ph * 22)), "z", font=zf, fill="#9ca3af", anchor="mm")

    # name tag: short, at the bottom
    tw, th = core.PET_W - 6, core.TAG_H
    tag = core.bubble_image(tw, th, T["tag_bg"], T["tag_outline"], None)
    paste(im, tag, tw * U, th * U, X(3), Y(core.PET_H - 2 - th))
    name, room = title, (core.PET_W - 14) * U
    while name and text_w(SANS, name) > room:
        name = name[:-2] + "\u2026"
    d.text((X(core.PET_W / 2), Y(core.PET_H - 2 - th / 2 + 1.5)), name, font=SANS, fill=T["tag_fg"], anchor="mm")

    # badges: one per session, tabs on the tag's top edge (faint red while it needs you), wrapping upwards
    h, gap = 11, 2
    hot = hot or [st == "needs_input"] * len(badges)
    x, rowy = G + 6, core.PET_H - 2 - th - TRIM - h + 4
    for label, red in zip(badges + (["\u25be"] if collapse else []), hot + [False]):
        w = int(round(12 + text_w(SANS_SMALL, label) / U))
        if (x + w > G + core.PET_W - 6 and x > G + 6) or label == "\u25be":  # the collapse tab: its own top row
            x, rowy = G + 6, rowy - (h + gap)
        bg = core.BADGE_ATTENTION_BG if red else T["tag_bg"]
        b = core.bubble_image(w, h, bg, T["tag_outline"], None)
        paste(im, b, w * U, h * U, x * U, rowy * U)
        d.rectangle(((x + 3) * U, (rowy + 4) * U, (x + 6) * U - 1, (rowy + 7) * U - 1), fill=core.badge_dot(label))
        d.text(((x + 8) * U, (rowy + h / 2) * U), label, font=SANS_SMALL, fill="#111827" if red else T["tag_fg"], anchor="lm")
        x += w + gap
    return im


def main():
    random.seed(7)
    rows = ["".join(random.choice(core.TERMINAL_CHARS) for _ in range(15)) for _ in range(3)]
    cols, pad = 2, 6 * U
    frames = []
    for f in range(FRAMES):
        t = f / FPS
        for i in range(3):  # scroll the terminal rows a little each frame
            if random.random() < (0.55, 0.85, 0.4)[i]:
                rows[i] = rows[i][1:] + random.choice(core.TERMINAL_CHARS)
        sheet = Image.new("RGBA", (cols * W * U + pad * 3, 3 * H * U + pad * 5), BG)
        for n, (title, st, badges) in enumerate(PETS):
            pet = draw_pet(title, st, badges, t + n * 0.37, rows)
            sheet.alpha_composite(pet, (pad + (n % cols) * (W * U + pad), pad + (n // cols) * (H * U + pad)))
        # compact mode: one pet for three sessions; halfway, the first prompt is answered and the next steps forward
        y0 = 2 * H * U + pad * 4
        dd = ImageDraw.Draw(sheet)
        dd.line((pad, y0 - pad, sheet.width - pad, y0 - pad), fill="#d5e2e5", width=max(1, U // 2))
        dd.text((pad, y0 + pad // 2), "Compact mode", font=SANS_SMALL, fill="#536a73")
        dd.text((pad, y0 + pad // 2 + int(8 * U)), "one pet, a queue", font=SANS_SMALL, fill="#8aa0a8")
        first = f < FRAMES // 2
        sessions = ([("duck-software", "needs_input", "Claude CLI"), ("ai-pet", "needs_input", "Codex WSL"),
                     ("notes", "working", "Claude VS")] if first else
                    [("ai-pet", "needs_input", "Codex WSL"), ("duck-software", "working", "Claude CLI"),
                     ("notes", "working", "Claude VS")])
        pet = draw_pet(sessions[0][0], sessions[0][1], [b for _, _, b in sessions], t, rows,
                       heads=[s_ for _, s_, _ in sessions], hot=[s_ == "needs_input" for _, s_, _ in sessions],
                       collapse=True)
        sheet.alpha_composite(pet, ((sheet.width - W * U) // 2 + pad * 3, y0))
        frames.append(sheet.convert("RGB"))
    pal = frames[0].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    out = [fr.quantize(palette=pal, dither=Image.Dither.NONE) for fr in frames]
    out[0].save(OUT, save_all=True, append_images=out[1:], duration=int(1000 / FPS), loop=0, optimize=True, disposal=1)
    print(f"wrote {OUT} ({out[0].width}x{out[0].height}, {FRAMES} frames)")


if __name__ == "__main__":
    main()
