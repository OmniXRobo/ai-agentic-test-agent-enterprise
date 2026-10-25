"""Conversation context and command outcomes shared by the voice service and its command handlers."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Conversation:
    id: str
    user_story_id: int | None = None
    test_case_id: int | None = None
    script_id: str | None = None
    run_id: str | None = None
    run_kind: str | None = None
    perf_plan_id: str | None = None
    base_url: str | None = None
    design_run_id: str | None = None
    recording_run_id: str | None = None
    last_list: list[dict[str, Any]] = field(default_factory=list)  # what "select the second one" picks from
    pending: dict[str, Any] | None = None  # {"kind": "confirm"|"input", "intent": Intent, "param": str, "at": float}
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class Outcome:
    status: str  # completed | accepted | failed | unsupported | unclear | needs_confirmation | needs_input
    say: str
    navigate: str | None = None
    owner: str | None = None  # the agent that does the work (for the command handoff display)
    job: Callable[[Callable[[str], None]], tuple[str, str | None]] | None = None  # (say_progress) -> (say, navigate)
    job_title: str | None = None
    failure: str = "That did not complete"
    confirm_text: str | None = None  # what a destructive command will do, asked before it runs
