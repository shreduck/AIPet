"""Callable add-in API, masked detection and session-scoped asynchronous UI state."""
import json
from pathlib import Path
import tempfile
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aipet_addin import AIPet
from aipet_token_guard import findings, on_event
from aipetlib.addins import AddinManager
from aipetlib.addin_messages import MessageStore, for_items, unread_summary


class InterjectionTests(unittest.TestCase):
    def test_full_hook_text_over_ordinary_message_limit_survives_report_delivery(self):
        secret = 'ghp_' + 'SyntheticToken1234' * 2
        text = 'surrounding text ' * 2000 + secret + ' ending'
        on_event(dict(agent='claude', session_id='fixture', data={'prompt': text}), self.context)
        report = self.snapshot()[0]['messages'][0]['report']
        self.assertEqual(report['hook_text'], text)
        start, end = report['hook_spans'][0]
        self.assertEqual(text[start:end], secret)
    def test_each_warning_opens_its_own_window_and_acknowledges_independently(self):
        import aipet_app
        app = object.__new__(aipet_app.TrayApp)
        app.root = MagicMock()
        app.root.grab_current.return_value = None
        app.addins = self.manager
        app.pet = SimpleNamespace(addin_snapshot=[])
        screen = dict(id='pet-screen', work_area=[-1920, 0, 0, 1080])
        self.manager.set_pet_locations(dict(x=-100, y=900, screen_id='pet-screen', screen=screen), {})
        self.api.warning('Low warning: hash-like value', window='pet')
        app.pet.addin_snapshot = self.snapshot()
        with patch.object(aipet_app.addin_message_ui, 'show') as show:
            app.show_addin_alerts()
            show.assert_called_once()
            self.assertEqual(show.call_args.kwargs['screen'], screen)
            self.assertEqual(unread_summary(app.pet.addin_snapshot), ('warning', 1))
            app.show_addin_alerts()
            show.assert_called_once()
            self.api.error('High warning: encoded credentials', window='pet')
            app.pet.addin_snapshot = self.snapshot()
            app.show_addin_alerts()
            self.assertEqual(show.call_count, 2)
            show.return_value.set_addin_rows.assert_not_called()
            first_rows = show.call_args_list[0].args[1]
            second_rows = show.call_args_list[1].args[1]
            self.assertEqual(len(first_rows[0]['messages']), 1)
            self.assertEqual(len(second_rows[0]['messages']), 1)
            self.assertNotEqual(first_rows[0]['messages'][0]['id'], second_rows[0]['messages'][0]['id'])
            self.assertTrue(all(m['timestamp'] > 0 for r in first_rows + second_rows for m in r['messages']))
            self.api.warning('Arrived after display')
            self.snapshot()
            show.call_args_list[0].kwargs['on_close'](first_rows)
            self.assertEqual(unread_summary(app.pet.addin_snapshot), ('error', 2))
            show.call_args_list[1].kwargs['on_close'](second_rows)
            self.assertEqual(unread_summary(app.pet.addin_snapshot), ('warning', 1))
            # Explicit window requests only; the ordinary warning remains in the bubble.
            app.show_addin_alerts()
            self.assertEqual(show.call_count, 2)
            self.api.warning('New alert window', window='pet')
            app.pet.addin_snapshot = self.snapshot()
            app.show_addin_alerts()
            self.assertEqual(show.call_count, 3)

    def test_alert_window_uses_theme_tags_and_close_tracks_displayed_rows(self):
        from aipetlib.ui import addin_messages as ui
        self.api.warning('Low warning: hash-like value')
        rows = self.snapshot()
        parent, closed = MagicMock(), MagicMock()
        parent.winfo_viewable.return_value = False
        for method in ('winfo_rootx', 'winfo_rooty'):
            getattr(parent, method).return_value = 0
        parent.winfo_width.return_value = parent.winfo_height.return_value = 100
        with patch.object(ui.tk, 'Toplevel') as top, patch.object(ui.tk, 'Frame'), \
                patch.object(ui.tk, 'Label'), patch.object(ui.tk, 'Button'), patch.object(ui, 'WarningBeacon'), \
                patch.object(ui, 'ScrolledText') as text, patch.object(ui.theme, 'theme_window') as themed, \
                patch.object(ui.geometry, 'screen_info', return_value=dict(id='main', work_area=[0, 0, 1000, 800])) as screen_info:
            top.return_value.winfo_reqwidth.return_value = 600
            top.return_value.winfo_reqheight.return_value = 400
            text.return_value.yview.return_value = (0.0, 1.0)
            win = ui.show(parent, rows, on_close=closed)
            screen_info.assert_called_once_with(0, 0, win)
            win.geometry.assert_called_once_with('775x620+112+90')
            themed.assert_called_once_with(win)
            restyle = next(call.args[1] for call in win.bind.call_args_list if call.args[0] == '<<AIPetThemeChanged>>')
            with patch.dict(ui.theme.T, name='dark'):
                restyle()
                text.return_value.tag_configure.assert_any_call('warning', foreground='#fbbf24',
                    font=(ui.theme.UI_FONT, 11, 'bold'))
                text.return_value.tag_configure.assert_any_call('error', foreground='#f87171',
                    font=(ui.theme.UI_FONT, 11, 'bold'))
            self.api.error('High warning')
            updated = self.snapshot()
            win.set_addin_rows(updated)
            close = next(call.args[1] for call in win.protocol.call_args_list if call.args[0] == 'WM_DELETE_WINDOW')
            close()
            closed.assert_called_once_with(updated)
            win.destroy.assert_called_once()

    def test_sdk_pet_location_is_delivery_snapshot_and_alert_window_target_is_validated(self):
        screen = dict(id='display-two', bounds=[-1920, 0, 0, 1080], work_area=[-1920, 0, 0, 1040])
        position = dict(x=-200, y=900, width=100, height=100, screen_id=screen['id'], screen=screen)
        self.manager.set_pet_locations(position, {('claude', 'fixture'): position})
        api = AIPet(self.worker.context({'agent': 'claude', 'session_id': 'fixture'}))
        self.assertEqual(api.get_pet_position(), position)
        self.assertEqual(api.get_pet_screen(), screen)
        api.get_pet_screen()['bounds'][0] = 999
        self.assertEqual(api.get_pet_screen()['bounds'][0], -1920)
        self.manager.set_pet_locations(dict(position, x=100), {})
        self.assertEqual(api.get_pet_position()['x'], -200)
        api.warning('Center here', window='pet')
        self.assertEqual(self.snapshot()[0]['messages'][0]['window'], 'pet')
        with self.assertRaises(ValueError):
            api.warning('Invalid', window='somewhere')
        self.assertIsNone(AIPet({}).get_pet_position())

    def test_ui_publishes_locations_for_each_session_in_a_compact_pet(self):
        import aipet_app
        app = object.__new__(aipet_app.TrayApp)
        root, canvas = MagicMock(), MagicMock()
        overlay = dict(x=10, y=20, screen_id='main', screen=dict(id='main'))
        pet = dict(x=-200, y=30, screen_id='secondary', screen=dict(id='secondary'))
        view = SimpleNamespace(canvas=canvas, data=dict(everyone=[
            dict(agent='claude', session_id='one'), dict(agent='codex', session_id='two')]))
        app.pet = SimpleNamespace(root=root, pets={'compact': view})
        app.addins = self.manager
        with patch.object(aipet_app.geometry, 'widget_location', side_effect=[overlay, pet]):
            app.publish_addin_pet_locations()
        self.assertEqual(self.manager.pet_location(dict(agent='claude', session_id='one')), pet)
        self.assertEqual(self.manager.pet_location(dict(agent='codex', session_id='two')), pet)
        self.assertEqual(self.manager.pet_location(dict(session_id='unknown')), overlay)

    def test_alert_window_centers_on_requested_secondary_screen(self):
        from aipetlib.ui import addin_messages as ui
        parent = MagicMock()
        parent.winfo_viewable.return_value = False
        with patch.object(ui.tk, 'Toplevel') as top, patch.object(ui.tk, 'Frame'), \
                patch.object(ui.tk, 'Label'), patch.object(ui.tk, 'Button'), patch.object(ui, 'WarningBeacon'), \
                patch.object(ui, 'ScrolledText') as text, patch.object(ui.theme, 'theme_window'):
            top.return_value.winfo_reqwidth.return_value = 600
            top.return_value.winfo_reqheight.return_value = 400
            text.return_value.yview.return_value = (0.0, 1.0)
            ui.show(parent, [], screen=dict(id='secondary', work_area=[-1920, 0, 0, 1040]))
            top.return_value.geometry.assert_called_once_with('1487x806+-1704+117')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.manager = AddinManager(self.home, {'token-guard': True})
        self.addCleanup(self.manager.stop)
        self.worker = self.manager.workers['token-guard']
        self.context = self.worker.context({'agent': 'claude', 'session_id': 'fixture'})
        self.api = AIPet(self.context)

    def snapshot(self):
        self.manager.drain_output()
        return self.manager.messages.snapshot()

    def test_callable_levels_status_and_completion_preserve_attention(self):
        self.assertTrue(self.api.status('working'))
        self.assertEqual(self.snapshot()[0]['state'], 'working')
        self.api.info('Progress', 'An informational message')
        self.api.warning('Review', 'Needs your attention')
        self.api.error('Failed', 'An error message')
        self.api.status('stopped', _preserve_attention=True)
        rows = self.snapshot()
        self.assertEqual(rows[0]['state'], 'needs_attention')
        self.assertEqual(unread_summary(rows), ('error', 3))
        self.api.status('stopped')
        self.assertEqual(self.snapshot()[0]['state'], 'stopped')

    def test_acknowledgment_does_not_swallow_messages_arriving_after_open(self):
        self.api.info('First')
        old = self.snapshot()
        ids = {m['id'] for row in old for m in row['messages']}
        self.api.warning('Second')
        self.snapshot()
        self.manager.messages.acknowledge(ids)
        self.assertEqual(unread_summary(self.snapshot()), ('warning', 1))

    def test_only_matching_agent_and_session_receive_interjections(self):
        self.api.warning('Private to this session')
        rows = self.snapshot()
        self.assertEqual(for_items(rows, [{'agent': 'codex', 'session_id': 'fixture'}]), [])
        self.assertEqual(for_items(rows, [{'agent': 'claude', 'session_id': 'other'}]), [])
        self.assertEqual(len(for_items(rows, [{'agent': 'claude', 'session_id': 'fixture'}])), 1)

    def test_disable_rejects_stale_messages_and_marks_worker_stopped(self):
        self.api.status('working')
        self.snapshot()
        self.manager.configure({})
        self.api.error('Late result from disabled worker')
        row = self.snapshot()[0]
        self.assertEqual((row['state'], row['messages']), ('stopped', []))

    def test_wrong_generation_malformed_and_oversized_messages_are_discarded(self):
        self.api.context = dict(self.context, run_id='stale')
        self.api.error('Ignored')
        folder = self.home / 'addin-outbox'
        (folder / 'broken.json').write_text('{')
        (folder / 'oversized.json').write_text('x' * 16385)
        self.assertEqual(self.snapshot(), [])
        self.assertEqual(list(folder.iterdir()), [])
        with self.assertRaises(ValueError):
            self.api.status('unknown-state')

    def test_detector_finds_nested_credentials_without_revealing_values(self):
        secret = 'sk-proj-' + 'A1b2C3d4' * 6
        data = {'tool_input': {'command': 'echo ' + secret}, 'extra': [{'access_token': 'opaquecredential123456'}]}
        self.assertTrue(findings(data))
        self.assertEqual(findings({'input_tokens': 20000, 'token': '${MY_TOKEN}', 'text': 'normal code'}), [])
        event = {'data': data}
        on_event(event, self.context)
        rows = self.snapshot()
        rendered = json.dumps([dict(title=m['title'], content=m['content']) for row in rows for m in row['messages']])
        self.assertNotIn(secret, rendered)
        self.assertNotIn('opaquecredential123456', rendered)
        self.assertIn('fingerprint', rendered.lower())
        self.assertEqual(unread_summary(rows), ('warning', 2))
        on_event(event, self.context)
        self.assertEqual(unread_summary(self.snapshot()), ('warning', 2))

    def test_session_metadata_reaches_worker_and_report_ingestion_fills_missing_title(self):
        self.manager.set_session_context({('claude', 'fixture'): dict(conversation_title='Conversation fixture',
            harness='Claude Code', cwd='/fixture')})
        context = self.worker.context(dict(agent='claude', session_id='fixture'))
        self.assertEqual(context['session']['conversation_title'], 'Conversation fixture')
        # Simulate a delivery captured before the UI learned the conversation title.
        on_event(dict(agent='claude', session_id='fixture', event='UserPromptSubmit',
                      data=dict(token='synthetic-credential123456')), self.context)
        report = self.snapshot()[0]['messages'][0]['report']
        self.assertEqual(report['context']['conversation_title'], 'Conversation fixture')
        self.assertEqual(report['context']['harness'], 'Claude Code')
        self.assertEqual(report['context']['hook_event'], 'UserPromptSubmit')

    def test_detected_value_is_also_censored_if_it_appears_in_conversation_title(self):
        secret = 'synthetic-credential123456'
        self.manager.set_session_context({('claude', 'fixture'): dict(conversation_title='Investigate ' + secret)})
        on_event(dict(agent='claude', session_id='fixture', data=dict(token=secret)), self.context)
        report = self.snapshot()[0]['messages'][0]['report']
        self.assertEqual(report['context']['conversation_title'], 'Investigate [censored]')
        self.assertEqual(report['value'], secret)

    def test_message_history_and_outbox_have_limits(self):
        for index in range(25):
            self.api.info(str(index), 'x' * 9000)
        rows = self.snapshot()
        self.assertEqual(len(rows[0]['messages']), 20)
        self.assertLessEqual(len(rows[0]['messages'][0]['content']), 8000)
        folder = self.home / 'addin-outbox'
        for index in range(256):
            (folder / f'{index}.json').write_text('{}')
        self.assertFalse(self.api.info('Queue full'))

    def test_mini_nodes_show_only_addin_name_and_status_and_open_contents(self):
        from aipetlib.ui.agent_graph import GraphView
        self.api.warning('Sensitive finding', 'Long finding details')
        rows = self.snapshot()
        view = SimpleNamespace(pet=SimpleNamespace(addin_snapshot=rows, open_addin_messages=MagicMock()),
                               win=MagicMock(), s=1.0, g_bold=MagicMock(), g_small=MagicMock(),
                               P={'mu': '#888888', 'node': '#ffffff'}, _fit=lambda text, width, font: text)
        graph = GraphView(view)
        graph.canvas = MagicMock()
        graph.node_rects = []
        graph.line_height = lambda: 14
        graph.draw_addins({'session_id': 'fixture'}, 0, 0)
        labels = [call.kwargs['text'] for call in graph.canvas.create_text.call_args_list]
        self.assertEqual(labels, ['Token guard', 'warning'])
        graph.open_addin(rows[0])
        view.pet.open_addin_messages.assert_called_once_with([rows[0]], parent=view.win)

    def test_custom_token_guard_process_calls_the_bundled_sdk(self):
        source = Path(__file__).resolve().parents[1] / 'examples/addins/token-guard'
        shutil.copytree(source, self.home / 'addins/token-guard-custom')
        self.manager.configure({'token-guard-custom': True})
        worker = self.manager.workers['token-guard-custom']
        secret = 'ghp_' + 'Synthetic0123456789' * 3
        worker.execute({'agent': 'codex', 'session_id': 'custom', 'data': {'text': secret}})
        rows = self.snapshot()
        self.assertEqual(rows[0]['name'], 'Token guard (custom example)')
        self.assertEqual(rows[0]['state'], 'needs_attention')
        self.assertEqual(unread_summary(rows), ('warning', 1))
        self.assertNotIn(secret, rows[0]['messages'][0]['content'])
        self.assertEqual(rows[0]['messages'][0]['report']['value'], secret)

    def test_secondary_bubble_is_left_of_pet_and_uses_highest_unread_severity(self):
        from aipetlib.ui.pet_view import PetView
        self.api.info('First')
        self.api.error('Second')
        rows = self.snapshot()
        pet = object.__new__(PetView)
        pet.app = SimpleNamespace(addin_snapshot=rows)
        pet.data = {'everyone': [{'session_id': 'fixture'}, {'session_id': 'other'}]}
        pet.canvas = MagicMock()
        with patch('aipetlib.ui.pet_view.sprites.mono', return_value='font'):
            left = pet._draw_addin_bubble(-10)
        self.assertLess(left, -10)
        self.assertEqual(pet.canvas.create_rectangle.call_args.kwargs['outline'], '#b91c1c')
        self.assertEqual(pet.canvas.create_text.call_args.kwargs['text'], '× 2')
        self.manager.messages.acknowledge({m['id'] for row in rows for m in row['messages']})
        pet.app.addin_snapshot = self.snapshot()
        self.assertIsNone(pet._draw_addin_bubble())
