"""Answer-file waiting, cancellation and request settlement."""
import os
import signal
import time
from . import config as hook_config
from . import diagnostics as hook_diagnostics
from . import io as hook_io
from . import requests as hook_requests

def pet_alive(base):
    """The pet touches <pet dir>/alive every couple of seconds while it runs."""
    try:
        return time.time() - os.path.getmtime(os.path.join(base, "alive")) < 8
    except OSError:
        return False


def await_answer(base, key, seconds, session_path=None, request_id=None):
    """Wait for the pet to drop <pet dir>/answers/<key>.json ({"behavior": "allow"|"deny"|"release"}). None = no
    answer ("release": the user went to the session's window to answer there).
    seconds <= 0 waits with no limit. Also gives up once the session has moved past this prompt (answered in
    Claude Code itself), so a waiting hook never outlives its prompt."""
    path = os.path.join(base, "answers", key + ".json")
    waiting = os.path.join(base, "answers", key + ".waiting")  # touched while we wait: the pet only offers its
    try:                                                         # buttons while this is fresh
        os.remove(path)  # an old answer must never apply to a new prompt
    except OSError:
        pass
    WAIT_END.clear()
    start = time.time()
    previous = {}

    def stopped(signum, frame):
        raise _Stopped(signum)

    for name in hook_config.STOP_SIGNALS:  # an agent that stops the hook politely: note it and leave cleanly
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                previous[sig] = signal.signal(sig, stopped)
            except (ValueError, OSError):
                pass
    try:
        return _await(path, waiting, base, seconds, session_path, request_id)
    except _Stopped as e:
        WAIT_END.update(reason="stopped", signal=getattr(signal.Signals(e.signum), "name", str(e.signum)))
        return None
    finally:
        WAIT_END["after"] = round(time.time() - start, 1)
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        try:
            os.remove(waiting)
        except OSError:
            pass


WAIT_END = {}  # how the last wait ended: reason (answered / released / timed_out / answered_elsewhere / answers_off /


class _Stopped(Exception):
    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


LAST_ANSWER = {}  # the whole answer file of the last click (behavior, and "answers" for a question)


def _await(path, waiting, base, seconds, session_path, request_id):
    deadline = time.time() + seconds if seconds > 0 else None
    checked, touched = time.time(), 0.0
    while deadline is None or time.time() < deadline:
        if not answers_enabled(base):
            WAIT_END["reason"] = "answers_off"
            return None  # toggled off while waiting: show the agent's normal prompt
        if time.time() - touched > 1:
            touched = time.time()
            try:
                os.makedirs(os.path.dirname(waiting), exist_ok=True)
                with open(waiting, "w") as f:
                    f.write(str(os.getpid()))
            except OSError:
                pass
        if session_path and request_id and time.time() - checked > 2:
            checked = time.time()
            if request_id not in {r.get("id") for r in hook_requests.pending_requests(hook_io.read_json(session_path))}:
                WAIT_END["reason"] = "answered_elsewhere"
                return None  # answered elsewhere: the record no longer holds this prompt
        if os.path.exists(path):
            time.sleep(0.05)
            data = hook_io.read_json(path)
            try:
                os.remove(path)
            except OSError:
                pass
            if data.get("behavior") == "release":
                WAIT_END["reason"] = "released"
                return None  # "Go to window" (Codex): let go, so the agent shows its own prompt with all its choices
            if data.get("behavior") in ("allow", "deny"):
                WAIT_END["reason"] = "answered"
                LAST_ANSWER.clear()
                LAST_ANSWER.update(data)  # answers to a question travel with the click
                return data["behavior"]
        if not pet_alive(base):
            WAIT_END["reason"] = "pet_closed"
            return None  # the pet was closed: fall back to the normal prompt
        time.sleep(0.2)
    WAIT_END["reason"] = "timed_out"
    return None


def configured_wait(base):
    """Seconds the user allows for answering from the pet (0 = no limit). Set with the pet's "Answer timeout" slider,
    which mirrors it into <pet dir>/answer-wait; 3 minutes if it was never set."""
    try:
        raw = os.environ.get("AIPET_ANSWER_WAIT") or os.environ.get("CLAUDE_PET_ANSWER_WAIT")  # LEGACY
        if not raw:
            with open(os.path.join(base, "answer-wait"), encoding="utf-8") as f:
                raw = f.read().strip()
        value = float(raw)
    except (OSError, ValueError):
        value = 180.0
    return max(0.0, min(hook_config.MAX_WAIT, value))


def answers_enabled(base):
    """Manual answering is independent for each agent; auto-approval has its own rules."""
    if os.path.exists(os.path.join(base, "no-answers")):
        return False
    if hook_config.AGENT == "codex":
        return os.path.exists(os.path.join(base, "codex-answers"))
    return not os.path.exists(os.path.join(base, "no-claude-answers"))


def _set_requests(record, requests):
    """Store the queue and show its oldest prompt; with none left the session is working again."""
    record["requests"] = requests
    record["request"] = requests[0] if requests else {}
    now = time.time()
    if requests:
        record.update(message=hook_requests.permission_message(requests[0]), wait_agent=requests[0].get("agent", ""), updated=now)
    else:
        record.update(state="working", message="", wait_agent="", changed=now, updated=now)


def answer_flow(base, path, aid, request_id=None, tool_input=None):
    """After the request is recorded: give the user a window to answer from the pet, then print the decision.
    Prints nothing (normal prompt) if the pet isn't running, answering is switched off, or nobody clicks in time.
    request_id: this hook's own prompt (several can wait at once); None = the one on screen."""
    seconds = configured_wait(base)  # 0 = wait until answered
    if not pet_alive(base) or not answers_enabled(base):
        return  # observe only
    with hook_io.SessionLock(path):
        record = hook_io.read_json(path)
        requests = hook_requests.pending_requests(record)
        req = next((r for r in requests if r.get("id") == request_id), None) if request_id else (requests or [None])[0]
        if not req:
            return
        key = hook_io.safe_name(req.get("id") or record.get("id") or "request")
        req["answerable"] = True
        record["requests"], record["request"] = requests, requests[0]
        hook_io.write_atomic(path, record)
    decision = await_answer(base, key, seconds, path, req.get("id"))  # no lock held while waiting
    with hook_io.SessionLock(path):
        cur = hook_io.read_json(path)
        requests = hook_requests.pending_requests(cur)
        mine = [r for r in requests if r.get("id") == req.get("id")]
        if mine:  # still waiting: settle this prompt only; any others stay queued and the next one shows
            if decision:
                _set_requests(cur, [r for r in requests if r not in mine])
                if decision == "allow":  # its PostToolUse must not retire another queued prompt for the same command
                    cur["answered"] = (cur.get("answered") or [])[-9:] + [
                        {"tool_use_id": mine[0].get("tool_use_id", ""), "sig": mine[0].get("sig", "")}]
            else:
                mine[0]["answerable"] = False
                if WAIT_END.get("reason") == "stopped":  # the card says so instead of guessing
                    mine[0]["hook_stopped"] = {"after": WAIT_END.get("after"), "signal": WAIT_END.get("signal", "")}
                _set_requests(cur, requests)
            hook_io.write_atomic(path, cur)
    hook_diagnostics.log_wait_end(base, cur, req, decision)
    if WAIT_END.get("reason") == "stopped":
        return  # the agent has given up on this hook: answer nothing, exit now
    if decision:
        updated = None
        answers = LAST_ANSWER.get("answers")
        permissions = None
        picked = LAST_ANSWER.get("permissions")
        if decision == "allow" and isinstance(picked, list) and picked:
            # only Claude Code's own suggestions for this very prompt, never anything else the answer file says
            offered = req.get("suggestions") or []
            permissions = [p for p in picked if p in offered] or None
        if decision == "allow" and req.get("kind") == "question":
            if not isinstance(answers, dict) or not answers:
                return  # a question can't be "allowed" without answers: leave it to Claude Code's own dialog
            # the questions exactly as Claude Code sent them, plus {question text: answer text}
            updated = dict(tool_input if isinstance(tool_input, dict) else {"questions": req.get("questions")},
                           answers=hook_requests.answer_texts(answers))
        hook_io.write_stdout(hook_requests.decision_output(decision, updated, permissions))
