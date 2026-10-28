"""AIPet's branded robot with a rotating warning light on its screen."""
import math
import tkinter as tk

from . import theme, sprites


class WarningBeacon:
    def __init__(self, parent):
        self.robot = sprites.load_sprites()
        self.body = sprites.pixel_rects(('warning-robot-body',), self.robot['img']['robot']) if self.robot else []
        self.canvas = tk.Canvas(parent, width=78, height=108, highlightthickness=0, bg=theme.T['win_bg'])
        self.phase, self.level, self.timer = 0.0, 'warning', None
        self.scale = 1.0
        self.canvas.bind('<Destroy>', lambda event: self.stop() if event.widget is self.canvas else None)
        self.canvas.bind('<<AIPetThemeChanged>>', lambda event: self.draw())
        self.tick()

    def set_level(self, level):
        self.level = level

    def set_scale(self, scale):
        self.scale = scale
        self.canvas.configure(width=round(78*scale), height=round(108*scale))
        self.draw()

    def draw(self):
        c = self.canvas
        c.delete('all')
        color = self.robot['meta'].get('cyan', '#8ceaf3') if self.robot else '#8ceaf3'
        dark = '#164e63'
        highlight = '#%02x%02x%02x' % sprites.CYAN_LIGHT
        c.configure(bg=theme.T['win_bg'])
        if self.robot:
            # Use the actual body asset, at an integer pixel scale, rather than a new mascot drawing.
            for x0, y0, x1, y1, fill in self.body:
                c.create_rectangle(2+x0*2, 2+y0*2, 2+x1*2, 2+y1*2,
                                   fill=fill, outline='', width=0)
            x0, y0, x1, y1 = self.robot['meta']['robot']['screen']
            origin_x, origin_y, width, height = 2+x0*2, 2+y0*2, (x1-x0+1)*2, (y1-y0+1)*2
            c.create_rectangle(origin_x, origin_y, origin_x+width, origin_y+height,
                               fill=self.robot['meta'].get('screen_color', '#0e151a'), outline='')
        else:
            origin_x, origin_y, width, height = 1, 24, 76, 58
        tags = ('alarm',)
        # Stepped glass dome, dark detachable base, and a moving reflector highlight.
        c.create_polygon(20, 41, 20, 18, 25, 18, 25, 11, 31, 11, 31, 8,
                         45, 8, 45, 11, 51, 11, 51, 18, 56, 18, 56, 41,
                         fill=dark, outline=color, width=2, tags=tags)
        glint = 38 + 13 * math.sin(self.phase)
        c.create_rectangle(glint-3, 17, glint+3, 38, fill=color, outline='', tags=tags)
        if math.cos(self.phase) > 0.65:
            c.create_rectangle(glint-1, 19, glint+1, 34, fill=highlight, outline='', tags=tags)
        for direction in (-1, 1):
            strength = max(0, direction * math.sin(self.phase))
            if strength > 0.4:
                x = 38 + direction * 25
                for dy in (-8, 0, 8):
                    c.create_line(x, 26 + dy, x + direction * (5 + 5 * strength), 26 + dy * 1.4,
                                  fill=color, width=1, tags=tags)
        c.create_rectangle(17, 42, 59, 48, fill=dark, outline=color, tags=tags)
        c.create_rectangle(20, 48, 56, 51, fill=color, outline='', tags=tags)
        c.scale('alarm', 0, 0, width/76, height/58)
        c.move('alarm', origin_x, origin_y)
        if self.scale != 1.0:
            c.scale('all', 0, 0, self.scale, self.scale)

    def tick(self):
        self.phase += 0.22
        self.draw()
        self.timer = self.canvas.after(70, self.tick)

    def stop(self):
        if self.timer is not None:
            self.canvas.after_cancel(self.timer)
            self.timer = None
