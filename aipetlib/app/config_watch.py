"""config.json edited by hand (or by an AI with the settings guide): what changed is applied while AIPet runs."""
import json
import os

from .. import config as app_config
from ..runtime import diagnostics
from ..runtime import paths
from ..runtime import settings as runtime_settings

WATCH_MS = 1500  # how often config.json is checked for hand edits
RESTART_KEYS = {"workbench", "claude_code", "click_through", "max_pets"}  # read once at start


def config_stamp():
    try:
        st = os.stat(paths.CONFIG_PATH)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


class ConfigWatcher:
    """Owns when config.json was last seen. Each change goes through the app's own setters
    (app.config_handlers()) with saving paused, since the file already says so; keys read only at start are reported.

    Needs from the app: root, pet.cfg, config_handlers(), refresh_menu(), info(text).
    """

    def __init__(self, app):
        self.app = app
        self.stamp = None

    def start(self):
        self.stamp = config_stamp()
        self.app.root.after(WATCH_MS, self.tick)

    def tick(self):
        try:
            stamp = config_stamp()
            if stamp != self.stamp:
                self.stamp = stamp
                self.apply()
        except Exception as e:
            diagnostics.log_error(f"config watch: {e!r}")
        self.app.root.after(WATCH_MS, self.tick)

    def apply(self):
        """Apply what changed in config.json since the app last saw it. A file that isn't valid JSON (an editor
        mid-save, a typo) is skipped until it is. Returns the keys that only apply after a restart."""
        app = self.app
        try:
            with open(paths.CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        cfg = app.pet.cfg
        merged = app_config.deep_merge(app_config.DEFAULT_CONFIG, data)  # nested sections as the app holds them
        changed = {k: merged[k] for k in data if cfg.get(k) != merged[k]}
        if not changed:
            return []
        handlers = app.config_handlers()
        restart = []
        runtime_settings.SAVE_PAUSED["v"] = True  # the setters save; the file already says so
        try:
            for key, value in changed.items():
                if key in RESTART_KEYS:
                    restart.append(key)
                    continue
                try:
                    if key in handlers:
                        handlers[key](value)
                    cfg[key] = value  # everything else is read where it's used (sound_style, update_check...)
                except Exception as e:
                    diagnostics.log_error(f"config.json {key}={value!r}: {e!r}")
        finally:
            runtime_settings.SAVE_PAUSED["v"] = False
        app.refresh_menu()  # menus and the settings window show the new values
        if restart:
            app.info("config.json changed. Applied what could be applied now; these take effect after AIPet "
                     "restarts: " + ", ".join(sorted(restart)))
        return restart
