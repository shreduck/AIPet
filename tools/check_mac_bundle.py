"""Exercise the packaged Mac hooks, their standalone deployment, and ZIP relocation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hooks_installer as hi


def check_hook(executable, base):
    for agent in ('claude', 'codex'):
        config = base / agent
        config.mkdir(parents=True)
        (config / 'debug-events').touch()
        (config / 'no-answers').touch()  # observe synthetic prompts; never approve anything
        record = config / 'sessions' / 'packaging-check.json'
        env = dict(os.environ, AIPET_DIR=str(config))

        def event(name, **fields):
            data = dict(session_id='packaging-check', hook_event_name=name, cwd=str(config), **fields)
            command = [str(executable)] + (['--codex'] if agent == 'codex' else [])
            result = subprocess.run(command, input=json.dumps(data), text=True, capture_output=True,
                                    env=env, timeout=15)
            if result.returncode or result.stderr:
                raise RuntimeError(f'{agent} {name}: {result.returncode} {result.stderr}')
            return json.loads(record.read_text()) if record.exists() else None

        assert event('SessionStart')['state'] == 'idle'
        assert event('UserPromptSubmit', prompt='Packaging test')['state'] == 'working'
        if agent == 'claude':
            question = {'questions': [{'question': 'Test question?', 'options': [{'label': 'Test'}]}]}
            assert event('PermissionRequest', tool_name='AskUserQuestion', tool_input=question)['state'] == 'needs_input'
            answered = event('PostToolUse', tool_name='AskUserQuestion', tool_use_id='question-1',
                             tool_input=dict(question, answers={'Test question?': 'Test'}))
        else:
            question = {'questions': [{'title': 'Test question?', 'options': ['Test']}]}
            assert event('PostToolUse', tool_name='request_user_input_async', tool_input=question,
                         tool_use_id='question-1', tool_response={'accepted': True})['state'] == 'needs_input'
            assert event('PostToolUse', tool_name='clocksleep', tool_input={'duration_ms': 1})['state'] == 'needs_input'
            reply = '<send_user_message_question_reply>\n' + json.dumps([
                {'questionItemId': '["request_user_input_async","question-1",0]',
                 'question': 'Test question?', 'answer': 'Test'}]) + '\n</send_user_message_question_reply>'
            answered = event('UserPromptSubmit', prompt=reply)
            completed = event('PostToolUse', tool_name='request_user_input', tool_input=question,
                              tool_use_id='question-2', tool_response={'answers': {'Test question?': 'Test'}})
            assert completed['state'] == 'working' and not completed['requests'], completed
        assert answered['state'] == 'working' and not answered['requests'], answered
        assert event('Stop')['state'] == 'done'
        assert event('SessionEnd') is None
        rows = [json.loads(line) for line in (config / 'events.log').read_text().splitlines()]
        assert any(row['prev'] == 'needs_input' and row['new'] == 'working' for row in rows)
        print(f'  {agent}: lifecycle, question answered in AI UI, and debug logging passed')


def check_bundle(bundle):
    bundle = Path(bundle).resolve()
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    with tempfile.TemporaryDirectory(prefix='aipet-package-check-') as directory:
        root = Path(directory)
        archive = root / 'app.zip'
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--keepParent', str(bundle), str(archive)], check=True)
        relocated = root / 'unpacked elsewhere'
        subprocess.run(['/usr/bin/ditto', '-x', '-k', str(archive), str(relocated)], check=True)
        restored = relocated / bundle.name
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(restored)], check=True)
        assets_report = root / 'assets.json'
        subprocess.run([str(restored / 'Contents/MacOS/AIPet'), '--selftest-assets', str(assets_report)],
                       check=True, timeout=30)
        assets = json.loads(assets_report.read_text())
        assert assets['ok'], assets
        print(f"  Robot assets: PNG loading and {assets['faces']} rendered faces passed")
        resources = restored / 'Contents/Resources'
        check_hook(resources / 'hook/aipet-hook', root / 'bundled-check')
        deployed = root / 'standalone-hook'
        with patch.object(hi, 'resource_path', lambda *p: str(resources.joinpath(*p))), \
                patch.object(hi, 'INSTALL_DIR', str(deployed)), patch.object(hi, 'PET_DIR', str(root / 'config')):
            hi.deploy_files()
        assert not any(p.is_symlink() for p in deployed.rglob('*')), 'Deployment still depends on bundle links'
        # Make every original bundle path unavailable before exercising the installed copy.
        restored.rename(relocated / 'moved.app')
        check_hook(deployed / 'hook/aipet-hook', root / 'deployed-check')
        size = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file() and not p.is_symlink())
        print(f'Verified ZIP relocation and independent deployment: app {size / 2**20:.2f} MiB, ZIP {archive.stat().st_size / 2**20:.2f} MiB')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    check_bundle(parser.parse_args().bundle)
