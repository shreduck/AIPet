"""Installer contracts exercised against temporary configuration and deployment directories."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from aipetlib.integrations import backups, commands, deployment, discovery, installer, locations, protocol, settings, targets


class InstallerContracts(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(support.installation())

    def test_install_is_idempotent_preserves_other_hooks_and_restores_exact_bytes(self):
        original = b'{\r\n "permissions": {"allow": ["Read"]}, "hooks": {"Stop": [{"hooks": [{"command": "other-hook"}]}]}\r\n}\r\n'
        target = Path(locations.WINDOWS_SETTINGS)
        target.parent.mkdir(parents=True)
        target.write_bytes(original)
        with patch.object(deployment, 'deploy_files'), patch.object(commands, 'windows_hook_command', return_value='aipet-hook'):
            installer.install('windows')
            installed = json.loads(target.read_bytes())
            self.assertEqual(installed['permissions'], {'allow': ['Read']})
            self.assertEqual(installed['hooks']['Stop'][0]['hooks'][0]['command'], 'other-hook')
            installer.install('windows')
        saved = backups.list_backups('windows')
        self.assertEqual(len(saved), 1)
        self.assertEqual(Path(saved[0]['path']).read_bytes(), original)
        backups.restore_backup('windows', saved[0]['path'])
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(len(backups.list_backups('windows')), 2)

    def test_invalid_configuration_is_never_overwritten_or_backed_up_as_changed(self):
        target = Path(locations.WINDOWS_SETTINGS)
        target.parent.mkdir(parents=True)
        target.write_bytes(b'not json')
        with patch.object(deployment, 'deploy_files'), patch.object(commands, 'windows_hook_command', return_value='aipet-hook'):
            with self.assertRaises(json.JSONDecodeError):
                installer.install('windows')
        self.assertEqual(target.read_bytes(), b'not json')
        self.assertEqual(backups.list_backups('windows'), [])

    def test_absent_file_backup_restores_absence_and_keeps_undo_backup(self):
        with patch.object(deployment, 'deploy_files'), patch.object(commands, 'windows_hook_command', return_value='aipet-hook'):
            installer.install('windows')
        saved = backups.list_backups('windows')[0]
        self.assertTrue(saved['absent'])
        backups.restore_backup('windows', saved['path'])
        self.assertFalse(Path(locations.WINDOWS_SETTINGS).exists())
        self.assertEqual(len(backups.list_backups('windows')), 2)

    def test_codex_home_events_timeouts_and_trust_message_are_preserved(self):
        with patch.object(deployment, 'deploy_files'), patch.object(commands, 'codex_windows_command', return_value='aipet-hook --codex'):
            message = installer.install('codex:windows')
        cfg = json.loads((self.root / '.codex/hooks.json').read_bytes())
        self.assertIn('/hooks', message)
        self.assertEqual(set(cfg['hooks']), {event for event, _ in protocol.CODEX_HOOK_EVENTS})
        for event, groups in cfg['hooks'].items():
            self.assertEqual(groups[0]['hooks'][0]['timeout'], protocol.CODEX_HOOK_TIMEOUTS[event])

    def test_source_and_frozen_resources_resolve_from_the_original_roots(self):
        self.assertEqual(Path(locations.resource_path('aipet_hook.py')), Path(__file__).resolve().parents[1] / 'aipet_hook.py')
        with patch.object(locations.sys, '_MEIPASS', str(self.root / 'bundle'), create=True):
            self.assertEqual(locations.resource_path('hook'), str(self.root / 'bundle/hook'))

    def test_wsl_command_quotes_mapped_paths_and_stopped_distro_is_not_probed(self):
        with patch.object(targets, '_wslpath', side_effect=['/mnt/c/Pet Home', "/mnt/c/Pet Home/bin/aipet_hook.py"]):
            self.assertEqual(commands.wsl_hook_command('Ubuntu'), "AIPET_DIR='/mnt/c/Pet Home' python3 '/mnt/c/Pet Home/bin/aipet_hook.py'")
        with patch.object(discovery, 'IS_MAC', False), patch.object(discovery.os, 'name', 'posix'), \
                patch.object(targets, 'list_distros', return_value=[('Ubuntu', 'Stopped')]), \
                patch.object(discovery, 'probe_wsl') as probe:
            found, hidden = discovery.detect_targets()
        probe.assert_not_called()
        self.assertEqual(hidden, 0)
        self.assertEqual([(t['key'], t['kind']) for t in found], [('wsl:Ubuntu', 'unknown')])


if __name__ == '__main__':
    unittest.main()
