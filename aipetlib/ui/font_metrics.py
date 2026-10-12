"""Cached pet text metrics, using the shared drawing scale."""


def _text_w(services, font, text):
    """Width of text in drawing units (real font metrics, so the label stays centred at every size)."""
    key = ("font", font)
    if key not in services._IMG_CACHE:
        import tkinter.font as tkfont
        services._IMG_CACHE[key] = tkfont.Font(family=font[0], size=font[1], weight=font[2] if len(font) > 2 else "normal")
    return services._IMG_CACHE[key].measure(text) / services.SCALE["v"]


def _char_w(services, font):
    key = ("charw", font)
    if key not in services._IMG_CACHE:
        import tkinter.font as tkfont
        services._IMG_CACHE[key] = tkfont.Font(family=font[0], size=font[1]).measure("0") / services.SCALE["v"]
    return services._IMG_CACHE[key]
