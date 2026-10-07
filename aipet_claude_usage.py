"""Cached Claude quota fallback for harnesses that never run a CLI status line.

The hook starts a detached worker, so permission handling never waits for HTTPS.
Only quota readings are persisted. Credentials remain in Claude's own store.
"""
from datetime import datetime
import hashlib
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

import aipet_usage as usage

URL = "https://api.anthropic.com/api/oauth/usage"
POLL_SECONDS = 300


def enabled(target):
    # Require explicit consent in the shared pet configuration, including in
    # detached workers that may have been queued before the user switched it off.
    return read(os.path.join(os.path.dirname(target), "config.json")).get("claude_oauth_usage") is True


def paths(target):
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    identity = sys.platform + "|" + config + "|" + os.environ.get("WSL_DISTRO_NAME", "")
    name = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    cache = os.path.join(os.path.dirname(target), "usage", "_claude-" + name + ".json")
    return config, cache, cache + ".lock"


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def access_token(config):
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        return token
    candidates = [read(os.path.join(config, ".credentials.json"))]
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                                    capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                candidates.append(json.loads(result.stdout))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    for data in candidates:
        oauth = data.get("claudeAiOauth") or {}
        expiry = oauth.get("expiresAt")
        if expiry and expiry / 1000 <= time.time():
            continue
        if oauth.get("accessToken"):
            return oauth["accessToken"]
    return None


def convert(data):
    limits = {}
    for key in ("five_hour", "seven_day"):
        value = data.get(key)
        if not isinstance(value, dict):
            continue
        reset = value.get("resets_at")
        if isinstance(reset, str):
            try:
                reset = datetime.fromisoformat(reset.replace("Z", "+00:00")).timestamp()
            except ValueError:
                reset = None
        limits[key] = {"used_percentage": value.get("utilization"), "resets_at": reset}
    return limits


def fetch(token):
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except ImportError:  # source hook running with the harness's system Python
        pass
    request = urllib.request.Request(URL, headers={"Authorization": "Bearer " + token,
        "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json", "User-Agent": "AIPet"})
    with urllib.request.urlopen(request, timeout=5, context=context) as response:
        return convert(json.loads(response.read().decode("utf-8")))


def publish(target, sid, cache):
    if cache.get("limits"):
        current = read(os.path.join(os.path.dirname(target), "usage", sid + ".json"))
        if current.get("updated", 0) > cache.get("updated", 0):
            return  # keep a newer reading supplied directly by the status line
        usage.save(target, sid, cache["limits"], updated=cache.get("updated", 0), provider="Claude Code account · local login")


def schedule(target, sid, command):
    if not enabled(target):
        return
    _, cache_path, lock = paths(target)
    cache = read(cache_path)
    publish(target, sid, cache)
    if time.time() < cache.get("retry_at", 0):
        return
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    try:
        os.mkdir(lock)
    except FileExistsError:
        if time.time() - os.path.getmtime(lock) > 30:
            try:
                os.rmdir(lock)
            except OSError:
                pass
        return
    try:
        subprocess.Popen(command + ["--claude-usage-worker=" + sid], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=os.name != "nt")
    except OSError:
        os.rmdir(lock)


def worker(target, sid):
    config, path, lock = paths(target)
    cache = read(path)
    if not enabled(target):
        try:
            os.rmdir(lock)
        except OSError:
            pass
        return
    now = time.time()
    cache["retry_at"] = now + POLL_SECONDS
    try:
        token = access_token(config)
        if not token:
            cache["error"] = "Claude Code login unavailable or expired"
        else:
            limits = fetch(token)
            if usage.normalize(limits):
                cache.update(limits=limits, updated=now, error="")
            else:
                cache["error"] = "Account limits not reported"
    except urllib.error.HTTPError as e:
        cache["error"] = "Claude usage HTTP " + str(e.code)
        if e.code == 429:
            cache["retry_at"] = now + 900
    except Exception:
        cache["error"] = "Claude usage request unavailable"
    finally:
        try:
            temp = path + f".{os.getpid()}.tmp"
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            os.replace(temp, path)
            publish(target, sid, cache)
        finally:
            try:
                os.rmdir(lock)
            except OSError:
                pass
