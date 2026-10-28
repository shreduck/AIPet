"""Alert navigation, individual dismissal and safe asynchronous dismiss-all."""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from aipetlib.addin_messages import AlertCarousel
from aipetlib.ui import addin_messages as ui


def rows(*ids):
    return [dict(name='Test add-in', level='warning', messages=[
        dict(id=key, title=f'Alert {key}', content='Test report', level='warning', timestamp=1234567890)
        for key in ids])]


@contextmanager
def window(*ids, options=None, on_options=None):
    with ExitStack() as stack:
        mocks = {name: stack.enter_context(patch.object(ui.tk, name))
                 for name in ('Toplevel', 'Frame', 'Label', 'Button', 'BooleanVar', 'Checkbutton', 'Scale')}
        stack.enter_context(patch.object(ui, 'WarningBeacon'))
        stack.enter_context(patch.object(ui, 'ToggleSwitch'))
        text = stack.enter_context(patch.object(ui, 'ScrolledText'))
        text.return_value.yview.return_value = (0.0, 1.0)
        stack.enter_context(patch.object(ui.theme, 'theme_window'))
        confirm = stack.enter_context(patch.object(ui.theme, 'themed_dialog'))
        dismissed = MagicMock()
        parent = MagicMock()
        parent.winfo_viewable.return_value = False
        top = mocks['Toplevel'].return_value
        top.winfo_width.return_value, top.winfo_height.return_value = 775, 620
        top.winfo_x.return_value, top.winfo_y.return_value = 100, 100
        win = ui.show(parent, rows(*ids), on_close=dismissed, screen=dict(work_area=[0, 0, 1920, 1080]),
                      options=options if options is not None else {'addin_alert_no_focus': False}, on_options=on_options)
        commands = {call.kwargs['text']: call.kwargs['command'] for call in mocks['Button'].call_args_list}
        commands['scale'] = mocks['Scale'].call_args.kwargs['command']
        route = next(call.args[2] for call in win.bind_class.call_args_list if call.args[1] == '<KeyPress>')
        commands['keys'] = {sequence: lambda event, key=key: route(SimpleNamespace(**vars(event), keysym=key))
                            for sequence, key in (('<Left>', 'Left'), ('<Right>', 'Right'),
                                                  ('<KeyPress-d>', 'd'), ('<KeyPress-D>', 'D'))}
        close = next(call.args[1] for call in win.protocol.call_args_list if call.args[0] == 'WM_DELETE_WINDOW')
        yield win, commands, close, confirm, dismissed, text.return_value


class CarouselTests(unittest.TestCase):
    def test_no_focus_default_and_live_topmost_update(self):
        with window('one', options={}) as (win, buttons, close, confirm, dismissed, text):
            win.after_idle.assert_not_called()
            win.attributes.assert_any_call('-topmost', True)
            win.set_addin_options({'addin_alert_topmost': False})
            win.attributes.assert_called_with('-topmost', False)
            win.after_idle.assert_not_called()

    def test_arrow_and_d_shortcuts_navigate_and_dismiss_only_the_current_alert(self):
        with window('one', 'two') as (win, buttons, close, confirm, dismissed, text):
            bindings = buttons['keys']
            widget = MagicMock()
            widget.winfo_class.return_value = 'Text'
            event = SimpleNamespace(state=0, widget=widget)
            self.assertEqual(bindings['<Right>'](event), 'break')
            self.assertIn('Alert two', win.title.call_args.args[0])
            bindings['<Left>'](event)
            self.assertIn('Alert one', win.title.call_args.args[0])
            bindings['<Left>'](event)  # Wrap to the last alert.
            dismissed.assert_not_called()
            self.assertEqual(bindings['<KeyPress-d>'](event), 'break')
            self.assertEqual(dismissed.call_args.args[0][0]['messages'][0]['id'], 'two')
            win.destroy.assert_not_called()
            bindings['<KeyPress-D>'](event)
            win.destroy.assert_called_once()

    def test_shortcuts_respect_slider_focus_modifiers_and_pending_confirmation(self):
        with window('one', 'two') as (win, buttons, close, confirm, dismissed, text):
            bindings = buttons['keys']
            widget = MagicMock()
            widget.winfo_class.return_value = 'Scale'
            event = SimpleNamespace(state=0, widget=widget)
            title_calls = win.title.call_count
            self.assertIsNone(bindings['<Right>'](event))
            widget.winfo_class.return_value = 'Text'
            event.state = 0x4  # Ctrl+D must not dismiss an alert.
            self.assertIsNone(bindings['<KeyPress-d>'](event))
            self.assertEqual(win.title.call_count, title_calls)
            event.state = 0
            close()
            self.assertIsNone(bindings['<KeyPress-d>'](event))
            dismissed.assert_not_called()

    def test_windows_numlock_and_extended_key_flags_do_not_block_shortcuts(self):
        with window('one', 'two') as (win, buttons, close, confirm, dismissed, text), \
                patch.object(ui.os, 'name', 'nt'), patch.object(ui.theme, 'IS_MAC', False):
            widget = MagicMock()
            widget.winfo_class.return_value = 'Text'
            event = SimpleNamespace(state=0x40008, widget=widget)
            self.assertEqual(buttons['keys']['<Right>'](event), 'break')
            self.assertIn('Alert two', win.title.call_args.args[0])
            event.state = 0x8
            self.assertEqual(buttons['keys']['<KeyPress-d>'](event), 'break')
            self.assertEqual(dismissed.call_args.args[0][0]['messages'][0]['id'], 'two')

    def test_d_dismisses_with_slider_focus_and_report_focus_is_set_after_mapping(self):
        with window('one', 'two') as (win, buttons, close, confirm, dismissed, text):
            win.after_idle.assert_any_call(win.focus_addin_report)
            bindings = buttons['keys']
            widget = MagicMock()
            widget.winfo_class.return_value = 'Scale'
            bindings['<KeyPress-d>'](SimpleNamespace(state=0, widget=widget))
            self.assertEqual(dismissed.call_args.args[0][0]['messages'][0]['id'], 'one')
            text.focus_set.assert_called()

    def test_content_slider_scales_markdown_and_secret_tags_without_replacing_text(self):
        with window('one') as (win, buttons, close, confirm, dismissed, text):
            text.insert.reset_mock()
            text.delete.reset_mock()
            buttons['scale']('150')
            text.configure.assert_any_call(font=(ui.theme.UI_FONT, 15))
            text.tag_configure.assert_any_call('md-h1', font=(ui.theme.UI_FONT, 27, 'bold'), spacing1=12, spacing3=6)
            text.tag_configure.assert_any_call('secret-emphasis', background=ui.theme.T['select_bg'],
                foreground=ui.theme.T['win_fg'], font=(ui.theme.UI_FONT, 15, 'bold'))
            text.insert.assert_not_called()
            text.delete.assert_not_called()

    def test_arrivals_do_not_change_selection_and_dismissed_ids_do_not_return(self):
        queue = AlertCarousel()
        queue.add(rows('one', 'two'))
        queue.step(1)
        queue.add(rows('one', 'two', 'three'))
        self.assertEqual(queue.current()[0]['messages'][0]['id'], 'two')
        queue.dismiss(queue.current())
        self.assertEqual(queue.current()[0]['messages'][0]['id'], 'three')
        queue.add(rows('two'))
        self.assertEqual(len(queue.entries), 2)
        queue.step(1)
        self.assertEqual(queue.current()[0]['messages'][0]['id'], 'one')

    def test_navigation_does_not_acknowledge_and_dismiss_only_removes_selected_alert(self):
        with window('one', 'two') as (win, buttons, close, confirm, dismissed, text):
            buttons['Next ›']()
            self.assertIn('Alert two', win.title.call_args.args[0])
            dismissed.assert_not_called()
            buttons['Dismiss']()
            self.assertEqual(dismissed.call_args.args[0][0]['messages'][0]['id'], 'two')
            self.assertIn('Alert one', win.title.call_args.args[0])
            win.destroy.assert_not_called()
            close()
            confirm.assert_not_called()
            win.destroy.assert_called_once()

    def test_close_cancel_preserves_all_alerts_and_repeated_close_reuses_confirmation(self):
        with window('one', 'two') as (win, buttons, close, confirm, dismissed, text):
            close()
            close()
            confirm.assert_called_once()
            confirm.return_value.lift.assert_called_once()
            dismissed.assert_not_called()
            confirm.call_args.kwargs['on_result'](False)
            win.destroy.assert_not_called()
            buttons['Next ›']()
            self.assertIn('Alert two', win.title.call_args.args[0])

    def test_confirming_close_only_dismisses_alerts_present_when_question_opened(self):
        with window('one', 'two') as (win, buttons, close, confirm, dismissed, text):
            close()
            text.insert.reset_mock()
            win.append_addin_rows(rows('three'))
            text.insert.assert_not_called()  # Reading/reveal state stays undisturbed by arrivals.
            confirm.call_args.kwargs['on_result'](True)
            self.assertEqual({m['id'] for row in dismissed.call_args.args[0] for m in row['messages']},
                             {'one', 'two'})
            win.destroy.assert_not_called()
            self.assertIn('Alert three', win.title.call_args.args[0])
            buttons['Dismiss']()
            win.destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
