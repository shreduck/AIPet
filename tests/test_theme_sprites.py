"""Theme and sprites own their state; every component reads it from them (stage 11: aipet no longer re-exports it)."""
import importlib.util
import unittest

HAVE_TK = importlib.util.find_spec("tkinter") is not None


@unittest.skipUnless(HAVE_TK, "Tk is not available")
class ThemeSpritesOwnershipTests(unittest.TestCase):
    def setUp(self):
        import aipet
        from aipetlib.runtime import preferences
        from aipetlib.ui import sprites, theme
        self.aipet, self.theme, self.sprites, self.preferences = aipet, theme, sprites, preferences

    def test_aipet_no_longer_reexports_what_it_does_not_own(self):
        for name in ("THEMES", "DIALOG_ACCENTS", "theme_window", "themed_dialog", "_PHOTOS", "robot_image",
                     "work_area", "SPRITE_DIR", "answers_dir", "DEFAULT_CONFIG"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(self.aipet, name))
        self.assertEqual(self.sprites.MONO_FAMILY, self.theme.UI_FONT)

    def test_theme_switch_mutates_the_shared_dict(self):
        from aipetlib.ui import pet_style
        T, before = self.theme.T, dict(self.theme.T)
        try:
            self.theme.set_theme("dark")
            self.assertIs(self.theme.T, T)  # the same dict everyone holds, changed in place
            self.assertEqual(T["name"], "dark")
            self.theme.set_theme("no such theme")
            self.assertEqual(T["name"], "light")
        finally:
            self.theme.set_theme(before.get("name", "light"))
        self.assertTrue(hasattr(pet_style, "px"))

    def test_dialogs_and_the_app_share_geometry(self):
        from aipetlib import geometry
        self.assertIs(self.theme.geometry, geometry)
        self.assertEqual(geometry.geo(-3.6, 4.2), "+-3+4")

    def test_sprites_resolve_the_bundled_folder(self):
        import os
        from aipetlib.runtime import paths
        self.assertTrue(os.path.isfile(os.path.join(self.sprites.SPRITE_DIR, "sprites.json")))
        self.assertEqual(paths.SPRITE_DIR, self.sprites.SPRITE_DIR)


if __name__ == "__main__":
    unittest.main()
