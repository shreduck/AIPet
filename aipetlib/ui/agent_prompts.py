"""Agent prompt chips, question overlays and answer interaction.

The owning window supplies its widgets and state; shared values come from their owner modules.
No application entry point is imported by this component.
"""
import tkinter as tk

from .. import permissions as permission_rules
from . import agent_common as common

def _draw_prompt(window):
    item = window.item
    who = common.prompt_agent(window.net, item) if window.net["agents"] else None
    window._has_prompt = who is not None
    if who is None:
        if window.in_window is not None:
            window.in_window = None
            window._draw_graph()
        window.chip.place_forget()
        if window.overlay_open:
            window.open_overlay(False)
        window._prompt_sig = None
        return
    req = item.get("request") or {}
    name = window.net["agents"][who]["name"]
    answerable = permission_rules.hook_waiting(item) and window.pet.answers_enabled(item)
    window.chip.configure(text=f"[!] {name} needs your input · " + ("space to open" if answerable
                                                                   else "answer it in the window"))
    in_window = None if answerable else who
    if in_window != window.in_window:
        window.in_window = in_window
        window._draw_graph()
    if not window.overlay_open:
        window.chip.place(in_=window.canvas, relx=1.0, rely=1.0, x=-window.px(170), y=-window.px(6), anchor="se")
    sig = (req.get("id"), str(req.get("questions")), window.theme, window.s, permission_rules.hook_waiting(item))
    if sig != window._prompt_sig:
        window._prompt_sig = sig
        window._build_overlay(name, req, item)


def open_overlay(window, show):
    window.overlay_open = bool(show) and window._has_prompt
    if window.overlay_open:
        if window.menu_open:
            window.toggle_menu(False)
        window.chip.place_forget()
        window.option = 0
        window._mark_option()
        window._place_overlay()
    else:
        window.overlay.place_forget()
        window._prompt_sig = None  # the chip comes back on the next refresh
    return "break"


def _place_overlay(window):
    """Over the logs, and up over the graph as far as the prompt needs (never under the bars)."""
    width = max(window.px(200), window.win.winfo_width() - window.px(20))
    for lab in getattr(window, "_wraps", []):
        try:
            lab.configure(wraplength=width - window.px(36))
        except tk.TclError:
            pass
    bottom = window.footbar.winfo_y() - window.px(6)
    window._flow_options(width - window.px(20))
    highest = window.body.winfo_y() + window.px(6)
    top = window.body.winfo_y() + window.canvas.winfo_y() + int(window.canvas.winfo_height() * 0.72)
    try:
        window.overlay.update_idletasks()
        need = window.overlay.winfo_reqheight() + window.px(4)
    except tk.TclError:
        need = 0
    top = max(highest, min(top, bottom - need))
    window.overlay.place(x=window.px(10), y=top, width=width, height=max(window.px(60), bottom - top))
    window.overlay.lift()


def _build_overlay(window, name, req, item):
    P, ov = window.P, window.overlay
    for wdg in ov.winfo_children():
        wdg.destroy()
    window.options, window._wraps = [], []
    pad = window.px(10)
    questions = req.get("questions") or []
    kind = "asks you" if req.get("kind") == "question" or questions else f"wants to run {req.get('tool') or 'a tool'}"
    top = tk.Frame(ov, bg=P["node"])
    top.pack(fill="x", padx=pad, pady=(pad, 0))
    tk.Label(top, text=f"? {name} {kind}", font=window.f_bold, bg=P["node"], fg=P["red"], anchor="w").pack(
        side="left")
    fold = tk.Label(top, text="[esc] fold", font=window.f_small, bg=P["node"], fg=P["mu"], cursor="hand2")
    fold.pack(side="right")
    fold.bind("<Button-1>", lambda e: window.open_overlay(False))
    detail = (req.get("detail") or "").strip()
    if req.get("description"):
        lab = tk.Label(ov, text="# " + req["description"], font=window.f_small, bg=P["node"], fg=P["mu"],
                       anchor="w", justify="left", wraplength=window.px(560))
        lab.pack(fill="x", padx=pad)
        window._wraps.append(lab)
    if detail and not questions:
        lines = detail.splitlines()
        shown = "\n".join(lines[:6]) + ("\n…" if len(lines) > 6 else "")
        lab = tk.Label(ov, text=shown, font=window.f_main, bg=P["bar"], fg=P["hi"], anchor="w", justify="left",
                       wraplength=window.px(560), padx=window.px(6), pady=window.px(3))
        lab.pack(fill="x", padx=pad, pady=window.px(4))
        window._wraps.append(lab)
    answerable = permission_rules.hook_waiting(item) and window.pet.answers_enabled(item)
    window.own = None  # (question, entry) when a typed answer can be sent
    window.radio = False  # the options are a pick-one list (a question)
    if questions:
        simple = len(questions) == 1 and not questions[0].get("multiSelect") and questions[0].get("options")
        for q in questions[:1]:
            lab = tk.Label(ov, text=q.get("question", ""), font=window.f_main, bg=P["node"], fg=P["hi"], anchor="w",
                           justify="left", wraplength=window.px(560))
            lab.pack(fill="x", padx=pad, pady=(window.px(4), 0))
            window._wraps.append(lab)
        if len(questions) > 1 or (questions and questions[0].get("multiSelect")):
            lab = tk.Label(ov, text="# several questions or picks: open the full card to see them all", font=window.f_small,
                           bg=P["node"], fg=P["mu"], anchor="w", justify="left")
            lab.pack(fill="x", padx=pad)
            window._wraps.append(lab)
        if answerable and simple:
            q, window.radio = questions[0], True
            for opt in q["options"][:8]:
                label = str(opt.get("label") if isinstance(opt, dict) else opt)
                window.options.append((label, lambda label=label, q=q: window._send(
                    "allow", {q["question"]: label})))
            window.options.append(("your own answer:", window._send_own))
        elif not answerable:
            for opt in questions[0].get("options") or []:
                label = opt.get("label") if isinstance(opt, dict) else str(opt)
                description = opt.get("description", "") if isinstance(opt, dict) else ""
                lab = tk.Label(ov, text=label + (" — " + description if description else ""),
                               font=window.f_small, bg=P["node"], fg=P["tx"], anchor="w", justify="left",
                               wraplength=window.px(560))
                lab.pack(fill="x", padx=pad, pady=(window.px(2), 0))
                window._wraps.append(lab)
    elif answerable:
        window.options = [("allow once", lambda: window._send("allow"))]
        for sug in (req.get("suggestions") or [])[:3]:  # the AI's own "don't ask again" choices
            agent_name = "codex" if (item or {}).get("agent") == "codex" else "claude"
            window.options.append((f"{agent_name}: {common.suggestion_label(sug)}",
                                 lambda sug=sug: window._send("allow", permissions=[sug])))
        window.options += [("aipet: always (whitelist)", window._always),
                         ("deny", lambda: window._send("deny"))]
    n_answers = len(window.options)
    window.options.append(("full card", lambda: window.pet.open_detail(window.key)))
    codex = (item or {}).get("agent") == "codex"
    window.options.append(("answer in codex" if codex and answerable else "go to window", window._go))
    window._opt_row = tk.Frame(ov, bg=P["node"])
    window._opt_row.pack(fill="x", padx=pad, pady=window.px(6))
    window._opt_labels, window._opt_flow = [], None
    for n, (label, _) in enumerate(window.options):
        if window.radio and label == "your own answer:":
            own_row = tk.Frame(window._opt_row, bg=P["node"])
            lab = tk.Label(own_row, font=window.f_main, cursor="hand2", padx=window.px(4), pady=window.px(1),
                           bg=P["node"], fg=P["tx"], anchor="w")
            lab.pack(side="left")
            entry = tk.Entry(own_row, font=window.f_main, bg=P["bar"], fg=P["hi"], insertbackground=P["hi"],
                             relief="flat", highlightthickness=1, highlightbackground=P["line"],
                             highlightcolor=P["red"])
            entry.pack(side="left", fill="x", expand=True, padx=(window.px(4), 0))
            entry.bind("<KeyRelease>", lambda e, n=n: window._own_typed(n))
            entry.bind("<FocusIn>", lambda e, n=n: window._pick(n))
            entry.bind("<Return>", lambda e: (window._send_own(), "break")[1])
            entry.bind("<Escape>", lambda e: (window.win.focus_set(), "break")[1])
            window.own = (questions[0], entry)
            lab.bind("<Button-1>", lambda e, n=n: window.choose(n))
            lab._row = own_row
        else:
            lab = tk.Label(window._opt_row, font=window.f_main, cursor="hand2", padx=window.px(4), pady=window.px(1),
                           bg=P["node"], fg=P["tx"], anchor="w", justify="left")
            lab.bind("<Button-1>", lambda e, n=n: window.choose(n))
            lab._row = None
        lab._text = label
        lab._answer = n < n_answers
        window._opt_labels.append(lab)
    window._flow_options(window.px(560))
    if answerable:
        hint = ("# 1-9 or ↑↓ + enter to answer · type your own + enter · esc fold" if window.radio
                else "# 1-9 or ←→ + enter to answer · esc fold")
    elif req.get("id") in window._released:
        hint = "# handed back to codex: answer it in the codex window now (go to window takes you there)"
    elif req.get("source") == "codex-question":
        hint = "# answer this question in Codex · go to window opens the session"
    else:
        hint = "# this prompt can only be answered in the session window (or turn on answering in permissions)"
    window.note = tk.Label(ov, text=hint, font=window.f_small, bg=P["node"], fg=P["mu"], anchor="w", justify="left")
    window.note.pack(fill="x", padx=pad, pady=(0, pad))
    window._wraps.append(window.note)
    window._mark_option()
    if window.overlay_open:
        window._place_overlay()


def _flow_options(window, width):
    """The answers side by side when they fit, else one per line (wrapping long ones); a question's answers are
    always one per line, like radio buttons."""
    labels = getattr(window, "_opt_labels", [])
    if not labels:
        return
    for lab in labels:
        lab.configure(wraplength=0)
    total = sum(lab.winfo_reqwidth() + window.px(4) for lab in labels)
    flow = "row" if not window.radio and total <= width else "column"
    if flow == window._opt_flow and flow == "row":
        return
    window._opt_flow = flow
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


def _mark_option(window):
    P = window.P
    for n, lab in enumerate(getattr(window, "_opt_labels", [])):
        try:
            on = n == window.option
            mark = ("(•) " if on else "( ) ") if window.radio and lab._answer else ""
            lab.configure(text=f"[{n + 1}] {mark}{lab._text}", bg=P["redbg"] if on else P["node"],
                          fg=P["red"] if on else P["tx"])
        except (tk.TclError, AttributeError):
            pass


def _pick(window, n):
    window.option = n
    window._mark_option()


def _own_typed(window, n):
    if window.own and window.own[1].get().strip():
        window._pick(n)


def _send_own(window):
    """Send what was typed as the answer; with nothing typed yet, go to the box."""
    if not window.own:
        return
    q, entry = window.own
    text = entry.get().strip()
    if not text:
        entry.focus_set()
        return
    window.win.focus_set()
    window._send("allow", {q["question"]: text})


def step_option(window, d):
    if getattr(window, "options", None):
        window.option = (window.option + d) % len(window.options)
        window._mark_option()
    return "break"


def choose(window, n):
    if window.overlay_open and 0 <= n < len(getattr(window, "options", [])):
        window.option = n
        window._mark_option()
        window.options[n][1]()
    return "break"


def _send(window, behavior, answers=None, permissions=None):
    ok = (window.pet.send_answer(window.key, behavior, answers=answers, permissions=permissions) if permissions
          else window.pet.send_answer(window.key, behavior, answers=answers))
    window.note.configure(text="# sent" if ok else "# couldn't send it: the prompt may have been answered already")
    if ok:
        window.win.after(600, lambda: window.open_overlay(False))


def _go(window):
    """Go to the session's window. Codex: AIPet lets go of the prompt, so Codex shows its own one there (with
    choices the pet doesn't have, like always allowing that tool)."""
    req = (window.item or {}).get("request") or {}
    if (window.item or {}).get("agent") == "codex" and req.get("id"):
        window._released.add(req["id"])
    window.pet.go_to_prompt(window.key)
    window._prompt_sig = None  # rebuilt as "answer it in the window"
    window.open_overlay(False)


def _always(window):
    added = window.pet.whitelist_request(window.item)
    window._send("allow")
    if added:
        window.note.configure(text="# " + added)
