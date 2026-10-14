"""HookJobs: the tray app's hooks operations."""
import os
import sys
import threading

from ..runtime import diagnostics
from ..integrations import backups as hook_backups, cowork as hook_cowork
from ..integrations import deployment as hook_deployment, discovery as hook_discovery, installer as hook_installer
from ..integrations import locations as hook_locations, protocol as hook_protocol, targets as hook_targets

IS_MAC = sys.platform == "darwin"


from . import setup as setup_state

class HookJobs:
    """Owns operation busy/probed state and updates the app's shared target/status snapshots.

    Uses UI dispatch, dialogs, refresh_menu, setup/legacy/update flows and a path-opening callback.
    """

    def __init__(self, app, open_path):
        self.app = app
        self.open_path = open_path
        self.busy = False
        self.probed = False

    def offer_update(self, keys):
        app = self.app
        names = "\n".join("  - " + hook_targets.describe(k) for k in keys)
        if app.ask("AIPet's hooks here point at an older location and may not be working:\n\n"
                    f"{names}\n\nUpdate them now? Your other hooks and settings are kept, and each file is "
                    "backed up first."):
            if self.busy:
                app.info("Another hook operation is still running - use tray > Claude Code hooks > Install / update.")
                return
            self.busy = True
            threading.Thread(target=self._update_job, args=(keys,), daemon=True).start()

    def _update_job(self, keys, runtimes=None):
        app = self.app
        results = []
        try:
            for k in keys:
                try:
                    results.append(f"{hook_targets.describe(k)}: {hook_installer.install(k, (runtimes or {}).get(k)).splitlines()[0]}")
                except Exception as e:
                    results.append(f"{hook_targets.describe(k)}: FAILED - {e}")
                app.status[k] = hook_installer.status(k)
            msg = "\n\n".join(results)
            app.ui(lambda: app.info(msg))
        finally:
            self.busy = False
            app.refresh_menu()

    def refresh_targets(self):
        app = self.app
        app.status[hook_targets.LOCAL] = hook_installer.status(hook_targets.LOCAL) if (os.name == "nt" or IS_MAC) else "n/a"
        app.distros = hook_targets.list_distros()
        self.probed = True
        for name, state in app.distros:
            # Only probe running distros so we never boot one just to show a menu.
            app.status["wsl:" + name] = hook_installer.status("wsl:" + name) if state.lower() == "running" else f"{state.lower()} · status unknown"
        codex = []
        if (os.name == "nt" or IS_MAC) and hook_discovery.detect_codex_local():
            codex.append(("This Mac" if IS_MAC else "This PC (Windows)", "codex:" + hook_targets.LOCAL))
        for name, state in app.distros:
            try:
                if state.lower() == "running" and hook_discovery.codex_in_wsl(name):
                    codex.append((f"WSL: {name}", "codex:wsl:" + name))
            except Exception:
                pass
        for _, key in codex:
            app.status[key] = hook_installer.status(key)
        app.codex_targets = codex
        app.refresh_menu()

    def confirm_usage(self, key):
        app = self.app
        on = not hook_installer.usage_enabled(key)
        text = (f"Enable Claude's CLI usage collector for {hook_targets.describe(key)}?\n\n"
                "This changes Claude Code's status line. If you have one, AIPet runs your existing command through Bash "
                "after recording Claude's usage data, adding a process on each refresh. If you have none, it adds a line "
                "showing usage percentages. The previous setting is saved in ~/.aipet and restored when disabled.\n\n"
                "This works in the CLI; VS Code and desktop sessions may not provide usage data. "
                "AIPet does not read sign-in credentials or the Keychain.") if on else (
                f"Disable Claude usage collection for {hook_targets.describe(key)} and restore your previous status line?")
        if app.ask(text):
            self.start_job(key, "usage", on)

    def confirm(self, key, install):
        app = self.app
        where = hook_targets.describe(key)
        if install:
            text = (f"Add AIPet hooks to:\n{where}\n\nYour existing hooks and settings are kept, and the "
                    f"current file is backed up first (tray > Claude Code hooks > Restore backup).\n"
                    f"Only sessions started afterwards will show up."
                    + ("\n\nClaude usage collection is a separate, optional setting in this menu." if not hook_targets.is_codex(key) else "")
                    + ("\n\nCodex then asks you to trust the new hooks once: run /hooks in Codex." if hook_targets.is_codex(key) else ""))
        else:
            text = f"Remove AIPet hooks from:\n{where}\n\nOther hooks are left untouched and a backup is taken first."
        if app.ask(text):
            self.start_job(key, "install" if install else "remove")

    def confirm_restore(self, key, b):
        app = self.app
        lines = [f"Restore {hook_targets.describe(key)}", f"to the backup from {b['when']} ({b['reason']})?", ""]
        if b["absent"]:
            lines.append("settings.json did not exist at that point, so it will be DELETED.")
        else:
            lines.append(f"That version {'contains' if b['pet_hooks'] else 'does not contain'} AIPet hooks "
                         f"({b['size']} bytes).")
            if not b["valid_json"]:
                lines.append("Warning: that backup is not valid JSON - Claude Code may reject it.")
        lines += ["", "The current file is backed up first, so you can undo this from the same menu.",
                  "Restart running Claude Code sessions for the change to take effect."]
        if app.ask("\n".join(lines)):
            self.start_job(key, "restore", b["path"])

    def start_job(self, key, action, arg=None):
        app = self.app
        if self.busy:
            app.info("Another hook operation is still running - try again in a moment.")
            return
        self.busy = True
        threading.Thread(target=self._run_job, args=(key, action, arg), daemon=True).start()

    def _run_job(self, key, action, arg=None):
        app = self.app
        try:
            if action == "install":
                msg = hook_installer.install(key)
            elif action == "remove":
                msg = hook_installer.uninstall(key)
            elif action == "usage":
                msg = hook_installer.set_usage(key, bool(arg))
            elif action == "restore":
                msg = hook_backups.restore_backup(key, arg)
            else:
                path = hook_backups.make_backup(key, "manual")
                msg = f"Backed up {hook_targets.describe(key)}\nto {path}"
            app.status[key] = hook_installer.status(key)
            app.ui(lambda: app.info(msg))
        except Exception as e:
            err = f"{action.capitalize()} failed for {hook_targets.describe(key)}:\n{e}"
            app.ui(lambda: app.info(err, error=True))
        finally:
            self.busy = False
            app.refresh_menu()

    def open_backups(self, key):
        d = hook_backups.backup_dir(key)
        os.makedirs(d, exist_ok=True)
        self.open_path(d)

    def startup(self):
        app = self.app
        try:
            hook_backups.migrate_legacy_backups()  # pull backups over from the old %LOCALAPPDATA% location
        except Exception as e:
            print(f"[aipet] backup migration failed: {e}", file=sys.stderr)
        try:
            hook_deployment.deploy_files(only_if_deployed=True)  # refresh an existing install only; never create one
            if os.path.isdir(hook_locations.INSTALL_DIR) and (os.name == "nt" or IS_MAC):
                hook_cowork.build_plugin()  # keep the Cowork plugin zip next to the exe current
        except Exception:
            pass
        self.refresh_targets()
        for key in [hook_targets.LOCAL] + ["wsl:" + n for n, state in app.distros if state.lower() == "running"]:
            try:
                hook_installer.migrate_legacy_usage(key)  # backup and restore v0.3.1's automatic wrapper once
            except Exception as e:
                diagnostics.log_error(f"restore legacy status line for {key}: {e!r}")
        app.ui(lambda: app.root.after(20000, app.legacy.tick))  # LEGACY: daily reminder about old-name hooks
        app.ui(lambda: app.root.after(30000, app.updates.tick))
        app.ui(app.updates.show_in_menus)
        stale = [k for k, v in app.status.items() if v == hook_protocol.STALE]
        if not setup_state.setup_is_done():
            app.ui(app.setup.show)  # first run: shows what is installed / up to date / outdated per target
        elif stale:
            app.ui(lambda: self.offer_update(stale))
        app.ui(lambda: app.root.after(1500, app.agent_windows.offer_intro))
