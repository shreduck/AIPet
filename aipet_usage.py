"""Account quota readings from harness payloads; never infer quotas from tokens."""
import json
import math
import os
import shutil
import subprocess
import sys
import time


class HTTPStatusError(Exception):
    """The server answered with an HTTP error status (code)."""

    def __init__(self, code, headers=None):
        super().__init__(f"HTTP {code}")
        self.code, self.headers = code, headers


def https_get_json(url, headers, timeout=10):
    """GET an HTTPS URL and parse its JSON. Python's own TLS when this runtime has it (running from source, the WSL /
    macOS script hooks); the AIPet app builds leave OpenSSL out to stay small and use the system's curl instead
    (Windows 10/11 and macOS ship it), which also trusts the OS certificate store, proxies' corporate CAs included.
    Raises HTTPStatusError for an HTTP error status, OSError / ValueError for anything else."""
    try:
        import ssl
    except ImportError:
        ssl = None
    if ssl is None:
        return _curl_get_json(url, headers, timeout)
    import urllib.error
    import urllib.request
    context = ssl.create_default_context()
    try:  # a frozen Python may lack a system CA path: add the bundle when it's there
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout,
                                    context=context) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise HTTPStatusError(e.code, e.headers)


def find_curl():
    if os.name == "nt":
        system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
        if os.path.isfile(system):
            return system  # Windows' own, never one earlier on the PATH
    return shutil.which("curl") or ("/usr/bin/curl" if os.path.isfile("/usr/bin/curl") else None)


def _curl_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _curl_get_json(url, headers, timeout):
    curl = find_curl()
    if not curl:
        raise OSError("curl not found")
    # URL and headers go in through stdin (-K -), never on the command line, where other programs could read a token
    config = "url = " + _curl_quote(url) + "\n" + "".join(
        "header = " + _curl_quote(f"{k}: {v}") + "\n" for k, v in headers.items())
    cmd = [curl, "--silent", "--show-error", "--location", "--proto", "=https", "--max-time", str(int(timeout)),
           "--write-out", "\n%{http_code}", "--config", "-"]
    if os.name == "nt":
        cmd.insert(1, "--ssl-revoke-best-effort")  # corporate proxies often block certificate revocation lookups
    done = subprocess.run(cmd, input=config.encode("utf-8"), capture_output=True, timeout=timeout + 5,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    body, _, status = done.stdout.decode("utf-8", "replace").rpartition("\n")
    if done.returncode != 0 or not status.strip().isdigit():
        raise OSError(f"curl failed ({done.returncode}): {done.stderr.decode('utf-8', 'replace').strip()[:200]}")
    code = int(status)
    if code >= 400:
        raise HTTPStatusError(code)
    return json.loads(body)


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


def save(target, sid, limits, updated=None, provider=None):
    rows = normalize(limits)
    if not rows:
        return
    if provider:
        rows = [dict(row, provider=provider) for row in rows]
    folder = os.path.join(os.path.dirname(target), "usage")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, sid + ".json")
    tmp = path + f".{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"updated": time.time() if updated is None else updated, "rows": rows}, f)
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


def badge_details(items):
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
    rows = {}
    for item in latest.values():
        ai = item.get("agent", "claude")
        for row in item["usage"]:
            key = (ai, row["window"], row["percent"])
            detail = rows.setdefault(key, {"agent": ai, "window": row["window"], "percent": row["percent"], "readings": []})
            detail["readings"].append({**row, "where": item.get("where") or ai.title(),
                                       "updated": row.get("updated", item.get("updated", 0))})
    return list(rows.values())


def badge_rows(items):
    return [(r["agent"], r["window"], r["percent"]) for r in badge_details(items)]


def tooltip(detail):
    ai = "Codex" if detail["agent"] == "codex" else "Claude"
    window = detail["window"]
    if window.endswith(("h", "d")):
        duration = window[:-1]
        unit = "hour" if window.endswith("h") else "day"
        window = f"{duration}-{unit} account usage limit"
    elif window == "$":
        window = "Account spend limit"
    lines = [ai + " · " + window, f"{detail['percent']}% used · {100 - detail['percent']}% remaining"]
    readings = detail["readings"]
    for row in readings:
        reset = row.get("resets_at")
        reset_text = time.strftime("%d %b %Y, %H:%M %Z", time.localtime(reset)) if reset else "not reported"
        lines.append(f"{row.get('provider') or row['where']}\nResets: {reset_text}")
    updated = max((r.get("updated", 0) for r in readings), default=0)
    if updated:
        lines.append("Last reported: " + time.strftime("%d %b, %H:%M:%S %Z", time.localtime(updated)))
    return "\n".join(lines)


def _usage_files(session_path):
    """The usage files belonging to a session file: <pet dir>/usage/<id>.json and <id>.rollout.json."""
    folder = os.path.join(os.path.dirname(os.path.dirname(session_path)), "usage")
    sid = os.path.splitext(os.path.basename(session_path))[0]
    return [os.path.join(folder, sid + ".json"), os.path.join(folder, sid + ".rollout.json")]


def remove_for(session_path):
    """Delete a session's usage readings together with its session file (stale, dismissed, cleared)."""
    for path in _usage_files(session_path):
        try:
            os.remove(path)
        except OSError:
            pass


def sweep(home, max_age=24 * 3600, now=None):
    """Delete usage files whose session file is gone and that are older than max_age (the pet ignores readings that
    old anyway), plus temp files left by an interrupted write. Keeps the Claude login caches (_claude-*.json), which
    rate-limit the opt-in collector. Returns how many files were removed."""
    folder = os.path.join(home, "usage")
    sessions = os.path.join(home, "sessions")
    now = time.time() if now is None else now
    removed = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(folder, name)
        if name.startswith("_claude-") and name.endswith(".json"):
            continue
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if name.endswith(".tmp"):
            stale = age > 3600
        elif name.endswith(".json"):
            sid = name[:-len(".rollout.json")] if name.endswith(".rollout.json") else name[:-len(".json")]
            stale = age > max_age and not os.path.exists(os.path.join(sessions, sid + ".json"))
        else:
            continue
        if stale:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed
