"""Account quota readings from harness payloads; never infer quotas from tokens."""
import json
import math
import os
import time


def normalize(limits):
    if not isinstance(limits, dict):
        return []
    rows = []
    for key, default in (("five_hour", "5h"), ("seven_day", "7d"),
                         ("primary", "5h"), ("secondary", "7d"), ("spend_limit", "$")):
        value = limits.get(key)
        if not isinstance(value, dict):
            continue
        pct = value.get("used_percentage", value.get("used_percent"))
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct):
            continue
        minutes = value.get("window_minutes")
        label = default
        if isinstance(minutes, (int, float)) and minutes > 0:
            label = f"{minutes / 1440:g}d" if minutes >= 1440 else f"{minutes / 60:g}h"
        reset = value.get("resets_at")
        if not isinstance(reset, (int, float)) or not math.isfinite(reset):
            reset = None
        rows.append({"window": label, "percent": round(max(0, min(100, pct))), "resets_at": reset})
    return rows


def save(target, sid, limits):
    rows = normalize(limits)
    if not rows:
        return
    folder = os.path.join(os.path.dirname(target), "usage")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, sid + ".json")
    tmp = path + f".{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"updated": time.time(), "rows": rows}, f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def load(session_path):
    path = os.path.join(os.path.dirname(os.path.dirname(session_path)), "usage", os.path.basename(session_path))
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        if now - data["updated"] > 24 * 3600:
            return []
        return [dict(r, updated=data["updated"]) for r in data["rows"] if not r.get("resets_at") or r["resets_at"] > now]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def badge_rows(items):
    """Equal readings merge per AI; readings from one harness use its newest snapshot."""
    latest = {}
    for item in items:
        if not item.get("usage"):
            continue
        harness = (item.get("agent", "claude"), item.get("env"), item.get("distro"), item.get("ide"), item.get("entry"))
        def stamp(entry):
            return max(r.get("updated", entry.get("updated", 0)) for r in entry["usage"])
        if harness not in latest or stamp(item) > stamp(latest[harness]):
            latest[harness] = item
    seen, rows = set(), []
    for item in latest.values():
        ai = item.get("agent", "claude")
        for row in item["usage"]:
            key = (ai, row["window"], row["percent"])
            if key not in seen:
                seen.add(key)
                rows.append((ai, row["window"], row["percent"]))
    return rows
