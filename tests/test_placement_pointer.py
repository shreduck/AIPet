"""The pet app's placement (the anchored bottom-right corner) and pointer (drag, clicks, click-through)."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def root(x=1000, y=700, w=200, h=150):
    r = MagicMock()
    r.winfo_reqwidth.return_value, r.winfo_reqheight.return_value = w, h
    r.winfo_width.return_value, r.winfo_height.return_value = w, h
    r.winfo_x.return_value, r.winfo_y.return_value = x, y
    r.winfo_screenwidth.return_value, r.winfo_screenheight.return_value = 1920, 1080
    return r


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class PlacementTests(unittest.TestCase):
    def setUp(self):
        from aipetlib.app import placement
        self.module = placement
        fit = patch.object(placement.geometry, "fit_on_screen", side_effect=lambda x, y, w, h, ref, r: (x, y))
        fit.start()
        self.addCleanup(fit.stop)
        self.app = SimpleNamespace(root=root(), pets={}, order=[], frame=MagicMock())
        self.placement = placement.Placement(self.app)

    def test_reposition_keeps_the_bottom_right_corner(self):
        self.placement.anchor = [1500, 900]
        self.placement.reposition()
        self.app.root.geometry.assert_called_with(self.module.geometry.geo(1300, 750))
        self.assertEqual(self.placement.anchor, [1500, 900])

    def test_growing_moves_the_window_left_before_it_redraws(self):
        self.placement.anchor = [1500, 900]
        self.placement.shift_for_width(40)
        self.app.root.geometry.assert_called_with(self.module.geometry.geo(1260, 750))

    def test_reset_goes_back_to_the_main_screen_corner(self):
        with patch.object(self.module, "IS_MAC", False):
            self.placement.reset_position()
        self.assertEqual(self.placement.anchor, [1920 - 24, 1080 - 60])
        self.app.root.deiconify.assert_called_once()


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class PointerTests(unittest.TestCase):
    def setUp(self):
        from aipetlib.app.pointer import Pointer
        item = {"key": "s1", "state": "needs_input"}
        self.app = SimpleNamespace(root=root(), placement=SimpleNamespace(anchor=[0, 0]), tooltips=SimpleNamespace(hide_tip=MagicMock()),
                                   cfg={}, _last_items=[item], ack={}, badges_expanded=False,
                                   open_detail=MagicMock(), focus_session=MagicMock(), _acknowledge=MagicMock(),
                                   pets={}, _menu_open=False, clickthru=MagicMock())
        self.pointer = Pointer(self.app)
        self.pet = SimpleNamespace(key="s1", data={"focus": "s1"}, _on_bubble=False, _on_badge=None)

    def test_a_drag_moves_the_window_and_sets_the_anchor(self):
        self.pointer.press(SimpleNamespace(x_root=10, y_root=10))
        self.pointer.move(SimpleNamespace(x_root=60, y_root=30))
        self.app.root.geometry.assert_called_with("+1050+720")
        self.pointer.release(SimpleNamespace(state=0), self.pet)
        self.assertEqual(self.app.placement.anchor, [1000 + 200, 700 + 150])
        self.app.focus_session.assert_not_called()  # a drag is not a click
        self.assertIsNone(self.pointer.drag)

    def test_a_click_acknowledges_and_goes_to_the_session(self):
        self.pointer.press(SimpleNamespace(x_root=10, y_root=10))
        self.pointer.release(SimpleNamespace(state=0), self.pet)
        self.app._acknowledge.assert_called_once_with(self.pet)
        self.app.focus_session.assert_called_once_with(self.pet)

    def test_a_badge_of_a_waiting_session_opens_its_prompt(self):
        self.pet._on_badge = "s1"
        self.pointer.release(SimpleNamespace(state=0), self.pet)
        self.app.open_detail.assert_called_once_with("s1")
        self.assertTrue(self.app.ack["s1"])

    def test_clicks_pass_through_away_from_the_pets(self):
        self.app.root.winfo_pointerxy.return_value = (5, 5)
        self.pointer.pass_tick()
        self.app.clickthru.set.assert_called_once_with(True)
        self.app.root.after.assert_called_with(50, self.pointer.pass_tick)


if __name__ == "__main__":
    unittest.main()
