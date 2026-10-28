"""A small rotating detachable warning light, drawn in AIPet's pixel style."""
import math
import tkinter as tk

from . import theme


class WarningBeacon:
    def __init__(self, parent):
        self.canvas = tk.Canvas(parent, width=76, height=58, highlightthickness=0, bg=theme.T['win_bg'])
        self.phase, self.level, self.timer = 0.0, 'warning', None
        self.canvas.bind('<Destroy>', lambda event: self.stop() if event.widget is self.canvas else None)
        self.canvas.bind('<<AIPetThemeChanged>>', lambda event: self.draw())
        self.tick()

    def set_level(self, level):
        self.level = level

    def draw(self):
        c = self.canvas
        c.delete('all')
        color = '#ef4444' if self.level == 'error' else '#f59e0b'
        dark = '#7f1d1d' if self.level == 'error' else '#78350f'
        c.configure(bg=theme.T['win_bg'])
        # Stepped glass dome, dark detachable base, and a moving reflector highlight.
        c.create_polygon(20, 41, 20, 18, 25, 18, 25, 11, 31, 11, 31, 8,
                         45, 8, 45, 11, 51, 11, 51, 18, 56, 18, 56, 41,
                         fill=dark, outline=color, width=2)
        glint = 38 + 13 * math.sin(self.phase)
        c.create_rectangle(glint-3, 17, glint+3, 38, fill=color, outline='')
        if math.cos(self.phase) > 0.65:
            c.create_rectangle(glint-1, 19, glint+1, 34, fill='#fff7ed', outline='')
        for direction in (-1, 1):
            strength = max(0, direction * math.sin(self.phase))
            if strength > 0.4:
                x = 38 + direction * 25
                for dy in (-8, 0, 8):
                    c.create_line(x, 26 + dy, x + direction * (5 + 5 * strength), 26 + dy * 1.4,
                                  fill=color, width=2)
        c.create_rectangle(17, 42, 59, 48, fill=theme.T['muted'], outline='')
        c.create_rectangle(20, 48, 56, 51, fill=theme.T['border'], outline='')

    def tick(self):
        self.phase += 0.22
        self.draw()
        self.timer = self.canvas.after(70, self.tick)

    def stop(self):
        if self.timer is not None:
            self.canvas.after_cancel(self.timer)
            self.timer = None
