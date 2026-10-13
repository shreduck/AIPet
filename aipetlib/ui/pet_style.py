"""What the pet draws with: its colours, state labels, badges, sizes, animation timings and scaled fonts.

Shared by the pet, its tooltips, the size sliders and the permission card. Imports no application entry point.
"""
import math
import sys

from ..runtime.preferences import CARD_SCALE, SCALE
from .theme import UI_FONT

IS_MAC = sys.platform == "darwin"
TRANSPARENT = "systemTransparent" if IS_MAC else "#ff00fe"
COLORS = {"working": "#5b8def", "needs_input": "#f59e0b", "done": "#22c55e", "error": "#ef4444", "idle": "#9ca3af"}
DARK = {"working": "#2f5bb7", "needs_input": "#b45309", "done": "#15803d", "error": "#991b1b", "idle": "#4b5563"}
LABELS = {"working": "working…", "needs_input": "needs you!", "done": "done", "error": "error", "idle": "idle"}
BADGE_DOTS = {"Claude": "#d97757", "Codex": "#0f8a6a", "Workbench": "#0f766e"}  # badge dot colour by its first word
BADGE_DOT_COWORK = "#c2410c"
BADGE_ATTENTION_BG = "#fde4e4"  # a very faint red: this session needs you


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
PET_W, PET_H = 92, 122  # the drawing area of one pet, before the badge column and top trim below
BADGE_GUTTER = 0  # extra width on the pet's left (badges now sit on the name tag's top edge, so none is needed)
TAG_H = 19  # name tag height (one unit taller than it was, for the name + conversation title lines)
TOP_TRIM = 14  # the empty strip the badges used to take above the bubble
CANVAS_W, CANVAS_H = PET_W + BADGE_GUTTER, PET_H - TOP_TRIM
USAGE_GUTTER = 0  # individual pets reserve quota space only when they show readings
USAGE_EDGE_ROOM = 18  # one shared margin keeps the rightmost pet stable as usage appears
MOUND, MOUND_DARK = "#a16207", "#713f12"
EMERGE_SECONDS, EMERGE_DEPTH, EMERGE_STAGGER = 0.7, 62, 0.25
DONE_FLASH_SECONDS = 1.6  # a working pet shows the done check this long when a subagent or another session finishes
BUBBLE_W = 270


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

TERMINAL_CHARS = "0123456789ABCDEF$#%&@<>/=+*;:"
# Working robots blink at 0.075x the mouth wave's 2 Hz: every 6.7 s (3/40, so blink and wave line up every 20 s).
# The threshold keeps each blink about 0.13 s long whatever the ratio.
BLINK_RATIO, BLINK_SECONDS = 0.075, 0.13
BLINK_THRESHOLD = math.cos(math.pi * BLINK_SECONDS * BLINK_RATIO * 2)

# macOS Tk paints no images at all on a transparent canvas (systemTransparent), whatever the image format, while
# rectangles and text do render (found with tools/mac_image_probe.py on CI). So on macOS the pet window draws its pixel
# art as rectangles: each image becomes merged runs of same-coloured pixels, computed once per image.
RECT_SPRITES = IS_MAC

_CARD_FONTS = {}  # (family, size, style) -> tkfont.Font, resized in place when the scale changes


def card_font(size, *style, family=None):
    from tkinter import font as tkfont
    key = (family or UI_FONT, size, style)
    f = _CARD_FONTS.get(key)
    if f is None:
        f = _CARD_FONTS[key] = tkfont.Font(family=key[0], size=max(6, int(round(size * CARD_SCALE["v"]))),
                                            weight="bold" if "bold" in style else "normal",
                                            underline="underline" in style)
    return f


def rescale_card_fonts():
    for (family, size, style), f in _CARD_FONTS.items():
        f.configure(size=max(6, int(round(size * CARD_SCALE["v"]))))


NO_CHOICE = "\x00none"  # "nothing picked" for a question's radio buttons (Tk draws "" as all of them selected)
OWN_CHOICE = "\x00own"  # the "Your own answer" radio button: send the typed text
