"""Controller contracts: background work publishes through the UI queue and releases the busy flag."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aipetlib.app import hooks, setup, cowork


class HookJobsTests(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.app = SimpleNamespace(status={}, ui=self.pending.append, info=MagicMock(),
                                   ask=MagicMock(return_value=False), refresh_menu=MagicMock())
        self.jobs = hooks.HookJobs(self.app, MagicMock())

    def test_success_and_error_report_on_ui_thread_and_release_busy(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                self.pending.clear()
                self.app.info.reset_mock()
                self.app.refresh_menu.reset_mock()
                self.jobs.busy = True
                with patch.object(hooks.hook_installer, 'install', side_effect=RuntimeError('invalid JSON') if failure else None,
                                  return_value='installed'), patch.object(hooks.hook_installer, 'status', return_value='installed'), \
                        patch.object(hooks.hook_targets, 'describe', return_value='isolated settings'):
                    self.jobs._run_job('windows', 'install')
                self.assertFalse(self.jobs.busy)
                self.app.refresh_menu.assert_called_once()
                self.app.info.assert_not_called()
                self.assertEqual(len(self.pending), 1)
                self.pending.pop()()
                if failure:
                    self.app.info.assert_called_once_with('Install failed for isolated settings:\ninvalid JSON', error=True)
                else:
                    self.app.info.assert_called_once_with('installed')
                    self.assertEqual(self.app.status['windows'], 'installed')

    def test_busy_job_refuses_a_second_operation(self):
        self.jobs.busy = True
        with patch.object(hooks.threading, 'Thread') as worker:
            self.jobs.start_job('windows', 'remove')
        worker.assert_not_called()
        self.app.info.assert_called_once()

    def test_selected_installs_preserve_runtime_and_continue_after_one_failure(self):
        self.jobs.busy = True
        with patch.object(hooks.hook_installer, 'install', side_effect=[RuntimeError('bad settings'), 'installed\nDetails']) as install, \
                patch.object(hooks.hook_installer, 'status', side_effect=['unreadable', 'installed']), \
                patch.object(hooks.hook_targets, 'describe', side_effect=lambda key: key):
            self.jobs._update_job(['mac', 'codex:mac'], {'mac': 'python', 'codex:mac': 'builtin'})
        self.assertEqual([c.args for c in install.call_args_list], [('mac', 'python'), ('codex:mac', 'builtin')])
        self.assertEqual(self.app.status, {'mac': 'unreadable', 'codex:mac': 'installed'})
        self.assertFalse(self.jobs.busy)
        self.pending.pop()()
        self.app.info.assert_called_once_with('mac: FAILED - bad settings\n\ncodex:mac: installed')

    def test_cowork_plugin_result_is_built_only_after_ui_dispatch(self):
        flow = cowork.CoworkFlow(self.app, MagicMock())
        flow.build = MagicMock()
        result = {'zip': 'isolated.zip', 'market': 'isolated-market'}
        with patch.object(cowork.hook_cowork, 'build_plugin', return_value=result):
            flow.prepare()
        flow.build.assert_not_called()
        self.pending.pop()()
        flow.build.assert_called_once_with(result)

    def test_setup_reuses_its_window_and_clears_reference_when_closed(self):
        flow = setup.SetupFlow(self.app)
        window = flow.win = MagicMock()
        with patch.object(setup.threading, 'Thread') as worker:
            flow.show()
        window.lift.assert_called_once()
        worker.assert_not_called()
        with patch.object(setup, 'mark_setup_done') as done:
            flow.close()
        done.assert_called_once()
        window.destroy.assert_called_once()
        self.assertIsNone(flow.win)


if __name__ == '__main__':
    unittest.main()
