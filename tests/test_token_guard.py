"""Confidence tiers, bounded Base64 decoding, and redaction of credential alerts."""
import base64
import json
import unittest
from unittest.mock import MagicMock, patch

from aipet_token_guard import findings, on_event, detect, RULES, PATTERNS


def encode(value):
    return base64.b64encode(value.encode('utf-8')).decode('ascii')


class TokenGuardTests(unittest.TestCase):
    def test_large_non_encoded_payload_does_not_hold_the_interpreter_for_seconds(self):
        import subprocess
        import sys
        from pathlib import Path
        result = subprocess.run([sys.executable, '-c',
            'from aipet_token_guard import detect; assert detect(chr(90)*262144) == []; print("bounded scan passed")'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=4)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('bounded scan passed', result.stdout)

    def test_new_contextual_credential_rules_and_inline_spans(self):
        cases = [('credentials are admin:admin', 'credential.basic_plain', 'admin:admin'),
                 ('postgres://demo:short@db.invalid/example', 'credential.uri', 'demo:short'),
                 ('curl -u demo:short https://example.invalid', 'credential.cli_user', 'demo:short'),
                 ('tool --password short', 'credential.cli_secret', 'short'),
                 ('mysql -uroot -pshort', 'credential.mysql_flag', 'short'),
                 ('password="short"', 'credential.password', 'short'),
                 ('Cookie: theme=dark; sessionid=fixture-session-value', 'credential.session_cookie', 'fixture-session-value')]
        for text, rule, secret in cases:
            with self.subTest(rule=rule):
                reports = detect({'prompt': text})
                report = next(report for report in reports if report['rule'] == rule)
                self.assertEqual(report['warning_level'], 'high')
                self.assertEqual(report['value'], secret)
                self.assertTrue(any(text[start:end] == secret for start, end in report['hook_spans']))

    def test_provider_formats_include_temporary_aws_stripe_and_google(self):
        for token in ('ASIA' + 'A1' * 8, 'sk_live_' + 'Synthetic123456789' * 2,
                      'rk_test_' + 'Synthetic123456789' * 2, 'AIza' + 'Ab1' * 11 + 'Cd'):
            self.assertTrue(any(report['rule'] == 'token.known_prefix' for report in detect(token)))
        self.assertEqual(findings('pk_live_' + 'Synthetic123456789' * 2), [])

    def test_url_and_nested_base64_decoding_are_bounded_and_structural(self):
        from urllib.parse import quote
        for value, rule in ((quote('demo:short', safe=''), 'encoding.url'),
                            (encode(encode('demo:short')), 'encoding.layered'),
                            (encode(quote('demo:short', safe='')), 'encoding.layered')):
            report = next(report for report in detect(value) if report['rule'] == rule)
            self.assertEqual(report['decoded'], 'demo:short')
            self.assertEqual(report['warning_level'], 'high')
        value = 'demo:short'
        for _ in range(4):
            value = encode(value)
        self.assertEqual(findings(value), [])

    def test_contextual_entropy_requires_sensitive_name_and_randomness(self):
        secret = 'R8mQ2vK9pL4zT7xN'
        for data in ('service_auth := "' + secret + '"', {'service_auth': secret}):
            self.assertTrue(any(report['rule'] == 'token.entropy_context' for report in detect(data)))
        for data in (secret, {'object_id': secret}, {'auth_id': secret}, {'authId': secret}, {'tokenUUID': secret}, {'input_tokens': secret},
                     {'service_auth': 'aaaaaaaaaaaaaaaaaaaa'}):
            self.assertEqual(findings(data), [])
        report, = detect('service_auth = ' + 'abcdef0123456789' * 4)
        self.assertEqual(report['warning_level'], 'standard', 'credential context should upgrade a bare low-confidence hash')

    def test_negative_contexts_and_placeholders_do_not_get_strong_credential_alerts(self):
        for text in ('https://example.invalid/path', 'postgres://user@db.invalid/name',
                     'curl --user ${USER}:${PASSWORD}', 'tool --password ${PASSWORD}',
                     'Cookie: theme=dark; analytics=fixture-analytics-value',
                     encode('{"password":"${MY_PASSWORD}"}'), encode('Cookie: sessionid=${SESSION_TOKEN}')):
            with self.subTest(context=text[:12]):
                self.assertEqual(findings(text), [])
        for placeholder in ('$PASSWORD', '${PASSWORD}', '%PASSWORD%', '{{PASSWORD}}'):
            self.assertEqual(findings({'password': placeholder}), [])
        report, = detect({'password': 'short'})
        self.assertEqual(report['rule'], 'credential.password_field')
        report, = detect({'password': 'sk_live_' + 'Synthetic123456789' * 2})
        self.assertEqual(report['warning_level'], 'high', 'a weaker token match must not downgrade an explicit password field')

    def test_full_hook_text_retains_surroundings_and_masks_all_detected_spans(self):
        first, second = 'ghp_' + 'SyntheticToken1234' * 2, 'abcdef0123456789' * 4
        text = f'Unicode café 🐾 before {first} between {second} after.'
        reports = detect({'prompt': text})
        self.assertEqual(len(reports), 2)
        for report in reports:
            self.assertEqual(report['hook_text'], text)
            self.assertEqual(report['hook_field'], 'prompt')
            self.assertEqual([text[start:end] for start, end in report['hook_spans']], [first, second])

    def test_overlapping_hash_and_assignment_rules_only_mask_the_value_once(self):
        secret = 'abcdef0123456789' * 4
        report, = detect({'prompt': 'my token is ' + secret + ' thanks'})
        self.assertEqual(report['hook_spans'], [[12, 12 + len(secret)]])

    def test_private_key_body_is_masked_along_with_header(self):
        block = '-----BEGIN PRIVATE KEY-----\nSyntheticBody0123456789\n-----END PRIVATE KEY-----'
        text = 'before\n' + block + '\nafter'
        report, = detect({'prompt': text})
        self.assertEqual(report['hook_spans'], [[7, 7 + len(block)]])
    def test_rules_have_unique_stable_names_levels_and_explanations(self):
        self.assertEqual(len({rule[0] for rule in RULES.values()}), len(RULES))
        for kind, _ in PATTERNS:
            self.assertIn(kind, RULES)
        for rule, name, level, explanation in RULES.values():
            self.assertTrue(rule and name and explanation)
            self.assertIn(level, ('low', 'standard', 'high'))

    def test_report_includes_conversation_harness_and_triggering_event_context(self):
        api = MagicMock()
        event = dict(agent='codex', session_id='session-test', event='UserPromptSubmit', id='event-test',
            timestamp=1234567890, data=dict(prompt='abcdef0123456789' * 4, cwd='/project', model='model-test'))
        context = dict(session=dict(conversation_title='Conversation fixture', harness='Codex',
                                    environment='Windows', project='Project fixture'))
        with patch('aipet_token_guard.AIPet', return_value=api):
            on_event(event, context)
        report = api.warning.call_args.kwargs['report']
        self.assertEqual(report['context']['conversation_title'], 'Conversation fixture')
        self.assertEqual(report['context']['harness'], 'Codex')
        self.assertEqual(report['context']['hook_event'], 'UserPromptSubmit')
        self.assertEqual(report['context']['session_id'], 'session-test')
        self.assertEqual(report['context']['model'], 'model-test')
        self.assertNotIn('prompt', report['context'])

    def test_large_report_value_is_explicitly_truncated_and_summary_stays_redacted(self):
        value = 'opaque-' + 'x' * 5000
        report, = detect({'token': value})
        self.assertTrue(report['truncated'])
        self.assertEqual(len(report['value']), 4096)
        self.assertNotIn(value, repr(findings({'token': value})))

    def notification(self, text):
        api = MagicMock()
        with patch('aipet_token_guard.AIPet', return_value=api):
            on_event({'data': {'prompt': text}}, {})
        return api

    def test_standalone_hash_lengths_raise_low_warnings_with_no_payload_excerpt(self):
        for length in (32, 40, 64, 96, 128):
            secret = ('abcdef0123456789' * 8)[:length]
            api = self.notification('Possible hash ' + secret)
            api.warning.assert_called_once()
            api.error.assert_not_called()
            self.assertIn('Low warning', api.warning.call_args.args[0])
            self.assertNotIn(secret, repr(api.warning.call_args.args))
            self.assertEqual(api.warning.call_args.kwargs['report']['value'], secret)
            self.assertEqual(api.warning.call_args.kwargs['report']['rule'], 'hash.hex')
        self.assertEqual(findings('a' * 31), [])
        self.assertEqual(findings('a' * 129), [])

    def test_base64_credential_pairs_are_high_and_both_encoded_and_decoded_values_are_hidden(self):
        credentials = 'fixture-user:fixture-password'
        encoded = encode(credentials)
        api = self.notification('basic=' + encoded)
        api.error.assert_called_once()
        self.assertIn('High warning', api.error.call_args.args[0])
        self.assertNotIn(encoded, repr(api.error.call_args.args))
        self.assertNotIn(credentials, repr(api.error.call_args.args))
        report = api.error.call_args.kwargs['report']
        self.assertEqual(report['value'], encoded)
        self.assertEqual(report['decoded'], credentials)
        self.assertEqual(report['rule'], 'base64.credentials')
        self.assertTrue(report['explanation'])

    def test_short_basic_credentials_and_unpadded_values_are_detected(self):
        for credentials in ('admin:admin', 'a:b', 'user:password'):
            for encoded in (encode(credentials), encode(credentials).rstrip('=')):
                self.assertTrue(any(kind == 'Base64 credentials' for kind, _ in findings('basic=' + encoded)))

    def test_encoded_token_structures_and_nested_credential_json_are_high(self):
        for decoded in ('ghp_' + 'SyntheticToken1234' * 2, 'Bearer ' + 'opaqueToken1234' * 3,
                        'abcdef0123456789' * 4,
                        json.dumps({'credentials': {'username': 'test', 'password': 'short'}})):
            with self.subTest(decoded_type=decoded[:3]):
                api = self.notification(encode(decoded))
                api.error.assert_called_once()

    def test_urlsafe_base64_is_supported(self):
        encoded = base64.urlsafe_b64encode('üser:pässword'.encode('utf-8')).decode('ascii').rstrip('=')
        self.notification(encoded).error.assert_called_once()

    def test_benign_base64_binary_urls_placeholders_and_token_counters_do_not_alert(self):
        for decoded in ('Hello world', 'https://example.com', json.dumps({'input_tokens': 2000}),
                        json.dumps({'token': '${MY_TOKEN}'}), '\x00binary:data'):
            self.assertEqual(findings(encode(decoded)), [])
        self.assertEqual(findings({'input_tokens': 20000, 'text': 'normal code', 'token': '${MY_TOKEN}'}), [])

    def test_bearer_assignment_and_token_prose_are_recognized(self):
        for text in ('BEARER=' + 'opaqueToken1234' * 3, 'my token is ' + 'opaqueToken1234' * 3):
            self.notification(text).warning.assert_called_once()

    def test_mixed_levels_stay_separate_and_hashes_do_not_hide_high_findings(self):
        text = ' '.join(f'{i:064x}' for i in range(20)) + ' basic=' + encode('fixture:password')
        matches = findings(text)
        self.assertEqual(len(matches), 10)
        self.assertEqual(matches[0][0], 'Base64 credentials')
        api = self.notification(text)
        api.error.assert_called_once()
        self.assertEqual(api.warning.call_count, 9)

    def test_invalid_or_oversized_base64_does_not_crash(self):
        for text in ('basic=!!!not-base64!!!', 'A' * 10000, encode('[' * 2000)):
            self.assertEqual(findings(text), [])


if __name__ == '__main__':
    unittest.main()
