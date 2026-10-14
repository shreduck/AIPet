"""Shared isolated Claude/Codex hook lifecycle and answer protocol checks."""
import json
import os
from pathlib import Path
import subprocess


def check_hook(executable, base, interpreter=None):
    executable = Path(executable).resolve()
    prefix = ([interpreter] if isinstance(interpreter, str) else list(interpreter or []))
    for agent in ('claude', 'codex'):
        config = base / agent
        config.mkdir(parents=True)
        (config / 'debug-events').touch()
        (config / 'no-answers').touch()  # observe synthetic prompts; never approve anything
        record = config / 'sessions' / 'packaging-check.json'
        env = dict(os.environ, AIPET_DIR=str(config), HOME=str(config), USERPROFILE=str(config), CODEX_HOME=str(config / 'codex'), PYTHONPATH='')
        for name in ('CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_PROJECT_DIR', 'CLAUDE_PET_DIR'):
            env.pop(name, None)

        def event(name, **fields):
            data = dict(session_id='packaging-check', hook_event_name=name, cwd=str(config), **fields)
            command = prefix + [str(executable)] + (['--codex'] if agent == 'codex' else [])
            result = subprocess.run(command, input=json.dumps(data), text=True, capture_output=True,
                                    env=env, cwd=config, timeout=15)
            if result.returncode or result.stderr:
                raise RuntimeError(f'{agent} {name}: {result.returncode} {result.stderr}')
            return json.loads(record.read_text()) if record.exists() else None

        assert event('SessionStart')['state'] == 'idle'
        assert event('UserPromptSubmit', prompt='Packaging test')['state'] == 'working'
        if agent == 'codex':
            rollout = config / 'rollout-2026-10-12T12-00-00-packaging-check_resumed.jsonl'
            rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'packaging-check'}}) + '\n')
            child = config / 'rollout-2026-10-12T12-01-00-child.jsonl'
            child.write_text(json.dumps({'type': 'session_meta', 'payload': {
                'id': 'child', 'parent_thread_id': 'packaging-check'}}) + '\n')
            assert event('UserPromptSubmit', prompt='Resume check', transcript_path=str(rollout))['transcript_path'] == str(rollout)
            assert event('PostToolUse', tool_name='Test', agent_id='child', transcript_path=str(child))['transcript_path'] == str(rollout)
            assert event('SubagentStop', agent_id='child', transcript_path=str(child))['transcript_path'] == str(rollout)
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

