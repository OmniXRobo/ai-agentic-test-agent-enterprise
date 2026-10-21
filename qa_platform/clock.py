"""Time helpers.

The platform uses the Windows (host) system clock only. There is deliberately no
application-level timezone or GMT setting: every timestamp is the local system
time with the system's own UTC offset attached.
"""

from __future__ import annotations

from datetime import datetime


def now() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now().isoformat(timespec="milliseconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
