"""Answering the AI from the pet: handing your click to the waiting hook, letting go of a prompt, timed auto
approve, whitelisting a prompt, clearing a stale one, and whether the pet answers Claude Code / Codex at all."""
import json
import os
import re
import time

from .. import permissions
from ..runtime import diagnostics
from ..runtime import paths
from ..runtime import settings as runtime_settings
from ..ui import theme


class Answers:
    """Writes the answers the hooks wait for (<pet dir>/answers/<session>.json) and the flags they read
    (no-claude-answers, codex-answers). No state of its own beyond the config.

    Needs from the app: root, cfg, _last_items, focus_key(), sync_bubbles(), and its menu's claude_answer_var /
    codex_answer_var.
    """

    def __init__(self, app):
        self.app = app

    def send(self, key, behavior, answers=None, permissions=None):
        """Hand the user's click to the waiting PermissionRequest hook. Returns True if it was written.
        answers: for a question, {question text: label, or [labels] for multi-select}.
        permissions: Claude Code's own suggestions to apply with the allow (its "don't ask again" choices)."""
        app = self.app
        item = next((i for i in app._last_items if i["key"] == key), None)
        req = (item or {}).get("request") or {}
        if not self.enabled(item) or not permissions.hook_waiting(item) or behavior not in ("allow", "deny", "release"):
            return False
        name = permissions.answer_name(item)
        try:
            os.makedirs(permissions.answers_dir(), exist_ok=True)
            path = os.path.join(permissions.answers_dir(), name + ".json")
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(dict({"behavior": behavior, "t": time.time()}, **({"answers": answers} if answers else {}),
                               **({"permissions": permissions} if permissions else {})), f)
            os.replace(path + ".tmp", path)
            return True
        except OSError:
            return False

    def go_to_prompt(self, key):
        """Go to window for a session that is asking: Codex shows its own prompt only once AIPet's hook lets go of
        it, so let go first (its own prompt has choices the pet doesn't, like always allowing a tool). Claude Code
        shows its prompt alongside anyway, so the pet's buttons stay."""
        app = self.app
        item = next((i for i in app._last_items if i["key"] == key), None)
        if item and item.get("agent") == "codex" and item.get("state") == "needs_input":
            self.send(key, "release")
        app.focus_key(key)

    def timed_minutes(self):
        app = self.app
        try:
            return max(permissions.TIMED_MIN, min(permissions.TIMED_MAX, int(app.cfg.get("timed_approve_minutes", permissions.TIMED_DEFAULT))))
        except (TypeError, ValueError):
            return permissions.TIMED_DEFAULT

    def set_timed_minutes(self, minutes):
        app = self.app
        app.cfg["timed_approve_minutes"] = max(permissions.TIMED_MIN, min(permissions.TIMED_MAX, int(round(float(minutes)))))
        runtime_settings.save_setting("timed_approve_minutes", app.cfg["timed_approve_minutes"])

    def start_timed(self, item, minutes=None):
        """Auto approve every permission prompt from this session for N minutes, after a safety confirmation.
        Returns True once the grant is running."""
        app = self.app
        if not item or not item.get("session_id"):
            return False
        minutes = minutes or self.timed_minutes()
        name = item.get("title") or "this session"
        if item.get("conv"):
            name += f" \u00b7 {item['conv']}"
        ok = theme.themed_dialog(
            app.root, "AIPet", kind="warning", heading=f"Auto approve {name} for {minutes} min?",
            text=(f"Every permission prompt from this session (commands, file edits, tool calls) will be approved "
                  f"without asking for the next {minutes} minutes. Other sessions keep asking, and questions from the "
                  "AI still wait for you.\n\nLive Management in the right-click menu shows the countdown and can "
                  "stop it early. It also stops when AIPet quits."),
            buttons=(("Cancel", False, "secondary"), (f"Auto approve {minutes} min", True, "danger")),
            cancel=False, enter_confirms=False)
        if not ok:
            return False
        return permissions.set_timed_grant(item["session_id"], minutes, name)

    def stop_timed(self, session_id):
        permissions.stop_timed_grant(session_id)

    def clear_prompt(self, key, req_id):
        """Drop one permission prompt from a session that still shows it although nobody is asking anymore (its
        subagent died on an API error, say). A hook still waiting for it notices and lets go."""
        app = self.app
        item = next((i for i in app._last_items if i["key"] == key), None)
        path = (item or {}).get("path")
        if not path or not req_id:
            return False
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            reqs = [r for r in (rec.get("requests") or ([rec["request"]] if rec.get("request") else []))
                    if isinstance(r, dict) and r.get("id") != req_id]
            now = time.time()
            rec.update(requests=reqs, request=reqs[0] if reqs else {}, updated=now)
            if not reqs and rec.get("state") == "needs_input":
                rec.update(state="working", message="", wait_agent="", changed=now)
            elif reqs:
                rec["message"] = f"Claude needs your permission to use {reqs[0].get('tool') or 'a tool'}"
            tmp = path + ".pet.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            os.replace(tmp, path)
            return True
        except (OSError, ValueError) as e:
            diagnostics.log_error(f"clear prompt: {e!r}")
            return False

    def whitelist(self, item):
        """Add the prompt on this session's card to its hook config's auto-approve whitelist, as an exact match:
        the whole command (or path, URL...), or the tool itself when it has no such argument (MCP tools). Returns a
        line for the card, or None if nothing was saved."""
        req = (item or {}).get("request") or {}
        tool = req.get("tool") or ""
        if not tool:
            return None
        subject = req.get("subject") if req.get("primary") else ""
        pattern = re.escape(subject) if subject else re.escape(tool)
        key = permissions.auto_approve_key_for(item)
        rules = permissions.auto_approve_rules().get(key) or permissions.default_auto_rules()
        white = [x for x in rules.get("whitelist") or [] if str(x).strip()]
        if pattern not in white:
            white.append(pattern)
        black = rules.get("blacklist")
        if black == permissions.DEFAULT_BLACKLIST and len(white) == 1:
            # the default "ask for everything" would keep the whitelist from ever applying; with a fresh list of exact
            # matches only, nothing broader gets through
            black = []
        rules = dict(rules, enabled=True, whitelist=white, blacklist=black)
        if not permissions.save_auto_approve(key, rules):
            return None
        blocked = any(permissions._search(p, subject or tool) for p in (black or []))
        what = "this exact command" if subject else f"every {tool} request"
        return (f"Allowed, and {what} is now on the auto-approve whitelist ({key})."
                + (" Your blacklist still asks for it, though." if blocked else ""))

    def enabled(self, item):
        app = self.app
        agent = "codex" if (item or {}).get("agent") == "codex" else "claude"
        return bool(app.cfg.get(agent + "_answers", True))

    def set_claude_answers(self, on):
        """Choose whether Claude hooks wait for an answer from the pet."""
        app = self.app
        app.cfg["claude_answers"] = bool(on)
        runtime_settings.save_setting("claude_answers", app.cfg["claude_answers"])
        app.claude_answer_var.set(app.cfg["claude_answers"])
        self.write_claude_flag()
        app.sync_bubbles(app._last_items)

    def write_claude_flag(self):
        # An opt-out preserves Claude answering for existing installations.
        app = self.app
        flag = os.path.join(paths.HOME_DIR, "no-claude-answers")
        try:
            if not app.cfg.get("claude_answers", True):
                os.makedirs(paths.HOME_DIR, exist_ok=True)
                open(flag, "w").close()
            elif os.path.exists(flag):
                os.remove(flag)
        except OSError:
            pass

    def set_codex_answers(self, on):
        """Codex asks its hooks before showing its own prompt: with this on, the pet waits for your click first (up to
        the answer timeout), so Codex's prompt only appears if you don't answer on the pet."""
        app = self.app
        app.cfg["codex_answers"] = bool(on)
        runtime_settings.save_setting("codex_answers", app.cfg["codex_answers"])
        if app.codex_answer_var.get() != app.cfg["codex_answers"]:
            app.codex_answer_var.set(app.cfg["codex_answers"])
        self.write_codex_flag()
        app.sync_bubbles(app._last_items)

    def write_codex_flag(self):
        app = self.app
        flag = os.path.join(paths.HOME_DIR, "codex-answers")  # read by the hooks, including WSL ones (shared folder)
        try:
            if app.cfg.get("codex_answers"):
                os.makedirs(paths.HOME_DIR, exist_ok=True)
                open(flag, "w").close()
            elif os.path.exists(flag):
                os.remove(flag)
        except OSError:
            pass
