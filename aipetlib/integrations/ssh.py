"""Saved SSH profiles and independent background workers."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from aipet_hook_runtime.io import safe_name

from . import ssh_credentials, ssh_transport
from .. import permissions
from ..runtime import paths, preferences

INTERVAL = 1.5
RETRY = 30


def parse_endpoint(url, user, fingerprint):
    try:
        raw = url.strip()
        address = urlsplit(raw if "://" in raw else "ssh://" + raw)
        host = address.hostname
        port = address.port if address.port is not None else 22
        if (address.scheme != "ssh" or not host or address.username is not None or address.password is not None
                or address.path not in ("", "/") or address.query or address.fragment or not 1 <= port <= 65535
                or not re.fullmatch(r"[a-zA-Z0-9_.:%-]+", host)):
            raise ValueError
    except ValueError:
        raise ValueError("Enter an SSH hostname, IP address, or ssh://host:port URL.") from None
    user = user.strip()
    if not user or any(ch.isspace() or ord(ch) < 32 for ch in user):
        raise ValueError("Enter the username for this SSH host.")
    fingerprint = fingerprint.strip().rstrip("=")
    if fingerprint and not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint):
        raise ValueError("Enter the host fingerprint in OpenSSH SHA256 format.")
    return dict(host=host, port=port, user=user, fingerprint=fingerprint)


def label(profile):
    return profile["user"] + "@" + address_label(profile)


def address_label(profile):
    host = profile["host"]
    if ":" in host:
        host = "[" + host + "]"
    return host + (f':{profile["port"]}' if profile["port"] != 22 else "")


def _store_path():
    return Path(paths.HOME_DIR) / "ssh-connections.json"


def _load():
    try:
        profiles = json.loads(_store_path().read_text(encoding="utf-8"))
        if not isinstance(profiles, dict):
            raise ValueError
        for key, profile in profiles.items():
            if (not isinstance(profile, dict) or key != profile.get("id")
                    or not re.fullmatch(r"[0-9a-f]{32}", key)
                    or not isinstance(profile.get("secret"), str)
                    or not isinstance(profile.get("enabled"), bool)):
                raise ValueError
            host = profile["host"]
            host = f"[{host}]" if ":" in host else host
            parse_endpoint(f'{host}:{profile["port"]}', profile["user"], profile["fingerprint"])
        return profiles, ""
    except FileNotFoundError:
        return {}, ""
    except (OSError, ValueError, TypeError, KeyError):
        return {}, "Saved SSH connections could not be read."


def _save(profiles):
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(profiles, stream, indent=2)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class SSHConnections:
    def __init__(self, cfg):
        self.cfg = cfg
        self._profiles, self.load_error = _load()
        self._lock = threading.RLock()
        self._closed = False
        self._workers = {}
        self._dismissed = {}
        self._sync()

    def profiles(self):
        with self._lock:
            return [{key: value for key, value in profile.items() if key != "secret"}
                    for profile in self._profiles.values()]

    def save(self, key, url, user, password, fingerprint, enabled):
        endpoint = parse_endpoint(url, user, fingerprint)
        with self._lock:
            old = self._profiles.get(key)
            other_profiles = (p for k, p in self._profiles.items() if k != key)
            fields = ("host", "port", "user")
            if any(all(p[field] == endpoint[field] for field in fields) for p in other_profiles):
                raise ValueError("This SSH connection already exists.")
        same_host = old and all(old[field] == endpoint[field] for field in (*fields, "fingerprint"))
        profile = dict(endpoint, id=key if same_host else uuid4().hex, enabled=enabled)
        if password:
            try:
                profile["secret"] = ssh_credentials.seal(profile, password)
            except Exception:
                raise ValueError("Could not save the password in the operating system credential store.") from None
        elif same_host:
            profile["secret"] = old["secret"]
        else:
            raise ValueError("Enter the password for this SSH host.")
        with self._lock:
            updated = {k: p for k, p in self._profiles.items() if k != key or same_host}
            self._replace({**updated, profile["id"]: profile})
        return profile["id"]

    def remove(self, key):
        with self._lock:
            self._replace({k: p for k, p in self._profiles.items() if k != key})

    def _replace(self, profiles):
        if self.load_error:
            raise ValueError(self.load_error)
        if self._closed:
            raise ValueError("AIPet is closing.")
        _save(profiles)
        self._profiles = profiles
        self._sync()

    def _sync(self):
        for key, worker in list(self._workers.items()):
            if self._profiles.get(key) != worker.profile:
                worker.stop()
                del self._workers[key]
        for key, profile in self._profiles.items():
            if profile["enabled"] and key not in self._workers:
                worker = self._workers[key] = SSHWorker(profile, self.cfg)
                worker.start()

    def close(self):
        with self._lock:
            self._closed = True
            for worker in self._workers.values():
                worker.stop()
            self._workers.clear()

    def action(self, key, *, install=False, reconnect=False):
        with self._lock:
            worker = self._workers.get(key)
            if worker:
                worker.request(install=install, reconnect=reconnect)

    def status(self, key):
        with self._lock:
            worker = self._workers.get(key)
            return worker.snapshot()[0] if worker else "Disabled"

    def records(self):
        with self._lock:
            result = []
            for key, worker in self._workers.items():
                for record in worker.snapshot()[1]:
                    item_key = f'ssh:{key}:{record["id"]}'
                    if self._dismissed.get(item_key) == record["updated"]:
                        continue
                    self._dismissed.pop(item_key, None)
                    result.append((item_key, key, label(worker.profile), record))
            return result

    def dismiss(self, item):
        with self._lock:
            if len(self._dismissed) >= 1024:
                del self._dismissed[next(iter(self._dismissed))]
            self._dismissed[item["key"]] = item["updated"]

    def answer(self, item, data):
        with self._lock:
            worker = self._workers.get(item["ssh_connection"])
            req = item.get("request") or {}
            name = safe_name(req.get("id") or item.get("sid") or "request")
            return bool(worker and worker.answer(name, data, req.get("ssh_wait_until", 0)))


@contextmanager
def _deadline(client, seconds):
    """Also bound SSH channel requests whose library calls have no read timeout."""
    timer = threading.Timer(seconds, client.close)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


class SSHWorker(threading.Thread):
    def __init__(self, profile, cfg):
        super().__init__(name="AIPet SSH connection", daemon=True)
        self.profile = profile
        self.cfg = cfg
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._install = False
        self._reconnect = threading.Event()
        self._decisions = {}
        self._status = "Connecting…"
        self._records = []

    def stop(self):
        self._stop_event.set()
        self._wake.set()

    def request(self, *, install=False, reconnect=False):
        with self._lock:
            self._install |= install
            if reconnect:
                self._decisions.clear()
        if reconnect:
            self._reconnect.set()
        self._wake.set()

    def snapshot(self):
        with self._lock:
            return self._status, self._records

    def _publish(self, status, records=()):
        with self._lock:
            self._status, self._records = status, records

    def answer(self, name, data, deadline):
        with self._lock:
            if (time.monotonic() >= deadline or not self._records or self._stop_event.is_set()
                    or self._reconnect.is_set()):
                return False
            self._decisions[name] = data, deadline
        self._wake.set()
        return True

    def _wait(self, seconds):
        self._wake.wait(seconds)
        self._wake.clear()

    def run(self):
        while not self._stop_event.is_set():
            self._reconnect.clear()
            self._publish("Connecting…")
            try:
                with tempfile.TemporaryDirectory(prefix="aipet-ssh-", ignore_cleanup_errors=True) as cache:
                    self._connected(cache)
            except Exception as exc:
                with self._lock:
                    self._decisions.clear()
                if not self._stop_event.is_set():
                    message, retry = ssh_transport.failure_status(exc)
                    self._publish(message)
                    self._wait(RETRY if retry else None)

    def _connected(self, cache):
        import paramiko
        with paramiko.SSHClient() as client:
            ssh_transport.connect(client, self.profile)
            if self._stop_event.is_set():
                return
            with _deadline(client, 12):
                sftp = client.open_sftp()
                sftp.get_channel().settimeout(ssh_transport.TIMEOUT)
                remote = ssh_transport.RemotePet(client, sftp, cache)
            install_status = ""
            while not self._stop_event.is_set() and not self._reconnect.is_set():
                with self._lock:
                    install, self._install = self._install, False
                    decisions, self._decisions = self._decisions, {}
                if install:
                    self._publish("Installing remote hooks…")
                    try:
                        with _deadline(client, 40):
                            install_status = remote.install()
                    except ssh_transport.ConnectionProblem as exc:
                        install_status = str(exc)
                    except (OSError, ValueError):
                        install_status = "Could not install remote hooks. Check Python 3 and write access."
                controls = (self.cfg.get("claude_answers", True), self.cfg.get("codex_answers", False),
                            preferences.ANSWER_WAIT["v"])
                with _deadline(client, 15):
                    rows = remote.poll(controls, [(name, data, expiry) for name, (data, expiry) in decisions.items()],
                                       permissions.ssh_approval_state(self.profile["id"]))
                status = ("Connected · install hooks to start" if rows is None else "Connected" if remote.approvals_supported
                          else "Connected · update hooks for approval rules")
                self._publish(install_status or status, rows or [])
                self._wait(INTERVAL)
