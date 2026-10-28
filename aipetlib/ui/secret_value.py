"""Emphasized, revealable spans embedded directly in the surrounding hook text."""
import tkinter as tk

from . import theme


def inline(text, value, tag='secret-emphasis'):
    eye = tk.Canvas(text, width=24, height=20, highlightthickness=1, takefocus=True, cursor='hand2')
    prefix = f'secret_{id(eye)}'
    start, end = prefix + '_start', prefix + '_end'
    index = text.index('end-1c')
    text.mark_set(start, index)
    text.mark_set(end, index)
    text.mark_gravity(start, 'left')
    revealed = False

    def draw():
        eye.delete('all')
        eye.configure(bg=theme.T['btn_bg'], highlightbackground=theme.T['border'])
        color = theme.T['btn_fg']
        eye.create_oval(3, 5, 21, 15, outline=color, width=2)
        if revealed:
            eye.create_oval(9, 7, 15, 13, fill=color, outline='')
        else:
            eye.create_line(2, 18, 22, 2, fill=color, width=2)

    def render():
        text.configure(state='normal')
        text.delete(start, end)
        text.mark_gravity(end, 'right')
        text.insert(start, value if revealed else '••••••••••••', tag)
        text.mark_gravity(end, 'left')
        text.configure(state='disabled')
        draw()

    def toggle(_event=None):
        nonlocal revealed
        revealed = not revealed
        render()
        return 'break'

    eye.bind('<Button-1>', toggle)
    eye.bind('<Return>', toggle)
    eye.bind('<space>', toggle)
    def destroy(event):
        if event.widget is eye:
            try:
                text.mark_unset(start, end)
            except tk.TclError:
                pass  # parent text may already be closing
    eye.bind('<Destroy>', destroy)
    render()
    text.configure(state='normal')
    text.window_create('end', window=eye, align='center', padx=3)
    eye.retheme_secret = draw
    return eye
