"""Cached pet text metrics, using the shared drawing scale."""
from ..runtime import preferences
from . import sprites


def _text_w(font, text):
    """Width of text in drawing units (real font metrics, so the label stays centred at every size)."""
    key = ("font", font)
    if key not in sprites._IMG_CACHE:
        import tkinter.font as tkfont
        sprites._IMG_CACHE[key] = tkfont.Font(family=font[0], size=font[1], weight=font[2] if len(font) > 2 else "normal")
    return sprites._IMG_CACHE[key].measure(text) / preferences.SCALE["v"]


def _char_w(font):
    key = ("charw", font)
    if key not in sprites._IMG_CACHE:
        import tkinter.font as tkfont
        sprites._IMG_CACHE[key] = tkfont.Font(family=font[0], size=font[1]).measure("0") / preferences.SCALE["v"]
    return sprites._IMG_CACHE[key]
