#!/usr/bin/env python3
"""Build the app icons from the robot sprite: assets/aipet.ico (Windows exe), assets/aipet.icns (macOS app)
and assets/icon.png (1024 px preview). Run from anywhere: python tools/make_icons.py

Large sizes are integer nearest-neighbour enlargements, so the pixel art stays crisp; sizes too small for that are
downsampled from the large version. Works without Tk: tkinter is stubbed if it isn't installed.
"""
import os
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

from PIL import Image  # noqa: E402

import aipet as core  # noqa: E402

FACE, LIGHTS = "happy", ("amber", "green", "off")  # the mascot as it looks while working
FILL = 0.92  # share of the icon's height the robot uses


def icon(size):
    robot = core.robot_image(FACE, LIGHTS)
    k = int(size * FILL // robot.height)  # whole-pixel scale for crisp pixel art
    big = robot.resize((robot.width * max(1, k), robot.height * max(1, k)), Image.NEAREST)
    if k < 1 or big.height < size * FILL * 0.75:  # no integer scale fits well: shrink a large crisp version instead
        k2 = max(1, int(1024 * FILL // robot.height))
        big = robot.resize((robot.width * k2, robot.height * k2), Image.NEAREST)
        h = int(round(size * FILL))
        big = big.resize((max(1, round(big.width * h / big.height)), h), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(big, ((size - big.width) // 2, (size - big.height) // 2), big)
    return out


def main():
    assets = os.path.join(ROOT, "assets")
    ico_sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    master = icon(256)
    master.save(os.path.join(assets, "aipet.ico"), sizes=[(s, s) for s in ico_sizes],
                append_images=[icon(s) for s in ico_sizes if s != 256])
    big = icon(1024)
    big.save(os.path.join(assets, "icon.png"))
    big.save(os.path.join(assets, "aipet.icns"),
             append_images=[icon(s) for s in (16, 32, 64, 128, 256, 512)])
    print("wrote assets/aipet.ico, assets/aipet.icns, assets/icon.png")


if __name__ == "__main__":
    main()
