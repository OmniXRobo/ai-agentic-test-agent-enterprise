"""Secret storage.

TFS credentials and application credentials are kept in separate namespaces and
never written to settings.json, logs or API responses. On Windows the default
backend is Windows Credential Manager (via ``keyring``), which needs no
administrator rights.
"""

from __future__ import annotations

import threading
from typing import Protocol

from .errors import QAError

SERVICE = "AIAgenticTestAgentEnterprise"

TFS_NAMESPACE = "tfs"
APP_CREDENTIAL_NAMESPACE = "appcred"


class SecretBackend(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeyringBackend:
    def __init__(self) -> None:
        import keyring
        from keyring.backends import fail

        self._keyring = keyring
        if isinstance(keyring.get_keyring(), fail.Keyring):
            raise RuntimeError("No OS credential store is available on this machine.")

    def get(self, key: str) -> str | None:
        return self._keyring.get_password(SERVICE, key)

    def set(self, key: str, value: str) -> None:
        self._keyring.set_password(SERVICE, key, value)

    def delete(self, key: str) -> None:
        import keyring.errors

        try:
            self._keyring.delete_password(SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass


class MemoryBackend:
    """Process-local backend for tests."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class SecretStore:
    def __init__(self, backend: SecretBackend | None = None) -> None:
        self._backend = backend
        self._error: str | None = None
        if backend is None:
            try:
                self._backend = KeyringBackend()
            except Exception as exc:  # reported when a secret is actually needed
                self._error = str(exc)

    @property
    def available(self) -> bool:
        return self._backend is not None

    def _require(self) -> SecretBackend:
        if self._backend is None:
            raise QAError("SECRET_STORE_UNAVAILABLE",
                          "The Windows Credential Manager (or OS credential store) is not available.",
                          status=503, details=self._error)
        return self._backend

    @staticmethod
    def key(namespace: str, name: str) -> str:
        return f"{namespace}:{name}"

    def get(self, namespace: str, name: str) -> str | None:
        if self._backend is None:
            return None
        return self._backend.get(self.key(namespace, name))

    def has(self, namespace: str, name: str) -> bool:
        return bool(self.get(namespace, name))

    def set(self, namespace: str, name: str, value: str) -> None:
        if not value:
            raise QAError("SECRET_EMPTY", "An empty secret cannot be saved.")
        self._require().set(self.key(namespace, name), value)

    def delete(self, namespace: str, name: str) -> None:
        self._require().delete(self.key(namespace, name))
