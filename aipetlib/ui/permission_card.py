"""PermissionCard: rendering and interaction.

This component does not import the application entry point.
"""
import json
import math
import sys
import time
import tkinter as tk

from .. import geometry
from .. import permissions
from .. import text as textfmt
from ..mac_panels import MacOverlayWindow
from ..runtime import preferences
from . import pet_style
from . import sprites
from . import theme

IS_MAC = sys.platform == "darwin"


class PermissionCard:
    """A styled card, centred on the screen, with the full context of a session that needs the user. The robot
    beside it asks with a "?" and reacts when you answer."""

    ACCENT = "#f59e0b"

    def f(self, size, *style, family=None):
        return pet_style.card_font(size, *style, family=family)

    def __init__(self, app, key, item):
        self.app, self.key, self._shown, self.sent = app, key, None, None
        self.pinned = item.get("state") != "needs_input"  # opened from the menu: not a prompt that closes when answered
        self._after, self._t0, self._moved, self._size = None, time.time(), False, None
        bg, fg, msg_fg, muted = theme.T["bubble_bg"], theme.T["bubble_fg"], theme.T["bubble_msg"], theme.T["bubble_muted"]
        w = self.win = MacOverlayWindow(app.root) if IS_MAC else tk.Toplevel(app.root)
        w.withdraw()
        w.title(f"AIPet permission — {key}")
        if IS_MAC:
            w.attributes("-alpha", 0.0)
        w.overrideredirect(True)  # our own frame: accent border, draggable header, close button
        w.attributes("-topmost", True)
        w.configure(bg=self.ACCENT)
        w.bind("<Escape>", lambda e: self.close())
        card = tk.Frame(w, bg=bg)
        card.pack(fill='both', expand=True, padx=2, pady=2)
        tk.Frame(card, bg=self.ACCENT, height=5).pack(fill="x")
        cols = tk.Frame(card, bg=bg)
        cols.pack(fill="both", expand=True, padx=16, pady=(12, 14))

        spr = sprites.load_sprites()
        self.robot = None
        left = tk.Frame(cols, bg=bg)
        left.pack(side="left", anchor="n", fill="y", padx=(0, 16))
        if spr:  # left column: the robot (room below it for its whole shadow)
            sc = preferences.CARD_SCALE["v"]
            self.robot = tk.Canvas(left, width=int(140 * sc), height=int(212 * sc), bg=bg, highlightthickness=0, bd=0)
            self.robot.pack(side="top", anchor="n", pady=(int(12 * sc), 0))
        # size of this card: slider under the robot; also Ctrl + plus / minus / 0 and Ctrl + mouse wheel on the card
        size_row = tk.Frame(left, bg=bg)
        size_row.pack(side="bottom", fill="x", pady=(8, 0))
        self.size_var = tk.DoubleVar(master=w, value=preferences.CARD_SCALE["v"])
        self.size_label = tk.Label(size_row, text=f"{int(round(preferences.CARD_SCALE['v'] * 100))}%", bg=bg, fg=muted,
                                   font=self.f(8), width=5, anchor="e")
        self.size_scale = tk.Scale(size_row, from_=preferences.CARD_SCALE_MIN, to=preferences.CARD_SCALE_MAX, resolution=preferences.CARD_SCALE_STEP,
                                   orient="horizontal", variable=self.size_var, showvalue=False, length=90,
                                   sliderlength=14, width=8, bg=theme.T["primary"], troughcolor=theme.T["entry_bg"],
                                   activebackground=theme.T["primary_active"], sliderrelief="flat", highlightthickness=0,
                                   bd=0, relief="flat",
                                   command=lambda v: self.size_label.configure(text=f"{int(round(float(v) * 100))}%"))
        self.size_scale.pack(side="left")
        self.size_label.pack(side="left")
        self.size_scale.bind("<ButtonRelease-1>", lambda e: self.app.preferences.set_card_scale(self.size_var.get()))
        mod = "Command" if IS_MAC else "Control"
        for seq, step in (("plus", 1), ("equal", 1), ("KP_Add", 1), ("minus", -1), ("KP_Subtract", -1),
                          ("0", 0), ("KP_0", 0)):
            w.bind(f"<{mod}-{seq}>", lambda e, s=step: self.zoom_by(s))
        w.bind("<MouseWheel>", lambda e: self.zoom_by(1 if e.delta > 0 else -1)
               if e.state & (0x8 if IS_MAC else 0x4) else None)
        w.bind("<Control-Button-4>", lambda e: self.zoom_by(1))
        w.bind("<Control-Button-5>", lambda e: self.zoom_by(-1))
        right = tk.Frame(cols, bg=bg, width=430)
        right.pack(side="left", fill="both", expand=True, anchor="n")

        self.blocked_banner = tk.Label(right, text="BLOCKED BY BLACKLIST", bg="#b91c1c", fg="white",
                                       font=self.f(12, "bold"), padx=14, pady=8, anchor="e")
        top = self.header = tk.Frame(right, bg=bg)
        top.pack(fill="x")
        self.head = tk.Label(top, bg=bg, fg=fg, anchor="w", font=self.f(15, "bold"))
        self.head.pack(side="left", fill="x", expand=True)
        close = tk.Label(top, text="\u2715", bg=bg, fg=muted, cursor="hand2", font=self.f(12), padx=4)
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        self.status = tk.Label(right, text="needs your permission", bg=bg, fg=self.ACCENT, anchor="w",
                               font=self.f(9, "bold"))
        self.status.pack(fill="x")
        self.where = tk.Label(right, bg=bg, fg=muted, anchor="w", justify="left", wraplength=430, font=self.f(8))
        self.where.pack(fill="x", pady=(2, 0))
        tk.Frame(right, bg=theme.T["border"], height=1).pack(fill="x", pady=8)
        for widget in (top, self.head):  # drag the card by its header
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        text_area = tk.Frame(right, bg=bg)
        text_area.pack(fill="both", expand=True)
        self.prompt_arrows = []
        for step, text, side in ((-1, "‹", "left"), (1, "›", "right")):
            arrow = tk.Label(text_area, text=text, bg=bg, fg=self.ACCENT, cursor="hand2",
                             font=self.f(18, "bold"), padx=6, takefocus=True)
            arrow.bind("<Button-1>", lambda e, d=step: self.app.answers.step_prompt(self.key, d))
            arrow.bind("<Return>", lambda e, d=step: self.app.answers.step_prompt(self.key, d))
            arrow.bind("<space>", lambda e, d=step: self.app.answers.step_prompt(self.key, d))
            arrow.bind("<Enter>", lambda e, a=arrow: a.configure(bg=theme.T["btn_active"]))
            arrow.bind("<Leave>", lambda e, a=arrow: a.configure(bg=theme.T["bubble_bg"]))
            self.prompt_arrows.append((arrow, side))
        self.body = tk.Frame(text_area, bg=bg)
        self.body.pack(side="left", fill="both", expand=True)
        self.ask = tk.Label(self.body, bg=bg, fg=fg, anchor="w", justify="left", wraplength=430, font=self.f(11, "bold"))
        self.desc = tk.Label(self.body, bg=bg, fg=msg_fg, anchor="w", justify="left", wraplength=430, font=self.f(10))
        self.code = tk.Text(self.body, height=1, width=60, wrap="word", bg="#0b1220", fg="#d1fae5", relief="flat",
                            borderwidth=0, highlightthickness=0, padx=10, pady=8, font=self.f(9, family=sprites.MONO_FAMILY), state="disabled",
                            insertbackground="#d1fae5")
        wait_row = tk.Frame(right, bg=bg)
        wait_row.pack(fill="x", pady=(8, 0))
        self.wait = tk.Label(wait_row, bg=bg, fg=muted, anchor="w", font=self.f(9))
        self.wait.pack(side="left")
        self.golink = tk.Label(wait_row, text="Go to window ›", bg=bg, fg=self.ACCENT, cursor="hand2", padx=8, pady=1,
                               highlightthickness=1, highlightbackground=self.ACCENT, highlightcolor=self.ACCENT,
                               font=self.f(9, "bold"))
        self.golink.bind("<Button-1>", lambda e: self.go())
        self.golink.bind("<Enter>", lambda e: self.golink.configure(bg=theme.T["btn_active"]))
        self.golink.bind("<Leave>", lambda e: self.golink.configure(bg=theme.T["bubble_bg"]))
        self.note = tk.Label(right, bg=bg, fg=muted, anchor="w", justify="left", wraplength=430, font=self.f(8))
        self.note.pack(fill="x", pady=(2, 8))
        # Escape hatch for a prompt that is gone (its agent died, it was settled elsewhere) but still shows here
        self.clearlink = tk.Label(right, text="Not asking anymore? Clear this prompt", bg=bg, fg=muted, cursor="hand2",
                                  anchor="w", font=self.f(8, "underline"))
        self.clearlink.bind("<Button-1>", lambda e: self.clear_prompt())
        # Timed auto approve for this session (asks to confirm first)
        self.timed_row = tk.Frame(right, bg=bg)
        self.timedlink = tk.Label(self.timed_row, bg=bg, fg=theme.themed_color("#b45309"), cursor="hand2", anchor="w",
                                  font=self.f(8, "underline"))
        self.timedlink.pack(anchor="w")
        self.timedlink.bind("<Button-1>", lambda e: self.auto_approve_for_a_while())
        self.timed_var = tk.IntVar(master=w, value=self.app.answers.timed_minutes())
        self.timed_scale = tk.Scale(
            self.timed_row, from_=permissions.TIMED_MIN, to=permissions.TIMED_MAX, resolution=1,
            orient="horizontal", variable=self.timed_var, showvalue=False, sliderlength=14, width=8,
            bg=theme.T["primary"], troughcolor=theme.T["entry_bg"],
            activebackground=theme.T["primary_active"], sliderrelief="flat", highlightthickness=0,
            bd=0, relief="flat", command=self._preview_timed_minutes, takefocus=True)
        self.timed_scale.pack(anchor="w", pady=(2, 0))
        self.timedlink.bind("<Configure>", lambda e: self.timed_scale.configure(length=e.width))
        self.timed_scale.bind("<ButtonRelease-1>", self._save_timed_minutes)
        self.timed_scale.bind("<KeyRelease>", self._save_timed_minutes)
        # Questions (Claude's AskUserQuestion): one block per question, options as toggles, plus an own-answer field
        self.qframe = tk.Frame(self.body, bg=bg)
        self._qid, self._qvars = None, []
        self._question_drafts = {}
        self._view, self._timed_grant = None, False  # what the card last laid out (see update)
        self.q_row = tk.Frame(right, bg=bg)
        self.btn_send = tk.Button(self.q_row, text="Send answer", command=self.send_answers,
                                  **dict(theme.button_style("primary"), font=self.f(10, "bold")))
        self.btn_send.pack(side="right", ipady=2)
        self.answer_row = tk.Frame(right, bg=bg)
        self.btn_deny = tk.Button(self.answer_row, text="Deny", width=12, bg=bg, fg=theme.themed_color("#dc2626"),
                                  activebackground=theme.T["btn_active"], activeforeground=theme.themed_color("#b91c1c"),
                                  relief="flat", bd=0, highlightthickness=1, highlightbackground=theme.themed_color("#dc2626"),
                                  cursor="hand2",
                                  font=self.f(10, "bold"), command=lambda: self.answer("deny"))
        dark = theme.T.get("name") == "dark"  # dark theme: black text on a brighter green
        allow_fg, allow_bg, allow_active = ("#111827", "#22c55e", "#16a34a") if dark else ("white", "#16a34a", "#15803d")
        self.btn_allow = tk.Button(self.answer_row, text="Allow once", width=14, bg=allow_bg, fg=allow_fg,
                                   activebackground=allow_active, activeforeground=allow_fg, relief="flat", cursor="hand2",
                                   font=self.f(10, "bold"), command=lambda: self.answer("allow"))
        if IS_MAC:
            self.btn_allow.configure(**theme.button_style("primary"))
            self.btn_deny.configure(**theme.button_style("danger"))
        # Allow + whitelist: allows this one and adds it to the hook config's auto-approve whitelist (lighter green)
        wl_bg, wl_fg, wl_active = ("#14532d", "#bbf7d0", "#166534") if dark else ("#dcfce7", "#166534", "#bbf7d0")
        self.btn_whitelist = tk.Button(self.answer_row, text="Allow + whitelist", bg=wl_bg, fg=wl_fg,
                                       activebackground=wl_active, activeforeground=wl_fg, relief="flat", bd=0,
                                       highlightthickness=1, highlightbackground="#16a34a", cursor="hand2",
                                       font=self.f(10, "bold"), command=self.allow_and_whitelist)
        self.btn_deny.pack(side="left", ipady=3)
        self.btn_allow.pack(side="right", ipady=4)
        self.btn_whitelist.pack(side="right", ipady=3, padx=(0, 8))
        self.answer_row.pack(fill="x")
        self.go_row = tk.Frame(right, bg=bg)
        self.btn_go = tk.Button(self.go_row, text="Go to window", bg=self.ACCENT, fg="#111827", activebackground="#d97706",
                                activeforeground="#111827", relief="flat", cursor="hand2", font=self.f(10, "bold"),
                                command=self.go)
        self.btn_go.pack(side="left", ipady=4, ipadx=14)
        if IS_MAC:
            # These two also use Aqua's light native face, regardless of the
            # custom colours requested above or the host's dark appearance.
            for button in (self.btn_whitelist, self.btn_go):
                button.configure(**theme.button_style())
        self.update(item)
        self.apply_scale()
        self._place(first=True)
        from .window_resize import corner_grip
        self.resize_grip = corner_grip(w, minimum=(420, 300), on_resize=lambda: setattr(self, '_moved', True))
        w.deiconify()
        if IS_MAC:
            import mac_statusbar as mac
            w.update_idletasks()
            mac.set_all_spaces(w.title(), app.cfg.get("all_spaces", True))
            # Tk 8.6 skips native panel mouse events. Forward this card's events
            # so buttons, text selection, and dragging still work without activation.
            self._mouse_bridge = mac.PanelMouseBridge(w)
            w.bind("<Destroy>", lambda event: self._mouse_bridge.close()
                   if event.widget is w else None, add="+")
            w.attributes("-alpha", 1.0)
        else:
            w.focus_force()
        self._tick()

    # ---- placement: centred on the screen, cascading a little when several are open
    def _place(self, first=False):
        w = self.win
        w.update_idletasks()
        size = (w.winfo_reqwidth(), w.winfo_reqheight())
        if size == self._size and not first:
            return
        self._size = size
        if self._moved:
            return
        n = len(self.app.details) - (0 if self.key in self.app.details else 0)
        n = max(0, n - (1 if self.key in self.app.details else 0))
        root = self.app.root  # centre on the monitor the pets are on
        ref = (root.winfo_x() + root.winfo_width() // 2, root.winfo_y() + root.winfo_height() // 2)
        left, top, right, bottom = geometry.work_area(*ref, w) or (0, 0, w.winfo_screenwidth(), w.winfo_screenheight())
        x = left + (right - left - size[0]) // 2 + 28 * n
        y = max(top + 20, top + (bottom - top - size[1]) // 2 - 30 + 28 * n)
        w.geometry(geometry.geo(x, y))

    def _fit_code(self):
        """Size the command box to its real wrapped line count (long paths wrap mid-word, so estimates clip)."""
        try:
            if not self.code.winfo_ismapped():
                return
            self.code.config(state="normal", height=40)
            self.code.update_idletasks()
            n = self.code.count("1.0", "end-1c", "displaylines")  # number of line BREAKS, so rows = breaks + 1
            rows = (int(n[0]) if n else 0) + 1
            self.code.config(height=max(1, min(16, rows)))
            self.code.config(state="disabled")
            self._place()
        except tk.TclError:
            pass

    def _drag_start(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())
        self._moved = True

    def _drag_move(self, e):
        dx, dy = self._drag
        self.win.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    # ---- the robot: asks while waiting, reacts to your answer
    def _tick(self):
        try:
            if not self.win.winfo_exists():
                return
            if self.robot is not None:
                self._draw_robot(time.time() - self._t0)
        except tk.TclError:
            return
        self._after = self.win.after(60, self._tick)

    def _draw_robot(self, t):
        spr = sprites.load_sprites()
        if not spr:
            return
        c = self.robot
        c.delete("all")
        if self.sent in ("Allow once", "Answer"):
            face, state, dy = "joy", "done", -abs(math.sin(t * 6)) * 4
        elif self.sent == "Deny":
            face, state, dy = "worried", "error", 0.0
        elif getattr(self, "_state", "needs_input") != "needs_input" and self._state in sprites.STATE_FACE:
            face, state, dy = sprites.STATE_FACE[self._state], self._state, -abs(math.sin(t * 2)) * 3
        else:
            face, state, dy = "ask", "needs_input", -abs(math.sin(t * 4)) * 10
        rw, rh = spr["meta"]["robot"]["size"]
        sc = preferences.CARD_SCALE["v"]
        z, cx, base, dy = 3 * sc, 70 * sc, 186 * sc, dy * sc
        shadow = spr["img"]["shadow"]
        sw = int(rw * z * 1.1 * (1 - min(0.35, abs(dy) / (40 * sc))))
        c.create_image(cx, base + 4 * sc, image=sprites.sprite_photo("popup-shadow", shadow, sw, max(2, sw // 4)),
                       anchor="center")
        lights = sprites.light_cycle(state, t)
        im = sprites.robot_image(face, lights, spr)
        c.create_image(cx, base + dy, image=sprites.sprite_photo(("popup", face, lights), im, int(rw * z), int(rh * z)),
                       anchor="s")
        if state == "needs_input":  # floating question marks
            for i in range(3):
                ph = (t * 0.6 + i / 3) % 1
                c.create_text((26 + i * 44 + 8 * math.sin(t * 3 + i)) * sc, (60 - ph * 34) * sc, text="?", fill=self.ACCENT,
                              font=self.f(int(9 + 7 * (1 - ph)), "bold"))

    def update(self, item):
        """Called with the session's latest data on every pet refresh (about every 0.7 s). The card is laid out again
        only when something it shows changed (the prompt, whether it can be answered here, the session's title...);
        otherwise only the texts that tick are refreshed. Re-packing an unchanged card on every poll was wasted work,
        and on Windows it left the question options unpainted until the pointer passed over them."""
        req = item.get("request") or {}
        state = item.get("state", "needs_input")
        if self.sent and state == "needs_input" and req.get("id") and req.get("id") != getattr(self, "_sent_id", None):
            self.sent = None  # that prompt is settled and the next queued one is showing: answer it too
        view = self._view_of(item)
        self._item, self._state = item, state
        if view == self._view:
            return self._tick_texts(item)
        self._view = view
        for arrow, side in getattr(self, "prompt_arrows", []):
            arrow.pack_forget()
            if state == "needs_input" and len(item.get("requests") or []) > 1:
                arrow.pack(side=side, fill="y", before=self.body)
        self._render(item)
        live = {r.get("id") for r in item.get("requests") or [req]}
        if hasattr(self, "_question_drafts"):
            self._question_drafts = {rid: draft for rid, draft in self._question_drafts.items() if rid in live}

    def _view_of(self, item):
        """Everything the card's layout and texts depend on, except the ticking ones (how long it has waited, a timed
        auto approve's countdown)."""
        req = item.get("request") or {}
        can_answer = bool(self.app.answers.enabled(item) and permissions.hook_waiting(item) and not self.sent)
        stop = None if can_answer or self.sent or not req else permissions.hook_stop(item, permissions.answers_dir())
        grant = permissions.timed_grants().get(str(item.get("session_id"))) if can_answer else None
        return (item.get("state", "needs_input"), self.sent, getattr(self, "_sent_id", None),
                json.dumps(req, sort_keys=True, default=str), item.get("pending", 0), item.get("title"),
                tuple(r.get("id") for r in item.get("requests") or []),
                item.get("where"), item.get("detail"), item.get("agent"), item.get("message"), bool(item.get("changed")),
                can_answer, bool(self.app.answers.enabled(item)), json.dumps(stop, default=str),
                self.app.cfg.get("codex_answers", True), preferences.ANSWER_WAIT["v"],
                json.dumps(grant, sort_keys=True), permissions.blacklist_blocked(item),
                self.app.answers.timed_minutes() if can_answer else None)

    def _tick_texts(self, item):
        if item.get("changed"):
            prefix = "waiting" if self._state == "needs_input" or self.sent else "since"
            self.wait.config(text=f"{prefix} {textfmt.ago(item['changed'])}")
        if self._timed_grant:
            grant = permissions.timed_grants().get(str(item.get("session_id")))
            if grant:
                self.timedlink.config(text=self._timed_text(grant))

    def _preview_timed_minutes(self, value):
        if not self._timed_grant:
            self.timedlink.config(text=self._timed_text(None, int(float(value))))

    def _save_timed_minutes(self, event=None):
        minutes = self.timed_var.get()
        if minutes != self.app.answers.timed_minutes():
            self.app.answers.set_timed_minutes(minutes)

    def _timed_text(self, grant, minutes=None):
        mode = (" (reject blacklist)" if grant and grant.get("reject_blacklist") else
                " (pause on blacklist)" if grant and grant.get("respect_blacklist") else "")
        return (f"Auto approving this session{mode} \u00b7 {textfmt.fmt_left(grant['until'] - time.time())} left" if grant
                else f"Auto approve this session for {self.app.answers.timed_minutes() if minutes is None else minutes} min\u2026")

    def _render(self, item):
        self.blocked_banner.pack_forget()
        if not self.sent and item.get("state") == "needs_input" and permissions.blacklist_blocked(item):
            self.blocked_banner.pack(anchor="e", before=self.header, pady=(0, 8))
        req = item.get("request") or {}
        if self._state != "needs_input" and not self.sent:
            return self._update_status(item)
        pending = item.get("pending", 0)
        question = req.get("kind") == "question"
        self.status.config(text=("has a question for you" if question else "needs your permission")
                           + (f"  ·  {pending} prompts waiting" if pending > 1 else ""), fg=self.ACCENT)
        self.head.config(text=item.get("title", "session"))
        self.where.config(text=" \u00b7 ".join(x for x in (item.get("where"), item.get("detail")) if x))
        for widget in (self.ask, self.desc, self.code, self.qframe):
            widget.pack_forget()
        if question:
            self._show_questions(req)
        elif req:
            who = "Codex" if item.get("agent") == "codex" else "Claude"
            self.ask.config(text=f"Allow {who} to use {req.get('tool') or 'this tool'}?")
            self.ask.pack(fill="x")
            if req.get("description"):
                self.desc.config(text=req["description"])
                self.desc.pack(fill="x", pady=(4, 0))
            detail = req.get("detail", "")
            if detail:
                if detail != self._shown:
                    self.code.config(state="normal")
                    self.code.delete("1.0", "end")
                    self.code.insert("1.0", detail)
                    lines = sum(max(1, -(-len(ln) // 50)) for ln in detail.splitlines() or [""]) + 1  # provisional
                    self.code.config(height=max(1, min(16, lines)), state="disabled")
                    self.win.after(80, self._fit_code)  # then measure the real wrapped height once it is on screen
                    self._shown = detail
                self.code.pack(fill="both", expand=True, pady=(8, 0))
        else:
            self.ask.config(text=item.get("message") or "Waiting for your input")
            self.ask.pack(fill="x")
        self.wait.config(text=f"waiting {textfmt.ago(item['changed'])}" if item.get("changed") else "")
        can_answer = self.app.answers.enabled(item) and permissions.hook_waiting(item) and not self.sent
        who = "Codex" if item.get("agent") == "codex" else "Claude Code"
        stop = None if can_answer or self.sent or not req else permissions.hook_stop(item, permissions.answers_dir())
        if self.sent:
            note = f"Sent: {self.sent}. {who} will carry on in a moment."
        elif can_answer and item.get("agent") == "codex":
            note = ("Allow once or Deny answers this prompt right from here. Go to window hands it back to Codex, "
                    "which then shows its own prompt there (with its other choices, like always allowing a tool).")
        elif can_answer:
            note = ("Allow once or Deny answers this prompt right from here, or answer in the session window "
                    "(Go to window). If you do neither, the normal prompt appears.")
        elif req and not self.app.answers.enabled(item):
            note = (f"Answer this one in {who} (Go to window). To answer prompts here instead, switch on "
                    f"Answer {who} prompts from the pet under Permissions.")
        elif stop:
            after = f"after {'about ' if not stop['signal'] else ''}{round(stop['after'])} s " \
                if isinstance(stop.get("after"), (int, float)) else ""
            how = f"stopped AIPet's prompt {after}without a decision" + (
                f" ({stop['signal']})." if stop["signal"] else ": its hook was ended, not answered.")
            note = (f"{who} {how} If {who} shows its own prompt, answer there (Go to window). If it shows nothing, "
                    f"interrupt the turn in {who}" + (", or switch off Approve for me if it is on." if who == "Codex"
                                                     else "."))
        elif item.get("agent") == "codex" and req and self.app.cfg.get("codex_answers", True):
            note = ("Codex is no longer waiting for this window, so answer in Codex (Go to window). If this happens "
                    "right away, your Codex hooks are from an older AIPet with a short timeout: update them in the "
                    "Codex hooks menu and trust them again with /hooks in Codex.")
        elif req.get("source") == "transcript":
            note = ("This session type (the VS Code extension) sends no permission events, so the pet can't answer for it. "
                    "This is what Claude is asking, so you know what to approve there. Press Go to window to jump there.")
        elif req:
            if preferences.ANSWER_WAIT["v"] <= 0:
                note = ("The pet stopped waiting for this prompt (it was closed or restarted meanwhile). "
                        "Answer in the session window: press Go to window.")
            else:
                note = (f"The pet's {textfmt.fmt_wait(preferences.ANSWER_WAIT['v'])} answer window has passed. "
                        "Answer in the session window: press Go to window.")
        else:
            note = ("No command details were reported for this prompt, so there is nothing to answer here. "
                    "Press Go to window to jump to the session.")
        if question and can_answer:
            note = ("Pick an answer (or type your own) and press Send answer, or answer in the session window "
                    "(Go to window). If you do neither, Claude Code asks you itself.")
        elif question and self.sent:
            note = f"Sent your answer. {who} will carry on in a moment."
        elif question and req.get("source") == "codex-question":
            note = ("Codex is waiting for your answer in its own window: press Go to window and answer there "
                    "(the pet can't type into Codex).")
        elif question:
            note = f"Answer this in {who}: press Go to window."
        self.note.config(text=note)
        self.answer_row.pack_forget()
        self.q_row.pack_forget()
        if can_answer:
            (self.q_row if question else self.answer_row).pack(fill="x", after=self.note)
        self._req_id = req.get("id")
        if req and not self.sent and req.get("source") != "transcript":
            self.clearlink.pack(fill="x", pady=(0, 6), after=self.note)
        else:
            self.clearlink.pack_forget()
        self._timed_grant = False
        if can_answer and not question:
            grant = permissions.timed_grants().get(str(item.get("session_id")))
            self.timedlink.config(text=self._timed_text(grant))
            self._timed_grant = bool(grant)
            self.timed_var.set(self.app.answers.timed_minutes())
            if grant:
                self.timed_scale.pack_forget()
            else:
                self.timed_scale.pack(anchor="w", pady=(2, 0))
            self.timed_row.pack(fill="x", pady=(0, 4), after=self.note)
        else:
            self.timed_row.pack_forget()
        if can_answer or self.sent:  # small link while the answer buttons (or the "sent" note) are showing
            self.golink.pack(side="right")
            self.go_row.pack_forget()
        else:  # nothing to answer from here: the big button
            self.golink.pack_forget()
            self.go_row.pack(fill="x", after=self.note)
        self._place()

    def _show_questions(self, req):
        """Build the question blocks once per prompt (the card refreshes every poll: keep what was picked)."""
        self.qframe.pack(fill="x")
        readonly = not permissions.hook_waiting({"request": req}) or not self.app.answers.enabled(self._item or {})
        if self._qid == (req.get("id"), readonly):
            return
        if self._qid and self._qvars:
            self._question_drafts[self._qid[0]] = {
                q["question"]: (choice.get(), {label: var.get() for label, var in picks.items()}, own.get(), own_on.get())
                for q, _, choice, picks, own, own_on in self._qvars}
        self._qid, self._qvars = (req.get("id"), readonly), []
        drafts = self._question_drafts.get(req.get("id"), {})
        for child in self.qframe.winfo_children():
            child.destroy()
        bg, fg, muted = theme.T["bubble_bg"], theme.T["bubble_fg"], theme.T["bubble_muted"]
        for i, q in enumerate(req.get("questions") or []):
            block = tk.Frame(self.qframe, bg=bg)
            block.pack(fill="x", pady=(0 if i == 0 else 12, 0))
            if q.get("header"):
                tk.Label(block, text=q["header"].upper(), bg=bg, fg=self.ACCENT, anchor="w",
                         font=self.f(8, "bold")).pack(fill="x")
            tk.Label(block, text=q["question"], bg=bg, fg=fg, anchor="w", justify="left", wraplength=430,
                     font=self.f(11, "bold")).pack(fill="x", pady=(2, 4))
            multi = bool(q.get("multiSelect"))
            picks = {}
            # Single choice starts on the first option. Never "" as the value: that is Tk's tristate value, which
            # draws every radio button as selected
            first = next((o["label"] for o in q.get("options") or []), pet_style.NO_CHOICE)
            choice = tk.StringVar(master=self.win, value=pet_style.NO_CHOICE if multi else first)
            for opt in q.get("options") or []:
                row = tk.Frame(block, bg=bg)
                row.pack(fill="x", pady=1)
                if readonly:  # answered in the session's own window (Codex, or answering from the pet is off)
                    tk.Label(row, text="\u2022 " + opt["label"], bg=bg, fg=fg, anchor="w", justify="left",
                             wraplength=420, font=self.f(10, "bold")).pack(fill="x")
                    if opt.get("description"):
                        tk.Label(row, text=opt["description"], bg=bg, fg=muted, anchor="w", justify="left",
                                 wraplength=400, font=self.f(8)).pack(fill="x", padx=(14, 0))
                    continue
                if multi:
                    var = picks[opt["label"]] = tk.BooleanVar(master=self.win, value=False)
                    box = tk.Checkbutton(row, text=opt["label"], variable=var, anchor="w")
                else:
                    # tristatevalue: Tk draws every radio button as "half selected" while the variable holds its
                    # tristate value, "" by default - exactly the empty "nothing picked yet" value used here
                    box = tk.Radiobutton(row, text=opt["label"], variable=choice, value=opt["label"], anchor="w",
                                         tristatevalue="\x00none")
                box.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, selectcolor=theme.T["entry_bg"],
                              highlightthickness=0, font=self.f(10, "bold"), cursor="hand2")
                box.pack(fill="x")
                if opt.get("description"):
                    tk.Label(row, text=opt["description"], bg=bg, fg=muted, anchor="w", justify="left",
                             wraplength=400, font=self.f(8)).pack(fill="x", padx=(24, 0))
            if readonly:
                continue
            # Your own answer has its own radio button (check box for multi-select), so it is plain what gets sent:
            # typing selects it, picking an option deselects it again (the typed text stays, but isn't sent)
            own = tk.StringVar(master=self.win, value="")
            own_on = tk.BooleanVar(master=self.win, value=False)
            other = tk.Frame(block, bg=bg)
            other.pack(fill="x", pady=(4, 0))
            if multi:
                pick = tk.Checkbutton(other, text="Your own answer:", variable=own_on, anchor="w")
            else:
                pick = tk.Radiobutton(other, text="Your own answer:", variable=choice, value=pet_style.OWN_CHOICE, anchor="w",
                                      tristatevalue="\x00none")
            pick.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, selectcolor=theme.T["entry_bg"],
                           highlightthickness=0, font=self.f(10, "bold"), cursor="hand2")
            pick.pack(side="left")
            entry = tk.Entry(other, textvariable=own, bg=theme.T["entry_bg"], fg=theme.T["entry_fg"], relief="flat",
                             insertbackground=theme.T["entry_fg"], highlightthickness=1, highlightbackground=theme.T["border"],
                             highlightcolor=theme.T["primary"], font=self.f(9))
            entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
            if multi:
                own.trace_add("write", lambda *_, v=own_on, o=own: v.set(True) if o.get().strip() else None)
            else:
                own.trace_add("write", lambda *_, c=choice, o=own: c.set(pet_style.OWN_CHOICE) if o.get().strip() else None)
                pick.configure(command=lambda e=entry: e.focus_set())
            self._qvars.append((q, multi, choice, picks, own, own_on))
            draft = drafts.get(q["question"])
            if draft:
                selected, checked, typed, typed_on = draft
                own.set(typed)
                choice.set(selected)
                own_on.set(typed_on)
                for label, var in picks.items():
                    var.set(checked.get(label, False))
        self.win.after(60, self.apply_scale)

    def question_answers(self):
        """{question text: label} (single choice) or {question text: [labels]} (multi-select); typed answers count
        as a label, but only while its own radio button (check box) is selected. None until every question has an
        answer. (The hook sends a multi-select answer to Claude Code as one comma-separated text.)"""
        answers = {}
        for q, multi, choice, picks, own, own_on in self._qvars:
            typed = own.get().strip()
            if multi:
                value = [label for label, var in picks.items() if var.get()] + ([typed] if typed and own_on.get() else [])
            elif choice.get() == pet_style.OWN_CHOICE:
                value = typed
            else:
                value = choice.get() if choice.get() != pet_style.NO_CHOICE else ""
            if not value:
                return None
            answers[q["question"]] = value
        return answers or None

    def send_answers(self):
        answers = self.question_answers()
        if not answers:
            self.note.config(text="Answer every question first (pick an option or type your own).")
            return
        item = next((i for i in self.app._last_items if i["key"] == self.key), {})
        if self.app.answers.send(self.key, "allow", answers=answers):
            self.sent = "Answer"
            self._sent_id = (item.get("request") or {}).get("id")
            self.update(item or {"request": {}})

    def _update_status(self, item):
        """A session that isn't asking anything: show what it's doing, with no answer buttons."""
        state = item.get("state")
        self.status.config(text=pet_style.LABELS.get(state, state or ""), fg=pet_style.COLORS.get(state, self.ACCENT))
        self.head.config(text=item.get("title", "session"))
        self.where.config(text=" · ".join(x for x in (item.get("where"), item.get("detail")) if x))
        for widget in (self.ask, self.desc, self.code):
            widget.pack_forget()
        if item.get("message"):
            self.ask.config(text=item["message"])
            self.ask.pack(fill="x")
        self.wait.config(text=f"since {textfmt.ago(item['changed'])}" if item.get("changed") else "")
        self.note.config(text="Press Go to window to jump to the session.")
        self.answer_row.pack_forget()
        self.q_row.pack_forget()
        self.qframe.pack_forget()
        self.timed_row.pack_forget()
        self.golink.pack_forget()
        self.go_row.pack(fill="x", after=self.note)
        self._place()

    # ---- size
    def zoom_by(self, step):
        """Ctrl + plus / minus (step 1 / -1) and Ctrl + 0 (back to 100%) on the card."""
        value = 1.0 if step == 0 else preferences.clamp_card_scale(preferences.CARD_SCALE["v"] + step * preferences.CARD_SCALE_STEP)
        if value != preferences.CARD_SCALE["v"]:
            self.app.preferences.set_card_scale(value)
        return "break"

    def apply_scale(self):
        """Follow CARD_SCALE: fonts resize by themselves (shared named fonts); wrap widths, the robot and the
        slider are set here."""
        sc = preferences.CARD_SCALE["v"]
        try:
            self.size_var.set(sc)
            self.size_label.configure(text=f"{int(round(sc * 100))}%")
            if self.robot is not None:
                self.robot.configure(width=int(140 * sc), height=int(212 * sc))
                self.robot.pack_configure(pady=(int(12 * sc), 0))
            stack = [self.win]
            while stack:
                w = stack.pop()
                stack.extend(w.winfo_children())
                if w.winfo_class() == "Label":
                    base = getattr(w, "_base_wrap", None)
                    if base is None:
                        base = w._base_wrap = int(str(w.cget("wraplength")) or 0)
                    if base:
                        w.configure(wraplength=int(base * sc))
            self._size = None
            self._place()
        except tk.TclError:
            pass

    def allow_and_whitelist(self):
        """Allow this prompt and add it to the auto-approve whitelist of the session's hook config, so the same
        request is approved by itself next time."""
        item = next((i for i in self.app._last_items if i["key"] == self.key), {})
        added = self.app.answers.whitelist(item)
        self.answer("allow")
        if added:
            self.note.config(text=added)

    def go(self):
        """Open the session and show the next waiting prompt, or close a single-prompt card."""
        if not self.app.answers.go_to_prompt(self.key):
            self.close()

    def clear_prompt(self):
        self.app.answers.clear_prompt(self.key, getattr(self, "_req_id", None))  # the next poll moves the card on / closes it

    def auto_approve_for_a_while(self):
        """Start the timed auto approve for this session (after its confirmation), and allow the prompt on screen."""
        item = next((i for i in self.app._last_items if i["key"] == self.key), {})
        grant = permissions.timed_grants().get(str(item.get("session_id")))
        self._save_timed_minutes()
        def completed(accepted):
            if not accepted or not self.win.winfo_exists():
                return
            active = permissions.timed_grants().get(str(item.get('session_id'))) or {}
            if active.get('respect_blacklist') and not active.get('reject_blacklist'):
                self.close()
                return
            current = next((i for i in self.app._last_items if i['key'] == self.key), {})
            if current.get('session_id') != item.get('session_id'):
                return
            behavior = permissions.timed_behavior(current)
            if behavior:
                self.answer(behavior, auto_approved=True)
            else:
                self.update(current)
        if grant and not grant.get('respect_blacklist'):
            completed(True)
        else:
            self.app.answers.start_timed(item, parent=self.win, on_result=completed)

    def answer(self, behavior, auto_approved=False):
        item = next((i for i in self.app._last_items if i["key"] == self.key), {})
        if self.app.answers.send(self.key, behavior, **({"auto_approved": True} if auto_approved else {})):
            self.sent = "Allow once" if behavior == "allow" else "Deny"
            self._sent_id = (item.get("request") or {}).get("id")
            self.update(next((i for i in self.app._last_items if i["key"] == self.key), {"request": {}}))

    def close(self):
        self.app.details.pop(self.key, None)
        try:
            if self._after:
                self.win.after_cancel(self._after)
            if IS_MAC:
                import mac_statusbar as mac
                with mac.passive_window_mapping():
                    self.win.destroy()
            else:
                self.win.destroy()
        except tk.TclError:
            pass

    def destroy(self):
        self.close()
