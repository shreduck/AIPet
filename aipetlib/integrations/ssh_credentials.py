"""Encrypt SSH passwords with an AES-256 key held by the operating system."""
import base64
import os
import sys
import threading

_KEY_LOCK = threading.Lock()
_SERVICE = "AIPet SSH"
_ACCOUNT = "password encryption key v1"


def _vault():
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring
        vault = WinVaultKeyring()
        vault.persist = "local machine"
        return vault
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring
        return Keyring()
    raise RuntimeError("The operating system credential store is unavailable.")


def _cipher(create=False):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with _KEY_LOCK:
        vault = _vault()
        saved = vault.get_password(_SERVICE, _ACCOUNT)
        if saved is None and create:
            key = AESGCM.generate_key(bit_length=256)
            saved = base64.b64encode(key).decode("ascii")
            vault.set_password(_SERVICE, _ACCOUNT, saved)
        if saved is None:
            raise ValueError("The encryption key is missing. Save the SSH password again.")
        key = base64.b64decode(saved, validate=True)
        if len(key) != 32:
            raise ValueError("Invalid SSH encryption key")
        return AESGCM(key)


def _associated_data(profile):
    return "\0".join((profile["id"], profile["user"], profile["host"], str(profile["port"]))).encode("utf-8")


def seal(profile, password):
    nonce = os.urandom(12)
    ciphertext = _cipher(create=True).encrypt(nonce, password.encode("utf-8"), _associated_data(profile))
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def unlock(profile):
    payload = base64.b64decode(profile["secret"], validate=True)
    return _cipher().decrypt(payload[:12], payload[12:], _associated_data(profile)).decode("utf-8")
