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
    # 23.3.1: the test plan being worked on, what the user selected, and what "it" refers to
    test_plan_id: int | None = None
    test_plan_name: str | None = None
    sprint: str | None = None
    suite_selection: dict[str, bool] = field(default_factory=dict)  # testing type -> checked / unchecked
    focus: str | None = None  # test_plan | test_case | user_story: what "open it" means
    raw_text: str = ""  # the sentence as said (names keep their capitals)
    last_command: tuple[str, float, dict[str, Any]] | None = None  # (normalized text, time, reply) for duplicates
    last_action: str | None = None
    case_runs: list[str] = field(default_factory=list)  # suite test case generations started by voice, for review/save
    saved_case_ids: list[int] = field(default_factory=list)  # test cases saved to TFS by the last "save the test cases"
    data_case_ids: list[int] = field(default_factory=list)  # test cases of the last test data generation
    script_ids: list[str] = field(default_factory=list)  # 23.3.3: framework scripts made by the last voice generation
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
    verify: dict[str, Any] | None = None  # what the dashboard checks before it reports success (e.g. {"route": ...})
