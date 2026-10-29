"""Opt-in native Tk check for alert focus and actual widget key routing."""
import os
import sys
import time
import unittest


@unittest.skipUnless(os.environ.get('AIPET_NATIVE_WINDOW_TEST') == '1', 'opt-in native window test')
class NativeCarouselTests(unittest.TestCase):
    def test_scale_drag_does_not_resize_controls_until_mouse_release(self):
        import tkinter as tk
        from aipetlib.ui.addin_messages import show
        root = tk.Tk()
        root.withdraw()
        try:
            win = show(root, [dict(name='Test', level='info', messages=[
                dict(id='fixture', title='Scale test', level='info', content='Synthetic report')])],
                screen=dict(work_area=[0, 0, 1280, 800]))
            root.update()
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            slider = next(widget for widget in descendants(win) if widget.winfo_class() == 'Scale')
            size = win.winfo_width(), win.winfo_height(), slider.winfo_width()
            x, y = slider.coords(slider.get())
            end_x, end_y = slider.coords(150)
            slider.event_generate('<ButtonPress-1>', x=x, y=y)
            slider.event_generate('<B1-Motion>', x=end_x, y=end_y, state=0x100)
            root.update()
            self.assertGreater(slider.get(), 100)
            self.assertEqual((win.winfo_width(), win.winfo_height(), slider.winfo_width()), size)
            slider.event_generate('<ButtonRelease-1>', x=end_x, y=end_y)
            root.update()
            self.assertGreater(win.winfo_width(), size[0])
            self.assertGreater(slider.winfo_width(), size[2])
            committed = win.winfo_width(), win.winfo_height(), slider.get()
            until = time.monotonic()+0.15
            while time.monotonic() < until:
                root.update()
            self.assertEqual((win.winfo_width(), win.winfo_height(), slider.get()), committed)
        finally:
            root.destroy()

    def test_inline_whitelist_and_multiline_list_settings_work_with_real_widgets(self):
        import hashlib
        import json
        from pathlib import Path
        import tempfile
        import tkinter as tk
        from aipet_token_guard import detect, report_markdown
        from aipetlib.addins import AddinManager
        from aipetlib.ui import addin_settings
        from aipetlib.ui.addin_messages import show
        root = tk.Tk()
        root.withdraw()
        with tempfile.TemporaryDirectory() as folder:
            manager = AddinManager(folder)
            try:
                value = 'testuser:testpassword'
                report, = detect(value)
                report['markdown_template'] = report_markdown(report)
                row = dict(addin_id='token-guard', name='Token guard', level='warning', messages=[
                    dict(id='fixture', title='Test finding', level='warning', report=report)])
                def whitelist(row, value, done):
                    manager.request_append_setting('token-guard', 'whitelist',
                        'sha256:'+hashlib.sha256(value.encode()).hexdigest(), done)
                win = show(root, [row], screen=dict(work_area=[0, 0, 1280, 800]), on_whitelist=whitelist)
                root.update()
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)
                button = next(widget for widget in descendants(win)
                              if widget.winfo_class() == 'Button' and widget.cget('text') == 'Whitelist [+]')
                button.invoke()
                manager.apply_settings_requests()
                root.update()
                self.assertEqual(button.cget('text'), 'Whitelisted')
                settings = {prop['key']: prop['value'] for prop in manager.get_settings('token-guard')}
                self.assertEqual(detect(value, settings), [])
                saved = Path(folder, 'addin-settings', 'token-guard.json').read_text()
                self.assertNotIn(value, saved)
                editor_win = tk.Toplevel(root)
                def save(values, done):
                    manager.set_settings('token-guard', values)
                    done(None)
                editor = addin_settings.build(editor_win, manager.get_settings('token-guard'), save)
                editor.pack(fill='both', expand=True)
                root.update()
                lists = [widget for widget in descendants(editor_win) if widget.winfo_class() == 'Text']
                self.assertEqual(len(lists), 2)
                lists[1].insert('end', 'rule:hash.hex\nfixture!')
                next(widget for widget in descendants(editor_win)
                     if widget.winfo_class() == 'Button' and widget.cget('text') == 'Save').invoke()
                settings = {prop['key']: prop['value'] for prop in manager.get_settings('token-guard')}
                self.assertEqual(settings['blacklist'], ['rule:hash.hex', 'fixture!'])
            finally:
                manager.stop()
                root.destroy()

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
