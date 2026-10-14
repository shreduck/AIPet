"""Shared settings, tray, pet context and live-management menu definitions.

The owning app supplies its actions and state; shared values come from their owner modules.
No application entry point is imported by this component.
"""
import os
import sys
import threading
import time

from .. import permissions
from .. import sessions as session_text
from .. import text as textfmt
from ..app import diagnostics as app_diagnostics
from ..runtime import paths
from ..runtime import preferences
from . import pet_style
from . import theme

IS_MAC = sys.platform == "darwin"


def _context_menu_spec(app, pet):
    def item(label, action, enabled=True):
        return {"label": label, "action": action, "enabled": enabled}
    session = app.pet._menu_session(pet)
    real_pet = bool(pet and pet.key != "_none")
    session_actions = [
        item("Session details...", lambda: app.pet.open_detail(pet.data.get("focus", pet.key)), real_pet),
        item("Go to session window", lambda: app.pet.focus_session(pet), real_pet),
        item("Open in VS Code", lambda: app.pet.open_in_vscode(pet),
             bool(pet and pet.data.get("source") == "CC" and pet.data.get("cwd"))),
        None,
        item("Mark as finished", app.pet.finish_menu_pet,
             bool(session and session.get("source") == "CC" and session.get("state") in
                  ("working", "needs_input", "error"))),
        item("Dismiss this pet", app.pet.dismiss_menu_pet, real_pet),
    ]
    shared = app._mac_menu_spec()  # starts with AIPet menu... and a separator
    timers = app._timed_countdown_items()
    network = app.agent_windows.menu_item(pet.data.get("focus", pet.key) if real_pet else None)
    return shared[:1] + [network] + timers + [app._fast_cancel_item(pet, session),
                                  {"label": "This session", "submenu": session_actions}, None] + shared[2:]


def _mac_menu_spec(app, settings=False):
    """settings=True: the settings window's version, with a few entries that only make sense there."""
    def item(label, action=None, checked=None, enabled=True, submenu=None, default=False, help=None, icon=None,
             choice=False, slider=None):
        # help / icon / choice / slider are only read by the settings window; the menus ignore them
        entry = {"label": label, "action": action, "enabled": enabled, "submenu": submenu}
        if default:
            entry["default"] = True
        if checked is not None:
            entry["checked"] = checked
        if help:
            entry["help"] = help
        if icon:
            entry["icon"] = icon
        if choice:
            entry["choice"] = True  # the submenu is a pick-one list
        if slider:
            entry["slider"] = slider  # the settings window shows a slider; the menus show the choice list
        return entry

    def target(name, key):
        st = app.status.get(key, "checking…")
        backups = app.hook_backups(key)[:15]
        restore = [item(f"{b['when']} - {b['reason']}" + ("  [original]" if b["original"] else ""),
                        lambda b=b: app.confirm_restore(key, b)) for b in backups] or [item("No backups yet", enabled=False)]
        return item(f"{'✓ ' if st.startswith('installed') else ''}{name}  ({st})", submenu=[
            item("Install / update hooks", lambda: app.confirm(key, True),
                 help="Adds AIPet's hooks to this install's settings, or brings them up to date. Your own hooks stay."),
            item("Remove hooks", lambda: app.confirm(key, False),
                 help="Takes AIPet's hooks out again. The pet stops seeing sessions from here."),
            *([] if app.is_codex_target(key) else [item(
                "Claude usage status line...", lambda: app.confirm_usage(key),
                help="Lets the pet show Claude's usage limits by recording them from Claude Code's status line.")]),
            None,
            item("Restore backup", submenu=restore,
                 help="Puts back a settings file saved before AIPet changed it."),
            item("Back up now", lambda: app.start_job(key, "backup"),
                 help="Saves a copy of the current settings file."),
            item("Open backups folder", lambda: app.open_backups(key)),
        ], help=f"Hook status: {st}.")

    refresh = lambda: threading.Thread(target=app.refresh_targets, daemon=True).start()  # noqa: E731
    claude = [target("This Mac" if IS_MAC else "This PC (Windows)", app.LOCAL_TARGET)]
    claude += [target(f"WSL: {name}", "wsl:" + name) for name, _ in app.distros]
    claude += [None, item("Cowork (Claude desktop app)...", app.show_cowork,
                          help="How to let the pet follow Cowork sessions in the Claude desktop app."),
               item("Check for old Claude Pet hooks...", lambda: app.check_legacy(True),
                    help="Finds hooks left behind by Claude Pet, this app's old name, and offers to remove them.")]
    codex = [target(lab, key) for lab, key in app.codex_targets] or [item("Codex not found", enabled=False)]
    cfg = app.pet.cfg
    auto = []
    for title, entries in app.approvals.sections():
        auto.append(item(title, submenu=[
            item(app.approvals.label(lab, key), lambda k=key, lab=lab: app.approvals.open_editor(k, lab),
                 checked=key in app.approvals.targets_on) for lab, key in entries
        ] or [item("Not found", enabled=False)]))
    auto += [None, item("Turn all off", app.approvals.all_off, enabled=bool(app.approvals.targets_on),
                        help="Switches auto approve off everywhere at once.")]
    top = [item(f"Update available: {app.updates.info['tag']}...", app.updates.open, icon="refresh",
                help="A newer AIPet is out. See what changed and update."), None] if app.updates.info else []
    return [app.settings_item(), None] + top + [
        item("Show pet" if app.hidden else "Hide pet", app.toggle, default=True,
             help="Hides the pet from the screen. AIPet keeps watching your sessions in the background."),
        item("Clear finished sessions", app.pet.clear_finished,
             help="Removes the pets of sessions that are done."),
        item("Open config and data folder", app.open_config, icon="home",
             help=f"{paths.HOME_DIR}: settings (config.json, auto-approve.json), sessions, logs and backups."),
        *([item("Copy settings guide for AI", app.copy_ai_guide,
                help="Copies a Markdown guide to AIPet's settings files (where they are, every option and its "
                     "values) to the clipboard. Paste it into Claude, Codex or another assistant and ask it to "
                     "change AIPet's settings for you.")] if settings else []),
        None,
        item("Live Management", icon="clock", submenu=app._live_spec(item, settings),
             help="Your running sessions right now: give one a timed auto approve, and see its subagents."),
        item("Appearance", icon="palette", help="How the pet and its windows look.", submenu=[
            item("Pet style", choice=True, help="Which creature sits on your screen.",
                 submenu=[item(label, lambda k=key: app.set_style(k), checked=preferences.STYLE["v"] == key)
                          for key, label in pet_style.PET_STYLES if key not in pet_style.BETA_STYLES]),
            item("Pet size...", app.pet.open_size_slider, help="Make the pet bigger or smaller.",
                 slider={"value": preferences.SCALE["v"] / preferences.SCALE_UNIT, "min": 0.3, "max": 3.0, "step": 0.05,
                         "set": app.pet.set_size, "format": lambda v: f"{int(round(v * 100))}%"}),
            item("Tooltip text size...", app.pet.open_tooltip_text_slider,
                 help="Text in both session and usage tooltips. 100% is the default.",
                 slider={"value": max(0.5, min(2.5, float(cfg.get("tooltip_text_scale", 1.0)))),
                         "min": 0.5, "max": 2.5, "step": 0.05,
                         "set": app.pet.set_tooltip_text_scale, "format": lambda v: f"{int(round(v * 100))}%"}),
            item("Reset pet size", app.pet.reset_scale, help="Back to the standard size."),
            item("Reset agent network appearance", app.agent_windows.reset_appearance,
                 help="The agent network window's default look again (opacity, background, nodes, scale, title "
                      "bar, always on top), centred on the main screen."),
            item("Compact mode (one pet)", app.pet.toggle_compact, checked=bool(cfg.get("compact")),
                 help="One pet stands in for every session, showing the one that needs you most."),
            item("Context beside the pet", lambda: app.pet.set_context_banner(not cfg.get("context_banner", True)),
                 checked=bool(cfg.get("context_banner", True)),
                 help="Each AI's context in use and its window (CL ctx 84k/~200k), above its usage limits. "
                      "Claude's window is estimated from the model unless context_windows in config.json sets it."),
            None,
            item("Dark theme", app.toggle_theme, checked=theme.T.get("name") == "dark",
                 help="Dark name tags, bubbles and windows."),
            item("Window scale", choice=True,
                 help="Text and icons in this window. Also Ctrl + plus / minus / 0, or Ctrl + mouse wheel here.",
                 slider={"value": app.settings_zoom(), "min": preferences.ZOOM_MIN,
                         "max": preferences.ZOOM_MAX, "step": preferences.ZOOM_STEP,
                         "set": app.set_settings_zoom, "format": lambda v: f"{int(round(v * 100))}%"},
                 submenu=[item(f"{int(v * 100)}%", lambda v=v: app.set_settings_zoom(v),
                               checked=abs(app.settings_zoom() - v) < 0.01)
                          for v in (0.5, 0.75, 1.0, 1.25, 1.5)]),
            item("Session titles", choice=True, help="What the name tag under each pet shows.",
                 submenu=[item(text, lambda v=value: app.pet.set_session_titles(v),
                               checked=cfg.get("session_titles", "name") == value)
                          for value, text in (("name", "Session name"), ("prompt", "Last prompt"))]),
            item("Tooltips", help="What appears when you hover over a pet.",
                 submenu=[item(label, lambda k=kind: app.pet.set_tooltip(k, not cfg.get(k + "_tooltips", True)),
                               checked=bool(cfg.get(kind + "_tooltips", True)), help=text)
                          for kind, label, text in (
                              ("session", "Session details", "Title, state, last message and how long ago."),
                              ("usage", "Usage details", "Usage limits when you hover over a usage badge."))]),
            None,
            item("Alpha", icon="wrench",
                 help="Pet styles left over from earlier AIPet versions, no longer looked after. "
                      "Switch one off to go back to the robot.",
                 submenu=[item(f"{label} style", lambda k=key: app.set_style("robot" if preferences.STYLE["v"] == k else k),
                               checked=preferences.STYLE["v"] == key, help=pet_style.BETA_STYLES[key])
                          for key, label in pet_style.PET_STYLES if key in pet_style.BETA_STYLES]),
        ]),
        item("Behavior", icon="gear", help="What the pet does and when.", submenu=[
            *([item("Show on all desktops", lambda: app.pet.set_all_spaces(not cfg.get("all_spaces", True)),
                    checked=bool(cfg.get("all_spaces", True)),
                    help="Keep the pet visible when you switch virtual desktops" +
                         (" or Spaces." if IS_MAC else "."))] if IS_MAC or os.name == "nt" else []),
            item("Reset pet position (main screen)", app.reset_position,
                 help="Moves the pet back to the main screen, in case it ended up out of sight."),
            item("Click goes to the session's window",
                 lambda: app.pet.set_click_to_focus(not cfg.get("click_to_focus", True)),
                 checked=bool(cfg.get("click_to_focus", True)),
                 help="Clicking a pet brings its terminal or editor window to the front."),
            None,
            item("Sounds and notifications", icon="bell", help="How the pet gets your attention.", submenu=[
                item("Mute sounds", app.toggle_mute, checked=app.c_muted,
                     help="No sound when a session needs you or finishes."),
                item("Notifications", app.toggle_notify, checked=app.c_notify,
                     help="System notifications when a session needs you, hits an error or finishes."),
                *([item("Sound style", choice=True,
                        help="Choose AIPet's custom chimes (the default) or your operating system's sounds.",
                        submenu=[item(text, lambda v=value: app.pet.set_sound_style(v),
                                      checked=cfg.get("sound_style", "chimes") == value)
                                 for value, text in (("chimes", "AIPet chimes (default)"),
                                                     ("system", "macOS sounds" if IS_MAC else "Windows sounds"))])]
                  if IS_MAC or os.name == "nt" else []),
                item("Test sounds", icon="speaker", help="Hear each sound the pet makes, and when it plays it.",
                     submenu=[
                         item("Needs you: a permission prompt or a question", lambda: app.pet.beep(True, force=True),
                              help="Also plays as a reminder while the session keeps waiting for you."),
                         item("Error: a session hit an error", lambda: app.pet.beep(True, force=True, kind="error"),
                              help="Two low falling notes: something went wrong and needs a look."),
                         item("Done: a session finished its work", lambda: app.pet.beep(False, force=True),
                              help="Plays when a working session finishes (switch off with sound_on_done in config)."),
                     ]),
            ]),
            item("Session timing", icon="clock", help="How long the pet waits for things.", submenu=[
                item("Answer timeout...", app.pet.open_answer_slider,
                     help="How long a permission prompt waits for your answer from the pet. 0 means no limit.",
                     slider={"value": preferences.ANSWER_WAIT["v"], "min": 0, "max": preferences.MAX_WAIT, "step": 15,
                             "set": app.pet.set_answer_wait, "format": textfmt.fmt_wait}),
                item("Clear finished after...", app.pet.open_done_slider,
                     help="How long a finished session's pet stays before it leaves. 0 keeps it until you dismiss it.",
                     slider={"value": preferences.DONE_TIMEOUT["v"], "min": 0, "max": preferences.MAX_WAIT // 60, "step": 1,
                             "set": app.pet.set_done_timeout,
                             "format": lambda v: "never" if v <= 0 else f"{int(v)} min"}),
                item("Health check every...", app.pet.open_health_slider,
                     help="How often the pet checks that sessions are still running. 0 turns the check off.",
                     slider={"value": preferences.HEALTH["v"], "min": 0, "max": preferences.MAX_HEALTH, "step": 5,
                             "set": app.pet.set_health_interval,
                             "format": lambda v: "off" if v <= 0 else textfmt.fmt_wait(v)}),
            ]),
            None,
            *([item("Start at login" if IS_MAC else "Start with Windows", app.toggle_autostart,
                    checked=app.c_autostart, help="Start AIPet automatically when you sign in.")]
              if IS_MAC or os.name == "nt" else []),
        ]),
        item("Integrations", icon="plug", help="Where the pet gets its sessions from.", submenu=[
            item("Claude Code hooks", icon="link", submenu=claude,
                 help="Hooks let Claude Code tell the pet what each session is doing."),
            item("Codex hooks", icon="link", submenu=codex,
                 help="Hooks let Codex tell the pet what each session is doing."),
            item("Extra", icon="wrench", submenu=[
                item("Claude account usage (unofficial)", app.toggle_claude_oauth_usage,
                     checked=bool(cfg.get("claude_oauth_usage", False)),
                     help="Reads your Claude plan's usage from an unofficial endpoint. Experimental; asks before turning on."),
                None,
                item("Run setup again...", app.show_setup, help="The first-run setup: pick which installs to hook up."),
                item("Re-detect / refresh status", refresh, help="Look again for Claude Code, Codex and WSL installs."),
                item(f"Workbench: {app.c_wb}", enabled=False),
            ]),
        ]),
        item("Permissions" + (" (auto approve ON)" if app.approvals.targets_on else ""), icon="shield",
             help="Answering permission prompts.", submenu=[
            item("Auto approve", icon="check", submenu=auto,
                 help="Approve every permission prompt from these installs without asking. Use with care."),
            item("Answer Codex prompts from the pet", lambda: app.pet.answers.set_codex_answers(not cfg.get("codex_answers")),
                 checked=bool(cfg.get("codex_answers")),
                 help="Codex permission prompts get Allow / Deny buttons in the pet's popup."),
            item("Answer Claude Code prompts from the pet", lambda: app.pet.answers.set_claude_answers(not cfg.get("claude_answers", True)),
                 checked=bool(cfg.get("claude_answers", True)),
                 help="Claude Code permission prompts get Allow / Deny buttons in the pet's popup."),
        ]),
        None,
        item("Help", icon="help", help="About AIPet, updates and troubleshooting.", submenu=[
            item("About AIPet...", app.show_about),
            item(f"Version: {app.version}", enabled=False),
            item("Updates", icon="refresh", submenu=[
                item("Check for updates...", lambda: app.updates.check(True)),
                item("Check for updates automatically", app.updates.toggle_auto,
                     checked=bool(cfg.get("update_check", True)),
                     help="Look for a new AIPet now and then. Nothing is installed without asking."),
            ]),
            None,
            item("Diagnostics", icon="wrench", help="For when something isn't working.", submenu=[
                item("Save diagnostics...", lambda: app_diagnostics.save(app.pet),
                     help="Saves a report you can attach to a bug report."),
                item("Open config folder", app.open_config, help="Where AIPet keeps its settings and logs."),
                item("Log hook events (debug)", app.toggle_debug, checked=os.path.exists(paths.DEBUG_FLAG),
                     help="Writes what the hooks report to events.log (no prompt text)."),
            ]),
        ]),
        None,
        item("Quit AIPet", app.quit),
    ]


def _live_spec(app, item, settings=False):
    """Live Management: the timed auto approve (per session, with its countdown) and each session's subagents.
    Menus show exact countdowns (they are built when opened); the settings window rounds to minutes so it
    doesn't redraw every second."""
    grants, now = permissions.timed_grants(), time.time()
    default = app.pet.answers.timed_minutes()

    def left(grant):
        secs = grant["until"] - now
        return f"{max(1, int(-(-secs // 60)))} min left" if settings else f"{textfmt.fmt_left(secs)} left"

    spec = [item("Timed auto approve length", choice=True,
                 help='How long "Auto approve for ..." lasts. Every start asks you to confirm first.',
                 slider={"value": default, "min": permissions.TIMED_MIN, "max": permissions.TIMED_MAX, "step": 1,
                         "set": app.pet.answers.set_timed_minutes, "format": lambda v: f"{int(v)} min"},
                 submenu=[item(f"{m} min", lambda m=m: app.pet.answers.set_timed_minutes(m), checked=default == m)
                          for m in (5, 10, 15, 30, 60)]),
            dict(app.agent_windows.menu_item(None), icon="link"),
            app.agent_windows.separate_item(),
            None]
    sessions = [d for d in app.pet._last_items if d.get("source") == "CC" and d.get("session_id")]
    for d in sessions:
        grant = grants.get(str(d["session_id"]))
        name = d.get("title") or "session"
        if d.get("conv"):
            name += f" · {d['conv']}"
        state = pet_style.LABELS.get(d.get("state"), d.get("state") or "")
        agents = d.get("agent_types") or []
        entries = ([item(f"Auto approving · {left(grant)}", enabled=False),
                    item("Stop auto approving", lambda sid=d["session_id"]: app._stop_timed(sid))]
                   if grant else
                   [item(f"Auto approve for {default} min…", lambda d=d: app._start_timed(d),
                         help="Approves every permission prompt from this session until the timer ends "
                              "(asks to confirm first). Questions from the AI still ask you.")])
        entries += [None, item(f"Subagents running: {len(agents)}" if agents else "No subagents running",
                               enabled=False)]
        entries += [item(f"• {a}", enabled=False) for a in agents]
        entries += [None, item("Agent network...", lambda k=d.get("key"): app.agent_windows.show(k))]
        said = session_text.last_said(d)  # the newest reply phrase or your latest prompt, from the transcript
        who = "Codex" if d.get("agent") == "codex" else "Claude"
        said_text = (f"{who if said[0] == 'ai' else 'You'}: “{said[1]}”" if said else "")
        if said_text:
            entries = [item(said_text, enabled=False), None] + entries
        spec.append(item(f"{name}  ({state})" + (f"  ⏱ {left(grant)}" if grant else ""), submenu=entries,
                         icon="check" if grant else None,
                         help="\n".join(x for x in (d.get("where"), said_text) if x) or None))
    if not sessions:
        spec.append(item("No sessions right now", enabled=False))
    return spec
