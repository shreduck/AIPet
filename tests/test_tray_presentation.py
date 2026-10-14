"""Tray presentation contracts: session priority, cached drawing and notification dispatch."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aipetlib.app import presentation


class TrayPresentationTests(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(root=MagicMock(), pet=SimpleNamespace(_last_items=[]),
                                   approvals=SimpleNamespace(targets_on=[]), c_notify=True,
                                   hidden=False, ui=MagicMock(), _mac_menu_spec=MagicMock(return_value=[]))
        self.presenter = presentation.TrayPresenter(self.app)

    def test_summary_uses_all_sessions_and_mentions_auto_approve(self):
        self.assertEqual(self.presenter.summary(), ('idle', 'AIPet - no active sessions'))
        self.app.pet._last_items = [{'state': 'working'}, {'state': 'needs_input'}, {'state': 'done'}]
        self.app.approvals.targets_on = ['windows']
        state, text = self.presenter.summary()
        self.assertEqual(state, 'needs_input')
        self.assertIn('AUTO APPROVE ON', text)
        for label in ('1 working', '1 needs you', '1 done'):
            self.assertIn(label, text)
        self.assertLessEqual(len(text), 120)

    def test_tray_redraws_only_when_state_text_or_style_changes(self):
        self.presenter.icon = MagicMock()
        with patch.object(presentation, 'make_icon', return_value='image') as draw:
            self.presenter.update_tray()
            self.presenter.update_tray()
            draw.assert_called_once_with('idle')
            self.app.pet._last_items = [{'state': 'needs_input'}]
            self.presenter.update_tray()
            draw.assert_called_with('needs_input')
            self.assertEqual(draw.call_count, 2)
            self.presenter.invalidate()
            self.presenter.update_tray()
            self.assertEqual(draw.call_count, 3)

    def test_dock_bounces_once_per_attention_transition_and_retains_image(self):
        with patch.object(presentation, 'dock_image') as image, \
                patch.object(presentation.tk, 'PhotoImage', return_value='photo'):
            self.presenter.update_dock()
            self.app.pet._last_items = [{'state': 'needs_input'}]
            self.presenter.update_dock()
            self.presenter.update_dock()
            self.app.root.attributes.assert_called_once_with('-notify', True)
            self.assertEqual(image.call_count, 2)
            self.assertEqual(self.presenter._dock_ph, 'photo')
            self.app.pet._last_items = []
            self.presenter.update_dock()
            self.app.pet._last_items = [{'state': 'error'}]
            self.presenter.update_dock()
            self.assertEqual(self.app.root.attributes.call_count, 2)

    def test_notification_preferences_visibility_and_previous_state(self):
        self.presenter.icon = MagicMock()
        item = {'title': 'Project', 'where': 'Claude Code', 'message': 'Approve Read'}
        with patch.object(presentation, 'IS_MAC', False):
            self.presenter.on_alert('needs_input', 'working', item)
            self.presenter.icon.notify.assert_called_once_with('Approve Read', 'Project needs your input')
            self.presenter.icon.notify.reset_mock()
            self.presenter.on_alert('done', 'working', item)
            self.presenter.icon.notify.assert_not_called()
            self.app.hidden = True
            self.presenter.on_alert('done', 'working', item)
            self.presenter.icon.notify.assert_called_once_with('Claude Code', 'Project is done')
            self.presenter.icon.notify.reset_mock()
            self.presenter.on_alert('done', 'idle', item)
            self.app.c_notify = False
            self.presenter.on_alert('needs_input', 'working', item)
            self.presenter.icon.notify.assert_not_called()

    def test_mac_notification_quotes_body_and_title(self):
        with patch.object(presentation, 'IS_MAC', True), \
                patch.object(presentation.subprocess, 'Popen') as launch:
            self.presenter.notify('Say "hello"', 'A "title"')
        launch.assert_called_once_with(['osascript', '-e',
                                       'display notification "Say \\"hello\\"" with title "A \\"title\\""'])

    def test_tray_actions_are_queued_for_the_ui_thread(self):
        action = MagicMock()
        self.app._mac_menu_spec.return_value = [None, {'label': 'Action', 'action': action, 'checked': True}]
        fake = MagicMock()
        fake.Menu.side_effect = lambda *entries: entries
        fake.MenuItem.side_effect = lambda label, handler, **kw: SimpleNamespace(label=label, handler=handler, **kw)
        with patch.object(presentation, 'pystray', fake):
            factory = self.presenter.build_menu()[0]
            separator, entry = factory()
        self.assertIs(separator, fake.Menu.SEPARATOR)
        self.assertTrue(entry.checked(None))
        entry.handler()
        self.app.ui.assert_called_once_with(action)
        action.assert_not_called()

    def test_shutdown_removes_native_status_item_and_stops_tray(self):
        self.presenter.icon, self.presenter.status_item = MagicMock(), MagicMock()
        self.presenter.stop()
        self.presenter.status_item.remove.assert_called_once()
        self.presenter.icon.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
