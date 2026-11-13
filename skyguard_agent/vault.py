"""Credential vault. Passwords are encrypted for the signed-in Windows user and never enter the knowledge database,
chat history, logs, audit, the LLM or the learning memory; the database keeps only a reference (``credential_ref``).

Backends
* ``DpapiBackend`` (standalone companion on Windows): Windows Data Protection API, user scope (CryptProtectData).
  Each secret is one encrypted file in the vault folder; only the same Windows user on the same PC can decrypt it.
  No administrator rights are needed.
* ``SecretStoreBackend`` (SkyGuard QA): SkyGuard's own store, i.e. Windows Credential Manager through ``keyring``,
  namespace ``opsagent``, kept apart from TFS and test-script credentials.
* ``MemoryBackend``: tests only.

A secret is saved only with explicit consent, can expire, be replaced or revoked, and is wiped when its account is
deleted.
"""

from __future__ import annotations

import base64
import datetime as _dt
import os
import re
import secrets as _secrets
import sys
import threading
from pathlib import Path
from typing import Any, Protocol

from .util import AgentError, now_iso

REF_RE = re.compile(r"^vault:[a-z0-9-]{8,64}$")


class Backend(Protocol):
    name: str

    def get(self, ref: str) -> str | None: ...
    def set(self, ref: str, value: str) -> None: ...
    def delete(self, ref: str) -> None: ...


class MemoryBackend:
    name = "memory (tests only)"

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, ref: str) -> str | None:
        with self._lock:
            return self._data.get(ref)

    def set(self, ref: str, value: str) -> None:
        with self._lock:
            self._data[ref] = value

    def delete(self, ref: str) -> None:
        with self._lock:
            self._data.pop(ref, None)


class SecretStoreBackend:
    """Adapter for an object with get(namespace, name) / set(namespace, name, value) / delete(namespace, name)."""

    name = "Windows Credential Manager (SkyGuard QA)"
    NAMESPACE = "opsagent"

    def __init__(self, store: Any) -> None:
        self.store = store
        if not getattr(store, "available", True):
            raise AgentError("VAULT_UNAVAILABLE", "The Windows Credential Manager is not available.", 503)

    def get(self, ref: str) -> str | None:
        return self.store.get(self.NAMESPACE, ref)

    def set(self, ref: str, value: str) -> None:
        self.store.set(self.NAMESPACE, ref, value)

    def delete(self, ref: str) -> None:
        try:
            self.store.delete(self.NAMESPACE, ref)
        except Exception:
            pass


class DpapiBackend:
    """Windows DPAPI (user scope) through ctypes; one encrypted file per secret."""

    name = "Windows DPAPI (current user)"

    def __init__(self, folder: Path) -> None:
        if sys.platform != "win32":
            raise AgentError("VAULT_UNAVAILABLE", "Windows DPAPI is only available on Windows.", 503)
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        self._ct, self._Blob = ctypes, Blob
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        entropy_file = self.folder / "entropy.bin"
        if not entropy_file.exists():
            entropy_file.write_bytes(_secrets.token_bytes(32))
        self._entropy = entropy_file.read_bytes()

    def _blob(self, data: bytes) -> Any:
        buf = self._ct.create_string_buffer(data, len(data))
        return self._Blob(len(data), self._ct.cast(buf, self._ct.POINTER(self._ct.c_char))), buf

    def _call(self, fn: Any, data: bytes) -> bytes:
        inp, _keep1 = self._blob(data)
        ent, _keep2 = self._blob(self._entropy)
        out = self._Blob()
        CRYPTPROTECT_UI_FORBIDDEN = 0x01
        if not fn(self._ct.byref(inp), None, self._ct.byref(ent), None, None, CRYPTPROTECT_UI_FORBIDDEN,
                  self._ct.byref(out)):
            raise AgentError("VAULT_ERROR", "Windows could not encrypt or decrypt the credential.", 500)
        try:
            return self._ct.string_at(out.pbData, out.cbData)
        finally:
            self._kernel32.LocalFree(out.pbData)

    def _file(self, ref: str) -> Path:
        if not REF_RE.match(ref):
            raise AgentError("VAULT_REF_INVALID", "Invalid credential reference.", 400)
        return self.folder / (ref.split(":", 1)[1] + ".dpapi")

    def get(self, ref: str) -> str | None:
        f = self._file(ref)
        if not f.exists():
            return None
        return self._call(self._crypt32.CryptUnprotectData, f.read_bytes()).decode("utf-8")

    def set(self, ref: str, value: str) -> None:
        f = self._file(ref)
        tmp = f.with_suffix(".tmp")
        tmp.write_bytes(self._call(self._crypt32.CryptProtectData, value.encode("utf-8")))
        os.replace(tmp, f)

    def delete(self, ref: str) -> None:
        f = self._file(ref)
        if f.exists():
            # overwrite before unlinking; the content is ciphertext anyway
            f.write_bytes(base64.b64encode(_secrets.token_bytes(64)))
            f.unlink()


def default_backend(folder: Path) -> Backend | None:
    """DPAPI on Windows; elsewhere the OS keyring when the keyring package exists; else none (vault unavailable)."""
    if sys.platform == "win32":
        return DpapiBackend(folder)
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None

        class _Keyring:
            name = "OS keyring"

            def get(self, ref: str) -> str | None:
                return keyring.get_password("SkyGuardAgent", ref)

            def set(self, ref: str, value: str) -> None:
                keyring.set_password("SkyGuardAgent", ref, value)

            def delete(self, ref: str) -> None:
                try:
                    keyring.delete_password("SkyGuardAgent", ref)
                except Exception:
                    pass

        return _Keyring()
    except Exception:
        return None


class Vault:
    def __init__(self, backend: Backend | None) -> None:
        self.backend = backend
        self._recent: set[str] = set()  # values handed out in this process, for redaction
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.backend is not None

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", "unavailable") if self.backend else "unavailable"

    def _require(self) -> Backend:
        if self.backend is None:
            raise AgentError("VAULT_UNAVAILABLE", "No secure credential store is available on this computer "
                             "(Windows DPAPI / Credential Manager). Credentials cannot be saved.", 503)
        return self.backend

    @staticmethod
    def new_ref() -> str:
        return f"vault:{_secrets.token_hex(16)}"

    def save(self, value: str, *, consent: bool, ref: str | None = None) -> str:
        if not consent:
            raise AgentError("CONSENT_REQUIRED", "A credential is only saved after you agree to store it in the "
                             "Windows credential vault.", 400)
        if not value or len(value) > 512:
            raise AgentError("CREDENTIAL_INVALID", "The password must be between 1 and 512 characters.", 400)
        ref = ref if ref and REF_RE.match(ref) else self.new_ref()
        self._require().set(ref, value)
        return ref

    def reveal(self, ref: str | None) -> str | None:
        """Only the executor calls this, at the moment a login field is filled."""
        if not ref:
            return None
        value = self._require().get(ref)
        if value:
            with self._lock:
                self._recent.add(value)
        return value

    def has(self, ref: str | None) -> bool:
        if not ref or self.backend is None:
            return False
        try:
            return bool(self.backend.get(ref))
        except Exception:
            return False

    def revoke(self, ref: str | None) -> None:
        if ref:
            self._require().delete(ref)

    def known_values(self) -> list[str]:
        with self._lock:
            return list(self._recent)

    def remember(self, value: str) -> None:
        """A value typed into the secure form in this process (redacted from anything stored afterwards)."""
        if value:
            with self._lock:
                self._recent.add(value)


def expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        when = _dt.datetime.fromisoformat(expires_at)
        if when.tzinfo is None:
            when = when.astimezone()
        return when <= _dt.datetime.now().astimezone()
    except ValueError:
        return False


__all__ = ["Vault", "MemoryBackend", "SecretStoreBackend", "DpapiBackend", "default_backend", "expired", "now_iso"]
