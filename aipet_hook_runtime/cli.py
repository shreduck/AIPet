"""Invocation routing, usage-worker launch and hook lifecycle."""
import json
import base64
import os
import subprocess
import sys
import time
from . import approvals as hook_approvals
from . import addins as hook_addins
from . import capture as hook_capture
from . import config as hook_config
from . import diagnostics as hook_diagnostics
from . import hosts as hook_hosts
from . import io as hook_io
from . import requests as hook_requests
from . import sessions as hook_sessions
from . import waiting as hook_waiting

def main():
    raw = hook_io.read_stdin()
    try:
        data = json.loads(raw or "{}")
    except Exception:
        data = {}

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    event = data.get("hook_event_name") or (args[0] if args else "")
    session_id = data.get("session_id") or "unknown"
    wsl = hook_hosts.is_wsl()
    target = hook_hosts.sessions_dir(wsl)
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, hook_io.safe_name(session_id) + ".json")
    if any(a.startswith("--claude-usage-worker=") for a in sys.argv[1:]):
        sid = next(a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--claude-usage-worker="))
        if hook_capture.claude_usage is not None:
            hook_capture.claude_usage.worker(target, hook_io.safe_name(sid))
        return
    if "--usage" in sys.argv[1:]:
        try:
            if hook_capture.usage is not None:
                hook_capture.usage.save(target, hook_io.safe_name(session_id), data.get("rate_limits"))
        except Exception:
            pass
        forward = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--usage-forward=")), "")
        if forward:
            command = base64.urlsafe_b64decode(forward).decode("utf-8")
            bash = hook_hosts.find_statusline_bash()
            if not bash:
                return  # never reinterpret a Bash command through cmd.exe
            result = subprocess.run([bash, "-c", command], input=raw, text=True, capture_output=True, timeout=5,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            hook_io.write_stdout(result.stdout)
        else:
            rows = hook_capture.usage.normalize(data.get("rate_limits")) if hook_capture.usage is not None else []
            hook_io.write_stdout(" · ".join(f"{r['window']} {r['percent']}% used" for r in rows))
        return
    try:
        if hook_config.AGENT == "codex":
            hook_capture.capture_codex_usage(target, hook_io.safe_name(session_id), data, hook_io.read_json(path))
        elif hook_capture.usage is not None and event != "SessionEnd":
            hook_capture.usage.save(target, hook_io.safe_name(session_id), data.get("rate_limits"))
            if not hook_capture.usage.normalize(data.get("rate_limits")) and hook_capture.claude_usage is not None:
                command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aipet_hook.py")]
                hook_capture.claude_usage.schedule(target, hook_io.safe_name(session_id), command)
    except Exception:
        pass  # usage write/read failures cannot skip session updates or approvals
    hook_diagnostics.codex_raw_log(raw, os.path.dirname(target))  # TEMP (Codex testing)
    hook_addins.publish(os.path.dirname(target), hook_config.AGENT, event, data)

    if event == "SessionEnd" and (hook_hosts.is_cowork() or hook_io.read_json(path).get("app") == "cowork"):
        # Cowork ends its Claude Code session after every turn (and when the app closes), so removing the pet here
        # would make it vanish as soon as it answers. Mark it done instead: the pet then hides it after
        # the "Clear finished after" timeout, like any finished session, and the next message brings it back.
        with hook_io.SessionLock(path):
            rec = hook_io.read_json(path)
            prev_state = rec.get("state")
            if rec:
                now = time.time()
                rec.update({"state": "done", "message": "", "agents": {}, "main_stopped": False, "wait_agent": "",
                            "request": {}, "requests": [], "updated": now,
                            "changed": now if prev_state != "done" else rec.get("changed", now)})
                hook_io.write_atomic(path, rec)
            hook_diagnostics.debug_log(os.path.dirname(target), event, data, prev_state, "done (cowork: kept until timeout)")
        return

    if event == "SessionEnd":
        hook_diagnostics.debug_log(os.path.dirname(target), event, data, hook_io.read_json(path).get("state"), "(removed)")
        try:
            os.remove(path)
        except OSError:
            pass
        return

    auto = None
    if event == "PermissionRequest" and data.get("tool_name") != hook_config.QUESTION_TOOL:  # never auto-"approve" a question
        auto = hook_approvals.timed_decision(os.path.dirname(target), session_id, data=data,
                                           key=hook_approvals.auto_approve_key(wsl)) or \
            hook_approvals.auto_decision(hook_approvals.auto_approve_rules(os.path.dirname(target), hook_approvals.auto_approve_key(wsl)), data)
    if auto and auto != "blocked":
        with hook_io.SessionLock(path):
            hook_sessions._update_session(path, target, event, data, wsl, auto=auto)
        hook_io.write_stdout(hook_requests.decision_output("deny" if auto == "blacklist" else "allow"))
        return

    if event == "PermissionRequest":
        data["_aipet_request_id"] = hook_requests.request_id(data)  # this prompt's id, recorded and waited on by this run
    with hook_io.SessionLock(path):
        aid = hook_sessions._update_session(path, target, event, data, wsl)
    # Each agent's manual-answer toggle controls whether its hook waits for the pet.
    if event == "PermissionRequest" and hook_waiting.answers_enabled(os.path.dirname(target)):
        hook_waiting.answer_flow(os.path.dirname(target), path, aid or "", data.get("_aipet_request_id"), data.get("tool_input"))
