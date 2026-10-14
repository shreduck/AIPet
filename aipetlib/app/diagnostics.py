"""Diagnostics for a machine we can't test: ~/.aipet/diagnostics.txt with versions, sprite loading, image tests,
window state and recent errors (no prompts, commands or session contents). Save diagnostics... in the menus."""
import os
import subprocess
import sys
import time
import tkinter as tk

from .. import update as releases
from .. import vscode
from ..runtime import diagnostics as runtime_diagnostics
from ..runtime import paths
from ..runtime import preferences
from ..ui import pet_style
from ..ui import sprites

IS_MAC = sys.platform == "darwin"


def report(app=None):
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
    attempt("version", releases.current_version)
    add("platform", f"{platform.platform()} ({platform.machine()})")
    add("python", f"{sys.version.split()[0]} frozen={getattr(sys, 'frozen', False)} exe={sys.executable}")
    add("meipass", getattr(sys, "_MEIPASS", "-"))
    attempt("tk", lambda: f"TkVersion={tk.TkVersion} patchlevel={app.root.tk.call('info', 'patchlevel')} "
                          f"windowing={app.root.tk.call('tk', 'windowingsystem')}" if app else tk.TkVersion)
    attempt("pillow", lambda: __import__("PIL").__version__)
    add("style", preferences.STYLE["v"])
    add("scale", preferences.SCALE["v"])
    add("png_photos", sprites._PNG_PHOTOS["on"])
    add("sprite_dir", f"{paths.SPRITE_DIR} exists={os.path.isdir(paths.SPRITE_DIR)}")
    attempt("sprite_files", lambda: sorted(os.listdir(paths.SPRITE_DIR)))
    attempt("load_sprites", lambda: "ok " + str(sprites.load_sprites(paths.SPRITE_DIR)["img"]["robot"].size)
            if sprites.load_sprites(paths.SPRITE_DIR) else "False (see error.log)")

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
        spr = sprites.load_sprites()
        if not spr:
            return "no sprites"
        im = sprites.robot_image("happy", ("amber", "green", "off"), spr)
        ph = sprites.sprite_photo(("diag",), im, im.width * 2, im.height * 2)
        return f"ok {ph.width()}x{ph.height()} centre-pixel={ph.get(im.width, im.height)}"
    attempt("imagetk_photo", test_imagetk)
    attempt("png_photo", test_png)
    attempt("robot_photo", test_robot)

    def test_vscode():  # where the open VS Code folders come from (counts only, no paths)
        rep = []
        vscode.vscode_open_folders(report=rep)
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
                                  f"anchor={app.placement.anchor}")
        for key, pet in list(app.pets.items())[:6]:
            c = pet.canvas
            attempt(f"pet {key[:14]}", lambda c=c, pet=pet: (
                f"state={pet.data.get('state')} mapped={c.winfo_ismapped()} size={c.winfo_width()}x{c.winfo_height()} "
                f"items={len(c.find_all())} images={sum(1 for i in c.find_all() if c.type(i) == 'image')} "
                f"rects={sum(1 for i in c.find_all() if c.type(i) == 'rectangle')} rect_sprites={pet_style.RECT_SPRITES}"))
    try:
        with open(os.path.join(paths.HOME_DIR, "error.log"), encoding="utf-8") as f:
            tail = f.read()[-4000:]
    except OSError:
        tail = "(none)"
    lines += ["", "---- error.log (tail)", tail]
    return "\n".join(lines) + "\n"


def write(app):
    """Write ~/.aipet/diagnostics.txt. Returns the path, or None if it failed."""
    path = os.path.join(paths.HOME_DIR, "diagnostics.txt")
    try:
        text = report(app)
        os.makedirs(paths.HOME_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path
    except Exception as e:
        runtime_diagnostics.log_error(f"diagnostics failed: {e!r}")
        return None


def save(app):
    """Write the diagnostics and open them, so someone on a machine we can't test can send them."""
    path = write(app)
    if not path:
        return
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["open" if IS_MAC else "xdg-open", path])
    except (AttributeError, OSError):
        pass
