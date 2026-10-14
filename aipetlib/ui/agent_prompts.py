"""Agent prompt chips, question overlays and answer interaction.

The owning window supplies its widgets and state; shared values come from their owner modules.
No application entry point is imported by this component.
"""
import tkinter as tk

from .. import permissions as permission_rules
from . import agent_common as common


OWN = object()  # "your own answer" was picked (pick-one questions)


class PromptPanel:
    """The agent window's prompt: the chip on the graph when an agent needs you, and the overlay with the question or
    permission, its answers and the "answer in the window" choices.

    Owns those widgets and their state (whether the overlay is open, the highlighted answer, prompts handed back to
    Codex, what was built last). It reads the session and network from the window (item, net, key, pet, palette, fonts,
    scale) and asks it to close the settings menu or redraw the graph when needed.
    """

    def __init__(self, window):
        self.window = window
        self.overlay_open = self.has_prompt = False
        self.option = 0  # the highlighted answer
        self.sig = None  # what the overlay was last built for
        self.released = set()  # prompts handed back to Codex (Answer in Codex): answer them in its window
        self.in_window = None  # index of the agent whose prompt can only be answered in the session's window
        # questions, one at a time (a carousel): which one shows, and what was picked / typed for each
        self.q_req, self.q_index, self.q_state = None, 0, {}

    def invalidate(self):
        """Build the overlay again on the next refresh."""
        self.sig = None

    def build(self):
        """The prompt chip (on the graph) and the overlay it opens (over the logs)."""
        window = self.window
        w = window.win
        self.chip = tk.Label(w, font=window.f_main, cursor="hand2", padx=window.px(6), pady=window.px(1), bd=1,
                             relief="solid")
        self.chip.bind("<Button-1>", lambda e: self.open(True))
        self.overlay = tk.Frame(w, bd=1, relief="solid")

    def draw(self):
        window = self.window
        item = window.item
        who = common.prompt_agent(window.net, item) if window.net["agents"] else None
        self.has_prompt = who is not None
        if who is None:
            if self.in_window is not None:
                self.in_window = None
                window.graph.draw()
            self.chip.place_forget()
            if self.overlay_open:
                self.open(False)
            self.sig = None
            return
        req = item.get("request") or {}
        name = window.net["agents"][who]["name"]
        answerable = permission_rules.hook_waiting(item) and window.pet.answers_enabled(item)
        self.chip.configure(text=f"[!] {name} needs your input · " + ("space to open" if answerable
                                                                       else "answer it in the window"))
        in_window = None if answerable else who
        if in_window != self.in_window:
            self.in_window = in_window
            window.graph.draw()
        if not self.overlay_open:
            self.chip.place(in_=window.graph.canvas, relx=1.0, rely=1.0, x=-window.px(170), y=-window.px(6), anchor="se")
        if req.get("id") != self.q_req:  # a new prompt: its first question, nothing picked yet
            self.q_req, self.q_index, self.q_state = req.get("id"), 0, {}
        sig = (req.get("id"), str(req.get("questions")), window.theme, window.s, permission_rules.hook_waiting(item))
        if sig != self.sig:
            self.sig = sig
            self.build_overlay(name, req, item)

    def open(self, show):
        window = self.window
        self.overlay_open = bool(show) and self.has_prompt
        if self.overlay_open:
            if window.settings.open:
                window.settings.toggle(False)
            self.chip.place_forget()
            self.option = 0
            self.mark_option()
            self.place()
        else:
            self.overlay.place_forget()
            self.sig = None  # the chip comes back on the next refresh
            if hasattr(window, "logs"):
                window.logs.draw()  # what arrived while the prompt covered the log
        return "break"

    def covers_logs(self):
        """The open overlay hides the whole log (logs below the graph): no need to draw what can't be seen."""
        return self.overlay_open and self.window.opts.get("log_side", "bottom") == "bottom"

    def place(self):
        """Over the logs, and up over the graph as far as the prompt needs (never under the bars)."""
        window = self.window
        width = max(window.px(200), window.win.winfo_width() - window.px(20))
        for lab in getattr(self, "wraps", []):
            try:
                lab.configure(wraplength=width - window.px(36))
            except tk.TclError:
                pass
        bottom = window.footbar.winfo_y() - window.px(6)
        self.flow_options(width - window.px(20))
        highest = window.body.winfo_y() + window.px(6)
        top = window.body.winfo_y() + window.graph.canvas.winfo_y() + int(window.graph.canvas.winfo_height() * 0.72)
        try:
            self.overlay.update_idletasks()
            need = self.overlay.winfo_reqheight() + window.px(4)
        except tk.TclError:
            need = 0
        top = max(highest, min(top, bottom - need))
        self.overlay.place(x=window.px(10), y=top, width=width, height=max(window.px(60), bottom - top))
        self.overlay.lift()

    def build_overlay(self, name, req, item):
        window = self.window
        P, ov = window.P, self.overlay
        for wdg in ov.winfo_children():
            wdg.destroy()
        self.options, self.wraps = [], []
        pad = window.px(10)
        questions = req.get("questions") or []
        kind = "asks you" if req.get("kind") == "question" or questions else f"wants to run {req.get('tool') or 'a tool'}"
        top = tk.Frame(ov, bg=P["node"])
        top.pack(fill="x", padx=pad, pady=(pad, 0))
        tk.Label(top, text=f"? {name} {kind}", font=window.f_bold, bg=P["node"], fg=P["red"], anchor="w").pack(
            side="left")
        fold = tk.Label(top, text="[esc] fold", font=window.f_small, bg=P["node"], fg=P["mu"], cursor="hand2")
        fold.pack(side="right")
        fold.bind("<Button-1>", lambda e: self.open(False))
        detail = (req.get("detail") or "").strip()
        if req.get("description"):
            lab = tk.Label(ov, text="# " + req["description"], font=window.f_small, bg=P["node"], fg=P["mu"],
                           anchor="w", justify="left", wraplength=window.px(560))
            lab.pack(fill="x", padx=pad)
            self.wraps.append(lab)
        if detail and not questions:
            lines = detail.splitlines()
            shown = "\n".join(lines[:6]) + ("\n…" if len(lines) > 6 else "")
            lab = tk.Label(ov, text=shown, font=window.f_main, bg=P["bar"], fg=P["hi"], anchor="w", justify="left",
                           wraplength=window.px(560), padx=window.px(6), pady=window.px(3))
            lab.pack(fill="x", padx=pad, pady=window.px(4))
            self.wraps.append(lab)
        answerable = permission_rules.hook_waiting(item) and window.pet.answers_enabled(item)
        self.own = None  # (question, entry) when a typed answer can be sent
        self.radio = False  # the options are a pick-one list (a question)
        self.questions = questions if answerable else []
        if questions and answerable:
            self.build_question(ov, questions, pad)
        elif questions:  # answered in the session's window: show the first question and its options
            lab = tk.Label(ov, text=questions[0].get("question", ""), font=window.f_main, bg=P["node"], fg=P["hi"],
                           anchor="w", justify="left", wraplength=window.px(560))
            lab.pack(fill="x", padx=pad, pady=(window.px(4), 0))
            self.wraps.append(lab)
            for opt in questions[0].get("options") or []:
                label = opt.get("label") if isinstance(opt, dict) else str(opt)
                description = opt.get("description", "") if isinstance(opt, dict) else ""
                lab = tk.Label(ov, text=label + (" — " + description if description else ""),
                               font=window.f_small, bg=P["node"], fg=P["tx"], anchor="w", justify="left",
                               wraplength=window.px(560))
                lab.pack(fill="x", padx=pad, pady=(window.px(2), 0))
                self.wraps.append(lab)
        elif answerable:
            self.options = [("allow once", lambda: self.send("allow"))]
            for sug in (req.get("suggestions") or [])[:3]:  # the AI's own "don't ask again" choices
                agent_name = "codex" if (item or {}).get("agent") == "codex" else "claude"
                self.options.append((f"{agent_name}: {common.suggestion_label(sug)}",
                                     lambda sug=sug: self.send("allow", permissions=[sug])))
            self.options.append(("aipet: always (whitelist)", self.always))
            if (item or {}).get("session_id"):  # like the card: every prompt of this session, for a while
                grant = permission_rules.timed_grants().get(str(item["session_id"]))
                self.options.append(("aipet: auto approving this session · allow" if grant else
                                     f"aipet: auto approve this session for {window.pet.timed_minutes()} min…",
                                     self.timed))
            self.options.append(("deny", lambda: self.send("deny")))
        n_answers = len(self.options)
        self.options.append(("full card", lambda: window.pet.open_detail(window.key)))
        codex = (item or {}).get("agent") == "codex"
        self.options.append(("answer in codex" if codex and answerable else "go to window", self.go))
        self.opt_row = tk.Frame(ov, bg=P["node"])
        self.opt_row.pack(fill="x", padx=pad, pady=window.px(6))
        self.opt_labels, self.opt_flow = [], None
        for n, (label, _) in enumerate(self.options):
            if self.radio and label == "your own answer:":
                own_row = tk.Frame(self.opt_row, bg=P["node"])
                lab = tk.Label(own_row, font=window.f_main, cursor="hand2", padx=window.px(4), pady=window.px(1),
                               bg=P["node"], fg=P["tx"], anchor="w")
                lab.pack(side="left")
                entry = tk.Entry(own_row, font=window.f_main, bg=P["bar"], fg=P["hi"], insertbackground=P["hi"],
                                 relief="flat", highlightthickness=1, highlightbackground=P["line"],
                                 highlightcolor=P["red"])
                entry.pack(side="left", fill="x", expand=True, padx=(window.px(4), 0))
                entry.insert(0, self.current_state().get("own", ""))
                entry.bind("<KeyRelease>", lambda e, n=n: self.own_typed(n))
                entry.bind("<FocusIn>", lambda e, n=n: self.pick(n))
                entry.bind("<Return>", lambda e: (self.send_own(), "break")[1])
                entry.bind("<Escape>", lambda e: (window.win.focus_set(), "break")[1])
                self.own = (questions[self.q_index], entry)
                lab.bind("<Button-1>", lambda e, n=n: self.choose(n))
                lab._row = own_row
            else:
                lab = tk.Label(self.opt_row, font=window.f_main, cursor="hand2", padx=window.px(4), pady=window.px(1),
                               bg=P["node"], fg=P["tx"], anchor="w", justify="left")
                lab.bind("<Button-1>", lambda e, n=n: self.choose(n))
                lab._row = None
            lab._text = label
            lab._answer = n < n_answers
            self.opt_labels.append(lab)
        self.flow_options(window.px(560))
        if answerable and len(questions) > 1:
            hint = "# ↑↓ + enter to pick · ←→ other questions · type your own + enter · esc fold"
        elif answerable:
            hint = ("# 1-9 or ↑↓ + enter to answer · type your own + enter · esc fold" if self.radio
                    else "# 1-9 or ←→ + enter to answer · esc fold")
        elif req.get("id") in self.released:
            hint = "# handed back to codex: answer it in the codex window now (go to window takes you there)"
        elif req.get("source") == "codex-question":
            hint = "# answer this question in Codex · go to window opens the session"
        else:
            hint = "# this prompt can only be answered in the session window (or turn on answering in permissions)"
        self.note = tk.Label(ov, text=hint, font=window.f_small, bg=P["node"], fg=P["mu"], anchor="w", justify="left")
        self.note.pack(fill="x", padx=pad, pady=(0, pad))
        self.wraps.append(self.note)
        self.mark_option()
        if self.overlay_open:
            self.place()

    def flow_options(self, width):
        """The answers side by side when they fit, else one per line (wrapping long ones); a question's answers are
        always one per line, like radio buttons."""
        window = self.window
        labels = getattr(self, "opt_labels", [])
        if not labels:
            return
        for lab in labels:
            lab.configure(wraplength=0)
        total = sum(lab.winfo_reqwidth() + window.px(4) for lab in labels)
        flow = "row" if not self.radio and total <= width else "column"
        if flow == self.opt_flow and flow == "row":
            return
        self.opt_flow = flow
        for lab in labels:
            (lab._row or lab).pack_forget()
        for lab in labels:
            widget = lab._row or lab
            if flow == "row":
                widget.pack(side="left", padx=(0, window.px(4)))
            else:
                widget.pack(side="top", fill="x", anchor="w", pady=(0, window.px(1)))
                if widget is lab:
                    lab.configure(wraplength=max(window.px(120), width - window.px(30)))

    def mark_option(self):
        window = self.window
        P = window.P
        for n, lab in enumerate(getattr(self, "opt_labels", [])):
            try:
                on = n == self.option
                mark = self.pick_mark(lab, on) if self.radio and lab._answer else ""
                lab.configure(text=f"[{n + 1}] {mark}{lab._text}", bg=P["redbg"] if on else P["node"],
                              fg=P["red"] if on else P["tx"])
            except (tk.TclError, AttributeError):
                pass

    def pick(self, n):
        self.option = n
        self.mark_option()

    def own_typed(self, n):
        if self.own:
            text = self.own[1].get().strip()
            state = self.current_state()
            state["own"] = text
            if self.multi():
                state["own_on"] = bool(text)
                self.mark_option()
            if text:
                self.pick(n)

    def send_own(self):
        """Your own answer to the question shown: with nothing typed yet, go to the box."""
        window = self.window
        if not self.own:
            return
        q, entry = self.own
        text = entry.get().strip()
        if not text:
            entry.focus_set()
            return
        window.win.focus_set()
        state = self.current_state()
        state["own"] = text
        if self.multi():
            state["own_on"] = not state.get("own_on")
            self.mark_option()
            return
        state["picked"] = OWN
        self.answered()

    # ---- questions: one at a time, ←→ between them, sent together
    def build_question(self, ov, questions, pad):
        """The question shown in the carousel: its number, header and text, its options (pick one, or tick
        several) and your own answer; with several questions, next / send at the end."""
        window = self.window
        P = window.P
        self.q_index = max(0, min(self.q_index, len(questions) - 1))
        q, n = questions[self.q_index], len(questions)
        self.radio = True
        if n > 1:
            nav = tk.Frame(ov, bg=P["node"])
            nav.pack(fill="x", padx=pad, pady=(window.px(4), 0))
            dots = " ".join("●" if k == self.q_index else ("◉" if self.is_answered(k) else "○") for k in range(n))
            for text, d in (("‹", -1), (f"question {self.q_index + 1}/{n}  {dots}", 0), ("›", 1)):
                lab = tk.Label(nav, text=text, font=window.f_bold if d else window.f_small, bg=P["node"],
                               fg=P["blue"] if d else P["mu"], cursor="hand2" if d else "")
                lab.pack(side="left", padx=(0, window.px(6)))
                if d:
                    lab.bind("<Button-1>", lambda e, d=d: self.step_question(d))
        title = (f"{q['header']}: " if q.get("header") else "") + str(q.get("question", ""))
        title += "  (pick any)" if q.get("multiSelect") else ""
        lab = tk.Label(ov, text=title, font=window.f_main, bg=P["node"], fg=P["hi"], anchor="w", justify="left",
                       wraplength=window.px(560))
        lab.pack(fill="x", padx=pad, pady=(window.px(4), 0))
        self.wraps.append(lab)
        for opt in (q.get("options") or [])[:8]:
            label = str(opt.get("label") if isinstance(opt, dict) else opt)
            self.options.append((label, lambda label=label: self.pick_answer(label)))
        self.options.append(("your own answer:", self.send_own))
        if q.get("multiSelect") or n > 1:
            last = self.q_index == n - 1
            self.options.append(("send answers" if last else "next question ›", self.advance))

    def current_state(self):
        return self.q_state.setdefault(self.q_index, {})

    def multi(self):
        questions = getattr(self, "questions", None) or []
        return bool(questions and questions[self.q_index].get("multiSelect"))

    def pick_mark(self, lab, on):
        """( ) / (•) for pick-one, [ ] / [x] for pick-any; your own answer counts once typed and chosen."""
        if not getattr(self, "questions", None):
            return "(•) " if on else "( ) "
        state = self.current_state()
        if lab._text in ("send answers", "next question ›"):
            return ""
        if self.multi():
            ticked = state.get("own_on") if lab._text == "your own answer:" else lab._text in state.get("picks", ())
            return "[x] " if ticked else "[ ] "
        picked = state.get("picked")
        chosen = picked is OWN if lab._text == "your own answer:" else picked == lab._text
        return "(•) " if chosen else "( ) "

    def pick_answer(self, label):
        state = self.current_state()
        if self.multi():
            picks = state.setdefault("picks", [])
            picks.remove(label) if label in picks else picks.append(label)
            self.mark_option()
            return
        state["picked"] = label
        self.answered()

    def answered(self):
        """A pick-one question was answered: send (only question), else on to the next unanswered one."""
        if len(self.questions) == 1:
            self.send("allow", self.collect())
            return
        self.advance()

    def answer_of(self, k):
        q, state = self.questions[k], self.q_state.get(k, {})
        own = state.get("own", "").strip()
        if q.get("multiSelect"):
            picks = [str(o.get("label") if isinstance(o, dict) else o) for o in q.get("options") or []]
            chosen = [p for p in picks if p in state.get("picks", ())]
            return chosen + ([own] if own and state.get("own_on") else [])
        picked = state.get("picked")
        return own if picked is OWN else (picked or "")

    def is_answered(self, k):
        return bool(self.answer_of(k))

    def collect(self):
        return {q["question"]: self.answer_of(k) for k, q in enumerate(self.questions)}

    def advance(self):
        """Next question; at the last one (or when all are answered) send them all, or show the first one left."""
        if not self.is_answered(self.q_index):
            self.note.configure(text="# pick an answer first (or type your own)")
            return
        left = [k for k in range(len(self.questions)) if not self.is_answered(k)]
        if not left:
            self.send("allow", self.collect())
            return
        later = [k for k in left if k > self.q_index]
        self.show_question(later[0] if later else left[0])

    def step_question(self, d):
        """←→: the previous / next question (with several), else the highlighted answer."""
        questions = getattr(self, "questions", None) or []
        if len(questions) < 2:
            return self.step_option(d)
        self.show_question((self.q_index + d) % len(questions))
        return "break"

    def show_question(self, k):
        window = self.window
        self.q_index = k
        self.option = 0
        item = window.item or {}
        req = item.get("request") or {}
        who = common.prompt_agent(window.net, item)
        self.build_overlay(window.net["agents"][who]["name"] if who is not None else "agent", req, item)

    def step_option(self, d):
        window = self.window
        if getattr(self, "options", None):
            self.option = (self.option + d) % len(self.options)
            self.mark_option()
        return "break"

    def choose(self, n):
        window = self.window
        if self.overlay_open and 0 <= n < len(getattr(self, "options", [])):
            self.option = n
            self.mark_option()
            self.options[n][1]()
        return "break"

    def send(self, behavior, answers=None, permissions=None):
        window = self.window
        ok = (window.pet.send_answer(window.key, behavior, answers=answers, permissions=permissions) if permissions
              else window.pet.send_answer(window.key, behavior, answers=answers))
        self.note.configure(text="# sent" if ok else "# couldn't send it: the prompt may have been answered already")
        if ok:
            window.win.after(600, lambda: self.open(False))

    def go(self):
        """Go to the session's window. Codex: AIPet lets go of the prompt, so Codex shows its own one there (with
        choices the pet doesn't have, like always allowing that tool)."""
        window = self.window
        req = (window.item or {}).get("request") or {}
        if (window.item or {}).get("agent") == "codex" and req.get("id"):
            self.released.add(req["id"])
        window.pet.go_to_prompt(window.key)
        self.sig = None  # rebuilt as "answer it in the window"
        self.open(False)

    def timed(self):
        """Auto approve this session's prompts for a while (after the pet's confirmation), and allow this one."""
        window = self.window
        item = window.item or {}
        if permission_rules.timed_grants().get(str(item.get("session_id"))) or window.pet.start_timed_approve(item):
            self.send("allow")

    def always(self):
        window = self.window
        added = window.pet.whitelist_request(window.item)
        self.send("allow")
        if added:
            self.note.configure(text="# " + added)
