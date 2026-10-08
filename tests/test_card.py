import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

import aipet
import aipet_hook as hook


class WhitelistButtonTests(unittest.TestCase):
    def app(self):
        return object.__new__(aipet.PetApp)

    def test_hook_config_key_matches_the_hooks(self):
        self.assertEqual(aipet.auto_approve_key_for({"env": "wsl", "distro": "dev-esign-mcp"}), "wsl:dev-esign-mcp")
        self.assertEqual(aipet.auto_approve_key_for({"app": "cowork", "env": "windows"}), "cowork")
        self.assertEqual(aipet.auto_approve_key_for({"agent": "codex", "env": "wsl", "distro": "U"}), "codex:wsl:U")

    def test_exact_command_is_whitelisted_and_matches_only_itself(self):
        cmd = 'cd /x && python3 -m py_compile a.py | grep -n "Exit 0\\\\|warn"'
        data = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        req = hook.build_request(data)
        item = {"env": "wsl", "distro": "U", "request": req}
        with tempfile.TemporaryDirectory() as home, patch.object(aipet, "AUTO_APPROVE_PATH",
                                                                 os.path.join(home, "auto-approve.json")):
            line = self.app().whitelist_request(item)
            self.assertIn("exact command", line)
            with open(os.path.join(home, "auto-approve.json")) as f:
                rules = json.load(f)["targets"]["wsl:U"]
            self.assertTrue(rules["enabled"])
            self.assertEqual(rules["blacklist"], [])  # the default ".*" would have blocked it
            self.assertEqual(hook.auto_decision(rules, data), "whitelist")
            other = {"tool_name": "Bash", "tool_input": {"command": cmd + "; rm -rf ~"}}
            self.assertIsNone(hook.auto_decision(rules, other))

    def test_mcp_tool_is_whitelisted_by_name(self):
        data = {"tool_name": "mcp__wb__search", "tool_input": {"q2": "x", "n": 3}}
        item = {"env": "windows", "request": hook.build_request(data)}
        with tempfile.TemporaryDirectory() as home, patch.object(aipet, "AUTO_APPROVE_PATH",
                                                                 os.path.join(home, "auto-approve.json")):
            self.assertIn("every mcp__wb__search", self.app().whitelist_request(item))
            rules = aipet.auto_approve_rules()[aipet.auto_approve_key_for(item)]
            self.assertEqual(hook.auto_decision(rules, data), "whitelist")

    def test_card_scale_is_clamped(self):
        self.assertEqual(aipet.clamp_card_scale(3), 1.5)
        self.assertEqual(aipet.clamp_card_scale(0.1), 0.5)
        self.assertEqual(aipet.clamp_card_scale("x"), 1.0)


if __name__ == "__main__":
    unittest.main()
