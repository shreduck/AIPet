"""Auto approve rules per hook target: the rules window (on/off, Allow all, whitelist and blacklist), its warning,
and the menu entries that list the targets."""
import os
import sys
import tkinter as tk

from .. import permissions
from ..integrations import targets
from ..ui import sprites
from ..ui import theme

IS_MAC = sys.platform == "darwin"


class Approvals:
    """Owns the open rules windows (by target key) and which targets have auto approve on (targets_on, read by the
    tray menu from its own thread).

    Needs from the app: root, distros, codex_targets, status (hook status per target), refresh_menu() and
    _icon_key (cleared so the tray tooltip mentions auto approve).
    """

    def __init__(self, app):
        self.app = app
        self.editors = {}
        self.targets_on = permissions.auto_approve_targets()  # hook configs with auto-approval on (all off by default)

    def sections(self):
        """[(section title, [(label, key)])] for every hook config that can be auto-approved, plus any switched-on key
        that isn't detected right now, so it can always be switched off again."""
        app = self.app
        claude = [("This Mac" if IS_MAC else "This PC (Windows)", targets.LOCAL)]
        claude += [(f"WSL: {n}", "wsl:" + n) for n, _ in app.distros]
        if os.name == "nt" or IS_MAC:
            claude.append(("Cowork (Claude desktop app)", "cowork"))
        sections = [("Claude Code", claude), ("Codex", list(app.codex_targets))]
        known = {k for _, items in sections for _, k in items}
        other = [(k, k) for k in self.targets_on if k not in known]
        if other:
            sections.append(("Not detected now", other))
        return sections

    def label(self, label, key):
        """Menu text: the config, what its rules do when switched on, and a note when its hooks aren't installed."""
        app = self.app
        rules = permissions.auto_approve_rules().get(key)
        text = label
        if rules and rules.get("enabled"):
            n = len([x for x in rules.get("whitelist") or [] if x.strip() and not x.strip().startswith("#")])
            text += " - allow all" if rules.get("allow_all") else f" - {n} whitelist rule{'s' if n != 1 else ''}"
        st = app.status.get(key, "")
        if key != "cowork" and st and not st.startswith("installed") and not any(
                w in st for w in ("unknown", "checking", "n/a")):
            text += "  (hooks not installed)"
        return text + "..."

    def open_editor(self, key, label):
        """The rules window of one hook config: on/off, Allow all, and a whitelist and a blacklist of regexes."""
        app = self.app
        wins = self.editors
        if key in wins:
            try:
                wins[key].lift()
                return
            except tk.TclError:
                wins.pop(key, None)
        old = permissions.auto_approve_rules().get(key) or permissions.default_auto_rules()
        win = wins[key] = tk.Toplevel(app.root)
        agent_label = "Codex" if targets.is_codex(key) else "Claude Code"
        win.title(f"AIPet - auto approve - {agent_label} - {label}")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        accent = "#5b8def" if targets.is_codex(key) else "#d98960"
        frame = tk.Canvas(win, highlightthickness=0, bd=0)
        frame.pack()
        body = tk.Frame(frame)
        frame.create_window(14, 14, window=body, anchor="nw")

        def fit_card(_event=None):
            w, h = body.winfo_reqwidth() + 28, body.winfo_reqheight() + 28
            frame.configure(width=w, height=h)
            frame.delete("outline")
            frame.create_polygon(8, 2, w - 8, 2, w - 8, 5, w - 3, 5, w - 3, 8, w - 2, 8,
                                 w - 2, h - 8, w - 5, h - 8, w - 5, h - 3, w - 8, h - 3, w - 8, h - 2,
                                 8, h - 2, 8, h - 5, 3, h - 5, 3, h - 8, 2, h - 8, 2, 8, 5, 8, 5, 3, 8, 3,
                                 fill="", outline=accent, width=2, tags="outline")
        body.bind("<Configure>", fit_card)
        grey = "#6b7280"
        enabled = tk.BooleanVar(value=bool(old.get("enabled")))
        allow_all = tk.BooleanVar(value=bool(old.get("allow_all")))

        tk.Label(body, text="AUTO APPROVAL", fg=accent, font=(sprites.MONO_FAMILY, 9, "bold")).pack(anchor="w")
        tk.Label(body, text=label, font=(sprites.MONO_FAMILY, 17, "bold")).pack(anchor="w", pady=(3, 6))
        tk.Label(body, fg=grey, justify="left", wraplength=640, font=(sprites.MONO_FAMILY, 9),
                 text="Choose which requests can continue without asking.\nRequests outside your rules still need your approval."
                 ).pack(anchor="w", pady=(0, 14))
        mode = tk.LabelFrame(body, text=" Approval mode ", font=(sprites.MONO_FAMILY, 10, "bold"),
                             relief="solid", bd=1, padx=10, pady=8)
        mode.pack(fill="x")
        tk.Checkbutton(mode, text="Auto approve for this config", variable=enabled, font=(sprites.MONO_FAMILY, 10, "bold")
                       ).pack(anchor="w")
        tk.Checkbutton(mode, text="Allow all - approve every request (ignores both lists)",
                       variable=allow_all, font=(sprites.MONO_FAMILY, 9)).pack(anchor="w", pady=(4, 0))
        mode_hint = tk.Label(mode, fg=grey, justify="left", wraplength=620, font=(sprites.MONO_FAMILY, 8))
        mode_hint.pack(anchor="w", padx=4, pady=(6, 0))

        cols = tk.Frame(body)
        cols.pack(fill="x", pady=(16, 0))
        boxes = {}
        for col, (name, title, hint, lines) in enumerate((
                ("whitelist", "Whitelist - approve automatically",
                 "One regex per line. Must match the WHOLE command (or file path / URL), the tool name, or "
                 "Tool(command). Examples: git (status|diff)   npm test   Read", old.get("whitelist") or []),
                ("blacklist", "Blacklist - always ask me",
                 "One regex per line, matched ANYWHERE in the same texts. Wins over the whitelist. The default .* "
                 "asks for everything: replace it to let the whitelist work. Tip: [;&|`$<>] asks for chained or "
                 "redirected commands, so a whitelisted one can't smuggle in another.", old.get("blacklist")
                 if old.get("blacklist") is not None else permissions.DEFAULT_BLACKLIST))):
            box = tk.Frame(cols)
            box.grid(row=0, column=col, sticky="nw", padx=(0 if col == 0 else 12, 0))
            tk.Label(box, text=title, font=(sprites.MONO_FAMILY, 9, "bold")).pack(anchor="w", pady=(0, 5))
            tk.Label(box, text=hint, fg=grey, justify="left", anchor="nw", height=6,
                     wraplength=300, font=(sprites.MONO_FAMILY, 8)).pack(anchor="w")
            text = tk.Text(box, width=40, height=9, wrap="none", font=(sprites.MONO_FAMILY, 9), undo=True, padx=8, pady=8)
            text.insert("1.0", "\n".join(lines))
            text.pack(anchor="w", fill="x", pady=(4, 0))
            boxes[name] = text
        tk.Label(body, fg=grey, justify="left", wraplength=640, font=(sprites.MONO_FAMILY, 8),
                 text="Empty lines and lines starting with # are ignored. Only works while AIPet is running and its "
                      "hooks are installed for this config; the VS Code extension sends no permission events."
                 ).pack(anchor="w", pady=(10, 0))
        error = tk.Label(body, fg="#b91c1c", justify="left", wraplength=640, font=(sprites.MONO_FAMILY, 9))
        error.pack(anchor="w", padx=16)

        def sync(*_):  # Allow all switches the lists off
            state = "disabled" if allow_all.get() else "normal"
            for t in boxes.values():
                t.configure(state=state, fg=theme.T["muted"] if allow_all.get() else theme.T["entry_fg"])
            mode_hint.configure(text="Auto approval is off. Requests still need your approval." if not enabled.get()
                                else "All requests will be approved. The lists below are ignored." if allow_all.get()
                                else "Whitelist matches may proceed. Blacklist matches always ask you.")
        allow_all.trace_add("write", sync)
        enabled.trace_add("write", sync)

        def close():
            wins.pop(key, None)
            win.destroy()

        pending = {"value": False}

        def finish_save(rules, accepted):
            if not win.winfo_exists():
                return
            try:
                if not accepted:
                    return
                if not permissions.save_auto_approve(key, rules):
                    error.configure(text="Couldn't save these rules. Check that ~/.aipet is writable, then try again.")
                    return
                close()
                self.changed()
            finally:
                pending["value"] = False
                if win.winfo_exists():
                    save_button.configure(state="normal")

        def save():
            if pending["value"]:
                return
            error.configure(text="")
            lists = {}
            for name, t in boxes.items():
                lists[name] = [ln.rstrip() for ln in t.get("1.0", "end-1c").splitlines() if ln.strip()]
            bad = [(name, n, pat, err) for name in lists for n, pat, err in permissions.bad_patterns(lists[name])]
            if bad:
                error.configure(text="Fix these patterns first:\n" + "\n".join(
                    f"{name} line {n}: {pat}  ({err})" for name, n, pat, err in bad[:6]))
                return
            rules = {"enabled": enabled.get(), "allow_all": allow_all.get(), **lists}
            newly_on = rules["enabled"] and not old.get("enabled")
            all_on = rules["enabled"] and rules["allow_all"] and not (old.get("enabled") and old.get("allow_all"))
            pending["value"] = True
            save_button.configure(state="disabled")
            if all_on or newly_on:
                # Return from Aqua's button callback before opening the warning;
                # continue saving asynchronously after the warning is destroyed.
                win.after_idle(lambda: self.warn(label, rules, parent=win,
                               on_result=lambda accepted: finish_save(rules, accepted)))
                return
            finish_save(rules, True)

        buttons = tk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        save_button = tk.Button(buttons, text="Save", width=12, command=save, default="active")
        save_button.pack(side="right")
        tk.Button(buttons, text="Cancel", width=10, command=close).pack(side="right", padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: close())
        theme.theme_window(win)
        sync()
        win.update_idletasks()
        win.geometry(f"+{max(0, (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2)}"
                     f"+{max(0, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)}")
        if IS_MAC:
            import mac_statusbar
            win._mouse_bridge = mac_statusbar.PanelMouseBridge(win)
            win.bind("<Destroy>", lambda event: win._mouse_bridge.close() if event.widget is win else None, add="+")

    def warn(self, label, rules, parent=None, on_result=None):
        app = self.app
        if rules.get("allow_all"):
            heading = f"Auto approve everything from {label}?"
            what = (f"Every permission request that reaches AIPet's hook from {label} will be approved at once, "
                    "without asking you: shell commands, file edits and deletions, web access and any other tool, "
                    "in every session there, including subagents.")
        else:
            n = len([x for x in rules.get("whitelist") or [] if not x.strip().startswith("#")])
            heading = f"Turn on auto approve for {label}?"
            what = (f"Requests from {label} that match your {n} whitelist pattern{'s' if n != 1 else ''} and none of "
                    "your blacklist patterns will be approved without asking you, in every session there, including "
                    "subagents. A loose pattern can approve more than you meant: check them carefully.")
        return theme.themed_dialog(
            parent if parent is not None else app.root, "AIPet - auto approve", kind="warning", heading=heading,
            text=(what + "\n\nIt takes effect immediately for running sessions and only while AIPet is running.\n\n"
                  "Turn it off any time: pet or menu-bar / tray menu > Permissions > Auto approve."),
            buttons=(("Cancel", False, "secondary"), ("Auto approve", True, "danger")),
            cancel=False, enter_confirms=False, on_result=on_result)

    def all_off(self):
        permissions.save_auto_approve(None, None)
        self.changed()

    def changed(self):
        app = self.app
        self.targets_on = permissions.auto_approve_targets()
        app._icon_key = None  # the tray tooltip says when auto approve is on
        app.refresh_menu()
