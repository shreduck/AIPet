import base64
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from aipetlib import permissions, sessions
from aipetlib.integrations import ssh, ssh_credentials, ssh_transport


class LocalSFTP:
    """Expose only a temporary directory using the remote POSIX paths."""

    def __init__(self, root):
        self.root = Path(root)

    def path(self, name):
        return self.root / name.lstrip("/")

    def normalize(self, name):
        return "/home/test" if name == "." else name

    def mkdir(self, name, mode):
        self.path(name).mkdir(mode)

    def chmod(self, name, mode):
        self.path(name).chmod(mode)

    def open(self, name, mode):
        return self.path(name).open("xb" if mode == "xw" else mode)

    def stat(self, name):
        return self.path(name).stat()

    def listdir_attr(self, name):
        from types import SimpleNamespace
        result = []
        for entry in self.path(name).iterdir():
            attrs = entry.stat()
            result.append(SimpleNamespace(filename=entry.name, st_mtime=attrs.st_mtime, st_mode=attrs.st_mode))
        return result

    def posix_rename(self, source, target):
        self.path(source).replace(self.path(target))

    def remove(self, name):
        self.path(name).unlink()


class SSHTests(unittest.TestCase):
    def test_pinned_host_is_checked_before_sending_password(self):
        try:
            import paramiko
        except ImportError:
            self.skipTest("SSH dependency is unavailable")
        key = paramiko.RSAKey.generate(2048)
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")

        class Auth(paramiko.ServerInterface):
            def check_auth_password(self, username, password):
                credentials.append((username, password))
                return paramiko.AUTH_SUCCESSFUL

        for pin, accepted in ((fingerprint, True), ("SHA256:" + "A" * 43, False)):
            credentials = []
            done = threading.Event()
            with socket.socket() as listener:
                try:
                    listener.bind(("127.0.0.1", 0))
                except OSError as exc:
                    self.skipTest(f"Loopback is unavailable: {exc}")
                listener.listen()
                listener.settimeout(5)

                def serve():
                    with listener.accept()[0] as conn, paramiko.Transport(conn) as transport:
                        transport.add_server_key(key)
                        transport.set_log_channel(ssh_transport.LOG_CHANNEL)
                        try:
                            transport.start_server(server=Auth())
                        except (EOFError, OSError):
                            if accepted:
                                raise
                        done.wait(5)

                server = threading.Thread(target=serve, daemon=True)
                server.start()
                profile = dict(host="127.0.0.1", port=listener.getsockname()[1], user="agent", fingerprint=pin)
                try:
                    with patch.object(ssh_credentials, "unlock", return_value="example-secret"), paramiko.SSHClient() as client:
                        if accepted:
                            ssh_transport.connect(client, profile)
                        else:
                            with self.assertRaises(ssh_transport.ConnectionProblem):
                                ssh_transport.connect(client, profile)
                finally:
                    done.set()
                    server.join(5)
                self.assertEqual(credentials, [("agent", "example-secret")] if accepted else [])

    def test_password_store_encrypts_and_binds_password_to_endpoint(self):
        try:
            from cryptography.exceptions import InvalidTag
        except ImportError:
            self.skipTest("SSH crypto dependency is unavailable")
        stored_key = {}

        class Vault:
            def get_password(self, *key):
                return stored_key.get(key)

            def set_password(self, service, account, password):
                stored_key[service, account] = password

        with tempfile.TemporaryDirectory() as tmp, patch.object(ssh.paths, "HOME_DIR", tmp):
            connections = ssh.SSHConnections({})
            self.addCleanup(connections.close)
            with patch.object(ssh_credentials, "_vault", return_value=Vault()):
                key = connections.save(None, "ssh://[::1]:2222", "agent", "example-secret", "", False)
                raw = ssh._store_path().read_text()
                self.assertNotIn("example-secret", raw)
                self.assertNotIn(next(iter(stored_key.values())), raw)
                profile = json.loads(raw)[key]
                self.assertEqual(ssh_credentials.unlock(profile), "example-secret")
                with self.assertRaises(InvalidTag):
                    ssh_credentials.unlock(dict(profile, port=22))
                self.assertNotIn("secret", connections.profiles()[0])

    def test_offline_worker_does_not_remove_other_hosts(self):
        offline = threading.Event()
        online = threading.Event()

        def connected(worker, _cache):
            if worker.profile["host"] == "offline":
                offline.set()
                raise ssh_transport.ConnectionProblem("Host unavailable")
            worker._publish("Connected", [{"id": "shared-id", "updated": time.time()}])
            online.set()
            worker._stop_event.wait(3)

        with (tempfile.TemporaryDirectory() as tmp, patch.object(ssh.paths, "HOME_DIR", tmp),
              patch.object(ssh.SSHWorker, "_connected", connected),
              patch.object(ssh_credentials, "seal", return_value="ciphertext")):
            connections = ssh.SSHConnections({})
            try:
                bad = connections.save(None, "offline", "agent", "test", "", True)
                good = connections.save(None, "online", "agent", "test", "", True)
                self.assertTrue(offline.wait(2))
                self.assertTrue(online.wait(2))
                limit = time.monotonic() + 2
                while connections.status(bad) == "Connecting…" and time.monotonic() < limit:
                    time.sleep(0.01)
                self.assertEqual(connections.status(bad), "Host unavailable")
                self.assertEqual(connections.status(good), "Connected")
                self.assertEqual(connections.records()[0][0], f"ssh:{good}:shared-id")
            finally:
                connections.close()

    def test_install_runs_real_hook_and_relays_waiting_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            sftp = LocalSFTP(tmp)
            config_path = "/home/test/.codex/hooks.json"
            sftp.path(config_path).parent.mkdir(parents=True)
            sftp.path(config_path).write_text('{"other": true}', encoding="utf-8")
            cache = sftp.root / "cache"
            cache.mkdir()

            class Client:
                def exec_command(self, *args, **kwargs):
                    return None, io.BytesIO(json.dumps(["/usr/bin/python3", {"codex": config_path}]).encode()), None

            remote = ssh_transport.RemotePet(Client(), sftp, cache)
            self.assertIn("/hooks", remote.install())
            self.assertEqual(len(list(sftp.path(config_path).parent.glob("*.bak"))), 1)
            remote.install()
            self.assertEqual(len(list(sftp.path(config_path).parent.glob("*.bak"))), 1)
            self.assertTrue(json.loads(sftp.path(config_path).read_text())["other"])

            base = sftp.path(remote.base)
            hook = base / "aipet-hook.pyz"
            controls = True, True, 10
            remote.poll(controls, [])
            event = {"hook_event_name": "PermissionRequest", "session_id": "ssh-test",
                     "tool_name": "Bash", "tool_input": {"command": "pwd"}, "cwd": str(base)}
            with subprocess.Popen([sys.executable, str(hook), "--codex"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=dict(os.environ, AIPET_DIR=str(base))) as process:
                try:
                    process.stdin.write(json.dumps(event))
                    process.stdin.close()
                    process.stdin = None
                    limit = time.monotonic() + 5
                    while time.monotonic() < limit:
                        rows = remote.poll(controls, [])
                        if rows and rows[0]["request"].get("ssh_wait_until"):
                            break
                        time.sleep(0.05)
                    else:
                        self.fail("Remote hook never published a waiting request")
                    records = [("ssh:profile:ssh-test", "profile", "user@host", rows[0])]
                    cfg = {"claude_code": {"enabled": True}, "stale_hours": 12}
                    with (patch.object(sessions.paths, "SESSIONS_DIR", str(sftp.root / "unused")),
                          patch.object(sessions.paths, "LEGACY_SESSIONS_DIR", str(sftp.root / "unused"))):
                        item, = sessions.read_claude_code_sessions(cfg, records)
                    self.assertEqual(item["ssh_connection"], "profile")
                    self.assertTrue(permissions.hook_waiting(item))
                    request = item["request"]
                    remote.poll(controls, [(request["id"], {"behavior": "deny"}, request["ssh_wait_until"])])
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertFalse(stderr)
                    self.assertIn('"deny"', stdout)
                finally:
                    if process.poll() is None:
                        process.kill()
