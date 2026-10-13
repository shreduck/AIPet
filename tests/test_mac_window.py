"""Native regression checks: AIPET_NATIVE_WINDOW_TEST=1 python -m unittest discover -s tests.

Requires a logged-in macOS desktop. The regular suite does not open windows.
"""
import os
import sys
import unittest

import support


@unittest.skipUnless(sys.platform == "darwin" and os.environ.get("AIPET_NATIVE_WINDOW_TEST") == "1",
                     "requires an opt-in macOS GUI session")
class MacWindowTests(unittest.TestCase):
    def test_expanded_log_header_body_and_outline_follow_scroll_and_resize(self):
        import json
        from types import SimpleNamespace
        from unittest.mock import patch
        import aipet
        import aipetlib.agents.log_filters as agent_log_filters
        from aipet_agents_window import AgentNetworkWindow

        root = aipet.MacPetWindow()
        root.withdraw()
        window = None
        text = json.dumps({"command": "echo first\necho second", "output": "first\nsecond"}, indent=2)
        rows = [(1, 0, "input", text)] + [(i + 2, 0, "say", "other entry") for i in range(40)]
        try:
            window = AgentNetworkWindow(root, SimpleNamespace(cfg={}, _last_items=[]))
            window.net = {"agents": [{"name": "exec_command", "state": "working"}], "log": []}
            window.opts["log_tail"] = False
            with patch.object(agent_log_filters, "filtered_log_rows", return_value=rows):
                root.update()
                window._draw_log()
                window._entry_click(0)
                for width in (640, 1000):
                    window.win.geometry(f"{width}x800")
                    window.log.configure(height=16)
                    window.log.yview_moveto(0)
                    root.update()
                    expanded = window.log.get("entry0.first", "entry0.last")
                    self.assertIn("exec_command · Input  ‹\ncommand:\n", expanded)
                    self.assertIn("  echo first\n  echo second", expanded)
                    self.assertNotIn("\\n", expanded)
                    self.assertEqual(window._entries[0][1], text)  # original copy / popup content is preserved
                    edges = window._log_borders["entry0"]
                    self.assertTrue(all(edge.winfo_ismapped() for edge in edges))
                    self.assertEqual(edges[2].winfo_width(), window.log.winfo_width())
                    for edge in edges:
                        self.assertLessEqual(edge.winfo_x() + edge.winfo_width(), window.log.winfo_width())
                        self.assertLessEqual(edge.winfo_y() + edge.winfo_height(), window.log.winfo_height())
                    self.assertTrue(all(edge.cget("bg") == "#244c80" for edge in edges))
                    window.log.tag_add("sel", "entry0.first", "entry0.last")
                    self.assertEqual(window.log.get("sel.first", "sel.last"), expanded)
                    window.log.tag_remove("sel", "1.0", "end")
                    window.log.yview_moveto(1)
                    root.update()
                    self.assertFalse(any(edge.winfo_ismapped() for edge in edges))
                window.log.yview_moveto(0)
                window._entry_click(0)
                root.update()
                self.assertFalse(window._log_borders)
        finally:
            if window:
                window.close()
            root.destroy()

    def test_collapsed_logs_fit_one_display_line_and_expand_without_losing_text(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import aipet
        import aipetlib.agents.log_filters as agent_log_filters
        from aipet_agents_window import AgentNetworkWindow

        root = aipet.MacPetWindow()
        root.withdraw()
        window = None
        texts = ["A short preview that still needs folding in a narrow pane. " * 3,
                 "First line\nSecond line\nThird line", "工具 output\t" * 60]
        rows = [(1, 0, "output", text) for text in texts]
        try:
            window = AgentNetworkWindow(root, SimpleNamespace(cfg={}, _last_items=[]))
            window.net = {"agents": [{"name": "exec_command", "state": "working"}], "log": []}
            window.opts["log_tail"] = False
            with patch.object(agent_log_filters, "filtered_log_rows", return_value=rows):
                for text_scale in (100, 150):
                    window.opts["text_scale"] = text_scale
                    window.f_main.configure(size=round(12 * text_scale / 100))
                    window.f_bold.configure(size=round(12 * text_scale / 100))
                    for width in (640, 1100):
                        window.win.geometry(f"{width}x800")
                        root.update()
                        window._draw_log()
                        root.update()
                        for i, original in enumerate(texts):
                            tag = f"entry{i}"
                            self.assertEqual(window.log.count(f"{tag}.first", f"{tag}.last", "displaylines"), (1,))
                            if window._entries[i][3]:
                                window.log.see(f"{tag}.last-2c")
                                root.update()
                                marker = window.log.bbox(f"{tag}.last-2c")
                                self.assertIsNotNone(marker)
                                self.assertLessEqual(marker[0] + marker[2], window.log.winfo_width())
                                window._entry_click(i)
                                root.update()
                                self.assertIn(original, window.log.get(f"{tag}.first", f"{tag}.last"))
                                self.assertGreater(window.log.count(f"{tag}.first", f"{tag}.last", "displaylines")[0], 1)
                                window._entry_click(i)
                                root.update()
                                self.assertEqual(window.log.count(f"{tag}.first", f"{tag}.last", "displaylines"), (1,))
        finally:
            if window:
                window.close()
            root.destroy()

    def test_text_slider_is_independent_persists_and_resets_to_100(self):
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        import aipet
        from aipet_agents_window import AgentNetworkWindow, DEFAULT_OPTIONS

        root = aipet.MacPetWindow()
        root.withdraw()
        window = None
        try:
            with tempfile.TemporaryDirectory(prefix="aipet-text-scale-") as folder:
                config = Path(folder) / "config.json"
                with support.paths(HOME_DIR=folder), support.paths(CONFIG_PATH=str(config)):
                    window = AgentNetworkWindow(root, SimpleNamespace(cfg={}, _last_items=[]))
                    window.toggle_menu(True)
                    root.update()
                    self.assertEqual((window.scale_slider.value, window.text_scale_slider.value), (100, 100))
                    normal_size = window.f_main.cget("size")
                    normal_drawing = window.px(100)
                    window.text_scale_slider.set(150)
                    window.text_scale_slider._commit()
                    root.update()
                    self.assertEqual(window.opts["scale"], 100)
                    self.assertEqual(window.px(100), normal_drawing)
                    self.assertGreater(window.f_main.cget("size"), normal_size)
                    window._graph_fonts()
                    self.assertEqual(window.g_main.cget("size"), window.f_main.cget("size"))
                    window.win.geometry("640x480")
                    root.update()
                    self.assertEqual((window.opts["scale"], window.opts["text_scale"]), (100, 150))
                    window.close()  # immediate close must still save the pending change
                    saved = json.loads(config.read_text())
                    self.assertEqual(saved["agent_window"]["text_scale"], 150)
                    window = AgentNetworkWindow(root, SimpleNamespace(cfg=saved, _last_items=[]))
                    self.assertGreater(window.f_main.cget("size"), normal_size)
                    window.set_options(dict(window.opts, text_scale=125))
                    self.assertEqual(window.f_main.cget("size"), round(normal_size * 1.25))
                    window.reset_appearance(dict(DEFAULT_OPTIONS))
                    root.update()
                    self.assertEqual((window.opts["scale"], window.opts["text_scale"]), (100, 100))
                    self.assertEqual(window.f_main.cget("size"), normal_size)
                    window.close()
                    window = None
        finally:
            if window:
                window.close()
            root.destroy()

    def test_log_filter_toolbar_aligns_and_wraps_without_overlapping_logs(self):
        from types import SimpleNamespace
        import aipet
        from aipet_agents_window import AgentNetworkWindow

        root = aipet.MacPetWindow()
        root.withdraw()
        window = None
        try:
            for scale in (100, 150):
                pet = SimpleNamespace(cfg={"agent_window": {"scale": scale}}, _last_items=[])
                window = AgentNetworkWindow(root, pet)
                for side in ("bottom", "right", "top", "left"):
                    window.opts.update(log_side=side, log_cols=20)
                    window._arrange()
                    root.update()
                    bar = window.filterbar
                    boxes = [(b.winfo_x(), b.winfo_y(), b.winfo_width(), b.winfo_height())
                             for b in window._filter_buttons]
                    self.assertEqual(len({b[3] for b in boxes}), 1)
                    for x, y, width, height in boxes:
                        self.assertGreaterEqual(x, 0)
                        self.assertLessEqual(x + width, bar.winfo_width())
                        self.assertLessEqual(y + height, bar.winfo_height())
                    for first, second in zip(boxes, boxes[1:]):
                        self.assertTrue(second[1] >= first[1] + first[3] or
                                        (second[1] == first[1] and second[0] >= first[0] + first[2]))
                    self.assertGreaterEqual(bar.winfo_y(), window.tabs.winfo_y() + window.tabs.winfo_height())
                    self.assertGreaterEqual(window.log.winfo_y(), bar.winfo_y() + bar.winfo_height())
                    if side in ("left", "right"):
                        self.assertGreater(len({b[1] for b in boxes}), 1)
                window.show_tools_btn.event_generate("<Button-1>")
                root.update()
                self.assertFalse(window._log_filters["show_tools"])
                window.clear_filters_btn.event_generate("<Button-1>")
                root.update()
                self.assertTrue(window._log_filters["show_tools"])
                window.close()
                window = None
        finally:
            if window:
                window.close()
            root.destroy()

    def test_agent_network_opens_with_and_without_sessions(self):
        from types import SimpleNamespace
        import aipet
        from aipet_agents_window import AgentNetworkWindow

        root = aipet.MacPetWindow()
        root.withdraw()
        callbacks = []
        root.report_callback_exception = lambda *error: callbacks.append(error)
        try:
            for items in ([], [{"key": "test-network", "source": "CC", "session_id": "test-network",
                               "agent": "claude", "title": "Isolated network test", "state": "working"}]):
                with self.subTest(sessions=len(items)):
                    window = AgentNetworkWindow(root, SimpleNamespace(cfg={}, _last_items=items))
                    try:
                        root.update()
                        self.assertTrue(window.win.winfo_viewable())
                        self.assertEqual(window.opts["scale"], 100)
                        self.assertEqual(bool(window.net["agents"]), bool(items))
                        window.toggle_menu(True)
                        root.update()
                        self.assertTrue(window.menu.winfo_viewable())
                        self.assertEqual(callbacks, [])
                    finally:
                        window.close()
        finally:
            root.destroy()

    def test_resize_preserves_ui_scale_and_text_size(self):
        from types import SimpleNamespace
        import aipet
        from aipet_agents_window import AgentNetworkWindow

        root = aipet.MacPetWindow()
        root.withdraw()
        pet = SimpleNamespace(cfg={}, _last_items=[])
        window = None
        try:
            window = AgentNetworkWindow(root, pet)
            root.update()
            normal = window.f_main.metrics("linespace")
            for size in ("640x480", "1200x800"):
                window.win.geometry(size)
                root.update()
                self.assertEqual(window.opts["scale"], 100)
                self.assertEqual(window.f_main.metrics("linespace"), normal)
        finally:
            if window:
                window.close()
            root.destroy()

    def test_auto_rules_persist_and_reopen_after_confirmation(self):
        import json
        import tempfile
        import tkinter as tk
        from pathlib import Path
        from unittest.mock import MagicMock, patch
        import aipet
        import aipet_app

        root = aipet.MacPetWindow()
        root.title("AIPet isolated persistence regression")
        key = "codex:test-persistence"

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def click(window, label):
            button = next(w for w in descendants(window) if isinstance(w, tk.Button) and w.cget("text") == label)
            x = button.winfo_rootx() + button.winfo_width() // 2
            y = button.winfo_rooty() + button.winfo_height() // 2
            window._mouse_bridge._dispatch(1, 1, x, y, 0)
            window._mouse_bridge._dispatch(2, 1, x, y, 0)

        def open_settings():
            app = object.__new__(aipet_app.TrayApp)
            app.root, app._auto_changed = root, MagicMock()
            app.open_auto_rules(key, "Isolated persistence test")
            root.update()
            return app, app.auto_wins[key]

        failures = []
        try:
            with tempfile.TemporaryDirectory(prefix="aipet-rules-test-") as directory:
                path = Path(directory) / "auto-approve.json"
                # Exercise real reads and atomic writes, without touching user rules.
                with support.paths(HOME_DIR=directory), support.paths(AUTO_APPROVE_PATH=str(path)):
                    app, settings = open_settings()
                    checks = [w for w in descendants(settings) if isinstance(w, tk.Checkbutton)]
                    checks[0].invoke()
                    expected = {"enabled": True, "allow_all": False,
                                "whitelist": ["git (status|diff)", "# retained comment", "Read"],
                                "blacklist": ["rm", "sudo"]}
                    editors = [w for w in descendants(settings) if isinstance(w, tk.Text)]
                    for editor, name in zip(editors, ("whitelist", "blacklist")):
                        editor.delete("1.0", "end")
                        editor.insert("1.0", "\n".join(expected[name]))

                    def confirm_test_warning():
                        try:
                            warning = next(w for w in settings.winfo_children() if isinstance(w, tk.Toplevel))
                            click(warning, "Auto approve")
                        except Exception as error:
                            failures.append(error)

                    settings.after(100, confirm_test_warning)
                    settled = tk.BooleanVar(master=root, value=False)
                    root.after(350, lambda: settled.set(True))
                    click(settings, "Save")
                    root.wait_variable(settled)
                    if failures:
                        raise failures[0]
                    self.assertFalse(settings.winfo_exists())
                    app._auto_changed.assert_called_once()
                    self.assertEqual(json.loads(path.read_text()), {"targets": {key: expected}, "timed": {}})
                    self.assertEqual(aipet.auto_approve_rules()[key], expected)
                    self.assertFalse(Path(str(path) + ".tmp").exists())

                    # A fresh controller must populate the form from the saved file.
                    _, reopened = open_settings()
                    checks = [w for w in descendants(reopened) if isinstance(w, tk.Checkbutton)]
                    self.assertEqual([bool(root.getboolean(root.getvar(w.cget("variable")))) for w in checks],
                                     [True, False])
                    editors = [w for w in descendants(reopened) if isinstance(w, tk.Text)]
                    for editor, name in zip(editors, ("whitelist", "blacklist")):
                        self.assertEqual(editor.get("1.0", "end-1c"), "\n".join(expected[name]))
                    editors[0].insert("end", "\nnot saved")
                    checks[0].invoke()
                    click(reopened, "Cancel")
                    root.update()
                    self.assertFalse(reopened.winfo_exists())
                    self.assertEqual(json.loads(path.read_text()), {"targets": {key: expected}, "timed": {}})
        finally:
            root.destroy()

    def test_auto_rules_save_and_cancel_after_warning(self):
        import tkinter as tk
        from unittest.mock import MagicMock, patch
        import aipet
        import aipet_app

        root = aipet.MacPetWindow()
        root.title("AIPet modal regression")
        root.update()
        app = object.__new__(aipet_app.TrayApp)
        app.root, app._auto_changed = root, MagicMock()

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def button(window, label):
            return next(w for w in descendants(window) if isinstance(w, tk.Button) and w.cget("text") == label)

        def click(widget):
            bridge = widget.winfo_toplevel()._mouse_bridge
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() + widget.winfo_height() // 2
            bridge._dispatch(1, 1, x, y, 0)
            bridge._dispatch(2, 1, x, y, 0)

        try:
            with patch.object(aipet, "auto_approve_rules", return_value={}), \
                    patch.object(aipet, "save_auto_approve", return_value=True) as save:
                for accept, saved in ((False, True), (None, True), (True, False), (True, True)):
                    save.reset_mock()
                    save.return_value = saved
                    app.open_auto_rules("test-only", "Test only (nothing is saved)")
                    settings = app.auto_wins["test-only"]
                    root.update()
                    next(w for w in descendants(settings) if isinstance(w, tk.Checkbutton)).invoke()
                    def dismiss_warning():
                        warning = next(w for w in settings.winfo_children() if isinstance(w, tk.Toplevel))
                        click(button(settings, "Cancel"))
                        self.assertTrue(settings.winfo_exists())  # modal grab blocks its parent
                        if accept is None:
                            warning.destroy()
                        else:
                            click(button(warning, "Auto approve" if accept else "Cancel"))
                    settings.after(100, dismiss_warning)
                    settled = tk.BooleanVar(master=root, value=False)
                    root.after(300, lambda: settled.set(True))
                    with patch.object(settings, "wait_window", side_effect=AssertionError("nested modal wait")):
                        click(button(settings, "Save"))
                        root.wait_variable(settled)
                    self.assertIsNone(root.grab_current())
                    if accept:
                        save.assert_called_once()
                    else:
                        save.assert_not_called()
                    if accept and saved:
                        self.assertFalse(settings.winfo_exists())
                    else:
                        self.assertEqual(str(button(settings, "Save").cget("state")), "normal")
                        click(button(settings, "Cancel"))
                        root.update()
                        self.assertFalse(settings.winfo_exists())
                    self.assertTrue(settings._mouse_bridge._closed)
        finally:
            root.destroy()

    def test_permission_panel_preserves_focus_and_delivers_button_clicks(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        import aipet
        import aipetlib.ui.sprites as aipet_sprites
        import mac_statusbar as mac

        root = aipet.MacPetWindow()
        root.title("AIPet permission regression")
        root.update()
        native_app = mac.send(mac.cls("NSApplication"), "sharedApplication")
        key_before = mac.send(native_app, "keyWindow")
        item = {"key": "test-only", "agent": "codex", "title": "Test permission", "state": "needs_input",
                "request": {"id": "test-only", "tool": "Test", "detail": "No command is executed"}}
        app = SimpleNamespace(root=root, cfg={"all_spaces": True}, details={}, _last_items=[item],
                              timed_minutes=lambda: 5,
                              answers_enabled=lambda _: True, send_answer=MagicMock(return_value=False))
        detail = None
        try:
            with patch.object(aipet_sprites, "load_sprites", return_value=None), patch.object(aipet.permissions, "hook_waiting", return_value=True):
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
            for detail, scale in ((None, 1.0), ({"agent": "codex"}, 1.5), ({"agent": "claude"}, 2.0), (None, 1.0)):
                app.cfg["tooltip_text_scale"] = scale
                app._show_tooltip(pet, "Hover details\nTest content", usage_detail=detail)
                root.update()
                tip_canvas = app.tip.winfo_children()[0]
                text_items = [i for i in tip_canvas.find_all() if tip_canvas.type(i) == "text"]
                for item, base_size in zip(text_items, (9, 8)):
                    from tkinter import font
                    rendered = font.Font(root=root, font=tip_canvas.itemcget(item, "font"))
                    self.assertEqual(rendered.actual("size"), round(base_size * scale))
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

    def test_hover_does_not_activate_app_or_raise_agent_window(self):
        import tkinter as tk
        from types import SimpleNamespace
        import Quartz
        import aipet
        import mac_statusbar as mac
        from aipet_agents_window import AgentNetworkWindow

        workspace = mac.send(mac.cls("NSWorkspace"), "sharedWorkspace")
        previous = mac.send(workspace, "frontmostApplication")
        mac.send(previous, "retain")
        apps = mac.send(mac.cls("NSRunningApplication"), "runningApplicationsWithBundleIdentifier:",
                        mac.nsstr("com.apple.finder"), argtypes=[mac.c_void_p])
        finder = mac.send(apps, "objectAtIndex:", 0, argtypes=[mac.c_ulong])
        mac.send(finder, "retain")
        finder_pid = mac.send(finder, "processIdentifier", restype=mac.c_long)
        root = aipet.MacPetWindow()
        root.title("AIPet hover stacking regression")
        canvas = tk.Canvas(root, width=100, height=100)
        canvas.pack()
        app = object.__new__(aipet.PetApp)
        app.root, app.tip, app._menu_open = root, None, False
        app.cfg = {"all_spaces": True}
        pet = SimpleNamespace(canvas=canvas, gutter=0, data={})
        agent = None

        def settle():
            done = tk.BooleanVar(root, False)
            root.after(200, lambda: done.set(True))
            root.wait_variable(done)

        def foreground_pid():
            return mac.send(mac.send(workspace, "frontmostApplication"), "processIdentifier", restype=mac.c_long)

        def windows_above(number):
            windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly,
                                                       Quartz.kCGNullWindowID)
            ordered = [w["kCGWindowNumber"] for w in windows if w.get("kCGWindowLayer") == 0]
            self.assertIn(number, ordered)
            return set(ordered[:ordered.index(number)])

        try:
            agent = AgentNetworkWindow(root, SimpleNamespace(cfg={}, _last_items=[]))
            root.update()
            settle()
            settle()  # initial option application can replace Tk's native window
            number = mac.send(mac.window_titled(agent.win.title()), "windowNumber", restype=mac.c_long)
            for _ in range(3):
                mac.send(finder, "activateWithOptions:", 3, restype=mac.c_bool, argtypes=[mac.c_ulong])
                settle()
                if foreground_pid() == finder_pid:
                    break
            self.assertEqual(foreground_pid(), finder_pid)
            above = windows_above(number)
            for detail in (None, {"agent": "codex"}, {"agent": "claude"}):
                app._show_tooltip(pet, "Hover details\nTest content", usage_detail=detail)
                settle()
                self.assertEqual(foreground_pid(), finder_pid)
                self.assertTrue(above <= windows_above(number))
                app.hide_tip()
            # Deliberate opening still activates the app after the hover scope.
            agent.raise_()
            settle()
            self.assertEqual(foreground_pid(), os.getpid())
        finally:
            app.hide_tip()
            if agent:
                agent.close()
            root.update()
            root.destroy()
            mac.send(previous, "activateWithOptions:", 3, restype=mac.c_bool, argtypes=[mac.c_ulong])
            mac.send(previous, "release", restype=None)
            mac.send(finder, "release", restype=None)

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
