"""Automatic approvals produce brief feedback instead of a permission bubble."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet
from aipetlib.ui import pet_view


class AutoFeedbackTests(unittest.TestCase):
    def test_all_auto_modes_flash_for_two_seconds_then_expire(self):
        for kind in ('timed', 'all', 'whitelist', 'blacklist'):
            with self.subTest(kind=kind):
                app = object.__new__(aipet.PetApp)
                item = dict(key='s', state='working', auto_t=100, auto_kind=kind)
                with patch.object(aipet.time, 'time', return_value=101):
                    app._mark_auto_flash([item])
                self.assertTrue(item['auto_flash'])
                with patch.object(aipet.time, 'time', return_value=102):
                    app._mark_auto_flash([item])
                self.assertFalse(item['auto_flash'])

    def test_erroring_session_does_not_hide_another_sessions_auto_feedback(self):
        app = object.__new__(aipet.PetApp)
        item = dict(key='ok', state='working', auto_t=100, auto_kind='timed')
        with patch.object(aipet.time, 'time', return_value=101):
            app._mark_auto_flash([item, dict(key='bad', state='error')])
        group, = app._compact_items([item, dict(key='bad', state='error')])
        self.assertEqual(group['focus'], 'ok')
        self.assertTrue(group['auto_flash'])

    def test_real_pending_prompt_keeps_priority_over_auto_feedback(self):
        app = object.__new__(aipet.PetApp)
        item = dict(key='s', state='needs_input', auto_t=100, auto_kind='timed')
        with patch.object(aipet.time, 'time', return_value=101):
            app._mark_auto_flash([item])
        self.assertFalse(item['auto_flash'])

    def test_auto_flash_renders_only_auto_bubble(self):
        view = object.__new__(pet_view.PetView)
        view.app = SimpleNamespace(ack={}, done_flash={})
        view.key, view.seed, view.born, view.acked = 's', 0, 0, False
        view.data = dict(key='s', state='working', auto_flash=True)
        view.canvas = MagicMock()
        view._put, view._bubble, view._mark, view._draw_tag = (MagicMock() for _ in range(4))
        sprite = dict(meta=dict(robot=dict(size=(37, 52))), img=dict(shadow=object()))
        with patch.object(pet_view.sprites, 'load_sprites', return_value=sprite), \
                patch.object(pet_view.sprites, 'robot_image', return_value=object()):
            view._draw_robot(0)
        view._bubble.assert_called_once()
        self.assertNotIn('ans', view._bubble.call_args.kwargs.get('tags', ()))
        view._mark.assert_called_once()
        self.assertEqual(view._mark.call_args.args[0], 'ok')
        self.assertTrue(any(call.kwargs.get('text') == 'auto' for call in view.canvas.create_text.call_args_list))

        view.data['auto_kind'] = 'blacklist'
        view._mark.reset_mock()
        view.canvas.create_text.reset_mock()
        with patch.object(pet_view.sprites, 'load_sprites', return_value=sprite), \
                patch.object(pet_view.sprites, 'robot_image', return_value=object()):
            view._draw_robot(0)
        self.assertEqual(view._mark.call_args.args[0], 'no')
        self.assertTrue(any(call.kwargs.get('text') == 'blacklist' for call in view.canvas.create_text.call_args_list))


if __name__ == '__main__':
    unittest.main()
