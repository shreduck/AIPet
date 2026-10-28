"""Opt-in native Tk check for alert focus and actual widget key routing."""
import os
import sys
import time
import unittest


@unittest.skipUnless(os.environ.get('AIPET_NATIVE_WINDOW_TEST') == '1', 'opt-in native window test')
class NativeCarouselTests(unittest.TestCase):
    def test_no_focus_default_preserves_foreground_and_switches_apply_live(self):
        import tkinter as tk
        from aipetlib.ui.addin_messages import show
        root = tk.Tk()
        root.withdraw()
        changed = []
        try:
            if sys.platform == 'win32':
                import ctypes
                user = ctypes.windll.user32
                user.GetForegroundWindow.restype = ctypes.c_void_p
                foreground = user.GetForegroundWindow()
            win = show(root, [dict(name='Test', level='info', messages=[
                dict(id='fixture', title='Synthetic alert', level='info', content='No focus test')])],
                screen=dict(work_area=[0, 0, 1280, 800]),
                on_options=lambda key, value: changed.append((key, value)))
            end = time.monotonic()+0.15
            while time.monotonic() < end:
                root.update()
            if sys.platform == 'win32':
                self.assertEqual(user.GetForegroundWindow(), foreground)

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            switches = [widget for widget in descendants(win) if hasattr(widget, 'set_disabled')]
            switches[0].toggle()
            self.assertFalse(win.attributes('-topmost'))
            switches[1].toggle()
            self.assertEqual(changed, [('addin_alert_topmost', False), ('addin_alert_no_focus', False)])
        finally:
            root.destroy()

    def test_shortcuts_work_after_opening_and_using_scale_slider(self):
        import tkinter as tk
        from aipetlib.ui.addin_messages import show
        root = tk.Tk()
        root.withdraw()
        dismissed = []
        try:
            rows = [dict(name='Test add-in', level='warning', messages=[
                dict(id=key, title=f'Alert {key}', content='Synthetic test report', level='warning', timestamp=1234567890)
                for key in ('one', 'two', 'three')])]
            win = show(root, rows, on_close=lambda rows: dismissed.extend(rows),
                       screen=dict(work_area=[0, 0, 1280, 800]), options={'addin_alert_no_focus': False})

            def pump():
                end = time.monotonic()+0.15
                while time.monotonic() < end:
                    root.update()

            pump()
            focused = root.focus_get()
            self.assertIsNotNone(focused, 'the newly opened alert must receive keyboard focus')
            self.assertEqual(focused.winfo_class(), 'Text')
            received = []
            focused.bind_class('NativeTestKeyProbe', '<KeyPress>',
                               lambda event: received.append((event.keysym, event.state)))
            focused.bindtags(('NativeTestKeyProbe', *focused.bindtags()))

            if sys.platform == 'win32':
                import ctypes
                from ctypes import wintypes
                user = ctypes.windll.user32
                user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                user.GetAncestor.restype = ctypes.c_void_p
                user.GetForegroundWindow.restype = ctypes.c_void_p
                target = user.GetAncestor(win.winfo_id(), 2)
                if user.GetForegroundWindow() != target:
                    # Windows may refuse background activation. Exercise the real user click path.
                    rect, old = wintypes.RECT(), wintypes.POINT()
                    user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
                    user.GetWindowRect(target, ctypes.byref(rect))
                    point = wintypes.POINT((rect.left+rect.right)//2, rect.top+12)
                    user.WindowFromPoint.argtypes = [wintypes.POINT]
                    user.WindowFromPoint.restype = ctypes.c_void_p
                    self.assertEqual(user.GetAncestor(user.WindowFromPoint(point), 2), target,
                                     'Never click another application during this test')
                    user.GetCursorPos(ctypes.byref(old))
                    try:
                        user.SetCursorPos(point.x, point.y)
                        user.mouse_event(2, 0, 0, 0, 0)
                        user.mouse_event(4, 0, 0, 0, 0)
                        pump()
                    finally:
                        user.SetCursorPos(old.x, old.y)

            def press(key, vk):
                if sys.platform == 'win32':
                    import ctypes
                    user = ctypes.windll.user32
                    user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                    user.GetAncestor.restype = ctypes.c_void_p
                    user.GetForegroundWindow.restype = ctypes.c_void_p
                    self.assertEqual(user.GetForegroundWindow(), user.GetAncestor(win.winfo_id(), 2),
                                     'Never send a test key to another application')
                    extended = 1 if key in ('Left', 'Right') else 0
                    user.keybd_event(vk, 0, extended, 0)
                    user.keybd_event(vk, 0, extended | 2, 0)
                else:
                    root.focus_get().event_generate('<KeyPress>', keysym=key)
                pump()

            press('Right', 0x27)
            pump()
            self.assertIn('Alert two', win.title(), repr(received))
            press('Left', 0x25)
            pump()
            self.assertIn('Alert one', win.title())

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            button = next(widget for widget in descendants(win)
                          if widget.winfo_class() == 'Button' and widget.cget('text') == 'Next ›')
            # A native/widget handler consuming keys must not defeat carousel shortcuts.
            button.bind('<KeyPress>', lambda event: 'break')
            button.focus_force()
            pump()
            press('Right', 0x27)
            self.assertIn('Alert two', win.title())
            press('d', 0x44)
            self.assertEqual(dismissed[0]['messages'][0]['id'], 'two')
            self.assertIn('Alert three', win.title())

            slider = next(widget for widget in descendants(win) if widget.winfo_class() == 'Scale')
            slider.focus_force()
            pump()
            initial = slider.get()
            press('Right', 0x27)
            pump()
            self.assertNotEqual(slider.get(), initial)
            self.assertIn('Alert three', win.title(), 'slider arrows must not navigate the carousel')
            from tkinter import font
            self.assertEqual(font.Font(root=root, font=button.cget('font')).actual('size'), 10)
            self.assertGreater(win.winfo_width(), 991)
            switches = [widget for widget in descendants(win) if hasattr(widget, 'set_disabled')]
            self.assertEqual(switches[0].winfo_reqwidth(), round(42*1.1)+2)
            press('d', 0x44)
            pump()
            self.assertEqual(dismissed[1]['messages'][0]['id'], 'three')
            self.assertIn('Alert one', win.title())
            press('d', 0x44)
            pump()
            self.assertFalse(win.winfo_exists())
            self.assertEqual(len(dismissed), 3)
            self.assertIsNone(root.grab_current())
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
