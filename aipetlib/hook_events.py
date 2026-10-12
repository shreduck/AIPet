"""Hook event state transitions and request reconciliation.

Services are supplied by the compatibility entry point; this module does not import aipet.
"""
import os
import sys
import time


def _update_session(services, path, target, event, data, wsl, auto=None):
    """Apply one hook event to the session file (call under SessionLock). Returns the agent id, if any.
    auto: this PermissionRequest is being auto-approved ("all" or "whitelist"), so the session keeps working."""
    session_id = data.get("session_id") or "unknown"
    prev = services.read_json(path)
    state = prev.get("state", "idle")
    message = prev.get("message", "")
    # Subagents: their hook payloads (probably) carry an agent_id. Track the recently active ones so the pet can
    # show a stack, and so a subagent's tool call can't hide a permission prompt that belongs to someone else.
    aid = str(data.get("agent_id") or data.get("subagent_id") or "")
    transcript = data.get("transcript_path") or prev.get("transcript_path", "")
    if services.AGENT == "codex":
        transcript = services.codex_session_transcript(session_id, transcript, prev.get("transcript_path", ""))
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
    requests = services.pending_requests(prev)  # every prompt still waiting, oldest (the one on screen) first
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
        started[use_id] = [services.tool_signature(data), t_now]
        started = dict(sorted(started.items(), key=lambda kv: kv[1][1])[-30:])
    elif event in services.TOOL_DONE_EVENTS:  # a tool call is over: if it had a prompt, that prompt was answered
        started.pop(use_id, None)
        sig = services.tool_signature(data)
        mine = [r for r in requests if use_id and r.get("tool_use_id") == use_id]
        done = [a for a in answered if (use_id and a.get("tool_use_id") == use_id) or
                (not a.get("tool_use_id") and a.get("sig") == sig)][:1]
        if done and not mine:  # the prompt answered from the pet: already settled
            answered.remove(done[0])
            mine = [None]
        if not mine:  # not linked by id: the same tool with the same input, oldest first
            mine = [r for r in requests if not r.get("tool_use_id") and r.get("sig") == sig][:1]
        if not mine and data.get("tool_name") == services.QUESTION_TOOL:  # a question that was answered: the oldest one
            mine = [r for r in requests if r.get("kind") == "question" and r.get("tool") == services.QUESTION_TOOL][:1]
        if not mine:  # a record from before prompts were tracked one by one
            mine = [r for r in requests if not r.get("sig") and r.get("source") != "transcript"][:1]
        requests = [r for r in requests if r not in mine]

    if event == "PermissionRequest" and auto:  # answered "allow" right away: never shows as "needs you"
        if not [r for r in requests if r.get("source") != "transcript"]:
            state, message = ("working", "") if prev.get("state") != "needs_input" or wait_agent == aid else (state, message)
        auto_t, auto_n, auto_kind = t_now, auto_n + 1, auto
        req = services.build_request(data)
        auto_what = (req["tool"] + (": " + req["detail"].splitlines()[0] if req["detail"] else ""))[:120]
    elif event == "PostToolUse" and data.get("tool_name") in services.CODEX_QUESTION_TOOLS and services.codex_questions(data):
        # Codex asked a question: needs you until the answer comes in as the next prompt. Read-only on the pet.
        req = {"tool": str(data.get("tool_name")), "description": "", "detail": "", "id": services.request_id(data),
               "t": time.time(), "kind": "question", "source": "codex-question", "agent": aid,
               "tool_use_id": use_id,
               "sig": services.tool_signature(data), "shown": True, "questions": services.codex_questions(data)}
        requests = [r for r in requests if r.get("source") != "transcript"] + [req]
        state, message = "needs_input", services.permission_message(requests[0])
        wait_agent = requests[0].get("agent", aid)
    elif event in services.WORKING_EVENTS:
        # A waiting prompt survives activity that doesn't answer it: another agent working (a subagent while the
        # main agent asks, or the main agent while a subagent asks), the same agent starting ANOTHER tool (Claude
        # Code runs PreToolUse for parallel tool calls while the first one's prompt is still open), or a tool finishing
        # while other prompts are still queued - answering one of several prompts must not look like "working".
        other_agent = (aid or "") != (wait_agent or "")
        real = [r for r in requests if r.get("source") != "transcript"]
        if event == "UserPromptSubmit":
            requests = []
        keep = event != "UserPromptSubmit" and prev.get("state") == "needs_input" and (other_agent or bool(real))
        if keep:
            requests = real or requests
            if real and requests[0].get("id") != (services.pending_requests(prev)[:1] or [{}])[0].get("id"):
                message, wait_agent = services.permission_message(requests[0]), requests[0].get("agent", wait_agent)
        else:  # otherwise the prompt is still waiting for the user
            state, message, requests = "working", "", []
        if not aid:
            main_stopped = False  # the main agent itself is active again
    elif event == "PermissionRequest":  # observe only: print nothing, so the normal prompt is untouched
        req = services.build_request(data)
        req["agent"] = aid
        taken = {r.get("tool_use_id") for r in requests}
        req["sig"] = sig = services.tool_signature(data)
        match = [k for k, v in sorted(started.items(), key=lambda kv: kv[1][1]) if v[0] == sig and k not in taken]
        req["tool_use_id"] = use_id or (match[0] if match else "")
        requests = [r for r in requests if r.get("source") != "transcript" and r.get("id") != req["id"]] + [req]
        state, message = "needs_input", services.permission_message(requests[0])
        wait_agent = requests[0].get("agent", aid)
    elif event == "Notification":
        msg = data.get("message", "") or ""
        ntype = data.get("notification_type", "") or ""
        is_idle_reminder = ntype == "idle_prompt" or "waiting for your input" in msg.lower()
        # Only notifications that ask the user to act count. An idle reminder ("Claude is waiting for your input", which also
        # fires while background agents run) and informational kinds never become "needs you". Older versions send no type:
        # then a permission message counts.
        actionable = ntype in services.ACTIONABLE_NOTIFICATIONS or (not ntype and not is_idle_reminder and "permission" in msg.lower())
        if actionable and (ntype == "permission_prompt" or "permission" in msg.lower()) and t_now - auto_t < 5:
            actionable = False  # the permission prompt we just auto-approved: Claude Code may still announce it
        if ntype == "permission_prompt":
            # Claude Code announces a prompt it really shows (about 6 s after it appears). A PermissionRequest that
            # never gets this was settled without the user (auto mode, an allow rule...): the pet stops showing it.
            notifies = True
            unshown = [r for r in requests if not r.get("shown") and r.get("source") != "transcript"]
            if unshown:
                unshown[0]["shown"] = True
        if actionable:
            state, message = "needs_input", msg
            # a transcript guess is recomputed; real PermissionRequest prompts are kept whatever their age
            requests = [r for r in requests if r.get("source") != "transcript"]
            if requests:
                wait_agent = requests[0].get("agent", wait_agent)
            else:
                wait_agent = aid
                if ntype == "permission_prompt" or "permission" in msg.lower():
                    guess = services.request_from_transcript(data.get("transcript_path"))  # sessions without PermissionRequest
                    requests = [guess] if guess else []
    elif event == "StopFailure":  # the turn died on an API error (usage limit, overloaded...): show it, red
        kind = services.stop_failure_kind(data)
        state, message, main_stopped = "error", services.stop_failure_message(data, kind), False
        requests, wait_agent = [], ""
        error_kind = kind
    elif event == "Interrupt":  # Codex: the user interrupted the turn
        state, message, main_stopped = "done", "", False
        requests, wait_agent = [], ""
    elif event == "Stop":
        if prev.get("state") == "error" and error_kind:
            pass  # a StopFailure just ended this turn: keep showing why it stopped
        elif agents:  # the turn is over but background subagents are still running: not done yet
            if prev.get("state") != "needs_input":
                state, message = "working", ""
            main_stopped = True
        else:
            state, message, main_stopped = "done", "", False
    elif event == "SessionStart":
        state = "idle"
    elif event == "SubagentStop":
        # A subagent that stops (finished, or killed by an API error such as the usage limit) asks nothing anymore
        mine = [r for r in requests if aid and r.get("agent") == aid]
        if mine:
            requests = [r for r in requests if r not in mine]
            if prev.get("state") == "needs_input" and not [r for r in requests if r.get("source") != "transcript"]:
                state, message, wait_agent = ("done", "", "") if not agents and main_stopped else ("working", "", "")
                main_stopped = main_stopped and state != "done"
            elif requests:
                message, wait_agent = services.permission_message(requests[0]), requests[0].get("agent", "")
        if not agents and main_stopped and prev.get("state") == "working":
            state, message, main_stopped = "done", "", False  # the last background agent finished and the main one is idle
    else:
        services.debug_log(os.path.dirname(target), event, data, prev.get("state"), "(ignored)")
        return

    now = time.time()
    # Prefer the stable project root: payload cwd follows the shell, so it changes on every `cd`.
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or prev.get("cwd") or data.get("cwd") or os.getcwd()
    hwnd, host = prev.get("hwnd"), prev.get("host", "")
    if os.name == "nt" and not wsl and not (hwnd and services.window_alive(hwnd)):
        hwnd, host = services.find_host_window(data.get("cwd") or cwd)  # for click-to-focus; re-found if the window was closed
    cowork = services.is_cowork() or prev.get("app") == "cowork"
    folders = services.cowork_folders() if cowork else []
    if folders:  # name a Cowork session after the folder it works in, not its private "host-cwd"
        cwd = folders[0]
    title = os.path.basename(cwd.rstrip("\\/")) or cwd
    if len(folders) > 1:
        title += f" +{len(folders) - 1}"
    # The process running the session RIGHT NOW, looked up on every event: Cowork starts a new Claude Code process for
    # each turn under the same session id (and any session can be resumed), so a pid kept from the first event goes
    # stale - and the pet's health check would then call a working session finished.
    pid = services.find_claude_pid() or prev.get("pid")
    topic = prev.get("topic", "")
    last_prompt = prev.get("last_prompt", "")
    prompt_text = " ".join(str(data.get("prompt") or "").split()) if event == "UserPromptSubmit" and not aid else ""
    if prompt_text.startswith("<"):  # injected by the harness (<task-notification>, <command-name>...), not typed
        prompt_text = ""
    if prompt_text:  # the latest prompt the user typed: the name until the harness has one
        last_prompt = prompt_text[:60]
    # The conversation's name, kept stable like the harness shows it: a /rename always wins (and follows later renames);
    # otherwise the FIRST generated title is kept for good, even if the harness writes newer summaries later.
    conv, conv_src = prev.get("conv", ""), prev.get("conv_src", "")
    if event in ("Stop", "SessionStart", "UserPromptSubmit"):  # not on every tool call: it reads the transcript
        if services.AGENT == "codex":  # Codex keeps the thread's name (and later renames) in its session index
            custom, generated = services.codex_thread_name(str(data.get("session_id") or "")), ""
        else:
            custom, generated = services.conversation_title(data.get("transcript_path"))
        if custom:
            conv, conv_src = custom, "custom"
        elif generated and conv_src not in ("custom", "generated"):
            conv, conv_src = generated, "generated"
    if services.is_home_or_root(cwd) or (cowork and not folders):  # no folder to name it after: use the first prompt
        if not topic and event == "UserPromptSubmit":
            topic = prompt_text[:40]
        title = topic or ("Cowork" if cowork else "~")
    record = {
        "id": session_id,
        "source": "claude-code",
        "agent": services.AGENT,
        "app": "cowork" if cowork else "",
        "env": "wsl" if wsl else ("windows" if os.name == "nt" else sys.platform),
        "distro": os.environ.get("WSL_DISTRO_NAME", "") if wsl else "",
        "ide": services.detect_ide() or prev.get("ide", ""),
        "entry": os.environ.get("CLAUDE_CODE_ENTRYPOINT", "") or prev.get("entry", ""),  # e.g. cli, claude-vscode
        "title": title,
        "topic": topic,
        "hwnd": hwnd,
        "host": host,
        "pid": pid,
        "agents": agents,
        "agent_types": agent_types,
        "main_stopped": main_stopped,
        "wait_agent": wait_agent if state == "needs_input" else "",
        "request": requests[0] if state == "needs_input" and requests else {},  # the prompt on screen
        "requests": requests if state == "needs_input" else [],  # all waiting prompts, oldest first
        "started": started,
        "answered": answered if state != "done" else [],
        "notifies": notifies,
        "error_kind": error_kind if state == "error" else "",
        "cwd": cwd,
        "state": state,
        "message": message,
        "auto_t": auto_t,
        "auto_approved": auto_n,  # permission prompts auto-approved in this session (Auto approve menu)
        "auto_kind": auto_kind,  # how the last one was approved: "all" or "whitelist"
        "auto_what": auto_what,  # tool and first line of what it ran, shown by the pet for a few seconds
        "conv": conv,  # the conversation's name, shown when several sessions share a folder name
        "conv_src": conv_src,  # "custom" (/rename) or "generated"; until then the pet uses last_prompt
        "last_prompt": last_prompt,
        "transcript_path": transcript,
        "updated": now,
        "changed": now if state != prev.get("state") else prev.get("changed", now),
    }
    services.debug_log(os.path.dirname(target), event, data, prev.get("state"), state)
    services.write_atomic(path, record)
    return aid
