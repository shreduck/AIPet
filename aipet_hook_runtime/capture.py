"""Optional quota ingestion; failures cannot block session updates."""
import json
import os
from . import codex as hook_codex
from . import io as hook_io

try:
    import aipet_usage as usage
except Exception:  # Optional display data cannot disable session hooks.
    usage = None
try:
    import aipet_claude_usage as claude_usage
except Exception:
    claude_usage = None

def capture_codex_usage(target, sid, data, previous):
    if usage is None:
        return
    limits = data.get("rate_limits")
    if isinstance(limits, dict):
        usage.save(target, sid, limits)
        return
    transcript = data.get("transcript_path") or previous.get("transcript_path")
    transcript = hook_codex.root_rollout(sid, transcript) or transcript
    if transcript:
        data["transcript_path"] = transcript
    if not transcript:
        return
    try:
        stat = os.stat(transcript)
        fingerprint = [transcript, stat.st_mtime_ns, stat.st_size]
        cache = os.path.join(os.path.dirname(target), "usage", sid + ".rollout.json")
        if hook_io.read_json(cache).get("fingerprint") == fingerprint:
            return
        # Read only the tail: rollouts can contain many megabytes of tool output.
        with open(transcript, "rb") as f:
            f.seek(0, os.SEEK_END)
            start = max(0, f.tell() - 1024 * 1024)
            f.seek(start)
            if start:
                f.readline()
            lines = f.read().splitlines()
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            payload = record.get("payload") or {}
            if payload.get("type") == "token_count" and isinstance(payload.get("rate_limits"), dict):
                usage.save(target, sid, payload["rate_limits"])
                break
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        hook_io.write_atomic(cache, {"fingerprint": fingerprint})
    except OSError:
        pass
