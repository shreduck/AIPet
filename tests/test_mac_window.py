"""Native regression checks: AIPET_NATIVE_WINDOW_TEST=1 python -m unittest discover -s tests.

Requires a logged-in macOS desktop. The regular suite does not open windows.
"""
import os
import sys
import unittest


@unittest.skipUnless(sys.platform == "darwin" and os.environ.get("AIPET_NATIVE_WINDOW_TEST") == "1",
                     "requires an opt-in macOS GUI session")
class MacWindowTests(unittest.TestCase):
    def test_permission_panel_preserves_focus_and_delivers_button_clicks(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        import aipet
        import mac_statusbar as mac

        root = aipet.MacPetWindow()
        root.title("AIPet permission regression")
        root.update()
        native_app = mac.send(mac.cls("NSApplication"), "sharedApplication")
        key_before = mac.send(native_app, "keyWindow")
        item = {"key": "test-only", "agent": "codex", "title": "Test permission", "state": "needs_input",
                "request": {"id": "test-only", "tool": "Test", "detail": "No command is executed"}}
        app = SimpleNamespace(root=root, cfg={"all_spaces": True}, details={}, _last_items=[item],
                              answers_enabled=lambda _: True, send_answer=MagicMock(return_value=False))
        detail = None
        try:
            with patch.object(aipet, "load_sprites", return_value=None), patch.object(aipet, "hook_waiting", return_value=True):
                detail = aipet.Detail(app, item["key"], item)
                root.update()
                window = mac.window_titled(detail.win.title())
                self.assertTrue(mac.send(window, "isKindOfClass:", mac.cls("NSPanel"),
                                         restype=mac.c_bool, argtypes=[mac.c_void_p]))
                self.assertTrue(mac.send(window, "styleMask", restype=mac.c_ulong) & (1 << 7))
                self.assertEqual(mac.send(native_app, "keyWindow"), key_before)
                flags = mac.send(window, "collectionBehavior", restype=mac.c_ulong)
                self.assertTrue(flags & 1)
                self.assertTrue(flags & (1 << 8))
                self.assertFalse(flags & mac.ALL_SPACES_CONFLICTS)
                for button, answer in ((detail.btn_deny, "deny"), (detail.btn_allow, "allow")):
                    x = button.winfo_rootx() + button.winfo_width() // 2
                    y = button.winfo_rooty() + button.winfo_height() // 2
                    detail._mouse_bridge._dispatch(1, 1, x, y, 0)
                    detail._mouse_bridge._dispatch(2, 1, x, y, 0)
                    root.update()
                    app.send_answer.assert_called_with("test-only", answer)
        finally:
            if detail:
                detail.close()
                self.assertTrue(detail._mouse_bridge._closed)
            root.destroy()

    def test_hover_tooltips_are_passive_panels_on_the_pets_spaces(self):
        import tkinter as tk
        from types import SimpleNamespace
        import aipet
        import mac_statusbar as mac

        root = aipet.MacPetWindow()
        root.title("AIPet hover regression")
        root.geometry("100x100+400+600")
        canvas = tk.Canvas(root, width=100, height=100)
        canvas.pack()
        root.update()
        app = object.__new__(aipet.PetApp)
        app.root, app.tip, app._menu_open = root, None, False
        app.cfg = {"all_spaces": True}
        pet = SimpleNamespace(canvas=canvas, gutter=0)
        native_app = mac.send(mac.cls("NSApplication"), "sharedApplication")
        key_before = mac.send(native_app, "keyWindow")
        try:
            for detail in (None, {"agent": "codex"}, {"agent": "claude"}, None):
                app._show_tooltip(pet, "Hover details\nTest content", usage_detail=detail)
                root.update()
                self.assertLessEqual(app.tip.winfo_rooty() + app.tip.winfo_height(), canvas.winfo_rooty() - 6)
                window = mac.window_titled(app.tip.title())
                self.assertTrue(window)
                parent = mac.window_titled(root.title())
                self.assertGreater(mac.send(window, "level", restype=mac.c_long),
                                   mac.send(parent, "level", restype=mac.c_long))
                self.assertTrue(mac.send(window, "isKindOfClass:", mac.cls("NSPanel"),
                                         restype=mac.c_bool, argtypes=[mac.c_void_p]))
                self.assertTrue(mac.send(window, "styleMask", restype=mac.c_ulong) & (1 << 7))
                self.assertTrue(mac.send(window, "ignoresMouseEvents", restype=mac.c_bool))
                self.assertFalse(mac.send(window, "isKeyWindow", restype=mac.c_bool))
                self.assertEqual(mac.send(native_app, "keyWindow"), key_before)
                flags = mac.send(window, "collectionBehavior", restype=mac.c_ulong)
                self.assertTrue(flags & 1)
                self.assertTrue(flags & (1 << 8))
                self.assertFalse(flags & mac.ALL_SPACES_CONFLICTS)
                if mac.supports_all_applications():
                    self.assertTrue(flags & mac.ALL_APPLICATIONS)
                app.hide_tip()
                self.assertTrue(root.winfo_exists())
        finally:
            root.destroy()

    def test_panel_mouse_bridge_delivers_clicks_and_drag_outside_canvas(self):
        import tkinter as tk
        import aipet
        import mac_statusbar as mac

        root = aipet.MacPetWindow()
        root.title("AIPet mouse bridge regression")
        canvas = tk.Canvas(root, width=100, height=80)
        canvas.create_rectangle(10, 10, 60, 60, fill="blue", tags=("badge",))
        canvas.pack()
        root.update()
        bridge = mac.PanelMouseBridge(root)
        seen = []
        badge_clicks = []
        canvas.tag_bind("badge", "<ButtonPress-1>", lambda event: badge_clicks.append(True))
        for sequence in ("<ButtonPress-1>", "<ButtonRelease-1>", "<B1-Motion>", "<ButtonPress-2>",
                         "<Control-ButtonPress-1>"):
            canvas.bind(sequence, lambda event, seq=sequence: seen.append(seq))
        x, y = canvas.winfo_rootx() + 30, canvas.winfo_rooty() + 30
        try:
            bridge._dispatch(3, 2, x, y, 0)
            bridge._dispatch(4, 2, x, y, 0)
            bridge._dispatch(1, 1, x, y, 4)
            bridge._dispatch(2, 1, x, y, 4)
            bridge._dispatch(1, 1, x, y, 0)
            bridge._dispatch(6, 1, x + 200, y, 0)
            bridge._dispatch(2, 1, x + 200, y, 0)
            self.assertEqual(seen, ["<ButtonPress-2>", "<Control-ButtonPress-1>", "<ButtonRelease-1>",
                                    "<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"])
            self.assertTrue(badge_clicks)  # Canvas item bindings still receive normal clicks.
        finally:
            bridge.close()
            root.destroy()

    def test_warning_stays_above_its_settings_window(self):
        import tkinter as tk
        import aipet
        import mac_statusbar as mac

        root = aipet.MacPetWindow()
        parent = tk.Toplevel(root)
        parent.title("AIPet test settings")
        parent.attributes("-topmost", True)
        parent.update()
        failures = []

        def inspect_and_cancel():
            dialog = next(w for w in parent.winfo_children() if isinstance(w, tk.Toplevel))
            try:
                parent.lift()
                parent.update_idletasks()
                native_parent = mac.window_titled(parent.title())
                native_dialog = mac.window_titled(dialog.title())
                self.assertEqual(mac.send(native_dialog, "parentWindow"), native_parent)
                app = mac.send(mac.cls("NSApplication"), "sharedApplication")
                windows = mac.send(app, "orderedWindows")
                order = [mac.send(windows, "objectAtIndex:", i, argtypes=[mac.c_ulong])
                         for i in range(mac.send(windows, "count", restype=mac.c_ulong))]
                dialog_level = mac.send(native_dialog, "level", restype=mac.c_long)
                parent_level = mac.send(native_parent, "level", restype=mac.c_long)
                # AppKit's orderedWindows can list a raised owner first even
                # when its child is drawn in a higher WindowServer layer.
                self.assertGreater((dialog_level, -order.index(native_dialog)),
                                   (parent_level, -order.index(native_parent)))
                for kind in ("primary", "danger", "secondary"):
                    b = tk.Button(parent, **aipet.button_style(kind))
                    self.assertEqual(b.winfo_rgb(b.cget("fg")), b.winfo_rgb("systemButtonText"))
                    b.destroy()
            except Exception as error:
                failures.append(error)
            finally:
                # Return must still cancel this warning; never approve a rule.
                dialog.event_generate("<Return>")

        try:
            parent.after(200, inspect_and_cancel)
            result = aipet.themed_dialog(parent, "AIPet test warning", "Test only; no settings are changed.",
                                         kind="warning", buttons=(("Cancel", False, "secondary"),
                                                                  ("Auto approve", True, "danger")),
                                         cancel=False, enter_confirms=False)
            self.assertFalse(result)
            if failures:
                raise failures[0]
        finally:
            root.destroy()

    def test_panel_survives_resize_remap_and_sharing_toggle(self):
        import aipet
        import mac_statusbar as mac

        root = aipet.MacPetWindow()
        owner = root._owner
        try:
            root.title("AIPet native regression test")
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.geometry("80x60+50+50")
            root.update()
            for on, remap in ((True, False), (False, False), (True, True)):
                if remap:
                    root.withdraw()
                    root.update()
                    root.deiconify()
                    root.geometry("100x70+50+50")
                    root.update()
                self.assertTrue(mac.set_all_spaces(root.title(), on))
                root.update()
                w = mac.window_titled(root.title())
                self.assertTrue(mac.send(w, "isKindOfClass:", mac.cls("NSPanel"),
                                         restype=mac.c_bool, argtypes=[mac.c_void_p]))
                self.assertTrue(mac.send(w, "styleMask", restype=mac.c_ulong) & (1 << 7))
                self.assertFalse(mac.send(w, "hidesOnDeactivate", restype=mac.c_bool))
                self.assertGreater(mac.send(w, "level", restype=mac.c_long), 0)
                flags = mac.send(w, "collectionBehavior", restype=mac.c_ulong)
                self.assertEqual(bool(flags & 1), on)
                self.assertEqual(bool(flags & (1 << 8)), on)
                self.assertEqual(bool(flags & mac.ALL_APPLICATIONS), on and mac.supports_all_applications())
                if on:
                    self.assertFalse(flags & mac.ALL_SPACES_CONFLICTS)
        finally:
            root.destroy()
        # Destroying the visible panel must also terminate the hidden root and
        # mainloop, or Quit leaves a windowless AIPet process behind.
        self.assertEqual(owner.children, {})
        owner.mainloop()
