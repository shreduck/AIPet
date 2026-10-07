#!/usr/bin/env python3
"""Which ways of drawing an image does macOS Tk actually paint on a transparent window?

The pet's macOS window is transparent (-transparent + systemTransparent). On the CI Mac, its text and rectangles show
but every image is invisible, although Tk creates them fine. This opens one transparent window with a grid of
variants plus an ordinary window as a control, each labelled in text (which is known to render), for a screenshot.
Run: python tools/mac_image_probe.py [seconds]   (closes itself; default 15 s)
"""
import base64
import io
import os
import sys
import tkinter as tk

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPRITE = os.path.join(ROOT, "assets", "sprites", "robot_body.png")
IS_MAC = sys.platform == "darwin"
CLEAR = "systemTransparent" if IS_MAC else "#ff00fe"
K = 3  # enlarge the 37x52 sprite


def png_photo(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"), format="png")


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 15
    base = Image.open(SPRITE).convert("RGBA")
    base = base.resize((base.width * K, base.height * K), Image.NEAREST)
    opaque_rgba = Image.new("RGBA", base.size, (255, 255, 255, 255))
    opaque_rgba.alpha_composite(base)  # RGBA, but every pixel alpha 255
    rgb = opaque_rgba.convert("RGB")  # no alpha channel at all
    one_bit = base.copy()  # alpha only 0 or 255
    one_bit.putalpha(base.getchannel("A").point(lambda a: 255 if a >= 128 else 0))

    root = tk.Tk()
    root.title("image probe")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=CLEAR)
    try:
        root.attributes("-transparent", True) if IS_MAC else root.attributes("-transparentcolor", CLEAR)
    except tk.TclError:
        pass
    keep = []

    def cell(col, row, label, make, canvas_bg=CLEAR, use_label=False):
        f = tk.Frame(root, bg=CLEAR)
        f.grid(row=row, column=col, padx=6, pady=6)
        cv = tk.Canvas(f, width=base.width + 20, height=base.height + 44, bg=canvas_bg, highlightthickness=0)
        cv.pack()
        cv.create_rectangle(1, 1, base.width + 19, base.height + 43, outline="#ff3b30")
        cv.create_text(6, 6, anchor="nw", text=label, fill="#ffd60a", font=("Helvetica", 11, "bold"))
        try:
            ph = make()
            keep.append(ph)
            if use_label:
                tk.Label(cv, image=ph, bg=canvas_bg, bd=0).place(x=10, y=30)
            else:
                cv.create_image(10, 30, image=ph, anchor="nw")
        except Exception as e:
            cv.create_text(6, 40, anchor="nw", text=f"ERR {type(e).__name__}", fill="#ff3b30")

    def imagetk(im):
        from PIL import ImageTk
        return ImageTk.PhotoImage(im)

    cell(0, 0, "A png RGBA", lambda: png_photo(base))
    cell(1, 0, "B file=", lambda: tk.PhotoImage(file=SPRITE).zoom(K))
    cell(2, 0, "C ImageTk", lambda: imagetk(base))
    cell(3, 0, "D 1-bit a", lambda: png_photo(one_bit))
    cell(0, 1, "E opaqRGBA", lambda: png_photo(opaque_rgba))
    cell(1, 1, "F RGB", lambda: png_photo(rgb))
    cell(2, 1, "G bg white", lambda: png_photo(base), canvas_bg="white")
    cell(3, 1, "H Label", lambda: png_photo(base), use_label=True)

    # I: the pet's pattern - delete and re-create the image every frame
    f = tk.Frame(root, bg=CLEAR)
    f.grid(row=0, column=4, rowspan=2, padx=6, pady=6)
    anim = tk.Canvas(f, width=base.width + 20, height=base.height + 44, bg=CLEAR, highlightthickness=0)
    anim.pack()
    aph = png_photo(base)
    keep.append(aph)

    def frame(n=0):
        anim.delete("all")
        anim.create_rectangle(1, 1, base.width + 19, base.height + 43, outline="#ff3b30")
        anim.create_text(6, 6, anchor="nw", text="I redraw", fill="#ffd60a", font=("Helvetica", 11, "bold"))
        anim.create_image(10, 30 + (n % 6), image=aph, anchor="nw")
        root.after(50, frame, n + 1)
    frame()

    # control: an ordinary (not transparent) window
    ctl = tk.Toplevel(root)
    ctl.title("control")
    ctl.configure(bg="#3a3a3c")
    cc = tk.Canvas(ctl, width=base.width + 20, height=base.height + 44, bg="#3a3a3c", highlightthickness=0)
    cc.pack(padx=6, pady=6)
    cc.create_text(6, 6, anchor="nw", text="J normal win", fill="#ffd60a", font=("Helvetica", 11, "bold"))
    cph = png_photo(base)
    keep.append(cph)
    cc.create_image(10, 30, image=cph, anchor="nw")

    root.update_idletasks()
    root.geometry(f"+{120}+{140}")
    ctl.geometry(f"+{120}+{140 + root.winfo_reqheight() + 40}")
    root.after(int(seconds * 1000), root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
