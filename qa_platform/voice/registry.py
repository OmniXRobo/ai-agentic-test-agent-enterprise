"""The voice command registry: every action the Voice Command Agent may perform, and nothing else.

Each entry names its parameters (validated before anything runs), whether it changes or deletes
data (then the agent asks "Confirm this operation?" first), and a one-line summary that is also
what the On-Premise AI is shown when it maps a free-form sentence to a command. The AI may only
answer with a registered command id; parameters it returns are kept only if they can be traced
to the spoken sentence (numbers the user said, names that appear in it, or one of a fixed set
of choices). Handlers live in ``service.py`` (``_cmd_<id>``) and call the same services the pages
use; there is no route, selector or shell command the model can invent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import intents


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # int | str | enum | bool
    description: str
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandSpec:
    id: str
    summary: str
    params: tuple[Param, ...] = ()
    destructive: bool = False  # changes or deletes saved data in TFS or here: confirmation first
    ai: bool = True  # offered to the On-Premise AI for free-form sentences
    examples: tuple[str, ...] = field(default_factory=tuple)


_ID = lambda name, what: Param(name, "int", what)  # noqa: E731
_NAME = Param("name", "str", "a test, script or plan name exactly as the user said it")

COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("help", "list what can be said", examples=("what can you do",)),
    CommandSpec("status", "say what is running and how today's runs went", examples=("what is running",)),
    CommandSpec("confirm", "confirm the operation waiting for confirmation", ai=False),
    CommandSpec("cancel", "cancel what is waiting, or stop listening", ai=False),
    CommandSpec("go_back", "go back to the previous page", examples=("go back",)),
    CommandSpec("open_page", "open a page of the dashboard",
                (Param("page", "enum", "the page", tuple(r for r, _l, _a in intents.PAGES)),),
                examples=("open the dashboard", "show execution history")),
    CommandSpec("open_work_item", "open a user story, test case or defect by its number",
                (Param("kind", "enum", "work item kind", ("user story", "test case", "defect")),
                 _ID("id", "work item number")), examples=("load user story 145",)),
    CommandSpec("search_work_items", "list TFS work items, optionally by kind, sprint, state or words in the title",
                (Param("kind", "enum", "work item kind", ("user story", "test case", "defect")),
                 Param("text", "str", "words to search in titles"), Param("sprint", "str", "sprint or iteration name"),
                 Param("state", "str", "work item state")), examples=("show test cases in sprint 5",)),
    CommandSpec("select_item", "choose an item from the list just shown (first, second, last)",
                (Param("position", "int", "1-based position in the list; -1 for the last"),),
                examples=("select the second one",)),
    CommandSpec("generate_test_cases", "generate test cases (and test plan, strategy and test data) for a user story",
                (_ID("user_story_id", "user story number"),), examples=("generate test cases for user story 145",)),
    CommandSpec("generate_test_plan", "create the sprint test plan with its default suites in TFS for a user story",
                (_ID("user_story_id", "user story number"),), destructive=True, examples=("create the test plan",)),
    CommandSpec("save_test_cases", "save the generated test cases of the current test design to TFS",
                (_ID("user_story_id", "user story number"),), destructive=True,
                examples=("save the test cases to TFS",)),
    CommandSpec("create_test_data", "show or create test data for a test case",
                (_ID("test_case_id", "test case number"),), examples=("create test data for test case 310",)),
    CommandSpec("write_test_data", "write the test data of a test case to TFS",
                (_ID("test_case_id", "test case number"),), destructive=True),
    CommandSpec("delete_test_data", "delete the saved test data of a test case",
                (_ID("test_case_id", "test case number"),), destructive=True),
    CommandSpec("generate_automation", "generate Playwright automation for a test case",
                (_ID("test_case_id", "test case number"), _NAME), examples=("generate automation",)),
    CommandSpec("automate_user_story", "read a user story's requirement, discover the application and create the "
                                       "automation scripts for all its test cases",
                (_ID("user_story_id", "user story number"),),
                examples=("automate user story 118", "read the requirement and create the scripts for user story 118")),
    CommandSpec("open_script", "open the script of a test case in Script Studio (view, edit, flow logic, data, "
                               "credentials, history, results, logs, evidence)",
                (_ID("test_case_id", "test case number"),
                 Param("tab", "enum", "Script Studio tab", ("view", "edit", "flow", "data", "credentials", "history",
                                                            "result", "logs", "evidence"))),
                examples=("open the flow logic for test case 310",)),
    CommandSpec("archive_script", "archive the automated script of a test case", (_ID("test_case_id", "test case number"),),
                destructive=True),
    CommandSpec("run_test_case", "run or play the automated test of a test case",
                (_ID("test_case_id", "test case number"), _NAME, Param("play", "bool", "visible playback")),
                examples=("run test case 310", "run it again")),
    CommandSpec("run_all_tests", "run every automated test", examples=("run all automated tests",)),
    CommandSpec("stop_run", "stop the running test, recording or performance test", examples=("stop the current test",)),
    CommandSpec("start_recording", "start a manual browser recording (Guided Browser Automation)",
                (Param("scenario", "str", "scenario name the user gave"), Param("url", "str", "start address")),
                examples=("start manual recording",)),
    CommandSpec("stop_recording", "stop the browser recording and keep the steps", examples=("stop recording",)),
    CommandSpec("browser_action", "do one action in the browser that is being recorded",
                (Param("action", "enum", "action", ("click", "fill", "select", "open_calendar", "select_date",
                                                     "scroll", "next_page", "press")),
                 Param("target", "str", "the element as the user named it"), Param("value", "str", "value to enter"),
                 Param("days", "int", "days from today for select_date")), ai=False,
                examples=("open the calendar", "select tomorrow", "click search")),
    CommandSpec("run_performance", "run a JMeter performance test plan",
                (_NAME, Param("kind", "enum", "test kind", ("load", "stress", "spike", "soak")),
                 Param("users", "int", "virtual users"), Param("duration_s", "int", "duration in seconds")),
                examples=("run a 100 user load test for checkout for five minutes",)),
    CommandSpec("show_failed", "show failed test runs", examples=("show failed tests",)),
    CommandSpec("show_result", "open the result, logs or evidence of the latest run",
                (Param("what", "enum", "what to open", ("result", "evidence", "logs")),),
                examples=("show me the evidence",)),
    CommandSpec("show_performance_report", "show the latest performance results"),
    CommandSpec("generate_report", "build a QA report (functional, performance or both) for a period",
                (Param("scope", "enum", "report scope", ("functional", "performance", "both")),
                 Param("days", "int", "number of days")), examples=("generate the report",)),
    CommandSpec("show_report", "open the latest QA report", examples=("show me the report",)),
    CommandSpec("analyze_failure", "analyze the latest failure"),
    CommandSpec("create_defects", "create TFS defects from the open failure drafts",
                (Param("source", "enum", "draft source", ("functional", "performance")),), destructive=True),
    CommandSpec("test_tfs", "test the TFS connection (online or offline) and report each check",
                (Param("mode", "enum", "TFS profile", ("online", "offline")),), examples=("test the TFS connection",)),
    CommandSpec("switch_tfs", "make the online or offline TFS profile the active one",
                (Param("mode", "enum", "TFS profile", ("online", "offline")),), destructive=True),
    CommandSpec("test_ai", "test the On-Premise AI (local LLM) and report each check", examples=("check the local AI",)),
    CommandSpec("read_notifications", "read the latest unread notifications", examples=("any notifications",)),
)

BY_ID: dict[str, CommandSpec] = {c.id: c for c in COMMANDS}


def ai_catalog() -> str:
    """The command list the On-Premise AI chooses from (built from the registry, never by hand)."""
    lines = []
    for c in COMMANDS:
        if not c.ai:
            continue
        ps = ", ".join(f"{p.name} ({p.kind}{': ' + '|'.join(p.choices) if p.choices and len(p.choices) < 12 else ''})"
                       for p in c.params)
        lines.append(f"- {c.id}: {c.summary}" + (f". Params: {ps}" if ps else ""))
    return "\n".join(lines)


def validate(command: str, raw: dict[str, Any], text: str) -> dict[str, Any] | None:
    """Keep only parameters that are declared and traceable to the sentence. None = command not allowed."""
    spec = BY_ID.get(command)
    if spec is None or not spec.ai:
        return None
    said = intents.normalize(text)
    digits = set(re.findall(r"-?\d+", said))
    out: dict[str, Any] = {}
    for p in spec.params:
        value = raw.get(p.name)
        if value in (None, ""):
            continue
        if p.kind == "int":
            try:
                n = int(value)
            except (TypeError, ValueError):
                continue
            if p.name == "duration_s":
                if any(str(n // f) in digits for f in (1, 60, 3600) if n % f == 0):
                    out[p.name] = n
            elif p.name == "position":
                ordinal = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last"}.get(n, "")
                if str(n) in digits or (ordinal and ordinal in said):
                    out[p.name] = n
            elif str(n) in digits:  # an ID or number the user did not say is never used
                out[p.name] = n
        elif p.kind == "enum":
            v = str(value).strip().lower()
            if p.name == "page":
                route = intents.match_page(intents.normalize(v)) or (v if v in p.choices else None)
                if route:
                    out[p.name] = route
            elif v in p.choices:
                out[p.name] = v
        elif p.kind == "bool":
            out[p.name] = bool(value)
        else:
            v = str(value).strip()
            if v and v.lower() in text.lower():
                out[p.name] = v
    return out
