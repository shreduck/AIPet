"""Gesture math and legacy input regressions without opening a desktop window."""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aipetlib.gestures import GestureBatch, over_graph
from aipetlib.ui import agent_common, agent_graph


class Viewport:
    """Model Tk's integer viewport positions independently of gesture helpers."""
    def __init__(self):
        self.left, self.top = 500, 600
        self.scrolls = []

    def canvasx(self, x):
        return self.left + x

    def canvasy(self, y):
        return self.top + y

    def winfo_width(self):
        return 600

    def winfo_height(self):
        return 400

    def xview_scroll(self, amount, unit):
        self.scrolls.append(('x', amount, unit))

    def yview_scroll(self, amount, unit):
        self.scrolls.append(('y', amount, unit))


def graph_window():
    """A GraphView over a modelled canvas, with its drawing and zoom bar stubbed out."""
    canvas = Viewport()
    view = agent_graph.GraphView(SimpleNamespace())
    view.canvas, view.content, view.center_next = canvas, (20000, 20000), False
    view.update_zoombar, view.shift_into_view = Mock(), Mock(return_value=False)

    def scroll_to(left, top):
        canvas.left, canvas.top = round(left), round(top)

    def draw(force=False):
        view.content = (20000 * view.gz, 20000 * view.gz)
    view.scroll_to, view.draw = scroll_to, draw
    return view


class GestureTests(unittest.TestCase):
    def test_fractional_pan_batches_preserve_both_axes_and_order(self):
        batch = GestureBatch()
        for _ in range(100):
            batch.pan(.01, -.02)
        batch.pinch(.1, (40, 50))
        batch.pan(3, 4)
        events = batch.drain()
        self.assertEqual([e[0] for e in events], ['pan', 'zoom', 'pan'])
        self.assertAlmostEqual(events[0][1], 1.)
        self.assertAlmostEqual(events[0][2], -2.)
        self.assertEqual(events[2], ('pan', 3, 4))
        self.assertEqual(batch.drain(), [])

    def test_pinch_composition_is_multiplicative_and_keeps_latest_anchor(self):
        batch = GestureBatch()
        batch.pinch(.1, (40, 50))
        batch.pinch(-.2, (41, 52))
        event, = batch.drain()
        self.assertAlmostEqual(event[1], 1.1 * .8)
        self.assertEqual(event[2], (41, 52))

    def test_invalid_gestures_do_not_poison_following_input(self):
        batch = GestureBatch()
        for dx, dy in ((math.inf, 0), (0, math.nan), (0, 0)):
            self.assertFalse(batch.pan(dx, dy))
        for amount, anchor in ((-1, (0, 0)), (-2, (0, 0)), (math.nan, (0, 0)), (.1, (math.inf, 0))):
            self.assertFalse(batch.pinch(amount, anchor))
        batch.pan(1, 2)
        self.assertEqual(batch.drain(), [('pan', 1, 2)])

    def test_graph_hit_testing_excludes_controls_and_far_edges(self):
        rect = (10, 20, 100, 200)
        self.assertTrue(over_graph((10, 20), rect))
        self.assertFalse(over_graph((100, 20), rect))
        self.assertFalse(over_graph((20, 200), rect))
        self.assertFalse(over_graph((40, 50), rect, [(30, 40, 50, 60)]))
        self.assertFalse(over_graph((40, 50), None))

    def test_tiny_pan_accumulates_and_speed_is_independent_of_zoom(self):
        for zoom in (.1, 1, 3):
            window = graph_window()
            window.gz = zoom
            for _ in range(100):
                window.pan(.05, .03)
            self.assertEqual((window.canvas.left, window.canvas.top), (505, 603))
            self.assertTrue(window.user_moved)
            self.assertFalse(window.center_next)

    def test_blocked_pan_discards_distance_so_reversing_responds(self):
        window = graph_window()
        window.scroll_to = lambda left, top: setattr(window.canvas, 'left', min(500, round(left)))
        window.pan(100, 0)
        self.assertEqual(window.canvas.left, 500)
        window.pan(-3, 0)
        self.assertEqual(window.canvas.left, 497)

    def test_precise_zoom_keeps_small_changes_anchor_and_limits(self):
        window = graph_window()
        anchor = (200, 150)
        before = window.canvas.canvasx(anchor[0]), window.canvas.canvasy(anchor[1])
        window.zoom(1.1, at=anchor, precise=True)
        after = window.canvas.canvasx(anchor[0]), window.canvas.canvasy(anchor[1])
        for old, new in zip(before, after):
            self.assertAlmostEqual(new, old * 1.1)
        window.zoom(1.00001, precise=True)
        self.assertAlmostEqual(window.gz, 1.100011)
        window.zoom(100, precise=True)
        self.assertEqual(window.gz, 3.)
        window.zoom(.00001, precise=True)
        self.assertEqual(window.gz, .01)

    def test_legacy_zoom_rounding_and_reset_are_preserved(self):
        window = graph_window()
        window.zoom(1.00001)
        self.assertEqual(window.gz, 1.)
        window.zoom(1.15)
        self.assertEqual(window.gz, 1.15)
        window.zoom(reset=True)
        self.assertEqual(window.gz, 1.)
        self.assertFalse(window.user_moved)
        self.assertTrue(window.center_next)

    def test_legacy_mouse_wheel_and_modifier_paths_on_mac_and_windows(self):
        for is_mac in (False, True):
            for state in (0, 1, 4, 8, 5, 9):
                for delta, num, up in ((120, 0, True), (-120, 0, False), (0, 4, True), (0, 5, False)):
                    with self.subTest(mac=is_mac, state=state, delta=delta, num=num):
                        window = graph_window()
                        window.zoom = Mock(return_value='break')
                        event = SimpleNamespace(delta=delta, num=num, state=state, x=31, y=47)
                        with patch.object(agent_common, 'IS_MAC', is_mac):
                            self.assertEqual(window.wheel(event), 'break')
                        if state & 4 or (is_mac and state & 8):
                            window.zoom.assert_called_once_with(1.15 if up else 1 / 1.15, at=(31, 47))
                            self.assertEqual(window.canvas.scrolls, [])
                        else:
                            window.zoom.assert_not_called()
                            self.assertEqual(window.canvas.scrolls, [('x' if state & 1 else 'y', -2 if up else 2, 'units')])
                            window.shift_into_view.assert_called_once()
