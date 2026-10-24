"""Intent analysis for voice and typed commands.

Rules first: a fixed grammar maps a sentence to one supported command and its
parameters. Only when no rule matches may the On-Premise AI be asked to choose
from the same list (see service.py); nothing outside this list is ever executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

COMMANDS = (
    "help", "confirm", "cancel", "status", "open_page", "open_work_item", "generate_test_cases",
    "generate_automation", "run_test_case", "run_all_tests", "start_recording", "run_performance",
    "stop_run", "show_failed", "show_performance_report", "analyze_failure", "create_defects",
)

# route, label, spoken aliases (longest alias wins)
PAGES: list[tuple[str, str, tuple[str, ...]]] = [
    ("dashboard", "Dashboard", ("dashboard", "home")),
    ("workflow", "Agent Workflow", ("agent workflow", "workflow", "agents")),
    ("notifications", "Notifications", ("notifications", "alerts")),
    ("history", "Execution History", ("execution history", "history", "runs")),
    ("tfs-explorer", "TFS Explorer", ("tfs explorer", "explorer", "work items")),
    ("user-stories", "User Stories", ("user stories", "stories")),
    ("test-design", "AI Test Design", ("ai test design", "test design")),
    ("acceptance-criteria", "Acceptance Criteria", ("acceptance criteria",)),
    ("test-plans", "Test Plans", ("test plans",)),
    ("test-suites", "Test Suites", ("test suites", "suites")),
    ("test-cases", "Test Cases", ("test cases",)),
    ("test-data", "Test Data", ("test data",)),
    ("ai-automation", "AI-Driven Automation", ("ai driven automation", "ai automation")),
    ("guided-automation", "Guided Browser Automation", ("guided browser automation", "guided automation",
                                                        "browser automation", "recorder", "browser recording")),
    ("discovery", "Application Discovery & Update", ("application discovery", "discovery")),
    ("hybrid-automation", "Hybrid Test Automation", ("hybrid test automation", "hybrid automation")),
    ("perf-plans", "Performance Test Plans", ("performance test plans", "performance plans", "load test plans")),
    ("perf-load", "Load Tests", ("load tests",)),
    ("perf-stress", "Stress Tests", ("stress tests",)),
    ("perf-spike", "Spike Tests", ("spike tests",)),
    ("perf-soak", "Soak Tests", ("soak tests",)),
    ("perf-results", "Performance Results", ("performance results", "load test results")),
    ("perf-defects", "Performance Defects", ("performance defects",)),
    ("defects", "Defects", ("defects", "bugs")),
    ("defect-review", "Defect Review", ("defect review", "defect drafts")),
    ("settings", "Preferences", ("preferences", "settings")),
    ("tfs-settings", "TFS Connection", ("tfs connection settings", "tfs connection", "tfs settings",
                                        "azure devops settings")),
    ("ai-settings", "On-Premise AI", ("on premise ai settings", "on premise ai", "ai settings", "llm settings")),
    ("app-credentials", "Application Credentials", ("application credentials", "credentials")),
    ("voice", "Voice Commands", ("voice commands", "voice settings", "voice")),
]
PAGE_LABELS = {route: label for route, label, _ in PAGES}

_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_SCALES = {"hundred": 100, "thousand": 1000}


def words_to_numbers(text: str) -> str:
    """'a hundred users for five minutes' -> '100 users for 5 minutes'."""
    out: list[str] = []
    total = current = 0
    pending = False
    for raw in text.split():
        word = raw.strip(",.").lower()
        if word in _UNITS or word in _TENS:
            current += _UNITS.get(word, 0) + _TENS.get(word, 0)
            pending = True
        elif word in _SCALES and (pending or (out and out[-1] in ("a", "one"))):
            if not pending and out and out[-1] in ("a", "one"):
                out.pop()
                current = 1
            current = max(current, 1) * _SCALES[word]
            if _SCALES[word] >= 1000:
                total, current = total + current, 0
            pending = True
        elif word == "and" and pending:
            continue
        else:
            if pending:
                out.append(str(total + current))
                total = current = 0
                pending = False
            out.append(raw)
    if pending:
        out.append(str(total + current))
    return " ".join(out)


# How offline recognizers tend to hear "JMeter".
_JMETER = re.compile(r"\b(?:j|jay|gee|g)\s?\.?\s?meters?\b")


def normalize(text: str) -> str:
    text = text.lower().replace("-", " ").replace("’", "'")
    text = _JMETER.sub("jmeter", text)
    text = re.sub(r"[^\w\s#'.]", " ", text)
    text = re.sub(r"\bnumber\b|#", " ", text)
    return re.sub(r"\s+", " ", words_to_numbers(text)).strip(" .")


@dataclass
class Intent:
    command: str
    params: dict[str, Any] = field(default_factory=dict)
    via: str = "rules"  # rules | ai | follow_up


_ID = r"(\d{1,9})"


def _duration(t: str) -> int | None:
    m = re.search(r"\b(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", t)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * (3600 if unit.startswith(("hour", "hr")) else 60 if unit.startswith("min") else 1)


def _perf_target(t: str) -> str | None:
    t = re.sub(r"\b\d+\s*(virtual\s+)?users?\b|\b\d+\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", " ", t)
    m = re.search(r"\b(?:for|on|against|of)\s+(?:the\s+)?([a-z0-9][\w .'/-]*?)(?:\s+(?:with|for|using|at|plan|page|test)\b|$)", t)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(1)).strip(" .")
    return None if name in ("", "a", "an", "it", "this", "that") else name


def parse(text: str) -> Intent | None:
    t = normalize(text)
    if not t:
        return None
    if re.fullmatch(r"(yes|yeah|yep|confirm(ed)?|go ahead|do it|proceed|ok(ay)?|sure)( please)?", t):
        return Intent("confirm")
    if re.fullmatch(r"(no|nope|cancel|never mind|nevermind|forget it|don't)( please| that| it)?", t):
        return Intent("cancel")
    if re.search(r"\b(help|what can (you|i) (do|say)|which commands)\b", t):
        return Intent("help")
    if re.search(r"\b(stop|abort|halt|cancel)\b.*\b(test|run|load|jmeter|execution|playback|everything|it)\b", t):
        return Intent("stop_run")

    m = re.search(r"\b(open|show|display|go to|bring up)\b.*?\b(user story|story|test case|defect|bug|work item|issue)\s+" + _ID, t)
    if m:
        kind = {"story": "user story", "bug": "defect", "issue": "defect"}.get(m.group(2), m.group(2))
        return Intent("open_work_item", {"kind": kind, "id": int(m.group(3))})

    if re.search(r"\b(generate|create|design|write|make|prepare)\b.*\btest ?cases?\b", t) and not re.search(r"\bautomat", t):
        m = re.search(r"\b(?:user story|story)\s+" + _ID, t)
        return Intent("generate_test_cases", {"user_story_id": int(m.group(1))} if m else {})

    if re.search(r"\b(generate|create|build|write)\b.*\b(automation|automated test|script)\b", t):
        p: dict[str, Any] = {}
        m = re.search(r"\btest case\s+" + _ID, t)
        if m:
            p["test_case_id"] = int(m.group(1))
        else:
            m = re.search(r"\bfor (?:the )?([\w .'-]+?)(?: test| test case| scenario)?$", t)
            if m and not re.fullmatch(r"(it|this|that|the same|current|this test|that test)( test case)?|test case", m.group(1)):
                p["name"] = m.group(1).strip()
        return Intent("generate_automation", p)

    if re.search(r"\b(run|execute|start|play)\b.*\ball\b.*\b(automated |automation |functional )?(tests?|scripts?)\b", t):
        return Intent("run_all_tests")

    if re.search(r"\b(load|stress|spike|soak|performance)\b.*\btest(s|ing)?\b|\bjmeter\b", t) and \
            re.search(r"\b(run|start|execute|launch|begin|do|perform|create|kick off)\b", t) and \
            not re.search(r"\b(defects?|bugs?|report|results?)\b", t):
        p = {}
        kind = re.search(r"\b(load|stress|spike|soak)\b", t)
        if kind:
            p["kind"] = kind.group(1)
        users = re.search(r"\b(\d+)\s*(?:virtual\s+|concurrent\s+)?users?\b", t)
        if users:
            p["users"] = int(users.group(1))
        dur = _duration(t)
        if dur:
            p["duration_s"] = dur
        target = _perf_target(t)
        if target and target not in ("jmeter", "performance", "load", "stress", "spike", "soak"):
            p["name"] = target
        return Intent("run_performance", p)

    m = re.search(r"\b(run|play|execute|replay|start)\b.*?\btest case\s+" + _ID, t)
    if m:
        return Intent("run_test_case", {"test_case_id": int(m.group(2)), "play": m.group(1) in ("play", "replay")})
    m = re.search(r"\b(run|play|execute|replay)\b\s+(?:the\s+)?([\w .'-]+?)\s+(?:test|script|automation)$", t)
    if m and not re.fullmatch(r"(it|this|that|the|same|current|automated|all)( test case)?", m.group(2)):
        return Intent("run_test_case", {"name": m.group(2).strip(), "play": m.group(1) in ("play", "replay")})
    if re.search(r"\b(run|play|execute|replay)\b\s+(it|this|that|the test|this test|that test|the test case|again)\b", t):
        return Intent("run_test_case", {"play": t.startswith(("play", "replay"))})

    if re.search(r"\b(start|begin|open|new)\b.*\brecord(ing)?\b|\brecord (a |an )?(new )?(test|script)\b", t):
        return Intent("start_recording")
    if re.search(r"\b(show|list|open|display|which)\b.*\bfail(ed|ing|ures?)\b", t) and "defect" not in t:
        return Intent("show_failed")
    if re.search(r"\b(show|open|display)\b.*\b(performance|load test|jmeter)\b.*\b(report|results?)\b", t):
        return Intent("show_performance_report")
    if re.search(r"\banaly[sz]e\b|\bwhy did\b.*\bfail\b", t):
        return Intent("analyze_failure")
    if re.search(r"\b(create|file|raise|log|open)\b.*\b(defects?|bugs?)\b", t):
        return Intent("create_defects", {"source": "performance" if "performance" in t else "functional"})
    if re.search(r"\b(status|what('s| is) (running|happening)|how many|summary|today)\b", t):
        return Intent("status")

    m = re.search(r"\b(open|show|go to|navigate to|take me to|display)\b\s+(?:the\s+|my\s+)?(.+)$", t)
    if m:
        page = match_page(m.group(2))
        if page:
            return Intent("open_page", {"route": page})
    return None


def match_page(phrase: str) -> str | None:
    phrase = re.sub(r"\b(page|screen|tab|view|please)\b", " ", phrase)
    phrase = re.sub(r"\s+", " ", phrase).strip()
    best: tuple[int, str] | None = None
    for route, _label, aliases in PAGES:
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", phrase) and (best is None or len(alias) > best[0]):
                best = (len(alias), route)
    return best[1] if best else None
