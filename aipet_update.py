"""
AIPet - new version check.

Asks GitHub for the newest release of the repo (`/releases/latest`: published, non-pre-release releases only, so the
rolling "latest" pre-release built from every push to master is ignored) and compares its tag with this build's
version. Nothing is downloaded or installed: the app only tells the user and opens the release page.

This build's version comes from _build_version.py, written at build time by tools/write_version.py
(`git describe`, e.g. "v0.2.4" or "v0.2.4-2-g573f03e"). From source it asks git directly; "dev" if neither works.
"""
import json
import os
import re
import subprocess
import time

import aipet_usage

REPO = "shreduck/AIPet"
RELEASES_URL = f"https://github.com/{REPO}/releases"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_EVERY = 24 * 3600  # automatic checks: once a day


def current_version():
    try:
        from _build_version import VERSION  # written at build time
        return VERSION
    except ImportError:
        pass
    try:  # running from source
        out = subprocess.run(["git", "describe", "--tags", "--match", "v[0-9]*"],
                             cwd=os.path.dirname(os.path.abspath(__file__)), capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "dev"


def parse(version):
    """'v0.2.4' / '0.2.4' / 'v0.2.4-2-g573f03e' -> (0, 2, 4); None when it isn't a version."""
    m = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(version or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def short(version):
    """The release a build is based on: 'v0.2.4-2-g573f03e' -> 'v0.2.4'."""
    p = parse(version)
    return "v%d.%d.%d" % p if p else str(version)


def is_newer(latest, current):
    a, b = parse(latest), parse(current)
    return bool(a and b and a > b)


def latest_release(timeout=10, token=None):
    """{"tag", "url", "name", "published"} of the newest published release. Raises on network / API errors."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "AIPet"}
    if token:  # supplied only by the opt-in CI probe; never persisted or bundled
        headers["Authorization"] = "Bearer " + token
    data = aipet_usage.https_get_json(API_URL, headers, timeout)  # Python's TLS, or the system's curl in app builds
    tag = str(data.get("tag_name") or "")
    if not parse(tag):
        raise ValueError(f"unexpected release tag {tag!r}")
    return {"tag": tag, "url": data.get("html_url") or f"{RELEASES_URL}/tag/{tag}", "name": data.get("name") or tag,
            "published": data.get("published_at") or ""}


class State:
    """~/.aipet/update.json: when the last check ran, what it found, and which version the user skipped / was told
    about (so the popup appears once per new version)."""

    def __init__(self, home):
        self.path = os.path.join(home, "update.json")

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, **changes):
        data = self.load()
        data.update(changes)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass
        return data

    def due(self):
        return time.time() - float(self.load().get("last_check") or 0) > CHECK_EVERY
