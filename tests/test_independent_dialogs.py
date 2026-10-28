"""Independent confirmations must never grab or restore another window's input."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aipetlib.ui import theme, window_resize


class IndependentDialogTests(unittest.TestCase):
    def test_failed_validation_is_deferred_and_can_be_corrected_without_reopening(self):
        validate, completed = MagicMock(return_value=False), MagicMock()
        win = theme.themed_dialog(self.root, 'Editor', 'Test validation',
            buttons=(('Cancel', False, 'secondary'), ('Approve', True, 'danger')),
            cancel=False, on_result=completed, validate=validate)
        win.after_idle.side_effect = None  # native button callback must return before validation
        choose = next(button['command'] for button in self.buttons if button['text'] == 'Approve')
        choose()
        validate.assert_not_called()
        win.after_idle.call_args.args[0]()
        validate.assert_called_once_with(True)
        win.destroy.assert_not_called()
        completed.assert_not_called()
        validate.return_value = True
        choose()
        win.after_idle.call_args.args[0]()  # validate the correction; queues close
        win.after_idle.call_args.args[0]()  # close and deliver just this dialog's result
        completed.assert_called_once_with(True)

    def test_token_alert_can_close_while_approval_remains_pending(self):
        from aipetlib.ui import addin_messages
        approval_result, alert_closed = MagicMock(), MagicMock()
        approval = self.open(approval_result)
        rows = [dict(name='Token guard', level='warning', messages=[
            dict(title='Token warning', level='warning', timestamp=1234, content='Masked summary')])]
        with patch.object(addin_messages, 'ScrolledText') as text, patch.object(addin_messages, 'WarningBeacon'), patch.object(addin_messages, 'ToggleSwitch'), \
                patch.object(addin_messages.theme, 'theme_window'), patch.object(addin_messages.tk, 'Checkbutton'), \
                patch.object(addin_messages.tk, 'BooleanVar') as preview:
            preview.return_value.get.return_value = False
            text.return_value.yview.return_value = (0.0, 1.0)
            alert = addin_messages.show(self.root, rows, on_close=alert_closed,
                                       screen=dict(work_area=[0, 0, 1920, 1080]))
            alert.grab_set.assert_not_called()
            approval.grab_set.assert_not_called()
            next(button['command'] for button in self.buttons if button['text'] == 'Dismiss')()
            alert_closed.assert_called_once_with(rows)
            approval_result.assert_not_called()
            approval.destroy.assert_not_called()
            self.assertIn(approval, theme.OPEN_DIALOGS)

    def test_ui_poll_is_scheduled_before_a_queued_confirmation_can_wait(self):
        import queue
        import aipet_app
        app = object.__new__(aipet_app.TrayApp)
        app.root, app.addins = MagicMock(), MagicMock()
        app.addins.entries.return_value = []
        app._addin_catalog_seen = []
        app.pet = SimpleNamespace(muted=MagicMock(), wb=None)
        app.q = queue.Queue()
        app.publish_addin_pet_locations, app.show_addin_alerts = MagicMock(), MagicMock()
        app.agent_windows, app.presentation = MagicMock(), MagicMock()
        def queued_confirmation():
            app.root.after.assert_called_once_with(250, app.pump)
        app.q.put(queued_confirmation)
        app.pump()
        app.root.after.assert_called_once_with(250, app.pump)

    def test_native_input_owner_defers_automatic_alerts_without_marking_them_seen(self):
        import aipet_app
        app = object.__new__(aipet_app.TrayApp)
        app.root = MagicMock()
        app.root.grab_current.return_value = MagicMock()
        app.pet = SimpleNamespace(addin_snapshot=[])
        with patch.object(aipet_app.addin_message_ui, 'show') as show:
            app.show_addin_alerts()
            show.assert_not_called()
        self.assertFalse(hasattr(app, '_addin_alert_seen'))

    def setUp(self):
        self.root = MagicMock()
        self.root.winfo_exists.return_value = True
        self.root.winfo_viewable.return_value = False
        self.root.winfo_x.return_value = self.root.winfo_y.return_value = 0
        self.root.winfo_width.return_value = self.root.winfo_height.return_value = 100
        self.root.after_idle.side_effect = lambda callback: callback()
        self.windows, self.buttons = [], []
        self.enterContext(patch.object(theme, 'IS_MAC', False))
        self.enterContext(patch.object(theme.tk, 'Toplevel', side_effect=self.window))
        self.enterContext(patch.object(theme.tk, 'Frame'))
        self.enterContext(patch.object(theme.tk, 'Label'))
        self.enterContext(patch.object(theme.tk, 'Button', side_effect=self.button))
        self.enterContext(patch.object(window_resize, 'corner_grip'))
        self.enterContext(patch.object(theme.geometry, 'work_area', return_value=(0, 0, 1920, 1080)))

    def window(self, *_):
        win = MagicMock()
        win.winfo_exists.return_value = True
        win.winfo_reqwidth.return_value, win.winfo_reqheight.return_value = 620, 440
        win.after_idle.side_effect = lambda callback: callback()
        callbacks = {}
        win.bind.side_effect = lambda event, callback, **kwargs: callbacks.setdefault(event, []).append(callback)
        def destroy():
            win.winfo_exists.return_value = False
            for callback in callbacks.get('<Destroy>', []):
                callback(SimpleNamespace(widget=win))
        win.destroy.side_effect = destroy
        self.windows.append(win)
        self.addCleanup(lambda: theme.OPEN_DIALOGS.discard(win))
        return win

    def button(self, *args, **kwargs):
        self.buttons.append(kwargs)
        return MagicMock()

    def open(self, callback=None):
        return theme.themed_dialog(self.root, 'Approval', 'Test confirmation',
            buttons=(('Cancel', False, 'secondary'), ('Approve', True, 'danger')),
            cancel=False, enter_confirms=False, on_result=callback)

    def test_two_confirmations_are_independent_and_closing_one_does_not_answer_the_other(self):
        first_result, second_result = MagicMock(), MagicMock()
        first, second = self.open(first_result), self.open(second_result)
        for win in (first, second):
            win.grab_set.assert_not_called()
            win.grab_set_global.assert_not_called()
            win.grab_release.assert_not_called()
        self.root.grab_current.assert_not_called()
        self.root.focus_get.assert_not_called()
        self.root.wait_window.assert_not_called()
        first_approve = next(button['command'] for button in self.buttons[:2] if button['text'] == 'Approve')
        first_approve()
        first_result.assert_called_once_with(True)
        second_result.assert_not_called()
        second.destroy.assert_not_called()
        self.assertNotIn(first, theme.OPEN_DIALOGS)
        self.assertIn(second, theme.OPEN_DIALOGS)
        second_cancel = next(button['command'] for button in self.buttons[2:] if button['text'] == 'Cancel')
        second_cancel()
        second_result.assert_called_once_with(False)

    def test_external_close_cancels_once_and_never_restores_a_stale_grab(self):
        callback = MagicMock()
        win = self.open(callback)
        win.destroy()
        win.destroy()
        callback.assert_called_once_with(False)
        win.grab_release.assert_not_called()
        self.root.grab_current.assert_not_called()

    def test_synchronous_wait_returns_choice_while_leaving_other_windows_interactive(self):
        def choose(win):
            win.grab_set.assert_not_called()
            next(button['command'] for button in self.buttons if button['text'] == 'Approve')()
        self.root.wait_window.side_effect = choose
        self.assertTrue(self.open())
        self.root.wait_window.assert_called_once_with(self.windows[0])
        self.assertNotIn(self.windows[0], theme.OPEN_DIALOGS)

    def test_optional_startup_offers_defer_without_input_grabs(self):
        import aipet_app
        app = object.__new__(aipet_app.TrayApp)
        app.root = self.root
        app.root.grab_current.return_value = None
        app.setup, app.hooks = SimpleNamespace(win=None), SimpleNamespace(busy=False)
        with patch.object(aipet_app, 'setup_is_done', return_value=True):
            win = self.open(MagicMock())
            self.assertTrue(app.startup_dialogs_open())
            win.destroy()
            self.assertFalse(app.startup_dialogs_open())


if __name__ == '__main__':
    unittest.main()
