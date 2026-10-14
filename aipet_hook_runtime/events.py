"""Hook event state transitions and request reconciliation.

Runs inside the hook process (standard library only, deployed beside aipet_hook.py). What it needs from the hook is
named in HOOK_API and wired from their owners by sessions for each event; it never imports the entry script.
"""
import os
import sys
import time
from types import SimpleNamespace
from . import claude, codex

# Everything the event logic uses from the hook: its settings and helpers, by name.
HOOK_API = (
    "ACTIONABLE_NOTIFICATIONS",
    "AGENT",
    "CODEX_QUESTION_TOOLS",
    "QUESTION_TOOL",
    "TOOL_DONE_EVENTS",
    "WORKING_EVENTS",
    "build_request",
    "codex_questions",
    "codex_session_transcript",
    "codex_thread_name",
    "conversation_title",
    "cowork_folders",
    "debug_log",
    "detect_ide",
    "find_claude_pid",
    "find_host_window",
    "is_cowork",
    "is_home_or_root",
    "pending_requests",
    "permission_message",
    "read_json",
    "request_from_transcript",
    "request_id",
    "stop_failure_kind",
    "stop_failure_message",
    "tool_signature",
    "window_alive",
    "write_atomic",
)


class HookAPI:
    """The settings and helpers listed in HOOK_API, and nothing else. sessions wires one per event from
    the owning modules, so patched helpers and the single provider setting are read at the lookup site."""
    __slots__ = HOOK_API

    def __init__(self, **values):
        missing, extra = set(HOOK_API) - set(values), set(values) - set(HOOK_API)
        if missing or extra:
            raise TypeError(f"HookAPI: missing {sorted(missing)}, unexpected {sorted(extra)}")
        for name, value in values.items():
            setattr(self, name, value)


def _update_session(hook, path, target, event, data, wsl, auto=None):
    """Apply one hook event to the session file (call under SessionLock). Returns the agent id, if any.
    auto: this PermissionRequest is being auto-approved ("all" or "whitelist"), so the session keeps working."""
    session_id = data.get("session_id") or "unknown"
    prev = hook.read_json(path)
    state = prev.get("state", "idle")
    message = prev.get("message", "")
    # Subagents: their hook payloads (probably) carry an agent_id. Track the recently active ones so the pet can
    # show a stack, and so a subagent's tool call can't hide a permission prompt that belongs to someone else.
    aid = str(data.get("agent_id") or data.get("subagent_id") or "")
    transcript = data.get("transcript_path") or prev.get("transcript_path", "")
    if hook.AGENT == "codex":
        transcript = hook.codex_session_transcript(session_id, transcript, prev.get("transcript_path", ""))
    t_now = time.time()
    agents = {k: v for k, v in (prev.get("agents") or {}).items() if t_now - v < 900}  # until SubagentStop (15 min safety)
    if event == "SubagentStop":
        agents.pop(aid, None)
    elif aid:
        agents[aid] = t_now
    agents = dict(sorted(agents.items(), key=lambda kv: kv[1])[-8:])
    # what each running subagent is (its agent_type, e.g. "tester"), for the pet's Live Management menu
    agent_types = {k: v for k, v in (prev.get("agent_types") or {}).items() if k in agents}
    if aid and aid in agents and data.get("agent_type"):
        agent_types[aid] = str(data.get("agent_type"))[:40]
    wait_agent = prev.get("wait_agent", "")
    requests = hook.pending_requests(prev)  # every prompt still waiting, oldest (the one on screen) first
    main_stopped = bool(prev.get("main_stopped"))  # the main agent finished its turn while subagents kept running
    auto_t, auto_n = prev.get("auto_t", 0), prev.get("auto_approved", 0)
    auto_kind, auto_what = prev.get("auto_kind", ""), prev.get("auto_what", "")
    # Tool calls that started (PreToolUse) and haven't finished: lets a PermissionRequest, which carries no
    # tool_use_id, be matched to its call, so the call's PostToolUse can retire exactly that prompt.
    started = {k: v for k, v in (prev.get("started") or {}).items() if isinstance(v, list) and t_now - v[1] < 900}
    use_id = str(data.get("tool_use_id") or "")
    answered = [a for a in (prev.get("answered") or []) if isinstance(a, dict)]  # allowed from the pet, not yet run
    notifies = bool(prev.get("notifies"))  # this session announces its permission prompts (Notification)
    error_kind = prev.get("error_kind", "") if state == "error" else ""
    if event == "PreToolUse" and use_id:
        started[use_id] = [hook.tool_signature(data), t_now]
        started = dict(sorted(started.items(), key=lambda kv: kv[1][1])[-30:])
    elif event in hook.TOOL_DONE_EVENTS:  # a tool call is over: if it had a prompt, that prompt was answered
        started.pop(use_id, None)
        sig = hook.tool_signature(data)
        mine = [r for r in requests if use_id and r.get("tool_use_id") == use_id]
        done = [a for a in answered if (use_id and a.get("tool_use_id") == use_id) or
                (not a.get("tool_use_id") and a.get("sig") == sig)][:1]
        if done and not mine:  # the prompt answered from the pet: already settled
            answered.remove(done[0])
            mine = [None]
        if not mine:  # not linked by id: the same tool with the same input, oldest first
            mine = [r for r in requests if not r.get("tool_use_id") and r.get("sig") == sig][:1]
        if not mine and data.get("tool_name") == hook.QUESTION_TOOL:  # a question that was answered: the oldest one
            mine = [r for r in requests if r.get("kind") == "question" and r.get("tool") == hook.QUESTION_TOOL][:1]
        if not mine:  # a record from before prompts were tracked one by one
            mine = [r for r in requests if not r.get("sig") and r.get("source") != "transcript"][:1]
        requests = [r for r in requests if r not in mine]

    transition = SimpleNamespace(
        state=state,
        message=message,
        requests=requests,
        wait_agent=wait_agent,
        main_stopped=main_stopped,
        auto_t=auto_t,
        auto_n=auto_n,
        auto_kind=auto_kind,
        auto_what=auto_what,
        notifies=notifies,
        error_kind=error_kind,
    )
    ctx = SimpleNamespace(
        hook=hook,
        event=event,
        data=data,
        prev=prev,
        aid=aid,
        agents=agents,
        started=started,
        use_id=use_id,
        t_now=t_now,
        auto=auto,
        path=path,
        target=target,
        wsl=wsl,
        session_id=session_id,
        transcript=transcript,
        agent_types=agent_types,
        answered=answered,
    )
    if not _interpret_event(ctx, transition):
        return
    return _write_session(ctx, transition)


def _interpret_event(ctx, transition):
    if ctx.event == "PermissionRequest" and ctx.auto:  # answered "allow" right away: never shows as "needs you"
        if not [r for r in transition.requests if r.get("source") != "transcript"]:
            transition.state, transition.message = ("working", "") if ctx.prev.get("state") != "needs_input" or transition.wait_agent == ctx.aid else (transition.state, transition.message)
        transition.auto_t, transition.auto_n, transition.auto_kind = ctx.t_now, transition.auto_n + 1, ctx.auto
        req = ctx.hook.build_request(ctx.data)
        transition.auto_what = (req["tool"] + (": " + req["detail"].splitlines()[0] if req["detail"] else ""))[:120]
    elif ctx.event == "PostToolUse" and ctx.data.get("tool_name") in ctx.hook.CODEX_QUESTION_TOOLS and ctx.hook.codex_questions(ctx.data):
        # Codex asked a question: needs you until the answer comes in as the next prompt. Read-only on the pet.
        codex.question_event(ctx, transition)
    elif ctx.event in ctx.hook.WORKING_EVENTS:
        # A waiting prompt survives activity that doesn't answer it: another agent working (a subagent while the
        # main agent asks, or the main agent while a subagent asks), the same agent starting ANOTHER tool (Claude
        # Code runs PreToolUse for parallel tool calls while the first one's prompt is still open), or a tool finishing
        # while other prompts are still queued - answering one of several prompts must not look like "working".
        other_agent = (ctx.aid or "") != (transition.wait_agent or "")
        real = [r for r in transition.requests if r.get("source") != "transcript"]
        if ctx.event == "UserPromptSubmit":
            transition.requests = []
        keep = ctx.event != "UserPromptSubmit" and ctx.prev.get("state") == "needs_input" and (other_agent or bool(real))
        if keep:
            transition.requests = real or transition.requests
            if real and transition.requests[0].get("id") != (ctx.hook.pending_requests(ctx.prev)[:1] or [{}])[0].get("id"):
                transition.message, transition.wait_agent = ctx.hook.permission_message(transition.requests[0]), transition.requests[0].get("agent", transition.wait_agent)
        else:  # otherwise the prompt is still waiting for the user
            transition.state, transition.message, transition.requests = "working", "", []
        if not ctx.aid:
            transition.main_stopped = False  # the main agent itself is active again
    elif ctx.event == "PermissionRequest":  # observe only: print nothing, so the normal prompt is untouched
        req = ctx.hook.build_request(ctx.data)
        req["agent"] = ctx.aid
        taken = {r.get("tool_use_id") for r in transition.requests}
        req["sig"] = sig = ctx.hook.tool_signature(ctx.data)
        match = [k for k, v in sorted(ctx.started.items(), key=lambda kv: kv[1][1]) if v[0] == sig and k not in taken]
        req["tool_use_id"] = ctx.use_id or (match[0] if match else "")
        transition.requests = [r for r in transition.requests if r.get("source") != "transcript" and r.get("id") != req["id"]] + [req]
        transition.state, transition.message = "needs_input", ctx.hook.permission_message(transition.requests[0])
        transition.wait_agent = transition.requests[0].get("agent", ctx.aid)
    elif ctx.event == "Notification":
        claude.notification(ctx, transition)
    elif ctx.event == "StopFailure":  # the turn died on an API error (usage limit, overloaded...): show it, red
        claude.stop_failure(ctx, transition)
    elif ctx.event == "Interrupt":  # Codex: the user interrupted the turn
        codex.interrupt(ctx, transition)
    elif ctx.event == "Stop":
        if ctx.prev.get("state") == "error" and transition.error_kind:
            pass  # a StopFailure just ended this turn: keep showing why it stopped
        elif ctx.agents:  # the turn is over but background subagents are still running: not done yet
            if ctx.prev.get("state") != "needs_input":
                transition.state, transition.message = "working", ""
            transition.main_stopped = True
        else:
            transition.state, transition.message, transition.main_stopped = "done", "", False
    elif ctx.event == "SessionStart":
        transition.state = "idle"
    elif ctx.event == "SubagentStop":
        # A subagent that stops (finished, or killed by an API error such as the usage limit) asks nothing anymore
        mine = [r for r in transition.requests if ctx.aid and r.get("agent") == ctx.aid]
        if mine:
            transition.requests = [r for r in transition.requests if r not in mine]
            if ctx.prev.get("state") == "needs_input" and not [r for r in transition.requests if r.get("source") != "transcript"]:
                transition.state, transition.message, transition.wait_agent = ("done", "", "") if not ctx.agents and transition.main_stopped else ("working", "", "")
                transition.main_stopped = transition.main_stopped and transition.state != "done"
            elif transition.requests:
                transition.message, transition.wait_agent = ctx.hook.permission_message(transition.requests[0]), transition.requests[0].get("agent", "")
        if not ctx.agents and transition.main_stopped and ctx.prev.get("state") == "working":
            transition.state, transition.message, transition.main_stopped = "done", "", False  # the last background agent finished and the main one is idle
    else:
        ctx.hook.debug_log(os.path.dirname(ctx.target), ctx.event, ctx.data, ctx.prev.get("state"), "(ignored)")
        return False
    return True


def _write_session(ctx, transition):

    now = time.time()
    # Prefer the stable project root: payload cwd follows the shell, so it changes on every `cd`.
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or ctx.prev.get("cwd") or ctx.data.get("cwd") or os.getcwd()
    hwnd, host = ctx.prev.get("hwnd"), ctx.prev.get("host", "")
    if os.name == "nt" and not ctx.wsl and not (hwnd and ctx.hook.window_alive(hwnd)):
        hwnd, host = ctx.hook.find_host_window(ctx.data.get("cwd") or cwd)  # for click-to-focus; re-found if the window was closed
    cowork = ctx.hook.is_cowork() or ctx.prev.get("app") == "cowork"
    folders = ctx.hook.cowork_folders() if cowork else []
    if folders:  # name a Cowork session after the folder it works in, not its private "host-cwd"
        cwd = folders[0]
    title = os.path.basename(cwd.rstrip("\\/")) or cwd
    if len(folders) > 1:
        title += f" +{len(folders) - 1}"
    # The process running the session RIGHT NOW, looked up on every event: Cowork starts a new Claude Code process for
    # each turn under the same session id (and any session can be resumed), so a pid kept from the first event goes
    # stale - and the pet's health check would then call a working session finished.
    pid = ctx.hook.find_claude_pid() or ctx.prev.get("pid")
    topic = ctx.prev.get("topic", "")
    last_prompt = ctx.prev.get("last_prompt", "")
    prompt_text = " ".join(str(ctx.data.get("prompt") or "").split()) if ctx.event == "UserPromptSubmit" and not ctx.aid else ""
    if prompt_text.startswith("<"):  # injected by the harness (<task-notification>, <command-name>...), not typed
        prompt_text = ""
    if prompt_text:  # the latest prompt the user typed: the name until the harness has one
        last_prompt = prompt_text[:60]
    # The conversation's name, kept stable like the harness shows it: a /rename always wins (and follows later renames);
    # otherwise the FIRST generated title is kept for good, even if the harness writes newer summaries later.
    conv, conv_src = ctx.prev.get("conv", ""), ctx.prev.get("conv_src", "")
    if ctx.event in ("Stop", "SessionStart", "UserPromptSubmit"):  # not on every tool call: it reads the transcript
        if ctx.hook.AGENT == "codex":  # Codex keeps the thread's name (and later renames) in its session index
            custom, generated = ctx.hook.codex_thread_name(str(ctx.data.get("session_id") or "")), ""
        else:
            custom, generated = ctx.hook.conversation_title(ctx.data.get("transcript_path"))
        if custom:
            conv, conv_src = custom, "custom"
        elif generated and conv_src not in ("custom", "generated"):
            conv, conv_src = generated, "generated"
    if ctx.hook.is_home_or_root(cwd) or (cowork and not folders):  # no folder to name it after: use the first prompt
        if not topic and ctx.event == "UserPromptSubmit":
            topic = prompt_text[:40]
        title = topic or ("Cowork" if cowork else "~")
    record = {
        "id": ctx.session_id,
        "source": "claude-code",
        "agent": ctx.hook.AGENT,
        "app": "cowork" if cowork else "",
        "env": "wsl" if ctx.wsl else ("windows" if os.name == "nt" else sys.platform),
        "distro": os.environ.get("WSL_DISTRO_NAME", "") if ctx.wsl else "",
        "ide": ctx.hook.detect_ide() or ctx.prev.get("ide", ""),
        "entry": os.environ.get("CLAUDE_CODE_ENTRYPOINT", "") or ctx.prev.get("entry", ""),  # e.g. cli, claude-vscode
        "title": title,
        "topic": topic,
        "hwnd": hwnd,
        "host": host,
        "pid": pid,
        "agents": ctx.agents,
        "agent_types": ctx.agent_types,
        "main_stopped": transition.main_stopped,
        "wait_agent": transition.wait_agent if transition.state == "needs_input" else "",
        "request": transition.requests[0] if transition.state == "needs_input" and transition.requests else {},  # the prompt on screen
        "requests": transition.requests if transition.state == "needs_input" else [],  # all waiting prompts, oldest first
        "started": ctx.started,
        "answered": ctx.answered if transition.state != "done" else [],
        "notifies": transition.notifies,
        "error_kind": transition.error_kind if transition.state == "error" else "",
        "cwd": cwd,
        "state": transition.state,
        "message": transition.message,
        "auto_t": transition.auto_t,
        "auto_approved": transition.auto_n,  # permission prompts auto-approved in this session (Auto approve menu)
        "auto_kind": transition.auto_kind,  # how the last one was approved: "all" or "whitelist"
        "auto_what": transition.auto_what,  # tool and first line of what it ran, shown by the pet for a few seconds
        "conv": conv,  # the conversation's name, shown when several sessions share a folder name
        "conv_src": conv_src,  # "custom" (/rename) or "generated"; until then the pet uses last_prompt
        "last_prompt": last_prompt,
        "transcript_path": ctx.transcript,
        "updated": now,
        "changed": now if transition.state != ctx.prev.get("state") else ctx.prev.get("changed", now),
    }
    ctx.hook.debug_log(os.path.dirname(ctx.target), ctx.event, ctx.data, ctx.prev.get("state"), transition.state)
    ctx.hook.write_atomic(ctx.path, record)
    return ctx.aid
