"""Keyboard-accessible sliding switch using the shared AIPet palette."""
import tkinter as tk

from . import theme


class ToggleSwitch(tk.Canvas):
    def __init__(self, parent, variable):
        super().__init__(parent, width=42, height=24, highlightthickness=1, bd=0,
                         takefocus=True, cursor='hand2', bg=theme.T['win_bg'])
        self.variable, self.disabled = variable, False
        self._trace = variable.trace_add('write', self.draw)
        self._pet_retheme = self.draw
        for event in ('<Button-1>', '<space>', '<Return>'):
            self.bind(event, self.toggle)
        self.bind('<FocusIn>', self.draw)
        self.bind('<FocusOut>', self.draw)
        self.bind('<Destroy>', self.close)
        self.draw()

    def toggle(self, _event=None):
        if not self.disabled:
            self.variable.set(not self.variable.get())
        return 'break'

    def set_disabled(self, disabled):
        self.disabled = bool(disabled)
        self.configure(takefocus=not disabled, cursor='arrow' if disabled else 'hand2')
        self.draw()

    def draw(self, *_):
        self.delete('all')
        self.configure(bg=theme.T['win_bg'], highlightbackground=theme.T['win_bg'], highlightcolor=theme.T['primary'])
        on = bool(self.variable.get())
        color = theme.T['menu_disabled'] if self.disabled else theme.T['primary'] if on else theme.T['border']
        self.create_oval(2, 3, 20, 21, fill=color, outline=color)
        self.create_oval(22, 3, 40, 21, fill=color, outline=color)
        self.create_rectangle(11, 3, 31, 21, fill=color, outline=color)
        x = 24 if on else 4
        self.create_oval(x, 5, x+14, 19, fill='#ffffff', outline='')

    def close(self, event):
        if event.widget is self:
            try:
                self.variable.trace_remove('write', self._trace)
            except tk.TclError:
                pass
