"""Small, toolkit-free helpers for precise graph gestures."""
import math


def inside(point, rect):
    x, y = point
    left, top, right, bottom = rect
    return left <= x < right and top <= y < bottom


def over_graph(point, rect, exclusions=()):
    return rect is not None and inside(point, rect) and not any(inside(point, r) for r in exclusions)


class GestureBatch:
    """Coalesce adjacent events without losing fractional distance or zoom.

    Keep pan/zoom ordering; the native adapter drains this on the Tk thread.
    """
    def __init__(self):
        self.events = []

    def pan(self, dx, dy):
        if not all(math.isfinite(v) for v in (dx, dy)) or not (dx or dy):
            return False
        if self.events and self.events[-1][0] == "pan":
            _, x, y = self.events[-1]
            self.events[-1] = ("pan", x + dx, y + dy)
        else:
            self.events.append(("pan", dx, dy))
        return True

    def pinch(self, magnification, at):
        if not all(math.isfinite(v) for v in (magnification, *at)) or magnification <= -1 or not magnification:
            return False
        change = math.log1p(magnification)
        if self.events and self.events[-1][0] == "zoom":
            change += self.events[-1][1]
            self.events[-1] = ("zoom", change, at)
        else:
            self.events.append(("zoom", change, at))
        return True

    def drain(self):
        events, self.events = self.events, []
        return [(kind, math.exp(max(-20, min(20, a))), b) if kind == "zoom" else (kind, a, b)
                for kind, a, b in events]

    def clear(self):
        self.events.clear()
