"""Incremental transcript history: read each file once, then only its appended complete records.

HistoryStore owns the per-file cache and its lock; STORE is the one the app uses (the background tracker and the
windows' fallback reads share it). A replaced or truncated file starts over; a partial last line waits for the next
read; a bootstrap read fills history an earlier tail read skipped.
"""
import itertools
import json
import os
import threading

from .claude import parse_claude
from .log_filters import _primary_results
from .records import MAX_LOG, _empty
from .tools import _merge_tool_calls
MAIN_TAIL = 262144  # bytes read from the end of the main transcript
SUB_TAIL = 131072  # ... and of each subagent's



def _read_updates(path, size, offset=None):
    """Read complete JSONL records since the last read, keeping partial lines for the next poll."""
    cursor = {"offset": offset or 0, "cut": False}

    def records():
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            start = (max(0, end - size) if size is not None else 0) if offset is None else offset
            f.seek(start)
            cursor["cut"] = offset is None and start > 0
            if offset is None and start:
                f.readline()  # skip the first cut line of an initial tail read
            cursor["offset"] = f.tell()
            while True:
                raw = f.readline()
                if not raw or not raw.endswith(b"\n"):
                    break
                cursor["offset"] = f.tell()
                try:
                    entry = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    yield entry

    return records(), cursor


def _merge_info(previous, current):
    before = (previous["lines"] if previous else []) + current["lines"]
    results = _primary_results({"lines": before, "final_lines": current["final_lines"],
                                "state": "done" if current["done"] is True else "working"})
    current["final_lines"] = list(dict.fromkeys((previous["final_lines"] if previous else []) +
                                                sorted(results, key=lambda row: row[0])))[-MAX_LOG:]
    current["queries"] = list(dict.fromkeys((previous["queries"] if previous else []) + current["queries"]))[-MAX_LOG:]
    if not previous:
        current["lines"] = current["lines"][-MAX_LOG:]
        current["usage_at"] = _usage_on_lines(current["usage_at"], current["lines"])
        return current
    _merge_tokens(previous, current)
    for key in ("model", "model_hint", "effort", "ctx", "ctx_max", "last_prompt_t", "closed_t"):
        current[key] = current.get(key) or previous.get(key) or current[key]
    current["first_t"] = previous["first_t"] or current["first_t"]
    if current["done"] is None:
        current["done"] = previous["done"]
    current["lines"] = sorted(dict.fromkeys(previous["lines"] + current["lines"]), key=lambda row: row[0])[-MAX_LOG:]
    current["usage_at"] = _usage_on_lines({**previous.get("usage_at", {}), **current["usage_at"]}, current["lines"])
    return current


def _merge_tokens(previous, current):
    """Token sums across reads: a request split over two reads counts once (the later copy of its usage)."""
    sums = [a + b for a, b in zip(previous.get("tokens") or [0, 0, 0], current["tokens"])]
    opened = previous.get("tok_open")
    if opened:
        first = current["tok_first"]
        if current["tok_open"] is None:
            current["tok_open"] = opened  # nothing new was answered in this read
        elif first and first[0] == opened[0]:  # the same request goes on: its usage stays on its first line
            usage = current["usage_at"].pop(first[1], None)
            if usage:
                current["usage_at"][opened[2]] = usage
            if current["tok_open"][0] == opened[0]:
                current["tok_open"] = current["tok_open"][:2] + (opened[2],)
        else:
            sums = [a + b for a, b in zip(sums, opened[1])]
    current["tokens"] = sums
    current["tok_first"] = previous.get("tok_first") or current["tok_first"]
    current["tok_abs"] = current["tok_abs"] or previous.get("tok_abs")


def _usage_on_lines(usage_at, lines):
    """Only the usage of lines still kept (the log is bounded)."""
    kept = {row[0] for row in lines}
    return {t: u for t, u in usage_at.items() if t in kept}


class HistoryStore:
    """The transcripts read so far: normalized path -> ((mtime_ns, size), parsed, complete-line offset, file
    identity), at most `capacity` files (the oldest goes first). Callers that read several files as one snapshot
    (a session's network) hold `lock` around them; it is re-entrant."""

    def __init__(self, capacity=128):
        self.lock = threading.RLock()
        self.capacity = capacity
        self._tails = {}

    def clear(self):
        with self.lock:
            self._tails.clear()

    def read(self, path, size, parse, bootstrap=False):
        """The parsed transcript at path, reading only what was appended since last time (the last `size` bytes on a
        first read, everything with bootstrap). None if it can't be read."""
        with self.lock:
            return self._read(path, size, parse, bootstrap)

    def _read(self, path, size, parse, bootstrap):
        try:
            st = os.stat(path)
        except OSError:
            return None
        stamp = (st.st_mtime_ns, st.st_size)
        key = str(path)
        if os.name == "nt":
            if key.startswith("\\\\?\\UNC\\"):
                key = "\\\\" + key[8:]
            elif key.startswith("\\\\?\\"):
                key = key[4:]
            key = os.path.normcase(os.path.abspath(key))
        identity = (st.st_dev, st.st_ino)
        hit = self._tails.pop(key, None)
        if hit and (hit[3] != identity or st.st_size < hit[0][1]):
            hit = None  # the transcript was replaced or truncated; do not mix sessions
        if bootstrap and hit and hit[1].get("cut"):
            hit = None  # fill any history missed by an earlier foreground tail read
        if hit and hit[0] == stamp:
            self._tails[key] = hit
            return hit[1]
        try:
            entries, cursor = _read_updates(path, None if bootstrap else size, hit[2] if hit else None)
            parsed = hit[1] if hit else None
            while True:
                batch = list(itertools.islice(entries, 128))
                if not batch:
                    break
                update = parse(batch)
                update["tool_calls"] = _merge_tool_calls(parsed["tool_calls"] if parsed else [], update["tool_calls"])
                parsed = _merge_info(parsed, update)
            parsed = parsed or _empty()
        except OSError:
            if hit:
                self._tails[key] = hit
            return None
        closed = parsed.get("closed_t") or (parsed.get("last_prompt_t") if parse is parse_claude else 0) or 0
        for call in parsed["tool_calls"]:
            if not call.get("finished_t") and call.get("started_t", closed) < closed:
                call["finished_t"] = closed  # a later turn cannot reactivate a tool with no recorded result
        parsed["mtime"] = st.st_mtime
        parsed["cut"] = cursor["cut"] or bool(hit and hit[1].get("cut"))
        if len(self._tails) >= self.capacity:
            self._tails.pop(next(iter(self._tails)))  # evict one old file rather than discard every active history
        self._tails[key] = (stamp, parsed, cursor["offset"], identity)
        return parsed


STORE = HistoryStore()  # the app's one history store
