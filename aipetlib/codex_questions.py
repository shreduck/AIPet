"""Read-only projection of outstanding Codex rollout questions, independent of Tk and hooks."""
import json
import os
from datetime import datetime

_QUESTIONS = {}


def question_list(inp):
    """Normalize both Codex question tools into the pet's display format."""
    if not isinstance(inp, dict):
        return []
    out = []
    questions = inp.get("questions")
    for q in (questions[:6] if isinstance(questions, list) else []):
        if not isinstance(q, dict):
            continue
        text = str(q.get("title") or q.get("question") or "").strip()
        if not text:
            continue
        options = []
        opts = q.get("options")
        for opt in (opts[:8] if isinstance(opts, list) else []):
            label = opt.get("label") if isinstance(opt, dict) else opt
            if label:
                options.append({"label": str(label)[:120], "description":
                                str(opt.get("description") or "")[:300] if isinstance(opt, dict) else ""})
        out.append({"question": text[:600], "header": str(q.get("header") or "")[:40],
                    "multiSelect": False, "options": options})
    return out


class _QuestionReader:
    """Incremental read-only question projection; hooks need not fire before the tool returns."""
    def __init__(self):
        self.stamp = self.identity = None
        self.offset = 0
        self.anchor = b""
        self.pending, self.seen = {}, {}

    def read(self, path):
        try:
            st = os.stat(path)
            stamp, identity = (st.st_mtime_ns, st.st_size), (st.st_dev, st.st_ino)
            if stamp == self.stamp and identity == self.identity:
                return self.result()
            reset = identity != self.identity or st.st_size < self.offset or (
                self.stamp is not None and st.st_size == self.stamp[1] and stamp != self.stamp)
            with open(path, "rb") as stream:
                if not reset and self.anchor:
                    stream.seek(self.offset - len(self.anchor))
                    reset = stream.read(len(self.anchor)) != self.anchor
                if reset:
                    self.pending, self.seen = {}, {}
                    # Bound initial work on long rollouts. Subsequent polls retain
                    # pending calls even when unrelated tool output grows large.
                    self.offset = max(0, st.st_size - 2 * 1024 * 1024)
                    stream.seek(self.offset)
                    if self.offset:
                        stream.readline()
                        self.offset = stream.tell()
                else:
                    stream.seek(self.offset)
                while True:
                    line = stream.readline()
                    if not line or not line.endswith(b"\n"):
                        break  # reread a partially written record next time
                    self.offset = stream.tell()
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    self.consume(entry)
                stream.seek(max(0, self.offset - 128))
                self.anchor = stream.read(min(self.offset, 128))
            self.stamp, self.identity = stamp, identity
            return self.result()
        except OSError:
            return None  # unavailable is not evidence that a hook prompt was answered

    def result(self):
        return {"pending": list(self.pending.values()), "seen": set(self.seen)}

    def consume(self, entry):
        if not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict):
            return
        p, kind = entry["payload"], entry.get("type")
        sub = p.get("type")
        if kind == "event_msg" and sub == "turn_aborted":
            self.pending.clear()
        elif kind == "event_msg" and sub in ("task_started", "task_complete"):
            # Async questions remain answerable after the assistant's turn ends.
            # Only a reply, a superseding user message or an abort settles them.
            self.pending = {call: req for call, req in self.pending.items()
                            if req["tool"] == "request_user_input_async"}
        elif kind == "response_item" and sub == "message" and p.get("role") == "user":
            content = p.get("content")
            texts = [c["text"] for c in (content if isinstance(content, list) else [])
                     if isinstance(c, dict) and c.get("type") == "input_text" and isinstance(c.get("text"), str)]
            for text in texts:
                if text.lstrip().startswith("<send_user_message_question_reply>"):
                    try:
                        replies = json.loads(text.split(">", 1)[1].split("</send_user_message_question_reply>", 1)[0])
                        for reply in replies:
                            ident = json.loads(reply.get("questionItemId", ""))
                            if isinstance(ident, list) and len(ident) > 1:
                                self.pending.pop(str(ident[1]), None)
                    except (ValueError, TypeError, AttributeError):
                        pass
                elif text.strip() and not text.lstrip().startswith("<"):
                    self.pending.clear()  # a new user message supersedes the old question
        elif kind == "response_item" and sub == "function_call":
            tool = str(p.get("name") or "").split(".")[-1]
            call = str(p.get("call_id") or "")
            if tool not in ("request_user_input", "request_user_input_async") or not call:
                return
            try:
                inp = p.get("arguments")
                inp = json.loads(inp) if isinstance(inp, str) else inp
            except ValueError:
                return
            questions = question_list(inp)
            if not questions:
                return
            try:
                started = datetime.fromisoformat(str(entry.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                started = 0.0
            self.seen[call] = True
            self.seen = dict(list(self.seen.items())[-256:])
            self.pending[call] = {"id": call, "tool_use_id": call, "tool": tool, "kind": "question",
                                  "source": "codex-question", "t": started, "shown": True,
                                  "agent": "", "questions": questions, "description": "", "detail": ""}
        elif kind == "response_item" and sub == "function_call_output":
            call = str(p.get("call_id") or "")
            req = self.pending.get(call)
            if not req:
                return
            output = p.get("output")
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except ValueError:
                    output = None
            # Async acceptance is not an answer. A blocking call, on the other
            # hand, has finished once its output is recorded (answer/cancel/error).
            if req["tool"] == "request_user_input" or not (
                    isinstance(output, dict) and output.get("accepted") is True and "answers" not in output):
                self.pending.pop(call, None)


def question_snapshot(path):
    if not path:
        return None
    if path not in _QUESTIONS:
        if len(_QUESTIONS) >= 64:
            _QUESTIONS.pop(next(iter(_QUESTIONS)))
        _QUESTIONS[path] = _QuestionReader()
    return _QUESTIONS[path].read(path)


def reconcile_questions(rec, snapshot):
    """Project rollout questions over a hook record without writing its shared file."""
    if snapshot is None:
        return rec
    requests = rec.get("requests") or ([rec["request"]] if rec.get("request") else [])
    requests = [r for r in requests if r.get("source") != "codex-question" or
                str(r.get("tool_use_id") or r.get("id") or "") not in snapshot["seen"]]
    questions = [q for q in snapshot["pending"] if
                 (q.get("tool") == "request_user_input_async" and rec.get("state") in ("done", "idle")) or
                 rec.get("state") not in ("done", "idle", "error") or
                 q["t"] > float(rec.get("updated") or 0)]
    ids = {r.get("id") for r in requests}
    requests += [q for q in questions if q["id"] not in ids]
    result = dict(rec, requests=requests, request=requests[0] if requests else {})
    if requests and (questions or rec.get("state") in ("working", "needs_input")):
        first = requests[0]
        message = "Codex has a question for you" if first.get("kind") == "question" else rec.get("message", "")
        result.update(state="needs_input", message=message, wait_agent=first.get("agent", ""))
        if rec.get("state") != "needs_input":
            result["changed"] = first.get("t") or rec.get("changed", 0)
    elif rec.get("state") == "needs_input" and any(r.get("source") == "codex-question" for r in (
            rec.get("requests") or ([rec["request"]] if rec.get("request") else []))):
        result.update(state="working", message="", wait_agent="")
    return result

