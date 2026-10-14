"""The first-run setup window: neutral wording, Claude Code and Codex installs in their own sections."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class SetupWindowTests(unittest.TestCase):
    def build(self, targets, hidden=0):
        from aipetlib.app import setup
        made = []

        def widget(kind):
            def make(*args, **kw):
                w = MagicMock(name=kind)
                w.winfo_screenwidth.return_value, w.winfo_screenheight.return_value = 1920, 1080
                w.winfo_reqwidth.return_value, w.winfo_reqheight.return_value = 460, 900
                made.append((kind, kw.get("text"), args, kw))
                return w
            return make
        fake_tk = MagicMock()
        for kind in ("Label", "Checkbutton", "Button", "Frame", "Toplevel", "LabelFrame"):
            setattr(fake_tk, kind, widget(kind))
        fake_ttk = MagicMock()
        fake_ttk.Separator = widget("Separator")
        with patch.object(setup, "tk", fake_tk), patch.object(setup, "ttk", fake_ttk), \
                patch.object(setup.theme, "theme_window"):
            flow = setup.SetupFlow(SimpleNamespace(root=MagicMock(), cowork=MagicMock(), hooks=MagicMock()))
            flow.build(targets, hidden)
        return made

    def texts(self, made, kind=None):
        return [t for k, t, _, _ in made if t and (kind is None or k == kind)]

    def targets(self):
        return [{"key": "windows", "label": "This PC (Windows)", "kind": "installed", "note": "terminal"},
                {"key": "codex:windows", "label": "Codex - This PC (Windows)", "kind": "ready", "note": "Codex CLI"},
                {"key": "wsl:dev", "label": "WSL: dev", "kind": "installed", "note": "Remote-WSL"},
                {"key": "codex:wsl:dev", "label": "Codex - WSL: dev", "kind": "ready", "note": "Codex CLI"}]

    def test_neutral_title_and_two_sections_with_a_line_between(self):
        made = self.build(self.targets())
        texts = self.texts(made)
        self.assertIn("Where should AIPet watch your AI agents?", texts)
        kinds = [(k, t) for k, t, _, _ in made if k in ("Label", "Checkbutton", "Separator")
                 and (k != "Label" or t in ("CLAUDE CODE", "CODEX"))]
        self.assertEqual(kinds, [("Label", "CLAUDE CODE"), ("Checkbutton", "This PC (Windows)"),
                                 ("Checkbutton", "WSL: dev"), ("Separator", None), ("Label", "CODEX"),
                                 ("Checkbutton", "This PC (Windows)"), ("Checkbutton", "WSL: dev")])
        self.assertFalse(any("Claude Code hooks" in t for t in texts))  # the tray menu is "Integrations" now

    def test_one_agent_only_has_no_line(self):
        made = self.build(self.targets()[:1])
        self.assertNotIn("Separator", [k for k, *_ in made])
        self.assertIn("CLAUDE CODE", self.texts(made, "Label"))
        self.assertNotIn("CODEX", self.texts(made, "Label"))


if __name__ == "__main__":
    unittest.main()
