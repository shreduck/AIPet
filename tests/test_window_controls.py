"""Switch interaction and frameless resizing preserve state and screen coordinates."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aipetlib.ui import theme, window_resize
from aipetlib.ui.toggle_switch import ToggleSwitch


class WindowControlTests(unittest.TestCase):
    def test_pause_button_style_is_light_orange_with_dark_text(self):
        with patch.object(theme, 'IS_MAC', False):
            style = theme.button_style('warning')
        self.assertEqual(style['bg'], '#ffedd5')
        self.assertEqual(style['fg'], '#9a3412')
        self.assertEqual(style['activebackground'], '#fed7aa')

    def test_switch_click_and_keyboard_toggle_value_but_disabled_switch_does_not(self):
        switch = object.__new__(ToggleSwitch)
        switch.variable = MagicMock()
        switch.variable.get.return_value = False
        switch.disabled = False
        self.assertEqual(switch.toggle(), 'break')
        switch.variable.set.assert_called_once_with(True)
        switch.variable.reset_mock()
        switch.variable.get.return_value = True
        switch.toggle()
        switch.variable.set.assert_called_once_with(False)
        switch.variable.reset_mock()
        switch.disabled = True
        switch.toggle()
        switch.variable.set.assert_not_called()

    def test_switch_thumb_moves_to_opposite_end_when_value_changes(self):
        switch = object.__new__(ToggleSwitch)
        switch.variable, switch.disabled = MagicMock(), False
        for method in ('delete', 'configure', 'create_oval', 'create_rectangle'):
            setattr(switch, method, MagicMock())
        for value, left in ((False, 4), (True, 24)):
            switch.variable.get.return_value = value
            switch.draw()
            self.assertEqual(switch.create_oval.call_args.args[:4], (left, 5, left+14, 19))

    def test_corner_resize_changes_size_keeps_position_and_honors_minimum(self):
        win, resized = MagicMock(), MagicMock()
        win.winfo_width.return_value, win.winfo_height.return_value = 400, 300
        win.winfo_x.return_value, win.winfo_y.return_value = -800, 100
        with patch.object(window_resize.tk, 'Canvas') as canvas:
            window_resize.corner_grip(win, minimum=(320, 220), on_resize=resized)
            bindings = {call.args[0]: call.args[1] for call in canvas.return_value.bind.call_args_list}
            bindings['<ButtonPress-1>'](SimpleNamespace(x_root=100, y_root=100))
            bindings['<B1-Motion>'](SimpleNamespace(x_root=250, y_root=180))
            win.geometry.assert_called_with('550x380+-800+100')
            bindings['<B1-Motion>'](SimpleNamespace(x_root=-500, y_root=-500))
            win.geometry.assert_called_with('320x220+-800+100')
            self.assertEqual(resized.call_count, 2)
            win.resizable.assert_called_once_with(True, True)
            bindings['<ButtonRelease-1>'](None)
            win.geometry.reset_mock()
            bindings['<B1-Motion>'](SimpleNamespace(x_root=500, y_root=500))
            win.geometry.assert_not_called()

    def test_shared_theme_repaints_custom_control(self):
        widget = SimpleNamespace(winfo_class=lambda: 'Canvas', configure=MagicMock(),
                                 winfo_children=lambda: [], _pet_retheme=MagicMock())
        theme.theme_widget(widget)
        widget._pet_retheme.assert_called_once()


if __name__ == '__main__':
    unittest.main()
