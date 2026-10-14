import unittest
import aipetlib.config as config
import aipet_guide


class GuideTests(unittest.TestCase):
    def test_every_setting_is_explained(self):
        missing = [k for k in config.DEFAULT_CONFIG if k not in aipet_guide.CONFIG_KEYS]
        self.assertEqual(missing, [])

    def test_guide_names_the_folder_and_current_values(self):
        cfg = dict(config.DEFAULT_CONFIG, theme="dark", size=1.5)
        text = aipet_guide.build_guide(cfg, r"C:\Users\me\.aipet", "v0.4.0")
        self.assertIn(r"C:\Users\me\.aipet", text)
        self.assertIn('| `theme` | `"dark"` |', text)
        self.assertIn("| `size` | `1.5` |", text)
        self.assertIn("auto-approve.json", text)


if __name__ == "__main__":
    unittest.main()
