"""Emphasized, revealable spans embedded directly in the surrounding hook text."""
import tkinter as tk

from . import theme


def inline(text, value, tag='secret-emphasis', *, on_whitelist=None):
    eye = tk.Canvas(text, width=24, height=20, highlightthickness=1, takefocus=True, cursor='hand2')
    prefix = f'secret_{id(eye)}'
    start, end = prefix + '_start', prefix + '_end'
    index = text.index('end-1c')
    text.mark_set(start, index)
    text.mark_set(end, index)
    text.mark_gravity(start, 'left')
    revealed = False
    scale = [1.0]
    allow = [None]

    def draw():
        eye.delete('all')
        eye.configure(bg=theme.T['btn_bg'], highlightbackground=theme.T['border'],
                      width=round(24*scale[0]), height=round(20*scale[0]))
        color = theme.T['btn_fg']
        eye.create_oval(3, 5, 21, 15, outline=color, width=2)
        if revealed:
            eye.create_oval(9, 7, 15, 13, fill=color, outline='')
        else:
            eye.create_line(2, 18, 22, 2, fill=color, width=2)
        eye.scale('all', 0, 0, scale[0], scale[0])
        if allow[0] is not None:
            allow[0].configure(**theme.button_style('secondary'))
            allow[0].configure(font=(theme.UI_FONT, max(1, round(8*scale[0])), 'bold'),
                               padx=round(6*scale[0]), pady=round(2*scale[0]),
                               relief='raised', overrelief='ridge', bd=max(1, round(scale[0])),
                               highlightthickness=max(1, round(2*scale[0])),
                               highlightbackground=theme.T['primary'], highlightcolor=theme.T['primary'])

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
            if allow[0] is not None:
                allow[0].destroy()
            try:
                text.mark_unset(start, end)
            except tk.TclError:
                pass  # parent text may already be closing
    eye.bind('<Destroy>', destroy)
    render()
    text.configure(state='normal')
    text.window_create('end', window=eye, align='center', padx=3)
    if on_whitelist is not None:
        def completed(error):
            if not allow[0].winfo_exists():
                return
            allow[0].configure(text='Whitelisted' if error is None else 'Retry whitelist',
                               state='disabled' if error is None else 'normal')
            if error:
                theme.themed_dialog(text.winfo_toplevel(), 'Could not whitelist value', str(error),
                                    kind='error', on_result=lambda _result: None)

        def whitelist():
            allow[0].configure(state='disabled')
            on_whitelist(completed)

        allow[0] = tk.Button(text, text='Whitelist [+]', command=whitelist, **theme.button_style('secondary'))
        text.window_create('end', window=allow[0], align='center', padx=2)
        draw()
    eye.retheme_secret = draw
    def set_content_scale(value):
        scale[0] = value
        draw()
    eye.set_content_scale = set_content_scale
    return eye
