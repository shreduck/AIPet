"""Agent prompt chips, question overlays and answer interaction.

The owning window supplies its widgets and state; shared values come from their owner modules.
No application entry point is imported by this component.
"""
import tkinter as tk

from .. import permissions as permission_rules
from . import agent_common as common


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
        if questions:
            simple = len(questions) == 1 and not questions[0].get("multiSelect") and questions[0].get("options")
            for q in questions[:1]:
                lab = tk.Label(ov, text=q.get("question", ""), font=window.f_main, bg=P["node"], fg=P["hi"], anchor="w",
                               justify="left", wraplength=window.px(560))
                lab.pack(fill="x", padx=pad, pady=(window.px(4), 0))
                self.wraps.append(lab)
            if len(questions) > 1 or (questions and questions[0].get("multiSelect")):
                lab = tk.Label(ov, text="# several questions or picks: open the full card to see them all", font=window.f_small,
                               bg=P["node"], fg=P["mu"], anchor="w", justify="left")
                lab.pack(fill="x", padx=pad)
                self.wraps.append(lab)
            if answerable and simple:
                q, self.radio = questions[0], True
                for opt in q["options"][:8]:
                    label = str(opt.get("label") if isinstance(opt, dict) else opt)
                    self.options.append((label, lambda label=label, q=q: self.send(
                        "allow", {q["question"]: label})))
                self.options.append(("your own answer:", self.send_own))
            elif not answerable:
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
            self.options += [("aipet: always (whitelist)", self.always),
                             ("deny", lambda: self.send("deny"))]
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
                entry.bind("<KeyRelease>", lambda e, n=n: self.own_typed(n))
                entry.bind("<FocusIn>", lambda e, n=n: self.pick(n))
                entry.bind("<Return>", lambda e: (self.send_own(), "break")[1])
                entry.bind("<Escape>", lambda e: (window.win.focus_set(), "break")[1])
                self.own = (questions[0], entry)
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
        if answerable:
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
                mark = ("(•) " if on else "( ) ") if self.radio and lab._answer else ""
                lab.configure(text=f"[{n + 1}] {mark}{lab._text}", bg=P["redbg"] if on else P["node"],
                              fg=P["red"] if on else P["tx"])
            except (tk.TclError, AttributeError):
                pass

    def pick(self, n):
        self.option = n
        self.mark_option()

    def own_typed(self, n):
        if self.own and self.own[1].get().strip():
            self.pick(n)

    def send_own(self):
        """Send what was typed as the answer; with nothing typed yet, go to the box."""
        window = self.window
        if not self.own:
            return
        q, entry = self.own
        text = entry.get().strip()
        if not text:
            entry.focus_set()
            return
        window.win.focus_set()
        self.send("allow", {q["question"]: text})

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

    def always(self):
        window = self.window
        added = window.pet.whitelist_request(window.item)
        self.send("allow")
        if added:
            self.note.configure(text="# " + added)
