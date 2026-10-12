"""Session discovery, transcript interpretation and health checks.

Services are supplied by the compatibility entry point; this module does not import aipet.
"""
import glob
import json
import os
import sys
import time
import aipet_usage as usage


def place_name(services, rec):
    """The name tag's top line: the session's folder. A session with no folder to name it after (Cowork without a
    connected folder, a session in the home folder) used to be titled by its first prompt; the conversation title now
    sits right below the name, so show where it runs instead - its badge tags: "Cowork", "WSL", "WSL VS Code", "CLI"."""
    title = rec.get("title") or "session"
    if not (rec.get("topic") and title == rec.get("topic")) and title not in ("~", "Cowork"):
        return title
    badge = services.session_badge("Codex" if rec.get("agent") == "codex" else "Claude", rec.get("app") == "cowork",
                          rec.get("env") == "wsl", rec.get("ide") == "vscode", rec.get("entry", ""))
    tags = [{"VS": "VS Code"}.get(t, t) for t in badge.split(" ")[1:]]
    return " ".join(tags) or title


def transcript_file(services, rec):
    """The session's transcript as this machine can open it (WSL paths through \\\\wsl.localhost\\<distro>)."""
    path = str(rec.get("transcript_path") or "")
    if rec.get("env") == "wsl" and os.name == "nt" and path.startswith("/") and rec.get("distro"):
        path = "\\\\wsl.localhost\\" + rec["distro"] + path.replace("/", "\\")
    if rec.get("agent") == "codex" and (rec.get("env") != "wsl" or os.name == "nt"):
        from aipet_codex import root_rollout
        path = root_rollout(rec.get("session_id") or rec.get("id"), path)
    return path


def visible_hook_session(services, rec):
    """Use the same conversation identity for desktop pets and agent graph groups."""
    if rec.get("agent") != "codex":
        return True
    from aipet_codex import session_meta, internal_thread
    path = services.transcript_file(rec)
    meta = session_meta(path or str(rec.get("transcript_path") or ""))
    if internal_thread(meta) or meta.get("parent_thread_id"):
        return False
    # Ephemeral post-turn jobs have no conversation rollout. Keep real alerts
    # visible even if their transcript is not available, and preserve remote WSL sessions.
    return bool(path or rec.get("env") == "wsl" or rec.get("state") in ("needs_input", "error"))


def turn_interrupted(services, rec):
    """True when the newest message in the session's transcript is Claude Code's "[Request interrupted by user]":
    the turn was stopped and nothing has happened since."""
    if time.time() - rec.get("updated", 0) < 1.5:
        return False
    return services.transcript_tail(rec)["interrupted"]


def last_said(services, rec):
    """("ai" | "you", text): the newest thing said in the session, the agent's latest reply phrase or your latest
    prompt, whichever came last; None when the transcript can't be read."""
    return services.transcript_tail(rec)["said"]


def _phrase(services, text, limit=140):
    """The last sentence-ish bit of a message, on one line."""
    lines = [ln.strip(" #>*-`") for ln in str(text).strip().splitlines() if ln.strip(" #>*-`")]
    phrase = " ".join((lines[-1] if lines else "").split())
    return phrase if len(phrase) <= limit else phrase[:limit - 1].rstrip() + "…"


def _transcript_message(services, entry):
    """(role, [texts], has_tool_result) for a chat message line of a Claude Code transcript or a Codex rollout,
    else None (bookkeeping lines: attachments, modes, summaries, tool calls...)."""
    if not isinstance(entry, dict):
        return None
    if entry.get("type") in ("user", "assistant") and isinstance(entry.get("message"), dict):
        content = entry["message"].get("content")
        parts = content if isinstance(content, list) else []
        texts = [content] if isinstance(content, str) else [
            c.get("text", "") for c in parts if isinstance(c, dict) and c.get("type") == "text"]
        return entry["type"], texts, any(isinstance(c, dict) and c.get("type") == "tool_result" for c in parts)
    payload = entry.get("payload")
    if entry.get("type") == "response_item" and isinstance(payload, dict) and payload.get("type") == "message":
        texts = [c.get("text", "") for c in payload.get("content") or []
                 if isinstance(c, dict) and c.get("type") in ("input_text", "output_text")]
        return payload.get("role"), texts, False
    return None


def transcript_tail(services, rec, max_bytes=(65536, 1048576)):
    """What the end of the session's transcript says: {"interrupted": the newest message is Claude Code's stop
    marker, "said": the newest reply phrase or typed prompt}. Only the tail is read, and only when the file changed."""
    empty = {"interrupted": False, "said": None}
    path = services.transcript_file(rec)
    if not path:
        return empty
    try:
        st = os.stat(path)
    except OSError:
        return empty
    stamp = (st.st_mtime_ns, st.st_size)
    cached = services._TRANSCRIPT_TAILS.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    result = {"interrupted": False, "said": None}
    try:
        for window in max_bytes:  # usually the last 64 KB; long tool output can push the last words further back
            result = {"interrupted": False, "said": None}
            with open(path, "rb") as f:
                f.seek(max(0, st.st_size - window))
                lines = f.read().decode("utf-8", "replace").splitlines()
            services._scan_tail(lines, result)
            if result["said"] or window >= st.st_size:
                break
    except OSError:
        return empty
    if len(services._TRANSCRIPT_TAILS) > 64:
        services._TRANSCRIPT_TAILS.clear()
    services._TRANSCRIPT_TAILS[path] = (stamp, result)
    return result


def _scan_tail(services, lines, result):
    """Fill result from transcript lines, newest first: is the newest message the stop marker, and what was
    said last (the newest reply text or typed prompt)."""
    newest = True
    for line in reversed(lines):
        try:
            found = services._transcript_message(json.loads(line))
        except ValueError:
            continue
        if not found:
            continue
        role, texts, tool_result = found
        if newest:
            result["interrupted"] = role == "user" and any(
                str(t).strip().startswith(services.INTERRUPT_MARKERS) for t in texts)
            newest = False
        # skip tool output, injected context (<...>), stop markers and image placeholders ([Image: ...])
        text = next((t for t in reversed(texts) if str(t).strip()
                     and not str(t).lstrip().startswith(("<", "[Request interrupted", "[Image"))), "")
        if tool_result or not text:
            continue  # nothing said in this one: keep looking
        result["said"] = ("ai" if role == "assistant" else "you", services._phrase(text))
        break


def pid_alive(services, pid, max_age=5.0):
    """Is the process running (cached for a few seconds)? None when it can't be told. Only for processes on this
    machine (not WSL ones)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    hit = services._PID_CACHE.get(pid)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    alive = None
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                alive = ctypes.get_last_error() == 5  # access denied: it exists; otherwise it is gone
            else:
                code = wintypes.DWORD()
                ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
                k32.CloseHandle(h)
                alive = bool(ok) and code.value == 259  # STILL_ACTIVE
        else:
            os.kill(pid, 0)
            alive = True
    except PermissionError:
        alive = True
    except ProcessLookupError:
        alive = False
    except Exception:
        alive = None
    services._PID_CACHE[pid] = (time.time(), alive)
    if len(services._PID_CACHE) > 200:
        services._PID_CACHE.clear()
    return alive


def session_title(services, rec, mode="name"):
    """The extra title of a session: "name" = the harness's session name (Claude Code's /rename or its own name for
    the session, Codex's thread name), else the latest prompt; "prompt" = the latest prompt, else the name."""
    prompt = next((p for p in (rec.get("last_prompt"), rec.get("first_prompt"))  # first_prompt: older hooks
                   if p and not str(p).startswith("<")), "")  # "<...>": injected by the harness, not typed
    name = rec.get("conv") or ""
    return (prompt or name) if mode == "prompt" else (name or prompt)


def disambiguate_titles(services, items):
    """Sessions named after the same folder ("esign-online", "esign-online") get their conversation's title added
    ("esign-online · Fix login bug"), or a short session id when there is none yet."""
    counts = {}
    for i in items:
        k = (i.get("title") or "").casefold()
        counts[k] = counts.get(k, 0) + 1
    for i in items:
        if counts.get((i.get("title") or "").casefold(), 0) > 1:
            extra = i.get("conv") or ("#" + str(i.get("session_id") or i.get("key") or "")[-4:])
            i["raw_title"] = i.get("title") or ""
            i["title"] = f"{i.get('title') or 'session'} \u00b7 {extra}"
            i["dup_title"] = True


def _active_agents(services, rec, now=None):
    """How many subagents are running: started (or seen) and not yet stopped, within 15 min (while the session is busy)."""
    if rec.get("state") not in ("working", "needs_input"):
        return 0
    now = now or time.time()
    return sum(1 for ts in (rec.get("agents") or {}).values() if now - ts < 900)


def read_claude_code_sessions(services, cfg):
    items = []
    if not cfg["claude_code"]["enabled"]:
        return items
    cutoff = time.time() - cfg["stale_hours"] * 3600
    dirs = [services.SESSIONS_DIR] + ([services.LEGACY_SESSIONS_DIR] if os.path.isdir(services.LEGACY_SESSIONS_DIR) else [])  # LEGACY
    dirs += list(cfg["claude_code"].get("extra_session_dirs") or [])
    paths = []
    for d in dirs:
        try:
            paths += glob.glob(os.path.join(d, "*.json"))
        except OSError:
            continue  # e.g. WSL distro not running
    newest = {}  # session id -> (path, record): the same session can have a file in two folders (old and new hooks)
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            continue  # mid-write or corrupt; next poll will catch it
        if rec.get("updated", 0) < cutoff and not (services.DONE_TIMEOUT["v"] == 0 and rec.get("state") in ("done", "idle")):
            try:
                os.remove(path)
                usage.remove_for(path)
            except OSError:
                pass
            continue
        sid = str(rec.get("id"))
        old = newest.get(sid)
        if old and old[1].get("updated", 0) >= rec.get("updated", 0):
            stale_path = path
        else:
            stale_path = old[0] if old else None
            newest[sid] = (path, rec)
        if stale_path:  # an older copy of a session that is written elsewhere now: drop it
            try:
                os.remove(stale_path)
            except OSError:
                pass
    for path, rec in sorted(newest.values(), key=lambda pr: pr[0]):
        if not services.visible_hook_session(rec):
            continue
        sid = str(rec.get("id"))
        local = rec.get("env") == ("windows" if os.name == "nt" else sys.platform)
        quiet = time.time() - rec.get("updated", 0) > max(60, services.HEALTH["v"])  # an active session isn't judged by its pid
        if rec.get("app") == "cowork" and time.time() - rec.get("updated", 0) > services.COWORK_QUIET_SECONDS:
            quiet = True  # Cowork runs one Claude Code process per turn: stopping the turn ends it, so its pid tells
        if (local and services.HEALTH["v"] > 0 and quiet and rec.get("state") in ("working", "needs_input") and rec.get("pid")
                and services.pid_alive(rec["pid"], services.HEALTH["v"]) is False):
            # its Claude Code / Codex process is gone without a Stop or SessionEnd (the app was closed, or Cowork moved
            # the conversation to a new session): show it as done, so the done timeout clears it
            rec = dict(rec, state="done", message="", request={}, changed=rec.get("updated", 0))
        if rec.get("state") == "working" and services.turn_interrupted(rec):
            # You stopped the turn (Esc / the stop button). Claude Code sends no hook event for that, only a line in
            # its transcript, so the session would look busy until your next message.
            rec = dict(rec, state="idle", message="Stopped", changed=rec.get("updated", 0))
        if services.unconfirmed_prompts(rec):
            # Claude Code announces every prompt it really shows (a Notification ~6 s in). These never were: settled
            # without the user (auto mode, a rule...), so the session is working, not waiting for you.
            rec = dict(rec, state="working", message="", request={}, requests=[])
        env, ide = rec.get("env", ""), rec.get("ide", "")
        cowork = rec.get("app") == "cowork"
        codex = rec.get("agent") == "codex"
        badges = [services.session_badge("Codex" if codex else "Claude", cowork, env == "wsl", ide == "vscode",
                                rec.get("entry", ""))]
        where = ["Claude app · Cowork" if cowork else ("Codex" if codex else "Claude Code")]
        if env == "wsl":
            where.append(f"WSL ({rec['distro']})" if rec.get("distro") else "WSL")
        if ide == "vscode":
            where.append("VS Code")
        items.append({
            "key": "cc:" + sid,
            "source": "CC",
            "badges": badges or ["CC"],
            "where": " · ".join(where),
            "env": env,
            "distro": rec.get("distro", ""),
            "ide": ide,
            "session_id": sid,
            "agent": rec.get("agent", "claude"),
            "entry": rec.get("entry", ""),
            "cwd": rec.get("cwd", ""),
            "title": services.place_name(rec),
            "state": rec.get("state", "idle"),
            "message": rec.get("message", ""),
            "detail": rec.get("cwd", ""),
            "changed": rec.get("changed", rec.get("updated", 0)),
            "path": path,
            "legacy": os.path.dirname(path) == services.LEGACY_SESSIONS_DIR,  # LEGACY: written by a hook on the old name
            "app": rec.get("app", ""),
            "hwnd": rec.get("hwnd"),
            "subagents": services._active_agents(rec),
            "transcript_path": rec.get("transcript_path", ""),
            "agent_ids": {a: (rec.get("agent_types") or {}).get(a) or "" for a, ts in (rec.get("agents") or {}).items()
                          if time.time() - ts < 900} if rec.get("state") in ("working", "needs_input") else {},
            "wait_agent": rec.get("wait_agent", ""),
            "agent_types": [((rec.get("agent_types") or {}).get(a) or "subagent")
                            for a, ts in (rec.get("agents") or {}).items() if time.time() - ts < 900]
            if rec.get("state") in ("working", "needs_input") else [],
            "request": rec.get("request") or {},
            "pending": len(rec.get("requests") or []) or (1 if rec.get("request") else 0),  # prompts queued
            "pid": rec.get("pid"),
            "sid": str(rec.get("id")),
            "auto_approved": rec.get("auto_approved", 0),
            "auto_t": rec.get("auto_t", 0),
            "auto_kind": rec.get("auto_kind", ""),
            "auto_what": rec.get("auto_what", ""),
            "conv": services.session_title(rec, cfg.get("session_titles", "name")),
            "usage": [r for r in usage.load(path) if cfg.get("claude_oauth_usage", False) or not r.get("provider", "").startswith("Claude Code account")],
            "updated": rec.get("updated", 0),
        })
    return items
