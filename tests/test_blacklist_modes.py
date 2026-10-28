"""Timed blacklist decisions agree between the hook and the displayed prompt."""
import os
import tempfile
import unittest
from unittest.mock import patch

from aipetlib import permissions
from aipet_hook_runtime import approvals
import support


class BlacklistModesTests(unittest.TestCase):
    def test_precedence_agrees_for_normal_hook_timed_hook_and_displayed_prompt(self):
        data = dict(tool_name='Bash', tool_input=dict(command='danger'))
        for precedence in ('blacklist', 'whitelist', 'unknown'):
            rules = dict(permissions.default_auto_rules(), whitelist=['danger'], blacklist=['danger'], precedence=precedence)
            permissions.save_auto_approve(self.key, rules)
            white = precedence == 'whitelist'
            self.assertEqual(permissions.auto_approve_rules()[self.key]['precedence'], precedence)
            self.assertEqual(approvals.auto_decision(rules, data), 'whitelist' if white else None)
            for reject in (False, True):
                permissions.set_timed_grant('s', 10, respect_blacklist=True, reject_blacklist=reject)
                self.assertEqual(permissions.blacklist_blocked(self.item), not white)
                self.assertEqual(permissions.timed_behavior(self.item), 'allow' if white else 'deny' if reject else None)
                self.assertEqual(approvals.timed_decision(self.folder, 's', key=self.key, data=data),
                                 'timed' if white else 'blacklist' if reject else 'blocked')

    def test_white_first_still_blocks_nonwhitelisted_requests_and_ignores_bad_whitelist(self):
        for white in (['safe'], ['['], ['# danger'], []):
            rules = dict(permissions.default_auto_rules(), precedence='whitelist', whitelist=white, blacklist=['danger'])
            permissions.save_auto_approve(self.key, rules)
            permissions.set_timed_grant('s', 10, reject_blacklist=True)
            self.assertEqual(permissions.timed_behavior(self.item), 'deny')
            self.assertIsNone(approvals.auto_decision(rules, dict(tool_name='Bash', tool_input=dict(command='danger'))))

    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(support.paths(AUTO_APPROVE_PATH=os.path.join(self.folder, 'auto-approve.json')))
        self.enterContext(patch.object(approvals.hook_waiting, 'pet_alive', return_value=True))
        self.item = dict(session_id='s', request=dict(tool='Bash', subject='danger', detail='danger'))
        self.key = permissions.auto_approve_key_for(self.item)
        permissions.save_auto_approve(self.key, dict(permissions.default_auto_rules(), blacklist=['danger']))

    def test_modes_pause_reject_or_allow_with_matching_rules(self):
        for respect, reject, behavior, hook in ((False, False, 'allow', 'timed'),
                                               (True, False, None, 'blocked'),
                                               (True, True, 'deny', 'blacklist')):
            with self.subTest(respect=respect, reject=reject):
                permissions.set_timed_grant('s', 10, respect_blacklist=respect, reject_blacklist=reject)
                self.assertEqual(permissions.timed_behavior(self.item), behavior)
                self.assertEqual(permissions.blacklist_blocked(self.item), respect)
                self.assertEqual(approvals.timed_decision(self.folder, 's', key=self.key,
                    data=dict(tool_name='Bash', tool_input=dict(command='danger'))), hook)
                safe = dict(self.item, request=dict(tool='Bash', subject='safe'))
                self.assertEqual(permissions.timed_behavior(safe), 'allow')

    def test_missing_or_invalid_rules_are_not_implicitly_approved(self):
        for black in (None, ['['], ['.*']):
            permissions.save_auto_approve(self.key, dict(blacklist=black))
            for reject in (False, True):
                permissions.set_timed_grant('s', 10, respect_blacklist=True, reject_blacklist=reject)
                self.assertTrue(permissions.blacklist_blocked(self.item))
                self.assertEqual(permissions.timed_behavior(self.item), 'deny' if reject else None)

    def test_questions_are_never_automatically_answered_or_marked_blacklisted(self):
        permissions.set_timed_grant('s', 10, reject_blacklist=True)
        item = dict(self.item, request=dict(kind='question', tool='AskUserQuestion'))
        self.assertFalse(permissions.blacklist_blocked(item))
        self.assertIsNone(permissions.timed_behavior(item))


if __name__ == '__main__':
    unittest.main()
