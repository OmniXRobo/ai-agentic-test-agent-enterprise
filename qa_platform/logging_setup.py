"""Structured JSON logging with secret redaction.

Each record carries: timestamp, level, run_id, agent, operation, user_story_id,
test_case_id, performance_test_id, defect_id, message. Pass the optional fields
through ``extra={...}``.
"""

from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .clock import now_iso

CONTEXT_FIELDS = (
    "run_id",
    "agent",
    "operation",
    "user_story_id",
    "test_case_id",
    "performance_test_id",
    "defect_id",
)

_SECRET_KEY = re.compile(r"(^|[_-])(pat|token|password|passwd|secret|api[_-]?key|apikey|authorization|credentials?)$", re.I)
_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(basic|bearer)\s+[A-Za-z0-9+/=._-]+"),
    re.compile(r"(?i)\b(basic|bearer)\s+[A-Za-z0-9+/=._-]{12,}"),
    re.compile(r"(?i)((?:pat|token|password|passwd|secret|api[_-]?key)\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}]+)"),
]
REDACTED = "***REDACTED***"


def redact_text(text: str) -> str:
    out = _SECRET_PATTERNS[0].sub(lambda m: m.group(1) + REDACTED, text)
    out = _SECRET_PATTERNS[1].sub(lambda m: m.group(1) + " " + REDACTED, out)
    out = _SECRET_PATTERNS[2].sub(lambda m: m.group(1) + REDACTED, out)
    return out


def redact(value: Any) -> Any:
    """Recursively redact secrets from dicts, lists and strings."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _SECRET_KEY.search(k) and v not in (None, "") else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": now_iso(),
            "level": record.levelname,
            "logger": record.name,
        }
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value
        payload["message"] = redact_text(record.getMessage())
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def configure_logging(log_dir: Path, level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("qa")
    root.setLevel(level)
    root.propagate = False
    file_handler = RotatingFileHandler(
        log_dir / "qa_platform.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    console = logging.StreamHandler()
    console.setFormatter(JsonFormatter())
    root.addHandler(file_handler)
    root.addHandler(console)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"qa.{name}")
