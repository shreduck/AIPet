"""Themed corner resizing for custom frameless dialogs."""
import tkinter as tk

from .. import geometry
from . import theme


def corner_grip(win, minimum=(320, 220), on_resize=None):
    win.resizable(True, True)
    win.minsize(*minimum)
    grip = tk.Canvas(win, width=16, height=16, highlightthickness=0, cursor='bottom_right_corner', bd=0)
    grip.place(relx=1, rely=1, x=-2, y=-2, anchor='se')
    drag = {}

    def draw():
        grip.configure(bg=theme.T['win_bg'])
        grip.delete('all')
        for offset in (4, 8, 12):
            grip.create_line(offset, 14, 14, offset, fill=theme.T['muted'], width=1)

    def press(event):
        drag.update(x=event.x_root, y=event.y_root, width=win.winfo_width(), height=win.winfo_height(),
                    left=win.winfo_x(), top=win.winfo_y())
        return 'break'

    def move(event):
        if not drag:
            return 'break'
        width = max(minimum[0], drag['width'] + event.x_root - drag['x'])
        height = max(minimum[1], drag['height'] + event.y_root - drag['y'])
        win.geometry(f'{int(width)}x{int(height)}' + geometry.geo(drag['left'], drag['top']))
        if on_resize:
            on_resize()
        return 'break'

    grip.bind('<ButtonPress-1>', press)
    grip.bind('<B1-Motion>', move)
    grip.bind('<ButtonRelease-1>', lambda event: drag.clear())
    grip._pet_retheme = draw
    draw()
    return grip
