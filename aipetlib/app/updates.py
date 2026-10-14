"""Checking for a new AIPet release: once a day in the background, or from the menus, and the dialog that tells you."""
import sys
import threading
import time
import webbrowser

from .. import update as releases
from ..runtime import paths
from ..runtime import settings as runtime_settings
from ..ui import theme

IS_MAC = sys.platform == "darwin"


class UpdateFlow:
    """Owns what is known about releases (~/.aipet/update.json, the newer release if any, a check in progress).

    Needs from the app: version, root, pet.cfg, ui(fn) (run on the Tk thread), info(text, error=), refresh_menu().
    The menus read `info` (a newer release, or None) and call check / open / toggle_auto.
    """

    def __init__(self, app):
        self.app = app
        self.state = releases.State(paths.HOME_DIR)
        found = self.state.load().get("latest") or {}
        self.info = found if releases.is_newer(found.get("tag"), app.version) else None  # a newer release, if any
        self.busy = False

    def tick(self):
        """Tk thread: check once a day while automatic checks are on (looked at every hour)."""
        app = self.app
        if app.pet.cfg.get("update_check", True) and self.state.due():
            self.check(False)
        app.root.after(3600 * 1000, self.tick)

    def check(self, manual=False):
        app = self.app
        if self.busy:
            return
        self.busy = True

        def job():
            try:
                latest = releases.latest_release()
            except Exception as e:
                self.state.save(last_check=time.time(), error=repr(e)[:200])
                if manual:
                    err = f"Couldn't check for a new version:\n{e}\n\nReleases: {releases.RELEASES_URL}"
                    app.ui(lambda: app.info(err, error=True))
                return
            finally:
                self.busy = False
            state = self.state.save(last_check=time.time(), latest=latest, error="")
            newer = releases.is_newer(latest["tag"], app.version)
            self.info = latest if newer else None
            app.ui(self.show_in_menus)
            if manual:
                app.ui(lambda: self.dialog(latest, newer, manual=True))
            elif newer and latest["tag"] not in (state.get("skipped"), state.get("notified")):
                self.state.save(notified=latest["tag"])
                app.ui(lambda: self.dialog(latest, True))
        threading.Thread(target=job, daemon=True).start()

    def dialog(self, latest, newer, manual=False):
        app = self.app
        if not newer:
            known = releases.parse(app.version)
            text = (f"You have the newest version, {releases.short(app.version)}." if known else
                    f"This copy has no version number (built from source). The newest release is {latest['tag']}.")
            choice = theme.themed_dialog(app.root, "AIPet - updates", text, kind="info", heading="No new version",
                                         buttons=(("Open releases page", "open", "secondary"), ("OK", "ok", "primary")),
                                         cancel="ok")
            if choice == "open":
                webbrowser.open(latest.get("url") or releases.RELEASES_URL)
            return
        choice = theme.themed_dialog(
            app.root, "AIPet - update", kind="info", heading=f"AIPet {latest['tag']} is available",
            text=(f"You have {releases.short(app.version)}. The new version is on the release page: download "
                  f"{'AIPet-mac-arm64.zip' if IS_MAC else 'AIPet-windows-x64.zip'}, quit AIPet (tray > Quit) and "
                  f"{'replace your copy with the app inside' if IS_MAC else 'unzip it over your AIPet folder'}. "
                  "Your settings, hooks and backups in ~/.aipet are kept.\n\n"
                  "The menus show the update until you install it."),
            buttons=(("Skip this version", "skip", "secondary"), ("Later", "later", "secondary"),
                     ("Open release page", "open", "primary")), cancel="later")
        if choice == "open":
            webbrowser.open(latest.get("url") or releases.RELEASES_URL)
        elif choice == "skip":
            self.state.save(skipped=latest["tag"])

    def open(self):
        if self.info:
            self.dialog(self.info, True, manual=True)
        else:
            self.check(True)

    def show_in_menus(self):
        """The update entries live in the tray / menu-bar menu only (rebuilt when they open)."""
        self.app.refresh_menu()

    def toggle_auto(self):
        """Check for updates automatically (on by default)."""
        app = self.app
        on = not app.pet.cfg.get("update_check", True)
        app.pet.cfg["update_check"] = on
        runtime_settings.save_setting("update_check", on)
        app.refresh_menu()
