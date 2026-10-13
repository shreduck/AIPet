"""Theme and sprites own their state; aipet re-exports the same objects, so components and entry points share them."""
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

    def test_aipet_reexports_the_owners_objects(self):
        theme_names = ("T", "THEMES", "THEMED", "DARK_TEXT", "DIALOG_ACCENTS", "UI_FONT", "INK", "button_style",
                       "theme_widget", "theme_window", "retheme_all", "themed_dialog", "set_theme")
        sprite_names = ("_SPR", "_PHOTOS", "_PNG_PHOTOS", "_RUNS", "_IMG_CACHE", "FACE_PARTS", "MOUTH_PIXELS",
                        "STATE_FACE", "load_sprites", "robot_image", "state_image", "bubble_image", "mark_image")
        for owner, names in ((self.theme, theme_names), (self.sprites, sprite_names)):
            for name in names:
                with self.subTest(name=name):
                    self.assertIs(getattr(self.aipet, name), getattr(owner, name))
        self.assertIs(self.aipet.SCALE, self.preferences.SCALE)
        self.assertEqual(self.sprites.MONO_FAMILY, self.theme.UI_FONT)

    def test_theme_switch_mutates_the_shared_dict(self):
        T, before = self.theme.T, dict(self.theme.T)
        try:
            self.theme.set_theme("dark")
            self.assertIs(self.aipet.T, T)
            self.assertEqual(T["name"], "dark")
            self.theme.set_theme("no such theme")
            self.assertEqual(T["name"], "light")
        finally:
            self.theme.set_theme(before.get("name", "light"))

    def test_dialogs_and_the_app_share_geometry(self):
        from aipetlib import geometry
        self.assertIs(self.theme.geometry, geometry)
        for name in ("work_area", "mac_screen_at", "fit_on_screen", "geo", "_MAC_SCREENS"):
            with self.subTest(name=name):
                self.assertIs(getattr(self.aipet, name), getattr(geometry, name))
        self.assertEqual(geometry.geo(-3.6, 4.2), "+-3+4")

    def test_sprites_resolve_the_bundled_folder(self):
        import os
        self.assertTrue(os.path.isfile(os.path.join(self.sprites.SPRITE_DIR, "sprites.json")))
        self.assertEqual(self.aipet.SPRITE_DIR, self.sprites.SPRITE_DIR)


if __name__ == "__main__":
    unittest.main()
