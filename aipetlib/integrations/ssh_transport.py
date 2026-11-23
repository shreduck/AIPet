"""SSH transport for a remote AIPet hook directory on a Unix host."""
import base64
import errno
import hashlib
import hmac
import io
import json
import logging
import math
import os
from pathlib import Path
import posixpath
import re
import shlex
import stat
import time
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from aipet_hook_runtime.io import safe_name
from aipet_hook_runtime.config import SSH_APPROVAL_FILE

from . import protocol, settings, ssh_credentials
from ..runtime import paths

TIMEOUT = 5
MAX_SESSIONS = 48
MAX_RECORD = 512 * 1024
MAX_TRANSCRIPT_TAIL = 1024 * 1024
APPROVALS_MARKER = "ssh-approval-hooks-v1"
LOG_CHANNEL = "aipet.ssh.transport"
_LOG = logging.getLogger(LOG_CHANNEL)
_LOG.addHandler(logging.NullHandler())
_LOG.propagate = False


class ConnectionProblem(Exception):
    """A short status message safe to display in Settings."""


def connect(client, profile):
    import paramiko
    client.set_log_channel(LOG_CHANNEL)

    if profile["fingerprint"]:
        class PinnedKey(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                digest = hashlib.sha256(key.asbytes()).digest()
                actual = "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
                if not hmac.compare_digest(profile["fingerprint"], actual):
                    raise ConnectionProblem("The server fingerprint does not match.")

        client.set_missing_host_key_policy(PinnedKey())
    else:
        client.load_system_host_keys()

    try:
        password = ssh_credentials.unlock(profile)
    except Exception:
        raise ConnectionProblem("Could not unlock the password. Save it again in Settings.") from None
    try:
        client.connect(profile["host"], port=profile["port"], username=profile["user"], password=password,
                       allow_agent=False, look_for_keys=False, timeout=TIMEOUT, banner_timeout=TIMEOUT,
                       auth_timeout=TIMEOUT, channel_timeout=TIMEOUT)
    finally:
        del password


def failure_status(exc):
    if isinstance(exc, ConnectionProblem):
        return str(exc), False
    if isinstance(exc, ImportError):
        return "SSH dependencies are unavailable.", False
    import paramiko
    if isinstance(exc, paramiko.AuthenticationException):
        return "Authentication failed. Check the username and password.", False
    if isinstance(exc, paramiko.BadHostKeyException):
        return "The server key changed. Verify the host before reconnecting.", False
    if isinstance(exc, paramiko.SSHException) and "not found in known_hosts" in str(exc):
        return "Host is unknown. Enter its verified SHA256 fingerprint in Settings.", False
    return "Connection unavailable. Retrying automatically.", True


def _missing(exc):
    return isinstance(exc, OSError) and exc.errno == errno.ENOENT


class RemotePet:
    def __init__(self, client, sftp, cache):
        self.client = client
        self.sftp = sftp
        self.cache = Path(cache)
        self.base = posixpath.join(sftp.normalize("."), ".aipet", "ssh")
        self.controls = None
        self.transcripts = {}
        self.approvals_supported = None
        self._approval_file_active = None

    def _path(self, *parts):
        return posixpath.join(self.base, *parts)

    def _mkdir(self, path):
        try:
            self.sftp.mkdir(path, 0o700)
        except OSError:
            if not stat.S_ISDIR(self.sftp.stat(path).st_mode):
                raise

    def _read(self, path, maximum):
        with self.sftp.open(path, "rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Remote file exceeds size limit")
        return data

    def _write(self, path, data, mode=0o600):
        temp = path + "." + uuid4().hex + ".tmp"
        try:
            with self.sftp.open(temp, "xw") as stream:
                self.sftp.chmod(temp, mode)
                stream.write(data)
            self.sftp.posix_rename(temp, path)
        except Exception:
            try:
                self.sftp.remove(temp)
            except OSError:
                pass
            raise

    def _remove(self, path):
        try:
            self.sftp.remove(path)
        except OSError as exc:
            if not _missing(exc):
                raise

    def _discover(self):
        script = """import json, os, shutil, sys
home = os.path.expanduser('~')
claude = os.path.abspath(os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(home, '.claude'))
codex = os.path.abspath(os.environ.get('CODEX_HOME') or os.path.join(home, '.codex'))
print(json.dumps([sys.executable, {kind: os.path.join(folder, name)
    for kind, folder, name in [('claude', claude, 'settings.json'), ('codex', codex, 'hooks.json')]
    if shutil.which(kind) or os.path.isdir(folder)}]))
"""
        _, stdout, _ = self.client.exec_command("python3 -c " + shlex.quote(script), timeout=TIMEOUT)
        try:
            executable, configs = json.loads(stdout.read(16384))
        except (ValueError, TypeError):
            raise ConnectionProblem("Remote hook setup needs Python 3 on the SSH host.") from None
        if not configs:
            raise ConnectionProblem("No Claude Code or Codex installation was found on the SSH host.")
        return executable, configs

    def _deploy_runtime(self):
        archive = io.BytesIO()
        root = Path(paths.RESOURCE_DIR)
        with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
            bundle.write(root / "aipet_hook.py", "aipet_hook.py")
            bundle.writestr("__main__.py", "import runpy\nimport sys\nsys.argv.append('--ssh')\n"
                           "runpy.run_module('aipet_hook', run_name='__main__')\n")
            for source in sorted((root / "aipet_hook_runtime").glob("*.py")):
                bundle.write(source, str(source.relative_to(root)).replace(os.sep, "/"))
            for name in ("aipet_usage.py", "aipet_claude_usage.py"):
                bundle.write(root / name, name)
        self._write(self._path("aipet-hook.pyz"), archive.getvalue())
        self._write(self._path(APPROVALS_MARKER), b"")
        self.approvals_supported = True

    def install(self):
        executable, configs = self._discover()
        hook = self._path("aipet-hook.pyz")
        command = "env " + " ".join(shlex.quote(part) for part in ("AIPET_DIR=" + self.base, executable, hook))
        changes = []
        for kind, path in configs.items():
            try:
                attrs = self.sftp.stat(path)
                path = self.sftp.normalize(path)
                original = self._read(path, MAX_RECORD)
                mode = stat.S_IMODE(attrs.st_mode) & 0o777
            except OSError as exc:
                if not _missing(exc):
                    raise
                original, mode = None, 0o600
            try:
                current = settings.parse_settings(original.decode("utf-8-sig") if original is not None else "")
                key = "codex:ssh" if kind == "codex" else "ssh"
                events, timeouts = protocol.events_for(key)
                entry = command + (" " + protocol.CODEX_FLAG if kind == "codex" else "")
                hooks = current.get("hooks") or {}
                for groups in hooks.values():
                    for group in groups:
                        for spec in group.get("hooks", []):
                            existing = spec.get("command", "")
                            if protocol.MARKER.search(existing) and hook not in existing:
                                raise ConnectionProblem("Another AIPet hook is installed on this host. Remove it before installing SSH hooks.")
                merged = settings.merge_hooks(current, entry, events, timeouts)
            except (AttributeError, TypeError, ValueError):
                raise ConnectionProblem("The remote hook settings could not be read. Check the agent configuration.") from None
            if current != merged:
                changes.append((path, original, settings.dump(merged).encode("utf-8"), mode))

        self._mkdir(posixpath.dirname(self.base))
        self._mkdir(self.base)
        for folder in ("answers", "sessions"):
            self._mkdir(self._path(folder))
        self._deploy_runtime()
        for path, original, updated, mode in changes:
            self._mkdir(posixpath.dirname(path))
            try:
                current = self._read(path, MAX_RECORD)
            except OSError as exc:
                if not _missing(exc):
                    raise
                current = None
            if current != original:
                raise ConnectionProblem("Remote settings changed during setup. Run Install hooks again.")
            if original is not None:
                self._write(path + ".aipet-" + uuid4().hex + ".bak", original)
            self._write(path, updated, mode)
        return "Hooks installed. Restart remote sessions" + (" and trust AIPet in Codex /hooks." if "codex" in configs else ".")

    def _sync_approvals(self, approvals, remote_now):
        if self.approvals_supported is None:
            try:
                self.sftp.stat(self._path(APPROVALS_MARKER))
                self.approvals_supported = True
            except OSError as exc:
                if not _missing(exc):
                    raise
                self.approvals_supported = False
                try:
                    # Existing hook commands still point to the same AIPet-owned archive.
                    self._deploy_runtime()
                except (OSError, ValueError):
                    pass  # Keep monitoring and expose the manual update in SSH settings.
        if not self.approvals_supported:
            return
        path = self._path(SSH_APPROVAL_FILE)
        approvals = approvals or {}
        targets = approvals.get("targets", {})
        local_now = time.time()
        timed = {key: dict(grant, until=remote_now + grant["until"] - local_now)
                 for key, grant in approvals.get("timed", {}).items() if grant["until"] > local_now}
        if timed or any(rules.get("enabled") for rules in targets.values()):
            # Refresh the lease only after the whole policy has been written atomically.
            self._write(path, json.dumps({"targets": targets, "timed": timed}).encode("utf-8"))
            self._approval_file_active = True
        elif self._approval_file_active is not False:
            self._remove(path)
            self._approval_file_active = False

    def poll(self, controls, decisions, approvals=None):
        try:
            self.sftp.stat(self._path("aipet-hook.pyz"))
        except OSError as exc:
            if _missing(exc):
                return None
            raise
        if controls != self.controls:
            claude, codex, wait = controls
            for name, enabled in (("no-claude-answers", not claude), ("codex-answers", codex)):
                if enabled:
                    self._write(self._path(name), b"")
                else:
                    self._remove(self._path(name))
            self._write(self._path("answer-wait"), str(wait).encode("ascii"))
            self.controls = controls

        heartbeat = self._path("alive")
        self._write(heartbeat, b"")
        remote_now = self.sftp.stat(heartbeat).st_mtime
        self._sync_approvals(approvals, remote_now)
        waiting = self._waiting(remote_now)
        for name, data, deadline in decisions:
            if name in waiting and time.monotonic() < deadline:
                answer = dict(data, t=remote_now)
                self._write(self._path("answers", name + ".json"), json.dumps(answer).encode("utf-8"))
                waiting.discard(name)

        records = []
        listed = [entry for entry in self.sftp.listdir_attr(self._path("sessions"))
                  if stat.S_ISREG(entry.st_mode) and re.fullmatch(r"[A-Za-z0-9_.-]{1,120}\.json", entry.filename)]
        for entry in sorted(listed, key=lambda file: file.st_mtime, reverse=True)[:MAX_SESSIONS]:
            try:
                record = json.loads(self._read(self._path("sessions", entry.filename), MAX_RECORD))
                if not _valid_record(record):
                    continue
                record["ssh_approvals"] = self.approvals_supported
                for req in [record.get("request") or {}, *(record.get("requests") or [])]:
                    name = safe_name(req.get("id") or record["id"])
                    if req:
                        req["ssh_wait_until"] = time.monotonic() + 3 if req.get("answerable") and name in waiting else 0
                record["transcript_path"] = self._transcript(record)
                records.append(record)
            except (ValueError, TypeError):
                continue
            except OSError as exc:
                if not _missing(exc) and exc.errno != errno.EACCES:
                    raise
        active_paths = {record["transcript_path"] for record in records}
        for source, (_, target) in list(self.transcripts.items()):
            if target not in active_paths:
                self.transcripts.pop(source)
                try:
                    Path(target).unlink()
                except OSError:
                    pass
        return records

    def _waiting(self, remote_now):
        return {entry.filename[:-8] for entry in self.sftp.listdir_attr(self._path("answers"))
                if entry.filename.endswith(".waiting") and abs(remote_now - entry.st_mtime) < 4}

    def _transcript(self, record):
        source = record.get("transcript_path")
        if not isinstance(source, str) or not source.startswith("/") or not source.endswith(".jsonl"):
            return ""
        try:
            attrs = self.sftp.stat(source)
            if not stat.S_ISREG(attrs.st_mode):
                return ""
        except OSError as exc:
            if _missing(exc) or exc.errno == errno.EACCES:
                return ""
            raise
        signature = attrs.st_size, attrs.st_mtime
        cached = self.transcripts.get(source)
        if cached and cached[0] == signature:
            return cached[1]
        sid = safe_name(record["id"])
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        target = self.cache / f"rollout-ssh-{digest}-{sid}.jsonl"
        with self.sftp.open(source, "rb") as stream:
            if attrs.st_size > MAX_TRANSCRIPT_TAIL:
                head = stream.readline(256 * 1024)
                head = head if head.endswith(b"\n") else b""
                stream.seek(attrs.st_size - MAX_TRANSCRIPT_TAIL)
                stream.readline(MAX_TRANSCRIPT_TAIL)  # skip the first, partial line
            else:
                head = b""
            content = head + stream.read(MAX_TRANSCRIPT_TAIL)
        temp = target.with_suffix(".tmp")
        temp.write_bytes(content)
        os.replace(temp, target)
        self.transcripts[source] = signature, str(target)
        return str(target)


def _valid_record(record):
    if not isinstance(record, dict) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", str(record.get("id", ""))):
        return False
    if record.get("agent") not in ("claude", "codex") or record.get("state") not in (
            "idle", "working", "done", "needs_input", "error"):
        return False
    requests = record.get("requests", [])
    agents = record.get("agents", {})
    return (isinstance(record.get("id"), str)
            and all(isinstance(record.get(field, ""), str) for field in
                    ("title", "cwd", "entry", "app", "message", "conv", "topic", "last_prompt", "first_prompt"))
            and _timestamp(record.get("updated"))
            and _timestamp(record.get("changed", 0))
            and isinstance(record.get("request", {}), dict)
            and isinstance(requests, list) and all(isinstance(req, dict) for req in requests)
            and isinstance(agents, dict) and all(_timestamp(ts) for ts in agents.values())
            and isinstance(record.get("agent_types", {}), dict))


def _timestamp(value):
    return isinstance(value, (float, int)) and 0 <= value <= 1e11 and math.isfinite(value)
