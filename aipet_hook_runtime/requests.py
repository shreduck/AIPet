"""Prompt normalization, tool identity, queue reads and decision formatting."""
import json
import hashlib
import os
import time
from . import config as hook_config

def build_request(data):
    """What the permission prompt is about: tool, description and the command/path/url. Kept short on purpose;
    it only lives in the session file while the prompt is pending."""
    inp = data.get("tool_input")
    if not isinstance(inp, dict):
        inp = {"input": inp} if inp else {}
    detail = ""
    for key in ("command", "file_path", "path", "url", "pattern", "query", "prompt"):
        if inp.get(key):
            detail = str(inp[key])
            break
    if not detail and inp:
        detail = "\n".join(f"{k}: {str(v)[:200]}" for k, v in list(inp.items())[:6])
    req = {"tool": str(data.get("tool_name") or ""), "description": str(inp.get("description") or "")[:300],
           "detail": detail[:1500], "id": request_id(data), "t": time.time(),
           # what auto-approve matches, untruncated (the pet's "Allow + whitelist" saves it as an exact pattern), and
           # whether it is a real argument (command, path, URL...) rather than the whole input as JSON
           "subject": request_subjects(data)[0][:4000] if request_subjects(data) else "",
           "primary": any(inp.get(k) for k in ("command", "file_path", "path", "url", "pattern", "query", "prompt"))}
    # Claude Code's own choices for this prompt ("Yes, and don't ask again for ...": a rule for this session or for
    # the project, a permission mode...), so the pet can offer them next to its own
    suggestions = [x for x in (data.get("permission_suggestions") or []) if isinstance(x, dict)][:4]
    if suggestions and len(json.dumps(suggestions)) < 4000:
        req["suggestions"] = suggestions
    questions = question_list(data)
    if questions:  # Claude's AskUserQuestion: the pet shows the questions and can send the answers back
        req.update(kind="question", questions=questions, detail="", description="")
    return req


def codex_questions(data):
    # A synchronous question's PostToolUse can already contain the user's
    # answers. Do not turn a completed tool back into an outstanding question.
    response = data.get("tool_response")
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            response = None
    if isinstance(response, dict) and ("answers" in response or response.get("accepted") is False):
        return []
    inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    out = []
    for q in (inp.get("questions") or [])[:6]:
        if not isinstance(q, dict):
            continue
        text = str(q.get("title") or q.get("question") or "").strip()
        if not text:
            continue
        opts = []
        for o in (q.get("options") or [])[:8]:
            label = o.get("label") if isinstance(o, dict) else o
            if label:
                opts.append({"label": str(label)[:120], "description": str(o.get("description") or "")[:300]
                             if isinstance(o, dict) else ""})
        out.append({"question": text[:600], "header": "", "multiSelect": False, "options": opts})
    return out


def question_list(data):
    """The questions of an AskUserQuestion call, trimmed to what the pet shows:
    [{"question", "header", "multiSelect", "options": [{"label", "description"}]}]. [] for any other tool."""
    if data.get("tool_name") != hook_config.QUESTION_TOOL:
        return []
    inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    out = []
    for q in (inp.get("questions") or [])[:6]:
        if not isinstance(q, dict) or not q.get("question"):
            continue
        opts = [{"label": str(o.get("label") or "")[:120], "description": str(o.get("description") or "")[:300]}
                for o in (q.get("options") or [])[:8] if isinstance(o, dict) and o.get("label")]
        out.append({"question": str(q["question"])[:600], "header": str(q.get("header") or "")[:40],
                    "multiSelect": bool(q.get("multiSelect")), "options": opts})
    return out


def request_id(data):
    """An id that is unique per permission prompt. Claude Code's PermissionRequest payload carries no tool_use_id and
    Codex only sends a turn_id (shared by every prompt in a turn), so without the unique part two prompts in a row had
    the same id (""): settling the first one (after Allow once on the pet) then wiped the second one from the session,
    and a waiting hook could no longer tell that its prompt had been answered in the app."""
    if data.get("_aipet_request_id"):  # chosen once per hook run (main), so recording and waiting agree
        return str(data["_aipet_request_id"])
    base = str(data.get("tool_use_id") or "")
    if base:
        return base
    turn = str(data.get("turn_id") or "")
    return (turn + "-" if turn else "pr-") + "%d-%d" % (os.getpid(), time.time_ns())


def tool_signature(data):
    """Tool name + its main argument (the command, path, url... as build_request shows it), hashed: links a
    PermissionRequest (no tool_use_id) to the PostToolUse / PostToolUseFailure of the same call. Not the whole input:
    Claude Code adds or normalises other fields (timeout, description...) between the two events."""
    inp = data.get("tool_input")
    key = ""
    if data.get("tool_name") == hook_config.QUESTION_TOOL and isinstance(inp, dict):
        # The question texts only: once answered, Claude Code's tool_input also carries the answers
        inp = {"questions": [q.get("question") for q in inp.get("questions") or [] if isinstance(q, dict)]}
    if isinstance(inp, dict):
        key = next((str(inp[k]) for k in ("command", "file_path", "path", "url", "pattern", "query", "prompt")
                    if inp.get(k)), "")
        if not key:
            try:
                key = json.dumps(inp, sort_keys=True, default=str)
            except Exception:
                key = repr(inp)
    elif inp:
        key = str(inp)
    raw = "%s\n%s" % (data.get("tool_name") or "", key.strip())
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def pending_requests(record):
    """The permission prompts still waiting, oldest first. Claude Code can ask several at once (parallel tool calls,
    subagents); it shows them one after the other, so the oldest is the one on screen. Older records kept only one."""
    reqs = record.get("requests")
    if isinstance(reqs, list):
        return [r for r in reqs if isinstance(r, dict) and r]
    req = record.get("request")
    return [req] if isinstance(req, dict) and req else []


def stop_failure_kind(data):
    """The StopFailure error type. Read defensively: the field's name isn't pinned down in every version."""
    for key in ("error_type", "error", "reason", "stop_reason"):
        v = data.get(key)
        if isinstance(v, dict):
            v = v.get("type") or v.get("error_type")
        if isinstance(v, str) and v:
            return v.strip().lower()
    return "unknown"


def stop_failure_message(data, kind):
    detail = ""
    for key in ("error_message", "error_details", "message", "details"):
        v = data.get(key)
        if isinstance(v, dict):
            v = v.get("message")
        if isinstance(v, str) and v.strip():
            detail = " ".join(v.split())[:140]
            break
    head = hook_config.STOP_FAILURES.get(kind, "Stopped on an API error")
    return head + (": " + detail if detail and detail.lower() not in head.lower() else "")


def permission_message(req):
    if req.get("kind") == "question":
        return f"{'Codex' if hook_config.AGENT == 'codex' else 'Claude'} has a question for you"
    return f"{'Codex' if hook_config.AGENT == 'codex' else 'Claude'} needs your permission to use {req.get('tool') or 'a tool'}"


def answer_texts(answers):
    """{question: text} as Claude Code's AskUserQuestion takes them: a multi-select question's labels (the pet sends a
    list) become one comma-separated text, the way Claude Code's own dialog answers it."""
    return {q: ", ".join(str(v) for v in a) if isinstance(a, (list, tuple)) else str(a) for q, a in answers.items()}


def decision_output(behavior, updated_input=None, updated_permissions=None):
    decision = {"behavior": behavior}
    if behavior == "deny":
        decision["message"] = "Denied from AIPet"
    else:
        if updated_input is not None:  # e.g. AskUserQuestion: the questions plus the answers picked on the pet
            decision["updatedInput"] = updated_input
        if updated_permissions:  # one of Claude Code's own "always allow" / "allow for this session" choices
            decision["updatedPermissions"] = updated_permissions
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}})


def request_subjects(data):
    """What the patterns are matched against, never truncated: the command (or file path, URL, pattern...), the tool
    name, and Tool(command) - so 'git status', 'Read' and 'Bash\\(npm test\\)' all work as patterns."""
    tool = str(data.get("tool_name") or "")
    inp = data.get("tool_input")
    if not isinstance(inp, dict):
        inp = {"input": inp} if inp else {}
    detail = ""
    for k in ("command", "file_path", "path", "url", "pattern", "query", "prompt"):
        if inp.get(k):
            detail = str(inp[k])
            break
    if not detail and inp:
        detail = json.dumps(inp, ensure_ascii=False, sort_keys=True)
    return [x for x in (detail, tool, f"{tool}({detail})") if x]
