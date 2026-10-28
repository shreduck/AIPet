"""Opt-in real Tk approval editor regressions; all permission I/O is mocked."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


@unittest.skipUnless(os.environ.get('AIPET_NATIVE_WINDOW_TEST') == '1', 'requires an opt-in desktop')
class ApprovalEditorGuiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from aipetlib.app import approvals
        from aipetlib.ui import theme
        self.tk, self.module, self.theme = tk, approvals, theme
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        import tempfile
        import support
        directory = tempfile.TemporaryDirectory(prefix="aipet-editor-test-")
        self.addCleanup(directory.cleanup)
        isolated = support.paths(HOME_DIR=directory.name,
                                 AUTO_APPROVE_PATH=os.path.join(directory.name, "auto-approve.json"))
        isolated.start()
        self.addCleanup(isolated.stop)
        self.rules = {}
        self.save = Mock(return_value=True)
        for target, name, value in ((approvals.permissions, 'auto_approve_rules', lambda: self.rules),
                                    (approvals.permissions, 'auto_approve_targets', lambda: []),
                                    (approvals.permissions, 'save_auto_approve', self.save),
                                    (approvals.diagnostics, 'log_error', Mock())):
            stub = patch.object(target, name, value)
            stub.start()
            self.addCleanup(stub.stop)
        app = SimpleNamespace(root=self.root, distros=[], codex_targets=[], status={},
                              refresh_menu=Mock(), presentation=SimpleNamespace(invalidate=Mock()))
        self.controller = approvals.Approvals(app)
        self.controller.open_editor('codex:isolated-qa', 'Isolated approval editor QA')
        self.win = self.controller.editors['codex:isolated-qa']
        self.root.update()

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def button(self, window, text):
        return next(w for w in self.descendants(window)
                    if isinstance(w, self.tk.Button) and w.cget('text') == text)

    def click(self, widget):
        top = widget.winfo_toplevel()
        if sys.platform == 'darwin':
            bridge = top._mouse_bridge
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() + widget.winfo_height() // 2
            bridge._dispatch(1, 1, x, y, 0)
            bridge._dispatch(2, 1, x, y, 0)
        else:
            widget.event_generate('<Enter>')
            widget.event_generate('<ButtonPress-1>', x=4, y=4)
            widget.event_generate('<ButtonRelease-1>', x=4, y=4)
        self.root.update()

    def warning(self):
        return next(w for w in self.win.winfo_children() if isinstance(w, self.tk.Toplevel))

    def texts(self):
        return [str(w.cget('text')) for w in self.descendants(self.win) if isinstance(w, self.tk.Label)]

    def test_cancel_warning_then_save_again_and_confirm(self):
        next(w for w in self.descendants(self.win) if isinstance(w, self.tk.Checkbutton)).invoke()
        save = self.button(self.win, 'Save')
        self.click(save)
        first_warning = self.warning()
        self.assertIsNone(self.root.grab_current())  # independent alerts/editors must remain interactive
        self.click(self.button(first_warning, 'Cancel'))
        self.assertIsNone(self.root.grab_current())
        self.assertEqual(save.cget('state'), 'normal')
        self.save.assert_not_called()
        # Save is still the editor bridge's hovered widget, while the other
        # bridge has left Tk's shared button Enter state on its destroyed Cancel.
        # A second click must refresh that shared state and invoke Save again.
        self.click(save)
        second_warning = self.warning()
        self.assertIsNot(first_warning, second_warning)
        self.click(self.button(second_warning, 'Auto approve'))
        self.assertNotIn('codex:isolated-qa', self.controller.editors)
        self.assertIsNone(self.root.grab_current())
        self.save.assert_called_once()
        self.assertTrue(self.save.call_args.args[1]['enabled'])

    def test_save_permission_failure_retains_draft_and_folder_retry(self):
        text = next(w for w in self.descendants(self.win) if isinstance(w, self.tk.Text))
        text.delete('1.0', 'end')
        text.insert('1.0', 'git status')
        self.save.side_effect = PermissionError('isolated QA permission denied')
        self.click(self.button(self.win, 'Save'))
        self.assertEqual(text.get('1.0', 'end-1c'), 'git status')
        self.assertEqual(self.button(self.win, 'Save').cget('state'), 'normal')
        self.assertTrue(any('Your edits are still here' in text for text in self.texts()))
        import tempfile
        with tempfile.TemporaryDirectory(prefix='aipet-approval-editor-qa-') as directory:
            with patch('tkinter.filedialog.askdirectory', return_value=directory):
                self.click(self.button(self.win, 'Choose rules folder…'))
            self.assertTrue(any(directory in text for text in self.texts()))
            self.save.side_effect = None
            self.click(self.button(self.win, 'Save'))
            self.assertNotIn('codex:isolated-qa', self.controller.editors)
            self.assertEqual(self.save.call_args.kwargs, {'directory': directory})
            self.assertEqual(self.save.call_args.args[1]['whitelist'], ['git status'])

    def test_failed_warning_restores_save_and_cancel(self):
        next(w for w in self.descendants(self.win) if isinstance(w, self.tk.Checkbutton)).invoke()
        with patch.object(self.controller, 'warn', side_effect=RuntimeError('isolated QA warning failure')):
            self.click(self.button(self.win, 'Save'))
        self.assertEqual(self.button(self.win, 'Save').cget('state'), 'normal')
        self.assertTrue(any('Your edits are still here' in text for text in self.texts()))
        self.click(self.button(self.win, 'Cancel'))
        self.assertNotIn('codex:isolated-qa', self.controller.editors)
        self.save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
