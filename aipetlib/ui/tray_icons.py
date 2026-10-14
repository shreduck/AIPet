"""Robot and fallback artwork for the tray, menu bar, Dock and window icons."""
import sys

from ..runtime import diagnostics, preferences
from . import pet_style, sprites, theme

IS_MAC = sys.platform == "darwin"


def set_window_icon(root):
    """The robot as the icon of every AIPet window (title bar and taskbar), in place of Tk's feather. Kept on the
    root so the images aren't garbage-collected. macOS shows the app's Dock icon instead."""
    if IS_MAC:
        return
    try:
        from PIL import Image, ImageTk
        spr = sprites.load_sprites()
        if not spr:
            return
        robot = sprites.robot_image("happy", ("amber", "green", "off"), spr)
        photos = []
        for size in (256, 64, 32, 16):  # Windows picks the closest size for the taskbar, title bar and Alt+Tab
            k = min(size / robot.width, size / robot.height)
            im = robot.resize((max(1, int(robot.width * k)), max(1, int(robot.height * k))), Image.NEAREST)
            square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            square.paste(im, ((size - im.width) // 2, size - im.height), im)
            photos.append(ImageTk.PhotoImage(square, master=root))
        root.iconphoto(True, *photos)  # True: also every Toplevel created later
        root._aipet_icons = photos
    except Exception as e:
        diagnostics.log_error(f"window icon: {e!r}")


def make_icon(state):
    from PIL import Image, ImageDraw
    col, dark = pet_style.COLORS.get(state, pet_style.COLORS["idle"]), pet_style.DARK.get(state, pet_style.DARK["idle"])
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    spr = sprites.load_sprites() if preferences.STYLE["v"] == "robot" else False
    if spr:  # the robot with the state's face (red while a session needs you), on a dot in the state colour
        im = robot_icon(state)
        k = min(60 / im.width, 60 / im.height)
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.NEAREST)
        img.paste(im, ((64 - im.width) // 2, 62 - im.height), im)
        d.ellipse((46, 46, 62, 62), fill=col, outline="white", width=2)
    elif preferences.STYLE["v"] != "cat":  # the mole: head poking out of a hill, ground rim in the state colour
        d.ellipse((2, 44, 62, 62), fill=pet_style.MOLE_EDGE, outline=col, width=4)
        d.ellipse((14, 4, 50, 56), fill=pet_style.MOLE_BODY, outline=pet_style.MOLE_EDGE, width=3)
        if state in ("done", "idle"):
            d.arc((20, 20, 29, 28), 180, 360, fill=theme.INK, width=3)
            d.arc((35, 20, 44, 28), 180, 360, fill=theme.INK, width=3)
        else:
            for x in (25, 39):
                d.ellipse((x - 3, 17, x + 3, 28), fill=theme.INK)
        d.ellipse((22, 29, 42, 44), fill=pet_style.MOLE_NOSE, outline=pet_style.MOLE_NOSE_EDGE, width=2)
        d.ellipse((8, 46, 56, 62), fill=pet_style.MOLE_HILL, outline=pet_style.MOLE_HILL_EDGE, width=2)
    else:
        d.polygon([(13, 24), (17, 4), (29, 15)], fill=col, outline=dark)
        d.polygon([(51, 24), (47, 4), (35, 15)], fill=col, outline=dark)
        d.ellipse((6, 12, 58, 60), fill=col, outline=dark, width=3)
        if state in ("done", "idle"):
            d.arc((17, 30, 29, 40), 180, 360, fill=theme.INK, width=3)
            d.arc((35, 30, 47, 40), 180, 360, fill=theme.INK, width=3)
        else:
            for x in (23, 41):
                d.ellipse((x - 6, 29, x + 6, 41), fill="white", outline=theme.INK, width=2)
                d.ellipse((x - 3, 32, x + 3, 38), fill=theme.INK)
    if state == "needs_input":
        d.ellipse((42, 0, 63, 21), fill="#dc2626", outline="white", width=2)
        d.rectangle((51, 4, 54, 12), fill="white")
        d.rectangle((51, 15, 54, 17), fill="white")
    return img


def robot_icon(state):
    """The robot for the taskbar / tray / Dock at sprite resolution: white normally, red with a "?" face while a
    session needs you (or a worried face on an error)."""
    attention = state in ("needs_input", "error")
    face = "ask" if state == "needs_input" else ("worried" if state == "error" else sprites.STATE_FACE.get(state, "happy"))
    im = sprites.robot_image(face, ("red",) * 3 if attention else sprites.light_cycle(state, 0)).copy()
    if attention:
        px = im.load()
        for y in range(im.height):
            for x in range(im.width):
                r, g, b, a = px[x, y]
                if a and min(r, g, b) > 120 and max(r, g, b) - min(r, g, b) < 40:  # the white / grey body -> red
                    v = (r + g + b) / 3 / 255
                    px[x, y] = (int(150 + 105 * v), int(25 + 45 * v), int(25 + 45 * v), a)
    return im


def menu_bar_icons():
    """macOS menu bar: a line-art robot as a template image (macOS paints its opaque pixels in the menu bar's colour:
    outline, screen and lights solid, white body and cyan face left out) and the red robot for 'needs you'."""
    from PIL import Image
    im = sprites.robot_image("happy", ("off", "off", "off")).copy()
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            dark = a and (r * 299 + g * 587 + b * 114) / 1000 < 110
            px[x, y] = (0, 0, 0, 255) if dark else (0, 0, 0, 0)
    pad = Image.new("RGBA", (im.width + 4, im.height + 4), (0, 0, 0, 0))
    pad.paste(im, (2, 2), im)
    red = robot_icon("needs_input")
    red_pad = Image.new("RGBA", (red.width + 4, red.height + 4), (0, 0, 0, 0))
    red_pad.paste(red, (2, 2), red)
    return {"normal": pad, "attention": red_pad}


def dock_image(state, size=256):
    """macOS Dock icon (see robot_icon), centred on a square canvas."""
    from PIL import Image
    im = robot_icon(state)
    k = max(1, int(size * 0.9 // im.height))
    big = im.resize((im.width * k, im.height * k), Image.NEAREST)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(big, ((size - big.width) // 2, (size - big.height) // 2), big)
    return out
