"""Intent analysis for voice and typed commands.

Rules first: a fixed grammar maps a sentence to one supported command and its
parameters. Only when no rule matches may the On-Premise AI be asked to choose
from the same list (see service.py); nothing outside this list is ever executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# The full list, with parameters and which commands need confirmation, is in registry.py.

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
    ("voice", "Voice & Command Agent", ("voice and command agent", "voice commands", "voice settings", "voice")),
    ("command-center", "AI Voice Command Center", ("voice command center", "command center", "mission control")),
    ("reports", "Reports", ("reports", "report list")),
    ("analytics", "Analytics", ("analytics", "quality analytics", "trends")),
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

    if re.fullmatch(r"(please )?(go |take me )?back( please)?|(go to |open )?(the )?previous (page|screen)", t):
        return Intent("go_back")
    m = re.fullmatch(r"(?:select|choose|pick|open|take|use)\s+(?:the\s+)?(first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th)?)"
                     r"(?:\s+(?:one|1|item|result|row|entry|test|test case|story|user story))?", t)
    if m:
        word = m.group(1)
        pos = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1}.get(word)
        return Intent("select_item", {"position": pos if pos else int(re.sub(r"\D", "", word))})
    if re.search(r"\b(stop|finish|end|save|close)\b\s+(?:the\s+)?(manual\s+|browser\s+)?record(ing)?\b", t):
        return Intent("stop_recording")
    m = re.search(r"\b(test|check|verify|diagnose|ping)\b\s+(?:the\s+)?(?:(online|offline|local|cloud)\s+)?(tfs|azure devops)\b", t)
    if m:
        mode = {"local": "offline", "cloud": "online"}.get(m.group(2) or "", m.group(2))
        return Intent("test_tfs", {"mode": mode} if mode else {})
    m = re.search(r"\b(switch|change|move)\b.*?\b(online|offline|local|cloud)\b.*\btfs\b|\buse\s+(?:the\s+)?(online|offline|local|cloud)\s+tfs\b", t)
    if m:
        mode = m.group(2) or m.group(3)
        return Intent("switch_tfs", {"mode": {"local": "offline", "cloud": "online"}.get(mode, mode)})
    if re.search(r"\b(test|check|verify|diagnose|ping)\b\s+(?:the\s+)?(?:local\s+|on premise\s+)?(ai|llm|language model|model|queen|qwen)\b", t):
        return Intent("test_ai")
    if re.search(r"\b(read|any|show|check)\b.*\b(notifications|alerts)\b", t) and not re.search(r"\b(open|go to)\b", t):
        return Intent("read_notifications")
    if re.search(r"\b(create|generate|make|prepare|build)\b.*\btest (plan|suites?)\b", t):
        m = re.search(r"\b(?:user story|story)\s+" + _ID, t)
        return Intent("generate_test_plan", {"user_story_id": int(m.group(1))} if m else {})
    if re.search(r"\b(save|publish|push|write|upload)\b.*\btest ?cases?\b", t) and not re.search(r"\bdata\b", t):
        m = re.search(r"\b(?:user story|story)\s+" + _ID, t)
        return Intent("save_test_cases", {"user_story_id": int(m.group(1))} if m else {})
    m = re.search(r"\b(create|generate|make|prepare|build|write|save|push|delete|remove|clear)\b.*\btest data\b", t)
    if m:
        verb = m.group(1)
        p: dict[str, Any] = {}
        tc = re.search(r"\btest case\s+" + _ID, t)
        if tc:
            p["test_case_id"] = int(tc.group(1))
        if verb in ("delete", "remove", "clear"):
            return Intent("delete_test_data", p)
        if verb in ("write", "save", "push") and re.search(r"\b(tfs|azure devops)\b", t):
            return Intent("write_test_data", p)
        return Intent("create_test_data", p)
    m = re.search(r"\b(open|show|edit|display)\b\s+(?:me\s+)?(?:the\s+)?(script editor|test script|script|flow logic|recorded steps|"
                  r"steps editor|script history|script credentials)\b", t)
    if m:
        tab = {"script editor": "edit", "test script": "view", "script": "view", "flow logic": "flow",
               "recorded steps": "edit", "steps editor": "edit", "script history": "history",
               "script credentials": "credentials"}[m.group(2)]
        p = {"tab": "edit" if m.group(1) == "edit" else tab}
        tc = re.search(r"\btest case\s+" + _ID, t)
        if tc:
            p["test_case_id"] = int(tc.group(1))
        return Intent("open_script", p)
    if re.search(r"\b(archive|delete|remove)\b\s+(?:the\s+)?(script|automation|automated test)\b", t):
        tc = re.search(r"\btest case\s+" + _ID, t)
        return Intent("archive_script", {"test_case_id": int(tc.group(1))} if tc else {})
    m = re.search(r"\b(open|show|display|go to|bring up|load|select|pick|get)\b.*?\b(user story|story|test case|defect|bug|work item|issue)\s+" + _ID, t)
    if m:
        kind = {"story": "user story", "bug": "defect", "issue": "defect"}.get(m.group(2), m.group(2))
        return Intent("open_work_item", {"kind": kind, "id": int(m.group(3))})

    if re.search(r"\b(generate|create|design|write|make|prepare)\b.*\btest ?cases?\b", t) and not re.search(r"\bautomat", t):
        m = re.search(r"\b(?:user story|story)\s+" + _ID, t)
        return Intent("generate_test_cases", {"user_story_id": int(m.group(1))} if m else {})

    m = re.search(r"\b(?:user story|story)\s+" + _ID, t)
    if m and re.search(r"\b(automate|automation|automated|scripts?)\b", t) and \
            re.search(r"\b(automate|generate|create|build|write|make|discover|read)\b", t):
        return Intent("automate_user_story", {"user_story_id": int(m.group(1))})
    if re.fullmatch(r"automate (?:the |this |that )?(?:user )?story", t):
        return Intent("automate_user_story", {})

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

    m = re.search(r"\b(start|begin|open|new)\b.*\brecord(ing)?\b|\brecord (a |an )?(new )?(test|script)\b", t)
    if m:
        p = {}
        sc = re.search(r"\brecord(?:ing)?\s+(?:for|of)\s+(?:the\s+)?([\w .'-]+?)(?:\s+scenario)?$", t)
        if sc:
            p["scenario"] = sc.group(1).strip()
        return Intent("start_recording", p)
    m = re.search(r"\b(show|list|find|search|which)\b\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?(?:open\s+)?(user stories|stories|test cases|defects|bugs)\b(.*)$", t)
    if m and m.group(3).strip():
        rest = m.group(3)
        kind = {"stories": "user story", "user stories": "user story", "test cases": "test case", "bugs": "defect",
                "defects": "defect"}[m.group(2)]
        p = {"kind": kind}
        sp = re.search(r"\b(?:in|for|from)\s+(?:the\s+)?((?:sprint|iteration)\s+[\w.-]+)", rest)
        if sp:
            p["sprint"] = sp.group(1)
        st = re.search(r"\b(?:with\s+)?(?:state|status)\s+([\w -]+?)(?:\s+(?:in|for|about)\b|$)", rest)
        if st:
            p["state"] = st.group(1).strip()
        tx = re.search(r"\b(?:about|containing|named|called|matching|with title)\s+(.+)$", rest)
        if tx:
            p["text"] = tx.group(1).strip()
        if len(p) > 1:
            return Intent("search_work_items", p)
    if re.search(r"\b(generate|create|build|make|prepare|produce)\b.*\breport\b", t) and "defect" not in t:
        scope = "performance" if re.search(r"\b(performance|load|jmeter)\b", t) else \
            "functional" if "functional" in t else "both"
        p = {"scope": scope}
        d = re.search(r"\b(?:last|past)\s+(\d+)\s+days?\b", t)
        if d:
            p["days"] = int(d.group(1))
        elif re.search(r"\b(last|past|this) week\b", t):
            p["days"] = 7
        return Intent("generate_report", p)
    if re.search(r"\b(show|open|display|view)\b\s+(?:me\s+)?(?:the\s+)?(?:latest\s+|last\s+)?(?:qa\s+|functional\s+)?report\b", t):
        return Intent("show_report")
    m = re.search(r"\b(show|open|display|view)\b\s+(?:me\s+)?(?:the\s+)?(?:latest\s+|last\s+)?(evidence|screenshots?|logs?|result|results)\b", t)
    if m and "performance" not in t:
        what = {"screenshot": "evidence", "screenshots": "evidence", "log": "logs", "results": "result"}.get(m.group(2), m.group(2))
        return Intent("show_result", {"what": what})
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
    return browser_action(t)


_DAYS = {"today": 0, "tomorrow": 1, "yesterday": -1, "previous day": -1, "next day": 1, "day after tomorrow": 2,
         "day before yesterday": -2}


def browser_action(t: str) -> Intent | None:
    """Contextual browser commands, valid while a recording is active (checked by the service)."""
    m = re.search(r"\b(?:select|choose|pick|set|enter)\b\s+(?:the\s+)?(?:date\s+(?:to|as)\s+)?(day after tomorrow|day before yesterday|"
                  r"today|tomorrow|yesterday|previous day|next day)\b", t)
    field = re.search(r"\b(?:in|for|on|into)\s+(?:the\s+)?(.+?)(?:\s+field|\s+box)?$", t[m.end():] if m else "")
    if m:
        return Intent("browser_action", {"action": "select_date", "days": _DAYS[m.group(1)],
                                         **({"target": field.group(1)} if field else {})})
    m = re.search(r"\b(?:select|choose|pick)\b.*?\b(\d+)\s+days?\s+(from now|later|ahead|ago|before|back)\b|\bin\s+(\d+)\s+days\b", t)
    if m:
        n = int(m.group(1) or m.group(3))
        field = re.search(r"\b(?:in|for|on|into)\s+(?:the\s+)?(.+?)(?:\s+field|\s+box)?$", t[m.end():])
        return Intent("browser_action", {"action": "select_date", "days": -n if m.group(2) in ("ago", "before", "back") else n,
                                         **({"target": field.group(1)} if field else {})})
    if re.search(r"\b(open|show|click)\b\s+(?:the\s+|this\s+)?(calendar|date picker|datepicker)\b", t):
        return Intent("browser_action", {"action": "open_calendar", "target": "calendar"})
    if re.search(r"\b(open|expand|click)\b\s+(?:the\s+|this\s+)?(drop ?down|list box|select box)\b", t):
        return Intent("browser_action", {"action": "click", "target": "dropdown"})
    if re.search(r"\b(go to|open|click)\b\s+(?:the\s+)?next page\b|^next page$", t):
        return Intent("browser_action", {"action": "next_page"})
    m = re.fullmatch(r"scroll\s+(down|up)(?:\s+a bit|\s+more)?", t)
    if m:
        return Intent("browser_action", {"action": "scroll", "value": m.group(1)})
    m = re.fullmatch(r"(?:enter|type|fill in|fill)\s+(.+?)\s+(?:in|into|on)\s+(?:the\s+)?(.+?)(?:\s+field|\s+box)?", t)
    if m:
        return Intent("browser_action", {"action": "fill", "value": m.group(1), "target": m.group(2)})
    if re.fullmatch(r"(?:enter|type)\s+(?:this|that|the)\s+value", t):
        return Intent("browser_action", {"action": "fill"})
    m = re.fullmatch(r"(?:select|choose|pick)\s+(.+?)(?:\s+(?:in|from)\s+(?:the\s+)?(.+?))?", t)
    if m:
        return Intent("browser_action", {"action": "select", "value": m.group(1), **({"target": m.group(2)} if m.group(2) else {})})
    m = re.fullmatch(r"(?:click|press|tap|hit)\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+button|\s+link)?", t)
    if m:
        return Intent("browser_action", {"action": "click", "target": m.group(1)})
    return None


# Nouns that show a sentence is about QA work, so an unmatched sentence is unclear rather than unsupported.
QA_NOUNS = {
    "user story": "Which user story, and what should I do with it: open it, generate test cases, or create the test plan?",
    "test case": "Which test case, and what should I do: open it, run it, generate automation or create test data?",
    "test data": "For which test case should I create, write or delete test data?",
    "automation": "Which test case should I generate or run automation for?",
    "script": "Which test case's script should I open, run or archive?",
    "record": "Should I start or stop a browser recording?",
    "report": "Should I generate a new report, or open the latest one?",
    "tfs": "Should I test the TFS connection, switch between online and offline, or open TFS settings?",
    "defect": "Should I analyze the failure, create defects from the drafts, or open Defect Review?",
    "test": "Which test should I run, and should it play visibly?",
    "page": "Which page should I open?",
}


def clarify_question(text: str) -> str | None:
    t = normalize(text)
    for noun, question in QA_NOUNS.items():
        if re.search(rf"\b{noun}", t):
            return question
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
