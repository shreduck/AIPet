"""Compact robots retain their own click targets; errors never displace other session states."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aipet
from aipetlib.app import presentation
from aipetlib.app.pointer import Pointer
from aipetlib.ui import pet_view


class CompactPriorityTests(unittest.TestCase):
    def test_errors_rank_below_every_other_state(self):
        app = object.__new__(aipet.PetApp)
        error = dict(key='bad', state='error', changed=999)
        for state in ('needs_input', 'working', 'done', 'idle'):
            with self.subTest(state=state):
                item = dict(key='other', state=state, changed=1)
                group, = app._compact_items([error, item])
                self.assertEqual(group['focus'], 'other')
                self.assertEqual(group['members'][-1]['key'], 'bad')
                self.assertGreater(presentation.RANK['error'], presentation.RANK[state])

    def test_permission_queue_still_serves_oldest_first(self):
        app = object.__new__(aipet.PetApp)
        group, = app._compact_items([dict(key='new', state='needs_input', changed=5),
                                    dict(key='old', state='needs_input', changed=1)])
        self.assertEqual(group['focus'], 'old')


class RobotClickTests(unittest.TestCase):
    def setUp(self):
        self.items = [dict(key='front', state='needs_input'), dict(key='side', state='needs_input')]
        self.app = SimpleNamespace(root=MagicMock(), frame=MagicMock(), cfg={}, ack={}, done_flash={},
                                   _last_items=self.items, tooltips=MagicMock(), open_detail=MagicMock(),
                                   focus_session=MagicMock(), _acknowledge=MagicMock())
        self.app.pointer = Pointer(self.app)
        with patch.object(pet_view.tk, 'Canvas'):
            self.pet = pet_view.PetView(self.app, '_group')
        self.pet.data = dict(self.items[0], focus='front', members=self.items)
        self.click = next(call.args[2] for call in self.pet.canvas.tag_bind.call_args_list
                          if call.args[:2] == ('bot', '<ButtonPress-1>'))

    def press(self, key):
        self.pet.canvas.gettags.return_value = ('hit', 'bot', 'bot:' + key)
        self.click(SimpleNamespace())

    def test_side_robot_opens_its_prompt_even_when_click_to_focus_is_off(self):
        self.app.cfg['click_to_focus'] = False
        self.press('side')
        self.app.pointer.release(SimpleNamespace(state=0), self.pet)
        self.app.open_detail.assert_called_once_with('side')
        self.app.focus_session.assert_not_called()
        self.assertTrue(self.app.ack['side'])
        self.assertNotIn('front', self.app.ack)

    def test_robot_without_prompt_focuses_its_own_session(self):
        self.items[1]['state'] = 'working'
        self.press('side')
        self.app.pointer.release(SimpleNamespace(state=0), self.pet)
        self.app.open_detail.assert_not_called()
        target, = self.app.focus_session.call_args.args
        self.assertEqual(target.key, 'side')

    def test_dragging_a_robot_does_not_open_a_prompt(self):
        self.app.placement = SimpleNamespace(anchor=None)
        self.press('side')
        self.app.pointer.drag = [0, 0, 0, 0, True]
        self.app.pointer.release(SimpleNamespace(state=0), self.pet)
        self.app.open_detail.assert_not_called()
        self.assertIsNone(self.pet._on_badge)

    def test_rendered_robots_carry_the_matching_session_keys(self):
        self.pet.data['members'].append(dict(key='bad', state='error'))
        self.pet._put = MagicMock()
        self.pet._bubble = self.pet._mark = self.pet._draw_tag = MagicMock()
        sprite = dict(meta=dict(robot=dict(size=(37, 52))), img=dict(shadow=object()))
        with patch.object(pet_view.sprites, 'load_sprites', return_value=sprite), \
                patch.object(pet_view.sprites, 'robot_image', return_value=object()):
            self.pet._draw_robot(0)
        robots = [c for c in self.pet._put.call_args_list if isinstance(c.args[0], tuple)
                  and c.args[0][0] == 'robot']
        self.assertEqual([c.kwargs['tags'][-1] for c in robots], ['bot:side', 'bot:bad', 'bot:front'])
        self.assertEqual([c.args[0][1] for c in robots], ['ask', 'error', 'ask'])


if __name__ == '__main__':
    unittest.main()
