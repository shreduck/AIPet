"""PetView: rendering and interaction.

This component does not import the application entry point.
"""
import math
import random
import sys
import time
import tkinter as tk

import aipet_usage as usage

from ..agents import context as agent_context
from ..runtime import preferences
from . import font_metrics
from . import pet_style
from . import sprites
from . import theme

IS_MAC = sys.platform == "darwin"


class PetView:
    def __init__(self, app, key):
        self.app = app
        self.key = key
        self.data = {}
        self.acked = False
        self.last_remind = 0.0
        self.born = 0.0  # when this pet starts popping out of the ground (0 = already out)
        self.seed = (sum(map(ord, key)) % 97) / 13.0  # desync animations
        self.usage_extra = pet_style.USAGE_GUTTER
        self._usage_hover = None
        self._usage_details = {}
        c = self.canvas = tk.Canvas(app.frame, width=pet_style.px(pet_style.CANVAS_W + pet_style.USAGE_GUTTER), height=pet_style.px(pet_style.CANVAS_H), bg=pet_style.TRANSPARENT,
                                    highlightthickness=0, bd=0)
        c.bind("<ButtonPress-1>", app.on_press)
        c.bind("<B1-Motion>", app.on_drag)
        c.bind("<ButtonRelease-1>", lambda e: app.on_release(e, self))
        c.bind("<Button-3>", lambda e: app.on_menu(e, self))
        if IS_MAC:  # macOS Tk: right button is Button-2; ctrl-click is the trackpad equivalent
            c.bind("<Button-2>", lambda e: app.on_menu(e, self))
            c.bind("<Control-Button-1>", lambda e: app.on_menu(e, self))
            c.bind("<Control-ButtonRelease-1>", lambda e: "break")
        c.bind("<Enter>", lambda e: app.show_tip(self))
        c.bind("<Leave>", lambda e: app.hide_tip())
        self._on_bubble = False  # the check/cross/? bubble ("ans" items) opens the details popup when clicked
        c.tag_bind("ans", "<Enter>", lambda e: c.config(cursor="hand2"))
        c.tag_bind("ans", "<Leave>", lambda e: c.config(cursor=""))
        c.tag_bind("ans", "<ButtonPress-1>", lambda e: setattr(self, "_on_bubble", True))
        self._on_badge = None  # a badge's session key, when a badge was pressed (it opens that session's prompt)
        c.tag_bind("badge", "<Enter>", lambda e: c.config(cursor="hand2"))
        c.tag_bind("badge", "<Leave>", lambda e: c.config(cursor=""))
        c.tag_bind("badge", "<ButtonPress-1>", lambda e: setattr(self, "_on_badge", self._badge_under_pointer()))

    def _badge_under_pointer(self):
        c = self.canvas
        for tag in c.gettags("current"):
            if tag.startswith("badge:"):
                return tag[6:] or None
        return None

    def destroy(self):
        self.canvas.destroy()

    def draw(self, t):
        c = self.canvas
        c.delete("all")
        if preferences.STYLE["v"] == "cat":
            self._draw_cat(t)
        elif preferences.STYLE["v"] == "mole":
            self._draw_mole(t)
        else:
            self._draw_robot(t)
        c.move("all", pet_style.BADGE_GUTTER, -pet_style.TOP_TRIM)  # drop the empty top strip
        min_x = self._draw_badges()
        extra = self._draw_usage()
        # compact mode, expanded: the badges stand in a column snapped to the pet's left; widen the canvas to the left
        # for it (the overlay is anchored bottom-right, so the pet itself doesn't move)
        gutter = int(math.ceil(-min_x)) + 1 if min_x is not None and min_x < 0 else 0
        if gutter:
            c.move("all", gutter, 0)
        if gutter != getattr(self, "gutter", 0) or extra != getattr(self, "usage_extra", 0):
            delta = pet_style.px(pet_style.CANVAS_W + gutter + extra) - pet_style.px(pet_style.CANVAS_W + getattr(self, "gutter", 0) + getattr(self, "usage_extra", 0))
            self.gutter = gutter
            self.usage_extra = extra
            c.config(width=pet_style.px(pet_style.CANVAS_W + gutter + extra))
            delta += self.app._sync_usage_padding()
            self.app.shift_for_width(delta)  # update the canvas and shared margin in the same redraw
        self._finish()
        self._update_usage_hover()

    def _draw_usage(self):
        group = self.data.get("everyone") or [self.data]
        cfg = getattr(getattr(self, "app", None), "cfg", None) or {}
        context_rows = agent_context.banner_rows(group, cfg.get("context_windows")) \
            if cfg.get("context_banner", True) else []
        details = context_rows + usage.badge_details(group)  # each AI's context first, then its account limits
        rows = [(r["agent"], r["window"], r["percent"]) for r in details]
        self._usage_details = {}
        if not rows:
            return pet_style.USAGE_GUTTER
        c, left = self.canvas, pet_style.PET_W - 26
        # Align a short column with the lower body, ending above the name tag.
        top = 81 - min(6, len(rows)) * 9
        font = sprites.mono(4.5)
        extra = 0
        # Start beside the robot, below its speech bubble. Never cover the name tag.
        for start in range(0, len(rows), 6):
            column = rows[start:start + 6]
            labels = [agent_context.label(detail) if detail.get("kind") == "context" else
                      ("CX" if ai == "codex" else "CL") + f" {window} {percent}%"
                      for (ai, window, percent), detail in zip(column, details[start:start + 6])]
            width = max(self._char_w(font) * len(label) + 6 for label in labels)
            for i, ((ai, window, percent), label) in enumerate(zip(column, labels)):
                colour = "#5b8def" if ai == "codex" else "#d98960"
                y = top + i * 9
                tag = f"{'ctx' if details[start + i].get('kind') == 'context' else 'usage'}:{ai}:{window}:{percent}"
                self._usage_details[tag] = details[start + i]
                c.create_rectangle(left, y, left + width, y + 8, fill=theme.T["tag_bg"], outline=colour, width=1, tags=("usage", tag))
                c.create_text(left + 3, y + 4, text=label, font=font, fill=colour, anchor="w", tags=("usage", tag))
            extra = max(extra, int(math.ceil(left + width + 2 - pet_style.CANVAS_W)))
            left += width + 3
        return max(pet_style.USAGE_GUTTER, extra)

    def _update_usage_hover(self):
        # Animation recreates canvas items. Track the reading under the pointer
        # instead of item Enter/Leave events, which would flicker every frame.
        c = self.canvas
        if not c.winfo_ismapped():
            self._usage_hover = None
            return
        x, y = c.winfo_pointerx() - c.winfo_rootx(), c.winfo_pointery() - c.winfo_rooty()
        tag = next((tag for item in c.find_overlapping(x, y, x, y) for tag in c.gettags(item)
                    if tag in self._usage_details), None)
        if tag == self._usage_hover:
            return
        previous, self._usage_hover = self._usage_hover, tag
        if tag:
            self.app.show_usage_tip(self, self._usage_details[tag])
        elif previous:
            if 0 <= x < c.winfo_width() and 0 <= y < c.winfo_height():
                self.app.show_tip(self)
            else:
                self.app.hide_tip()

    def _completion_seal(self, x, y):
        self.canvas.create_oval(x - 9, y - 9, x + 9, y + 9, fill="#dcfce7", outline="#15803d", width=1)
        self._mark("ok", x, y)

    def _finish(self):
        """Apply the overall size: everything is drawn at 92x122 and scaled here (line widths too)."""
        s = preferences.SCALE["v"]
        if abs(s - 1) < 0.01:
            return
        c = self.canvas
        c.scale("all", 0, 0, s, s)
        for it in c.find_all():
            if c.type(it) in ("line", "oval", "polygon", "arc", "rectangle"):
                try:
                    c.itemconfigure(it, width=max(1.0, float(c.itemcget(it, "width")) * s))
                except tk.TclError:
                    pass

    def _draw_tag(self):
        c = self.canvas
        st = self.data.get("state", "idle")
        col, dark = pet_style.COLORS.get(st, pet_style.COLORS["idle"]), pet_style.DARK.get(st, pet_style.DARK["idle"])
        # name label: a pixel-art rounded box like the speech bubbles (solid pixels: no colour fringing on the
        # transparent window), with a small "light" in the state colour like the ones on the robot
        tw_, th_ = pet_style.PET_W - 6, pet_style.TAG_H
        try:
            s_ = preferences.SCALE["v"]
            im = sprites.bubble_image(tw_, th_, theme.T["tag_bg"], theme.T["tag_outline"], None)
            self._put(("tag", tw_, th_, theme.T["tag_bg"], theme.T["tag_outline"]), im, int(round(tw_ * s_)), int(round(th_ * s_)),
                      3, pet_style.PET_H - 2 - pet_style.TAG_H, "nw")
        except Exception:  # no Pillow: a plain rectangle
            c.create_rectangle(3, pet_style.PET_H - 2 - pet_style.TAG_H, pet_style.PET_W - 3, pet_style.PET_H - 2, fill=theme.T["tag_bg"], outline=theme.T["tag_outline"], width=1)
        font, sub_font = sprites.mono(5.0), sprites.mono(4.0)  # console-style text, like the robot's screen
        room, left = pet_style.PET_W - 14, 7  # inside the tag's rounded border
        everyone = self.data.get("everyone") or []
        entries = [(m.get("raw_title") or m.get("title") or "session", m.get("conv") or "", m.get("state"))
                   for m in (everyone if len(everyone) > 1 else [self.data])]
        two = any(e[1] for e in entries)  # a conversation title to show: the name moves up, the title goes below it
        y_top0 = pet_style.PET_H - 2 - pet_style.TAG_H
        y1 = y_top0 + 6.4 if two else pet_style.PET_H - 2 - pet_style.TAG_H / 2 + 1.5  # name near the top; one line: a bit low (badges)
        y2 = y_top0 + 13.9  # clear of the tag's bottom border
        sub_fg = "#d1d5db" if theme.T.get("name") == "dark" else "#374151"
        if len(everyone) > 1:  # compact mode: every session scrolls past like a banner, its title right below it
            self._title_banner(entries, font, sub_font, left, room, y1, y2 if two else None, sub_fg)
            return
        name, title, _ = entries[0]  # one pet: never cut, a line too long for the tag scrolls round instead
        self._marquee(name, font, y1, theme.T["tag_fg"], left, room)
        if title:
            self._marquee(title, sub_font, y2, sub_fg, left, room)

    def _marquee(self, text, font, y, fill, left, room):
        """One line of the name tag: centred if it fits, else scrolling round like a ticker (characters drawn one by one
        and only while wholly inside the tag, since a Tk canvas can't clip text)."""
        c = self.canvas
        if self._text_w(font, text) <= room:
            c.create_text(pet_style.PET_W / 2, y, text=text, fill=fill, font=font)
            return
        gap = self.BANNER_GAP
        loop_text = text + gap
        loop = self._text_w(font, loop_text)
        offset = (time.time() * self.BANNER_PX + self.seed * 37) % loop
        right = left + room
        for k in (0, 1):
            cx = left - offset + k * loop
            for ch in loop_text:
                key = ("cw", font, ch)
                cw = sprites._IMG_CACHE[key] if key in sprites._IMG_CACHE else sprites._IMG_CACHE.setdefault(key, self._text_w(font, ch))
                if cx >= right:
                    break
                if cx >= left and cx + cw <= right and not ch.isspace():
                    c.create_text(cx, y, text=ch, anchor="w", fill=fill, font=font)
                cx += cw

    def _cut(self, font, text, room):
        """text cut to fit room drawing units, with an ellipsis (real font widths, not a character count)."""
        if self._text_w(font, text) <= room:
            return text
        while text and self._text_w(font, text + "\u2026") > room:
            text = text[:-1]
        return text.rstrip() + "\u2026"

    EXPAND, COLLAPSE = "\u25b4", "\u25be"  # compact mode's badges: up to expand, down to collapse
    # compact mode's banner: each title after its state symbol, both in a faded state colour
    BANNER_STATES = {"working": ("\u2743", "#6a9fd8"), "needs_input": ("\u2749", "#e07b74"),
                     "error": ("\u2749", "#e07b74"), "done": ("\u2713", "#6fb88a"), "idle": ("\u273a", "#9aa3ad")}
    BANNER_GAP = "    "

    BANNER_PX = 14  # scroll speed, drawing units per second
    BANNER_TITLE_MAX = 64  # a title is cut (with an ellipsis) to the width of its name or this, whichever is wider

    def _title_banner(self, entries, font, sub_font, left, room, y1, y2, sub_fg):
        """Compact mode: every session's name after its state symbol (working, waiting, done) in a faded state colour,
        and - when y2 is given - its conversation title in small text right below, both scrolling together through
        the name tag. Shown whole and centred when it all fits. Tk canvases can't clip text, so while scrolling each
        character is drawn on its own and only those wholly inside the tag are shown."""
        c, cols = self.canvas, []  # cols: (top text, colour, bottom text, width)
        gap = self._text_w(font, self.BANNER_GAP)
        for name, title, state in entries:
            sym, colour = self.BANNER_STATES.get(state, ("\u2743", "#9aa3ad"))
            top = f"{sym} {name}"
            tw = self._text_w(font, top)
            sub = self._cut(sub_font, title, max(tw, self.BANNER_TITLE_MAX)) if (title and y2 is not None) else ""
            cols.append((top, colour, sub, max(tw, self._text_w(sub_font, sub))))
        total = sum(w for *_, w in cols) + gap * (len(cols) - 1)
        if total <= room:  # it all fits: centred, no scrolling
            x = pet_style.PET_W / 2 - total / 2
            for top, colour, sub, w in cols:
                c.create_text(x, y1, text=top, anchor="w", fill=colour, font=font)
                if sub:
                    c.create_text(x, y2, text=sub, anchor="w", fill=sub_fg, font=sub_font)
                x += w + gap
            return
        loop = total + gap
        offset = (time.time() * self.BANNER_PX) % loop
        right = left + room
        for k in (0, 1):  # the strip twice, so the start follows the end seamlessly
            x = left - offset + k * loop
            for top, colour, sub, w in cols:
                if x < right and x + w > left:
                    for text, f, y, fill in ((top, font, y1, colour), (sub, sub_font, y2, sub_fg)):
                        cx = x
                        for ch in text:
                            key = ("cw", f, ch)  # per-character widths, measured once
                            cw = sprites._IMG_CACHE[key] if key in sprites._IMG_CACHE else sprites._IMG_CACHE.setdefault(key, self._text_w(f, ch))
                            if cx >= left and cx + cw <= right and not ch.isspace():
                                c.create_text(cx, y, text=ch, anchor="w", fill=fill, font=f)
                            cx += cw
                x += w + gap

    def _draw_badges(self):
        """Where each session runs: one small pixel box per session ("Claude CLI", "Codex WSL VS"...), styled like the
        name tag and sitting like tabs on its top edge, faint red while that session needs you. In compact mode there is
        one per session shown. Badges wrap onto rows above when a row is full. Called after the drawing has been moved
        (see draw())."""
        c = self.canvas
        texts = [pet_style.BADGE_NAMES.get(b, b) for b in self.data.get("badges", [])]
        if not texts:
            return
        flags = self.data.get("badge_attention") or [self.data.get("state") == "needs_input"] * len(texts)
        keys = self.data.get("badge_keys") or [self.data.get("focus", self.key)] * len(texts)  # whose prompt a click opens
        everyone = self.data.get("everyone") or []
        collapse = False
        if len(everyone) > 1:  # compact mode: one merged badge, or every session's badge plus a collapse badge
            if not self.app.badges_expanded:
                texts = [pet_style.merge_badges([(m.get("badges") or ["?"])[0] for m in everyone]) + " " + self.EXPAND]
                flags = [any(m.get("state") == "needs_input" for m in everyone)]
                keys = ["__expand"]
            else:  # the collapse badge goes on a row of its own, above all the others (see below)
                collapse = True
        font = pet_style.fnt(4.5)
        arrow_font = pet_style.fnt(4.5 * 1.2)  # the expand / collapse arrows, 20% bigger than the badge text
        h, gap, s_ = 11, 2, preferences.SCALE["v"]
        left, right = pet_style.BADGE_GUTTER + 6, pet_style.BADGE_GUTTER + pet_style.PET_W - 6

        def split(text):  # (label, arrow): the merged badge ends with the expand arrow, the collapse badge is one
            if text and text[-1] in (self.EXPAND, self.COLLAPSE):
                return text[:-1].rstrip(), text[-1]
            return text, ""

        def width(text):
            label, arrow = split(text)
            dot = 0 if not label else 8  # the arrow-only collapse badge has no dot
            return int(round(4 + dot + self._text_w(font, label) + (2 if label and arrow else 0)
                             + (self._text_w(arrow_font, arrow) if arrow else 0)))
        # expanded compact badges: each session's conversation title on a second, smaller line
        sub_font, sub_h = pet_style.fnt(4.5 * 0.85), 7
        sub_fg = "#d1d5db" if theme.T.get("name") == "dark" else "#374151"  # almost black grey (light grey on dark tags)
        titles = list(self.data.get("badge_titles") or []) if collapse else []
        titles += [""] * (len(texts) - len(titles))

        def fit(text, room):
            if self._text_w(sub_font, text) <= room:
                return text
            while text and self._text_w(sub_font, text + "\u2026") > room:
                text = text[:-1]
            return text.rstrip() + "\u2026" if text else ""
        col_max = 112  # expanded compact mode: the widest a badge in the side column gets (titles are cut to fit)
        rows, x = [[]], left
        for text, hot, key, sub in zip(texts, flags, keys, titles):
            w = width(text)
            if sub:
                sub = fit(sub, (col_max if collapse else right - left) - 6)
                w = max(w, int(round(6 + self._text_w(sub_font, sub))))
            if rows[-1] and x + w > right:  # no more room on this row: wrap to a new one above
                rows.append([])
                x = left
            rows[-1].append((text, hot, x, w, key, sub))
            x += w + gap
        if collapse:  # always the topmost row
            rows.append([(self.COLLAPSE, False, left, width(self.COLLAPSE), "__collapse", "")])
        placed = []  # (text, hot, x, y, w, height, key, title)
        if collapse:
            # expanded compact mode: one column to the LEFT of the pet, right-aligned against it, from the name tag's
            # bottom upwards (the collapse badge on top); a further column to the left when a column is full
            entries = [e for row in rows for e in row]
            col_right, bottom0 = -2, pet_style.PET_H - 2 - pet_style.TOP_TRIM
            bottom, col_w = bottom0, 0
            for text, hot, _, w, key, sub in entries:
                bh = h + (sub_h if sub else 0)
                if bottom - bh < 2 and bottom != bottom0:  # column full: start the next one further left
                    col_right -= col_w + 3
                    bottom, col_w = bottom0, 0
                placed.append((text, hot, col_right - w, bottom - bh, w, bh, key, sub))
                bottom -= bh + gap
                col_w = max(col_w, w)
        else:
            bottom = pet_style.PET_H - 2 - pet_style.TAG_H - pet_style.TOP_TRIM + 4  # the first row overlaps the tag's top border by 4 units
            for row in rows:
                row_h = h + (sub_h if any(e[5] for e in row) else 0)
                for text, hot, bx, w, key, sub in row:
                    bh = h + (sub_h if sub else 0)
                    placed.append((text, hot, bx, bottom - bh, w, bh, key, sub))  # a row's badges share one line
                bottom -= row_h + gap
        for text, hot, bx, y, w, bh, key, sub in placed:
            bg = pet_style.BADGE_ATTENTION_BG if hot else theme.T["tag_bg"]
            tags = ("badge", "badge:" + (key or ""))
            try:
                im = sprites.bubble_image(w, bh, bg, theme.T["tag_outline"], None)
                self._put(("badge", w, bh, bg, theme.T["tag_outline"]), im, int(round(w * s_)), int(round(bh * s_)), bx, y,
                          "nw", tags=tags)
            except Exception:  # no Pillow
                c.create_rectangle(bx, y, bx + w, y + bh, fill=bg, outline=theme.T["tag_outline"], tags=tags)
            if sub:
                c.create_text(bx + 3, y + h + sub_h / 2 - 1.5, text=sub, anchor="w",
                              fill="#374151" if hot else sub_fg, font=sub_font, tags=tags)
            label, arrow = split(text)
            fg = "#111827" if hot else theme.T["tag_fg"]
            tx = bx + 2
            if label:
                dot = pet_style.COLORS["needs_input"] if text.startswith("+") else pet_style.badge_dot(text)
                # the dot at the badge's middle; the text a unit higher than its anchor box would put it, because
                # its visual middle sits below the box's middle (room for descenders)
                c.create_rectangle(bx + 3, y + 4, bx + 6, y + 7, fill=dot, outline="", tags=tags)
                c.create_text(bx + 8, y + h / 2 - 0.5, text=label, anchor="w", fill=fg, font=font, tags=tags)
                tx = bx + 8 + self._text_w(font, label) + 2
            if arrow:
                c.create_text(tx, y + h / 2 - 0.5, text=arrow, anchor="w", fill=fg, font=arrow_font, tags=tags)
        return min((p[2] for p in placed), default=None)

    def _text_w(self, font, text):
        return font_metrics._text_w(font, text)

    def _mole_head(self, cx, bc, k, st, ph):
        c = self.canvas
        rx, ry = 21 * k, 29 * k
        c.create_oval(cx - rx, bc - ry, cx + rx, bc + ry, fill=pet_style.MOLE_BODY, outline=pet_style.MOLE_EDGE, width=2)
        c.create_oval(cx - rx * 0.62, bc - ry * 0.85, cx - rx * 0.2, bc - ry * 0.5, fill=pet_style.MOLE_LIGHT, outline="")
        ey, ex = bc - 9 * k, 8 * k
        for side in (-1, 1):  # eyes
            x = cx + side * ex
            if st in ("done", "idle"):
                c.create_arc(x - 4 * k, ey - 3 * k, x + 4 * k, ey + 3 * k, start=180, extent=180, style="arc",
                             width=2, outline=theme.INK)
            elif st == "error":
                c.create_line(x - 3 * k, ey - 3 * k, x + 3 * k, ey + 3 * k, width=2, fill=theme.INK)
                c.create_line(x - 3 * k, ey + 3 * k, x + 3 * k, ey - 3 * k, width=2, fill=theme.INK)
            elif st == "working" and (ph % 4) < 0.15:  # blink
                c.create_line(x - 3 * k, ey, x + 3 * k, ey, width=2, fill=theme.INK)
            else:
                r = (4.6 if st == "needs_input" else 3.4) * k
                c.create_oval(x - r, ey - r * 1.25, x + r, ey + r * 1.25, fill=theme.INK, outline="")
                c.create_oval(x - r * 0.15, ey - r * 0.95, x + r * 0.45, ey - r * 0.25, fill="white", outline="")
        wig = math.sin(ph * 10) * 1.2 * k if st == "working" else 0  # nose wiggles while digging
        ny = bc + 5 * k
        c.create_oval(cx - 9 * k + wig, ny - 6 * k, cx + 9 * k + wig, ny + 7 * k, fill=pet_style.MOLE_NOSE,
                      outline=pet_style.MOLE_NOSE_EDGE, width=2)
        c.create_oval(cx - 5 * k + wig, ny - 4 * k, cx - 1.5 * k + wig, ny - 0.5 * k, fill="white", outline="")

    def _draw_mole(self, t):
        c = self.canvas
        st = self.data.get("state", "idle")
        col = pet_style.COLORS.get(st, pet_style.COLORS["idle"])
        t += self.seed
        cx0, ground = pet_style.PET_W / 2, 90
        p = max(0.0, min(1.0, (time.time() - self.born) / pet_style.EMERGE_SECONDS)) if self.born else 1.0
        e = 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2 if p > 0 else 0.0
        emerging = p < 1.0
        heads = pet_style.HEAD_LAYOUTS[min(3, int(self.data.get("subagents", 0) or 0)) + 1]

        # the ground disc: its rim shows the state colour
        c.create_oval(cx0 - 40, ground - 9, cx0 + 40, ground + 9, fill=pet_style.MOLE_EDGE, outline=col, width=3)
        top = 99
        for i, (dx, k) in enumerate(heads):
            main = i == len(heads) - 1
            ph = t + i * 1.7
            cx, dy = cx0 + dx, 0.0
            if st == "working":  # popping up and down while digging
                dy = -abs(math.sin(ph * (5 if main else 4.2))) * 8 * k + 2
            elif st == "needs_input":
                dy = (-abs(math.sin(ph * 7)) * 15 if not self.acked else -2 * math.sin(ph * 3)) * (1 if main else 0.6)
            elif st == "error" and not self.acked:
                cx += 2 * math.sin(ph * 25)
            elif st in ("done", "idle"):  # asleep, mostly underground
                dy = 8 + 1.5 * math.sin(ph * 2)
            bc = ground - 34 * k + dy + pet_style.EMERGE_DEPTH * (1 - e)
            top = min(top, bc - 29 * k)
            self._mole_head(cx, bc, k, st, ph)

        # the front hill hides the buried part of the heads
        c.create_oval(cx0 - 31, ground - 15, cx0 + 31, ground + 1, fill=pet_style.MOLE_HILL, outline=pet_style.MOLE_HILL_EDGE, width=2)
        c.create_oval(cx0 - 16, ground - 11, cx0 + 8, ground - 4, fill="#7a4e2b", outline="")
        if not emerging:
            if st == "working":  # flying dirt
                for i in range(5):
                    ph = (t * 1.5 + i * 0.2) % 1
                    x = cx0 + (1 if i % 2 else -1) * (26 + ph * 14)
                    y = ground - 8 - 26 * math.sin(math.pi * ph)
                    r = 2.6 * (1 - ph * 0.5)
                    c.create_oval(x - r, y - r, x + r, y + r, fill=pet_style.MOLE_EDGE, outline="")
            elif st == "needs_input":
                bx, by = cx0 + 28, max(14.0, top - 4)
                r = 10 + (0 if self.acked else 1.5 * math.sin(t * 8))
                c.create_oval(bx - r, by - r, bx + r, by + r, fill="white", outline=pet_style.DARK["needs_input"], width=2)
                c.create_text(bx, by, text="!", fill="#dc2626", font=pet_style.fnt(12, "bold"))
            elif st == "done":
                self._completion_seal(cx0 - 28, top + 8)
            elif st == "idle":  # rising z's
                for i in range(2):
                    ph = (t * 0.6 + i * 0.5) % 1
                    zx, zy, s = cx0 + 22 + ph * 8, 40 - ph * 18, 3 + ph * 3
                    c.create_line(zx - s, zy - s, zx + s, zy - s, zx - s, zy + s, zx + s, zy + s, width=2, fill="#9ca3af")
        self._draw_tag()

    def _char_w(self, font):
        return font_metrics._char_w(font)

    def _terminal_rows(self, rows=3, cols=13):
        """Scrolling hacker text for the working bubble; each row advances at its own speed."""
        tx = self.__dict__.setdefault("_tx", {"rows": None, "t": 0.0})
        now = time.time()
        if tx["rows"] is None or len(tx["rows"][0]) != cols:
            tx["rows"] = ["".join(random.choice(pet_style.TERMINAL_CHARS) for _ in range(cols)) for _ in range(rows)]
        if now - tx["t"] > 0.09:
            tx["t"] = now
            for i in range(rows):
                if random.random() < (0.55, 0.85, 0.4)[i % 3]:
                    tx["rows"][i] = tx["rows"][i][1:] + random.choice(pet_style.TERMINAL_CHARS)
        return tx["rows"]

    def _put(self, key, image, w, h, x, y, anchor="nw", tags=()):
        """Draw a PIL image scaled to w x h (final pixels) at canvas point (x, y). Canvas coordinates are at base size
        and _finish() scales them by SCALE, which is why rectangles use w / SCALE. See RECT_SPRITES."""
        c = self.canvas
        if not pet_style.RECT_SPRITES:
            c.create_image(x, y, image=sprites.sprite_photo(key, image, w, h), anchor=anchor, tags=tags)
            return
        s = preferences.SCALE["v"]
        bw, bh = w / s, h / s  # size in base coordinates
        left = x - (bw / 2 if anchor in ("center", "n", "s") else (bw if "e" in anchor else 0))
        top = y - (bh / 2 if anchor in ("center", "e", "w") else (bh if "s" in anchor else 0))
        kx, ky = bw / image.width, bh / image.height
        for x0, y0, x1, y1, col in sprites.pixel_rects(key, image):
            c.create_rectangle(left + x0 * kx, top + y0 * ky, left + x1 * kx, top + y1 * ky,
                               fill=col, outline="", width=0, tags=tags)

    def _bubble(self, x1, y1, x2, y2, tail_x, fill, outline, tags=()):
        """Pixel-art speech bubble (see bubble_image) with its top-left at (x1, y1); the tail points down at tail_x."""
        w, h = max(14, int(round(x2 - x1))), max(9, int(round(y2 - y1)))
        tcx = max(5, min(w - 6, int(round(tail_x - x1))))
        im = sprites.bubble_image(w, h, fill, outline, tcx)
        s = preferences.SCALE["v"]
        self._put(("bubble", w, h, fill, outline, tcx), im, int(round(w * s)), int(round(im.height * s)),
                  int(round(x1)), int(round(y1)), "nw", tags)

    def _mark(self, kind, cx, cy, k=1, tags=()):
        im = sprites.mark_image(kind, k)
        s = preferences.SCALE["v"]
        self._put(("mark", kind, k), im, int(round(im.width * s)), int(round(im.height * s)),
                  int(round(cx)), int(round(cy)), "center", tags)

    def _draw_robot(self, t):
        spr = sprites.load_sprites()
        if not spr:
            return self._draw_mole(t)
        c = self.canvas
        st = self.data.get("state", "idle")
        t += self.seed
        s = preferences.SCALE["v"]
        cx0, ground = pet_style.PET_W / 2, 90
        p = max(0.0, min(1.0, (time.time() - self.born) / pet_style.EMERGE_SECONDS)) if self.born else 1.0
        e = 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2 if p > 0 else 0.0
        rise = pet_style.EMERGE_DEPTH * (1 - e)  # new pets rise up from behind the name tag
        emerging = p < 1.0
        shadow = spr["img"]["shadow"]

        # the robot (one per active agent: the main one in front, subagents smaller beside it)
        rm = spr["meta"]["robot"]
        rw0, rh0 = rm["size"]
        members = self.data.get("members")
        if members:  # compact mode: a robot per session, each in its own state; the front one is the focus
            heads = pet_style.HEAD_LAYOUTS[len(members)]
            states = [(m["state"], self.app.ack.get(m["key"], False)) for m in members[1:]] + [(st, self.acked)]
        else:  # one session: the main robot in front, its subagents smaller beside it, all in the session's state
            heads = pet_style.HEAD_LAYOUTS[min(3, int(self.data.get("subagents", 0) or 0)) + 1]
            states = [(st, self.acked)] * len(heads)
        top_main = ground
        flash = bool(self.data.get("auto_flash")) and st not in ("needs_input", "error")
        # a subagent or another session just finished: the done check briefly takes over the working bubble
        done_flash = st == "working" and time.time() < self.app.done_flash.get(self.data.get("focus", self.key), 0)
        for i, (dx, k) in enumerate(heads):
            main = i == len(heads) - 1
            hst, hacked = states[i]
            ph = t + i * 1.7
            cx, dy, squash = cx0 + dx, 0.0, 1.0
            face = sprites.STATE_FACE.get(hst, "sleep")
            if main and flash:  # just auto-approved something: a calm, content robot with green lights
                hst, face = "auto", "joy"
                dy = -abs(math.sin(ph * 2.5)) * 1.5
            elif hst == "working":
                dy = -abs(math.sin(ph * (5 if main else 4.2))) * 3 * k
                frame = int(ph * 8) % sprites.TALK_FRAMES  # the wave travels along the mouth (one loop = 0.5 s, 2 Hz)
                # Blink on a cosine at BLINK_RATIO x the mouth's frequency: a short blink near each peak. The two
                # rhythms drift apart and line up again now and then.
                blink = math.cos(2 * math.pi * pet_style.BLINK_RATIO * (8 / sprites.TALK_FRAMES) * ph) > pet_style.BLINK_THRESHOLD
                face = f"talkblink{frame}" if blink else f"talk{frame}"
                if main and done_flash:
                    face = "joy"
            elif hst == "needs_input":  # hops while it waits for you
                dy = (-abs(math.sin(ph * 7)) * 8 if not hacked else -1.5 * math.sin(ph * 3)) * (1 if main else 0.6)
                squash = 1.0 if dy < -1.2 else 0.94
            elif hst == "error" and not hacked:
                cx += 2 * math.sin(ph * 25)
            elif hst == "idle":
                squash = 1 + 0.025 * math.sin(ph * 2)  # slow breathing
            w, h = int(round(rw0 * k * s)), int(round(rh0 * k * squash * s))
            sw = int(round(rw0 * k * 1.1 * s))
            self._put("shadow", shadow, sw, max(2, int(sw * 0.25)), cx, ground + 2, "center")
            im = sprites.robot_image(face, sprites.light_cycle(hst, ph))
            self._put(("robot", face, sprites.light_cycle(hst, ph)), im, w, h, cx, ground + dy + rise, "s", tags=("hit",))
            if main:
                top_main = ground + dy - rh0 * k * squash

        if not emerging:
            bx1, bx2 = cx0 - 34, cx0 + 36
            by1 = 15.0  # the top of the drawing (the strip above is trimmed off: badges live in a left column)
            by2 = 36.0 if st == "needs_input" else min(top_main + 2, 40.0)  # the hop must not squash the bubble
            tail = cx0 - 6  # off the antenna
            if flash:  # a small pale-green "✓ auto" bubble: approved by the whitelist, nothing to do
                top = by2 - 19
                self._bubble(cx0 - 26, top, cx0 + 16, by2, tail, "#f0fdf4", "#4b8f63")
                y = (top + by2) / 2
                self._mark("ok", cx0 - 15, y)
                c.create_text(cx0 + 3, y, text="auto", fill="#4b8f63", font=pet_style.fnt(5))
            elif done_flash:
                self._completion_seal(cx0 - 27, top_main + 8)
            elif st == "working":  # hacker-screen bubble
                self._bubble(bx1, by1, bx2, by2, tail, "#0b1220", "#34d399")
                font = sprites.mono(5)
                cw = self._char_w(font)  # real width of one character, in drawing units
                rows = self._terminal_rows(cols=max(6, int((bx2 - bx1 - 12) / cw)))
                for i, row in enumerate(rows):
                    y = by1 + 6 + i * (by2 - by1 - 8) / 2.6
                    c.create_text(bx1 + 5, y, text=row[:-1], anchor="w", fill="#22c55e", font=font)
                    c.create_text(bx1 + 5 + cw * (len(row) - 1), y, text=row[-1], anchor="w", fill="#d1fae5", font=font)
            elif st == "done":
                # A completion seal, away from the interactive speech bubble.
                x, y = cx0 - 27, top_main + 8
                self._completion_seal(x, y)
            elif st == "error":
                top = by2 - 19  # a small bubble, centred on its tail
                self._bubble(cx0 - 22, top, cx0 + 10, by2, tail, "#fafafa", "#111827")
                bob = 1 if int(t * 3) % 2 else 0  # a one-pixel bob
                self._mark("ok" if st == "done" else "no", cx0 - 6, (top + by2) / 2 - bob)
            elif st == "needs_input":  # the answer bubble: check / cross / question mark (click it for the popup)
                self._bubble(bx1 + 4, by1, bx2 - 6, by2, tail, "#fafafa", "#111827", tags=("ans",))
                y, pulse = (by1 + by2) / 2, int(t * 2) % 3
                if (self.data.get("request") or {}).get("kind") == "question":  # a question: just a bobbing "?"
                    self._mark("?", cx0 - 1, y - (1 if int(t * 3) % 2 else 0), tags=("ans",))
                else:
                    for i, (x, kind) in enumerate(((cx0 - 16, "ok"), (cx0 - 1, "no"), (cx0 + 14, "?"))):
                        self._mark(kind, x, y - (1 if i == pulse else 0), tags=("ans",))
            else:  # idle: sleeping, rising z's
                for i in range(3):
                    ph = (t * 0.5 + i / 3) % 1
                    zx, zy = cx0 + 14 + ph * 14, top_main + 4 - ph * 22
                    c.create_text(zx, zy, text="z", fill="#9ca3af", font=pet_style.fnt(8 + 5 * ph, "bold"))
        self._draw_tag()

    def _draw_cat(self, t):
        c = self.canvas
        st = self.data.get("state", "idle")
        col, dark = pet_style.COLORS.get(st, pet_style.COLORS["idle"]), pet_style.DARK.get(st, pet_style.DARK["idle"])
        t += self.seed
        cx, base, rx, ry, dy = pet_style.PET_W / 2, 66, 26, 22, 0.0

        # Diglett-style entrance: rise out of a dirt mound with a little overshoot.
        p = max(0.0, min(1.0, (time.time() - self.born) / pet_style.EMERGE_SECONDS)) if self.born else 1.0
        e = 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2 if p > 0 else 0.0
        emerging = p < 1.0

        if st == "working":
            dy = -3 * math.sin(t * 6)
        elif st == "needs_input":
            dy = -abs(math.sin(t * 7)) * 12 if not self.acked else -2 * math.sin(t * 3)
        elif st == "error" and not self.acked:
            cx += 2 * math.sin(t * 25)
        else:
            ry *= 1 + 0.04 * math.sin(t * 2)  # breathing

        by = base + dy + pet_style.EMERGE_DEPTH * (1 - e)
        ground = base + 26
        sw = rx * (1 + dy / 40)
        c.create_oval(cx - sw, ground - 3, cx + sw, ground + 4, fill="#2b2b2b", outline="")

        top = by - ry
        for side in (-1, 1):  # ears
            c.create_polygon(cx + side * 18, top + 7, cx + side * 13, top - 9, cx + side * 4, top + 2,
                             fill=col, outline=dark, width=2)
        c.create_oval(cx - rx, by - ry, cx + rx, by + 22, fill=col, outline=dark, width=2)

        ey = by - 3
        for side in (-1, 1):  # eyes
            ex = cx + side * 9
            if st in ("done", "idle"):
                c.create_arc(ex - 5, ey - 3, ex + 5, ey + 4, start=180, extent=180, style="arc", width=2, outline=theme.INK)
            elif st == "error":
                c.create_line(ex - 4, ey - 4, ex + 4, ey + 4, width=2, fill=theme.INK)
                c.create_line(ex - 4, ey + 4, ex + 4, ey - 4, width=2, fill=theme.INK)
            elif st == "working" and (t % 4) < 0.15:  # blink
                c.create_line(ex - 4, ey, ex + 4, ey, width=2, fill=theme.INK)
            else:
                r = 6 if st == "needs_input" else 5
                px = 2 * math.sin(t * 1.7) if st == "working" else 0
                c.create_oval(ex - r, ey - r, ex + r, ey + r, fill="white", outline=theme.INK)
                c.create_oval(ex + px - 2.5, ey - 2.5, ex + px + 2.5, ey + 2.5, fill=theme.INK, outline="")

        my = by + 9  # mouth
        if st == "needs_input":
            c.create_oval(cx - 3, my - 3, cx + 3, my + 3, fill=theme.INK, outline="")
        elif st == "done":
            c.create_arc(cx - 6, my - 6, cx + 6, my + 3, start=200, extent=140, style="arc", width=2, outline=theme.INK)
            for side in (-1, 1):
                c.create_oval(cx + side * 17 - 4, my - 4, cx + side * 17 + 4, my + 1, fill="#f9a8d4", outline="")
        elif st == "error":
            c.create_line(cx - 6, my, cx - 2, my - 2, cx + 2, my, cx + 6, my - 2, width=2, fill=theme.INK)
        else:
            c.create_line(cx - 4, my, cx + 4, my, width=2, fill=theme.INK)

        if emerging:  # dirt mound hides the part of the body still underground
            mw = 34 * (1 - 0.5 * p)
            c.create_oval(cx - mw, ground - 12, cx + mw, ground + 3, fill=pet_style.MOUND, outline=pet_style.MOUND_DARK, width=2)
            c.create_oval(cx - mw * 0.55, ground - 8, cx + mw * 0.55, ground - 1, fill=pet_style.MOUND_DARK, outline="")

        if emerging:
            pass
        elif st == "working":  # orbiting dots
            for i in range(3):
                a = t * 3 + i * 2.094
                x, y = cx + 20 * math.cos(a), top - 14 + 5 * math.sin(a)
                c.create_oval(x - 3, y - 3, x + 3, y + 3, fill="white", outline=dark)
        elif st == "needs_input":
            bx, byy = cx + 24, top - 14
            pulse = 0 if self.acked else 1.5 * math.sin(t * 8)
            r = 10 + pulse
            c.create_oval(bx - r, byy - r, bx + r, byy + r, fill="white", outline=dark, width=2)
            c.create_text(bx, byy, text="!", fill="#dc2626", font=pet_style.fnt(12, "bold"))
        elif st == "done":
            self._completion_seal(cx - 28, top + 8)

        self._draw_tag()
