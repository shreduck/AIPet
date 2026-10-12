import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import aipet
import aipet_agents as agents
import aipet_codex as codex
import aipet_hook as hook
from aipet_agents_window import candidate_sessions


class CodexRolloutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, CODEX_HOME=str(self.home))
        self.env.start()
        self.addCleanup(self.env.stop)
        codex._META.clear()
        codex._LOOKUPS.clear()
        agents._CODEX_META.clear()
        self.now = time.time()

    def rollout(self, day, name, meta):
        path = self.home / 'sessions/2026/10' / f'{day:02d}' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'type': 'session_meta', 'payload': meta}) + '\n')
        os.utime(path, (self.now, self.now))
        return str(path)

    def test_resumed_root_recovers_from_empty_record_and_child_hook_paths(self):
        original = self.rollout(1, 'rollout-2026-10-01T10-00-00-root.jsonl', {'id': 'root'})
        resumed = self.rollout(4, 'rollout-2026-10-04T12-00-00-root_resume-id.jsonl', {'id': 'root'})
        child = self.rollout(4, 'rollout-2026-10-04T12-01-00-child.jsonl',
                             {'id': 'child', 'parent_thread_id': 'root'})
        os.utime(original, (self.now - 100, self.now - 100))
        self.assertEqual(codex.root_rollout('root', resumed), resumed)
        self.assertEqual(hook.codex_session_transcript('root', child, resumed), resumed)
        self.assertEqual(hook.codex_session_transcript('root', child), resumed)
        self.assertEqual(aipet.transcript_file({'agent': 'codex', 'id': 'root', 'transcript_path': ''}), resumed)
        self.assertEqual(codex.root_rollout('root', original), original)  # ordinary Windows/CLI filenames still work
        misleading = self.rollout(4, 'rollout-2026-10-04T12-02-00-root_wrong.jsonl', {'id': 'unrelated'})
        self.assertEqual(codex.root_rollout('root', misleading), resumed)

    def test_names_and_nested_branches_survive_resumes_and_multiple_dates(self):
        root = self.rollout(4, 'rollout-2026-10-04T12-00-00-root_resume.jsonl', {'id': 'root'})
        for day, aid, parent, name in ((1, 'child', 'root', 'first'), (3, 'grandchild', 'child', 'second'),
                                        (4, 'greatgrandchild', 'grandchild', 'third')):
            path = self.rollout(day, f'rollout-2026-10-{day:02d}T11-00-00-{aid}.jsonl', {
                'id': aid, 'source': {'subagent': {'thread_spawn': {
                    'parent_thread_id': parent, 'agent_nickname': name, 'agent_path': '/root/' + name}}}})
            # Include turn data so the parser has normal activity alongside metadata.
            with open(path, 'a') as f:
                f.write(json.dumps({'type': 'turn_context', 'payload': {'model': 'gpt-6-luna', 'effort': 'low'}}) + '\n')
        self.rollout(4, 'rollout-2026-10-04T12-05-00-guardian.jsonl', {
            'id': 'guardian', 'parent_thread_id': 'root', 'thread_source': 'guardian_review'})
        net = agents.read_network({'agent': 'codex', 'session_id': 'root', 'transcript_path': root,
                                   'state': 'done'}, now=self.now)
        by_id = {a['id']: a for a in net['agents']}
        self.assertEqual(set(by_id), {'', 'child', 'grandchild', 'greatgrandchild'})
        self.assertEqual((by_id['child']['name'], by_id['child']['depth']), ('first (first)', 1))
        self.assertEqual((by_id['grandchild']['parent'], by_id['grandchild']['depth']), ('child', 2))
        self.assertEqual((by_id['greatgrandchild']['parent'], by_id['greatgrandchild']['depth']), ('grandchild', 3))

    def test_graph_keeps_real_root_and_excludes_ephemeral_and_internal_sessions(self):
        root = self.rollout(4, 'rollout-2026-10-04T12-00-00-root_resume.jsonl', {'id': 'root'})
        child = self.rollout(4, 'rollout-2026-10-04T12-01-00-child.jsonl',
                             {'id': 'child', 'parent_thread_id': 'root'})
        guardian = self.rollout(4, 'rollout-2026-10-04T12-02-00-review.jsonl',
                                {'id': 'review', 'thread_source': 'guardian_review'})
        def item(sid, path=''):
            return {'key': 'cc:' + sid, 'source': 'CC', 'agent': 'codex', 'session_id': sid,
                    'transcript_path': path, 'state': 'working'}
        real = item('root')  # recover a broken saved path before filtering
        other = {'key': 'cc:claude', 'source': 'CC', 'agent': 'claude', 'session_id': 'claude'}
        self.assertEqual(candidate_sessions([real, item('temporary-job'), item('child', child),
                                             item('review', guardian), other]), [real, other])
        self.assertEqual(codex.root_rollout('root'), root)

    def test_desktop_pets_and_graph_share_the_same_filter_without_hiding_alerts(self):
        import copy
        self.rollout(4, 'rollout-2026-10-04T12-00-00-root_resume.jsonl', {'id': 'root'})
        review = self.rollout(4, 'rollout-2026-10-04T12-01-00-review.jsonl',
                              {'id': 'review', 'thread_source': 'guardian_review'})
        sessions = self.home / 'pet-sessions'
        sessions.mkdir()
        records = [{'id': 'root', 'agent': 'codex', 'state': 'working'},
                   {'id': 'post-turn-job', 'agent': 'codex', 'state': 'done'},
                   {'id': 'approval', 'agent': 'codex', 'state': 'needs_input', 'request': {'id': 'test-request'}},
                   {'id': 'failure', 'agent': 'codex', 'state': 'error'},
                   {'id': 'review', 'agent': 'codex', 'state': 'needs_input', 'transcript_path': review},
                   {'id': 'claude', 'agent': 'claude', 'state': 'working'}]
        for rec in records:
            rec.update(updated=self.now, changed=self.now)
            (sessions / (rec['id'] + '.json')).write_text(json.dumps(rec))
        cfg = copy.deepcopy(aipet.DEFAULT_CONFIG)
        cfg['claude_code']['enabled'] = True
        cfg['claude_code']['extra_session_dirs'] = []
        with patch.object(aipet, 'SESSIONS_DIR', str(sessions)), \
                patch.object(aipet, 'LEGACY_SESSIONS_DIR', str(self.home / 'unused')):
            items = aipet.read_claude_code_sessions(cfg)
        self.assertEqual({i['session_id'] for i in items}, {'root', 'approval', 'failure', 'claude'})
        self.assertEqual(candidate_sessions(items), items)
        self.assertTrue((sessions / 'post-turn-job.json').exists())  # filtering does not delete hook records

