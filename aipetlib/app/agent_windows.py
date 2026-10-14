"""The agent network windows the tray app opens: one for every session, or one per session, and the menu switches."""
import threading

from ..runtime import settings as runtime_settings
from ..agents import tracker
from ..ui import agent_window
from ..ui import theme


class AgentWindows:
    """Owns the open agent network windows (by session key, "*" for the shared one) and whether one is open, as last
    seen on the Tk thread (the Windows tray reads it from its own thread).

    Needs from the app: root, pet (cfg, _last_items, the window's pet), refresh_menu(), startup_dialogs_open() and
    _tk_thread.
    """

    def __init__(self, app):
        self.app = app
        self.wins = {}
        self.cached_open = False

    def offer_intro(self):
        """Offer the new view once, after setup and any other startup dialog have closed."""
        app = self.app
        if app.pet.cfg.get("agent_network_intro_seen"):
            return
        if app.startup_dialogs_open():
            app.root.after(1500, self.offer_intro)
            return
        app.pet.cfg["agent_network_intro_seen"] = True
        runtime_settings.save_setting("agent_network_intro_seen", True)

        def chosen(try_it):
            if try_it:
                self.show()
                app.refresh_menu()

        theme.themed_dialog(
            app.root, "Try AIPet's agent view", kind="question", heading="Meet the agent view",
            text="See your Claude and Codex agents in a live graph, including nested subagents, "
                 "their tool calls, inputs, outputs and shared logs.\n\nWould you like to try it? "
                 "You can also open it later from the AIPet menu: Agent network window.",
            buttons=(("Not now", False, "secondary"), ("Try it", True, "primary")),
            cancel=False, on_result=chosen)

    def open_windows(self):
        wins = {k: w for k, w in self.wins.items() if w.alive()}
        self.wins = wins
        return wins

    def is_open(self):
        """Whether an agent network window is open. The Windows tray builds its menu on pystray's own thread, which
        must never call into Tk: a Tk call from another thread waits for the Tk thread, and after Quit that wait never
        ends, so the tray thread (and the process, with its icon) would stay alive. Off the Tk thread this returns the
        answer last cached on it (by pump, the menus and the toggles)."""
        app = self.app
        if threading.current_thread() is getattr(app, "_tk_thread", threading.main_thread()):
            self.cached_open = bool(self.open_windows())
            tracker.set_active(self.cached_open)  # full histories only while a window shows them
        return self.cached_open

    def separate(self):
        app = self.app
        return agent_window.load_options(app.pet.cfg)["separate"]

    def menu_item(self, key=None, label="Agent network window"):
        """A switch: opens the agent network window (on this session), or closes it (them) when open."""
        return {"label": label, "action": lambda: self.toggle(key), "enabled": True, "submenu": None,
                "checked": self.is_open(),
                "help": "A live map of a session's main agent and its subagents: what each one is doing, its model "
                        "and context, a shared log, and prompts you can answer from there."}

    def separate_item(self):
        return {"label": "Agent network compact mode", "action": self.toggle_separate,
                "enabled": True, "submenu": None, "checked": not self.separate(),
                "help": "Like the pet's compact mode: one agent network window for every session (PgUp / PgDn "
                        "switch). Off: each session gets its own window."}

    def reset_appearance(self):
        """Default appearance for the agent network windows (one window or one per session stays as it is), and every
        open one centred on the main screen."""
        app = self.app
        opts = dict(agent_window.DEFAULT_OPTIONS,
                    separate=agent_window.load_options(app.pet.cfg)["separate"])
        app.pet.cfg[agent_window.OPTIONS_KEY] = opts
        runtime_settings.save_setting(agent_window.OPTIONS_KEY, opts)
        for n, w in enumerate(self.open_windows().values()):
            w.reset_appearance(opts, cascade=n)
        app.refresh_menu()

    def toggle(self, key=None):
        app = self.app
        if self.is_open():
            for w in list(self.open_windows().values()):
                w.close()
        else:
            self.show(key)
        app.refresh_menu()

    def toggle_separate(self):
        app = self.app
        opts = agent_window.load_options(app.pet.cfg)
        opts["separate"] = not opts["separate"]
        app.pet.cfg[agent_window.OPTIONS_KEY] = opts
        runtime_settings.save_setting(agent_window.OPTIONS_KEY, opts)
        self.mode_changed(None)

    def mode_changed(self, source):
        """Switched between one window and one per session: reopen what was open in the new mode."""
        app = self.app
        wins = self.open_windows()
        if wins:
            keep = source.key if source is not None else None
            ref = source if source is not None and source.alive() else next(iter(wins.values()))
            try:  # the new window(s) open where this one is
                at = ref.win.geometry()
            except Exception:
                at = None
            for w in list(wins.values()):
                w.close()
            self.show(keep if not self.separate() else None, at=at)
        app.refresh_menu()

    def options_changed(self, opts, source, changed):
        if changed == "each":
            self.mode_changed(source)
            return
        for w in self.open_windows().values():  # the same look for every agent network window
            if w is not source:
                w.set_options(opts)

    def show(self, key=None, at=None):
        """One window for all sessions (opening it again switches it to that session), or, in "one per session"
        mode, that session's own window (key None: every session's)."""
        app = self.app
        wins = self.open_windows()
        tracker.set_active(True)  # full histories for the window (it reads what the tracker publishes)
        new = lambda k, pinned, n=0: agent_window.AgentNetworkWindow(  # noqa: E731
            app.root, app.pet, k, pinned=pinned, on_options=self.options_changed, cascade=n, at=at)
        if not self.separate():
            win = wins.get("*")
            if win:
                win.show_session(key)
                win.raise_()
            else:
                wins["*"] = new(key, False)
            return
        keys = [key] if key else [i["key"] for i in agent_window.candidate_sessions(app.pet._last_items)][:6]
        if not keys:  # no session yet: one window that waits for the first
            wins["*"] = wins.get("*") or new(None, False)
            return
        for n, k in enumerate(keys):
            if k in wins:
                wins[k].raise_()
            else:
                wins[k] = new(k, True, len(wins) + n)
