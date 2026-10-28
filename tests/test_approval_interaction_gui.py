"""Opt-in desktop reproduction of typing into the timed blacklist editor."""
import faulthandler
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import support


@unittest.skipUnless(os.environ.get('AIPET_NATIVE_WINDOW_TEST') == '1', 'requires an opt-in desktop')
class ApprovalInteractionGuiTests(unittest.TestCase):
    def test_blacklist_editing_and_switch_keep_ui_heartbeat_and_cancel_alive(self):
        self.run_editor_scenario()

    def test_invalid_blacklist_can_be_corrected_and_saved_without_reopening(self):
        self.run_editor_scenario(recover_invalid=True)

    def run_editor_scenario(self, recover_invalid=False):
        import tkinter as tk
        from aipetlib.app.answers import Answers
        from aipetlib.ui import theme
        from aipetlib.ui.permission_card import PermissionCard
        from aipetlib import permissions
        faulthandler.dump_traceback_later(7, repeat=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        root = tk.Tk()
        root.title('AIPet isolated editor QA')
        root.geometry('850x650+50+50')
        root.withdraw()  # only the real permission card should participate in native focus
        self.addCleanup(root.destroy)
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(support.paths(HOME_DIR=temporary, AUTO_APPROVE_PATH=os.path.join(temporary, 'auto-approve.json')))
        app = SimpleNamespace(root=root, cfg={'timed_approve_minutes': 15, 'codex_answers': True},
                              details={}, preferences=MagicMock())
        answers = Answers(app)
        answers.send = MagicMock(return_value=True)  # never answer a live hook from this isolated fixture
        app.answers = answers
        item = dict(key='isolated-qa', session_id='isolated-qa', agent='codex', title='Isolated blacklist editing QA',
                    state='needs_input', changed=time.time(), request=dict(id='isolated-request', kind='permission',
                        tool='Bash', subject='Write-Output QA', detail='Write-Output QA', source='hook'))
        app._last_items = [item]
        self.enterContext(patch.object(permissions, 'hook_waiting', return_value=True))
        key = permissions.auto_approve_key_for(item)
        permissions.save_auto_approve(key, dict(permissions.default_auto_rules(), whitelist=[r'git add .*']))
        card = PermissionCard(app, item['key'], item)
        app.details[item['key']] = card
        self.addCleanup(card.close)
        errors, result, heartbeats, edited = [], [], [], []
        wait_window = self.enterContext(patch.object(root, 'wait_window', wraps=root.wait_window))
        original_start = answers.start_timed
        def tracked_start(*args, **kwargs):
            handler = kwargs['on_result']
            def completed(accepted):
                result.append(accepted)
                handler(accepted)
                root.after_idle(root.quit)
            kwargs['on_result'] = completed
            return original_start(*args, **kwargs)
        answers.start_timed = tracked_start
        def error(kind, value, tb):
            errors.append(value)
            root.quit()
        root.report_callback_exception = error
        def heartbeat():
            heartbeats.append(time.monotonic())
            root.after(20, heartbeat)
        def open_dialog():
            # Exercise the exact link callback; synthetic press focus ordering varies by desktop.
            card.auto_approve_for_a_while()
            print('Permission-card click returned without a nested window wait', flush=True)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        def edit():
            dialog = next(win for win in theme.OPEN_DIALOGS if win.winfo_exists())
            boxes = [widget for widget in descendants(dialog) if isinstance(widget, tk.Text)]
            self.assertEqual(len(boxes), 2)
            black = boxes[1]
            black.focus_force()
            black.event_generate('<ButtonPress-1>', x=12, y=12)
            black.event_generate('<ButtonRelease-1>', x=12, y=12)
            def type_text():
                from aipet_token_guard import detect
                scanner = threading.Thread(target=lambda: detect('Z' * 262144), daemon=True)
                scanner.start()
                black.delete('1.0', 'end')
                if recover_invalid:
                    black.insert('1.0', '*.')
                    button = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Button)
                                  and widget.cget('text') == 'Pause on blacklist')
                    if os.name == 'nt':
                        self.assertEqual(button.cget('background'), '#ffedd5')
                    button.focus_force()
                    button.invoke()
                    def correct():
                        self.assertTrue(dialog.winfo_exists())
                        self.assertEqual(str(black.cget('state')), 'normal')
                        self.assertIs(root.focus_get(), black)
                        black.delete('1.0', 'end')
                        for key in ('period', 'asterisk'):
                            black.event_generate('<KeyPress>', keysym=key)
                            black.event_generate('<KeyRelease>', keysym=key)
                        edited.append(black.get('1.0', 'end-1c'))
                        self.assertEqual(edited[-1], '.*')
                        root.after(100, button.invoke)
                    root.after(100, correct)
                else:
                    black.insert('1.0', 'danger')
                    black.event_generate('<Return>')
                    black.insert('end', 'rm .*')
                    edited.append(black.get('1.0', 'end-1c'))
                print('Blacklist edited successfully', flush=True)
                print('Background maximum-size Token guard scan started', flush=True)
                if not recover_invalid:
                    root.after(250, lambda: dialog.event_generate('<Escape>'))
            root.after(100, type_text)
        heartbeat()
        root.after(50, open_dialog)
        root.after(250, edit)
        root.after(5000, root.quit)
        try:
            root.mainloop()
        finally:
            faulthandler.cancel_dump_traceback_later()
            for job in root.tk.splitlist(root.tk.call('after', 'info')):
                root.after_cancel(job)
        if errors:
            raise errors[0]
        self.assertEqual(result, [recover_invalid])
        wait_window.assert_not_called()
        self.assertEqual(edited, ['.*'] if recover_invalid else ['danger\nrm .*'])
        self.assertGreater(len(heartbeats), 10)
        self.assertLess(max(b-a for a, b in zip(heartbeats, heartbeats[1:])), 1.0)
        if recover_invalid:
            self.assertFalse(card.win.winfo_exists(), 'Pause must dismiss the underlying permission card too')
            self.assertTrue(permissions.timed_grants()['isolated-qa']['respect_blacklist'])
            self.assertEqual(permissions.auto_approve_rules()[key]['blacklist'], ['.*'])
        else:
            self.assertEqual(permissions.timed_grants(), {})


if __name__ == '__main__':
    unittest.main()
