"""Claude event interpretation; shared record construction lives in events."""


def notification(ctx, transition):
    msg = ctx.data.get("message", "") or ""
    ntype = ctx.data.get("notification_type", "") or ""
    is_idle_reminder = ntype == "idle_prompt" or "waiting for your input" in msg.lower()
    # Only notifications that ask the user to act count. An idle reminder ("Claude is waiting for your input", which also
    # fires while background agents run) and informational kinds never become "needs you". Older versions send no type:
    # then a permission message counts.
    actionable = ntype in ctx.hook.ACTIONABLE_NOTIFICATIONS or (not ntype and not is_idle_reminder and "permission" in msg.lower())
    if actionable and (ntype == "permission_prompt" or "permission" in msg.lower()) and ctx.t_now - transition.auto_t < 5:
        actionable = False  # the permission prompt we just auto-approved: Claude Code may still announce it
    if ntype == "permission_prompt":
        # Claude Code announces a prompt it really shows (about 6 s after it appears). A PermissionRequest that
        # never gets this was settled without the user (auto mode, an allow rule...): the pet stops showing it.
        transition.notifies = True
        unshown = [r for r in transition.requests if not r.get("shown") and r.get("source") != "transcript"]
        if unshown:
            unshown[0]["shown"] = True
    if actionable:
        transition.state, transition.message = "needs_input", msg
        # a transcript guess is recomputed; real PermissionRequest prompts are kept whatever their age
        transition.requests = [r for r in transition.requests if r.get("source") != "transcript"]
        if transition.requests:
            transition.wait_agent = transition.requests[0].get("agent", transition.wait_agent)
        else:
            transition.wait_agent = ctx.aid
            if ntype == "permission_prompt" or "permission" in msg.lower():
                guess = ctx.hook.request_from_transcript(ctx.data.get("transcript_path"))  # sessions without PermissionRequest
                transition.requests = [guess] if guess else []


def stop_failure(ctx, transition):
    kind = ctx.hook.stop_failure_kind(ctx.data)
    transition.state, transition.message, transition.main_stopped = "error", ctx.hook.stop_failure_message(ctx.data, kind), False
    transition.requests, transition.wait_agent = [], ""
    transition.error_kind = kind
