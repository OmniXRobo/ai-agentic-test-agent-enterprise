"""AURA - Adaptive Understanding & Runtime Intelligence Agent (final patch).

Other modules reach AURA through ``learn`` / ``recall`` here, which never raise: learning must never break a test
run, a discovery or a recording. The runtime installs the service with ``install``.
"""

from __future__ import annotations

from typing import Any

_service: Any = None


def install(service: Any) -> None:
    global _service
    _service = service


def current() -> Any:
    """The installed AuraService (not named ``service``: that is also the submodule name)."""
    return _service


def learn(kind: str, application: str | None, key: str, data: dict[str, Any], *, source: str,
          approved: bool = False, agent: str | None = None) -> None:
    if _service is None:
        return
    try:
        _service.learn(kind, application, key, data, source=source, approved=approved, agent=agent)
    except Exception:  # noqa: BLE001 - learning is best effort by design
        pass


def recall(kind: str, application: str | None, key: str) -> dict[str, Any] | None:
    if _service is None:
        return None
    try:
        return _service.recall(kind, application, key)
    except Exception:  # noqa: BLE001
        return None
