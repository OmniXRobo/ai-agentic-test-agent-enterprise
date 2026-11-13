"""Small helpers shared by the agent modules."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import secrets
from typing import Any


def now_iso() -> str:
    """Local time with the system's UTC offset (the Windows clock is the only time source, as in SkyGuard)."""
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return {} if default is None else default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {} if default is None else default


def norm(text: Any) -> str:
    """Lower-case, single-spaced text for comparisons."""
    return " ".join(str(text or "").split()).strip().lower()


class AgentError(Exception):
    """An error the interfaces show as it is: a stable code, a readable message, an HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error_code": self.code, "message": self.message, "details": self.details}
