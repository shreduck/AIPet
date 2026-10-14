"""Standalone hook and source-upgrade contracts, entirely inside temporary homes."""
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import support
from aipetlib.integrations import deployment, locations
from aipet_hook_runtime import cli, capture, config, hosts, io, sessions


class HookRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(support.installation())
        deployment.deploy_files()
        self.entry = Path(locations.INSTALL_DIR) / 'aipet_hook.py'
        self.data = self.root / 'hook-data'
        self.data.mkdir()
        self.env = dict(os.environ, AIPET_DIR=str(self.data), HOME=str(self.root), USERPROFILE=str(self.root),
                        CODEX_HOME=str(self.root / 'codex'), PYTHONPATH='')
        for name in ('CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_PROJECT_DIR', 'CLAUDE_PET_DIR'):
            self.env.pop(name, None)

    def command(self, agent='claude'):
        return [sys.executable, '-S', str(self.entry)] + (['--codex'] if agent == 'codex' else [])

    def event(self, event, agent='claude', **fields):
        payload = dict(session_id='isolated', hook_event_name=event, cwd=str(self.root), **fields)
        result = subprocess.run(self.command(agent), input=json.dumps(payload), text=True, capture_output=True,
                                cwd=self.root, env=self.env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        path = self.data / 'sessions/isolated.json'
        return result.stdout, json.loads(path.read_bytes()) if path.exists() else None

    def test_copied_runtime_lifecycle_has_no_application_or_site_dependencies(self):
        for agent in ('claude', 'codex'):
            with self.subTest(agent=agent):
                for event, state in [('SessionStart', 'idle'), ('UserPromptSubmit', 'working'), ('Stop', 'done')]:
                    out, record = self.event(event, agent, prompt='fixture')
                    self.assertEqual(out, '')
                    self.assertEqual(record['state'], state)
                    self.assertEqual(record['agent'], agent)
                self.assertEqual(self.event('SessionEnd', agent), ('', None))
        inspected = subprocess.run([sys.executable, '-S', '-c',
                                    'import aipet_hook_runtime.cli,sys; print(any(n == "aipetlib" or n.startswith("aipetlib.") or n.split(".")[0] in ("tkinter","PIL","pystray") for n in sys.modules))'],
                                   cwd=self.entry.parent, env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(inspected.stdout.strip(), 'False')
        self.assertFalse((self.entry.parent / 'aipetlib').exists())

    def test_optional_usage_helpers_can_be_absent(self):
        for name in ('aipet_usage.py', 'aipet_claude_usage.py'):
            (self.entry.parent / name).unlink()
        out, record = self.event('UserPromptSubmit', prompt='fixture')
        self.assertEqual(out, '')
        self.assertEqual(record['state'], 'working')

    def test_wsl_override_keeps_events_in_the_supplied_shared_folder(self):
        self.env['WSL_DISTRO_NAME'] = 'Fixture Distro'
        out, record = self.event('UserPromptSubmit', prompt='fixture')
        self.assertEqual(out, '')
        self.assertEqual((record['env'], record['distro']), ('wsl', 'Fixture Distro'))
        self.assertTrue((self.data / 'sessions/isolated.json').exists())

    def test_usage_worker_relaunches_the_stable_entry_not_the_cli_module(self):
        from unittest.mock import MagicMock
        optional = MagicMock()
        payload = dict(session_id='isolated', hook_event_name='UserPromptSubmit')
        with patch.object(sys,'argv',['aipet_hook.py']), patch.object(config,'AGENT','claude'), \
                patch.object(io,'read_stdin',return_value=json.dumps(payload)), \
                patch.object(hosts,'is_wsl',return_value=False), \
                patch.object(hosts,'sessions_dir',return_value=str(self.data / 'sessions')), \
                patch.object(capture,'claude_usage',optional), \
                patch.object(capture.usage,'save'), patch.object(capture.usage,'normalize',return_value=[]), \
                patch.object(sessions,'_update_session'):
            cli.main()
        optional.schedule.assert_called_once_with(str(self.data / 'sessions'),'isolated',
                                                   [sys.executable,str(Path(__file__).resolve().parents[1] / 'aipet_hook.py')])

    def test_timed_approval_is_an_explicit_decision_and_never_answers_questions(self):
        (self.data / 'alive').touch()
        (self.data / 'auto-approve.json').write_text(json.dumps({'timed': {'isolated': {'until': time.time()+60}}}))
        out, record = self.event('PermissionRequest', tool_name='Bash', tool_input={'command': 'echo isolated fixture'})
        self.assertEqual(json.loads(out)['hookSpecificOutput']['decision']['behavior'], 'allow')
        self.assertEqual((record['state'], record['auto_kind']), ('working', 'timed'))
        (self.data / 'no-answers').touch()
        out, record = self.event('PermissionRequest', tool_name='AskUserQuestion', tool_input={
            'questions': [{'question': 'Which?', 'options': [{'label': 'A'}]}]})
        self.assertEqual(out, '')
        self.assertEqual(record['state'], 'needs_input')

    def test_manual_question_answer_reaches_stdout_and_cleans_wait_marker(self):
        (self.data / 'alive').touch()
        (self.data / 'answer-wait').write_text('5')
        payload = dict(session_id='isolated', hook_event_name='PermissionRequest', cwd=str(self.root),
                       tool_name='AskUserQuestion', tool_input={'questions': [
                           {'question': 'Which?', 'multiSelect': True, 'options': [{'label': 'A'}, {'label': 'B'}]}]})
        proc = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=self.root, env=self.env)
        try:
            proc.stdin.write(json.dumps(payload))
            proc.stdin.close()
            proc.stdin = None
            marker = None
            deadline = time.monotonic()+10
            while time.monotonic() < deadline and proc.poll() is None:
                markers = list((self.data / 'answers').glob('*.waiting'))
                if markers:
                    marker = markers[0]
                    break
                time.sleep(0.02)
            self.assertIsNotNone(marker, 'Hook never published its wait marker')
            answer = marker.with_suffix('.json')
            temporary = answer.with_suffix('.json.tmp')
            temporary.write_text(json.dumps({'behavior': 'allow', 'answers': {'Which?': ['A', 'B']}}))
            os.replace(temporary, answer)
            out, err = proc.communicate(timeout=10)
            self.assertEqual((proc.returncode, err), (0, ''))
            decision = json.loads(out)['hookSpecificOutput']['decision']
            self.assertEqual(decision['updatedInput']['answers'], {'Which?': 'A, B'})
            self.assertFalse(marker.exists())
            record = json.loads((self.data / 'sessions/isolated.json').read_bytes())
            self.assertEqual(record['state'], 'working')
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)

    def test_bad_input_exits_zero_without_approval_output(self):
        for payload in ('not JSON', '[1,2]'):
            result = subprocess.run(self.command(), input=payload, text=True, capture_output=True,
                                    cwd=self.root, env=self.env, timeout=15)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_package_is_deployed_before_entry_and_does_not_ship_bytecode(self):
        copied = []
        original = deployment._sync_file
        with patch.object(deployment, '_sync_file', side_effect=lambda src, dst: (copied.append(dst), original(src, dst))[-1]):
            deployment.deploy_files()
        self.assertEqual(copied[-1], str(self.entry))
        runtime = self.entry.parent / 'aipet_hook_runtime'
        expected = {p.relative_to(Path(locations.resource_path('aipet_hook_runtime'))) for p in Path(locations.resource_path('aipet_hook_runtime')).rglob('*.py')}
        self.assertEqual({p.relative_to(runtime) for p in runtime.rglob('*.py')}, expected)
        self.assertFalse(list(runtime.rglob('*.pyc')))

    def test_failed_runtime_update_keeps_the_old_entry(self):
        self.entry.write_text('# old entry must survive\n')
        old_entry = self.entry.read_bytes()
        failed = self.entry.parent / 'aipet_hook_runtime/cli.py'
        failed.write_text('# old incompatible runtime\n')
        original = deployment._sync_file
        with patch.object(deployment, '_sync_file', side_effect=lambda src,dst: None if dst==str(failed) else original(src,dst)):
            with self.assertRaisesRegex(RuntimeError, 'entry script was kept'):
                deployment.deploy_files()
        self.assertEqual(self.entry.read_bytes(), old_entry)

    def test_same_size_same_timestamp_update_invalidates_cached_bytecode(self):
        source, dest = self.root / 'new.py', self.entry.parent / 'cache_probe.py'
        source.write_text('VALUE = 2\n')
        dest.write_text('VALUE = 1\n')
        stamp = int(time.time())
        os.utime(dest, (stamp,stamp))
        cache = Path(py_compile.compile(str(dest), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
        self.assertTrue(cache.exists())
        deployment._sync_file(str(source),str(dest))
        os.utime(dest, (stamp,stamp))  # deliberately reproduce a valid-looking timestamp/size cache
        self.assertFalse(cache.exists())
        result = subprocess.run([sys.executable,'-S','-c','import cache_probe; print(cache_probe.VALUE)'],
                                cwd=dest.parent,env=self.env,text=True,capture_output=True,timeout=15)
        self.assertEqual((result.returncode,result.stdout.strip()),(0,'2'),result.stderr)

    def test_refresh_is_idle_until_installed_and_prunes_obsolete_runtime_sources(self):
        unrelated = self.entry.parent / 'keep.txt'
        unrelated.write_text('keep')
        obsolete = self.entry.parent / 'aipet_hook_runtime/obsolete.py'
        obsolete.write_text('VALUE=1\n')
        cache = Path(py_compile.compile(str(obsolete), doraise=True))
        before = self.entry.stat().st_mtime_ns
        deployment.deploy_files(only_if_deployed=True)
        self.assertEqual(self.entry.stat().st_mtime_ns,before)
        self.assertFalse(obsolete.exists())
        self.assertFalse(cache.exists())
        self.assertEqual(unrelated.read_text(),'keep')
        missing = self.root / 'not-installed'
        with patch.object(locations,'INSTALL_DIR',str(missing)):
            deployment.deploy_files(only_if_deployed=True)
        self.assertFalse(missing.exists())


if __name__=='__main__':
    unittest.main()
