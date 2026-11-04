"""Grammar for AI-Driven Automation by voice (23.3.3, milestone 6): run settings, generating Python Playwright
scripts for the current plan, suite or test cases, validating, running, reading a failure and regenerating."""

from __future__ import annotations

import re

from .intents_plans import SUITE_ALIASES

BROWSERS = {"google chrome": "chrome", "chrome": "chrome", "microsoft edge": "msedge", "edge": "msedge",
            "chromium": "chromium", "firefox": "firefox", "mozilla firefox": "firefox", "safari": "webkit",
            "webkit": "webkit"}
_BROWSER = r"(google chrome|microsoft edge|mozilla firefox|chrome|edge|chromium|firefox|safari|webkit)"
# Frameworks and languages this application does not generate; asked for by name, the agent says so.
OTHER_TOOLS = r"(java|javascript|typescript|c sharp|c#|csharp|selenium|cypress|webdriver|robot framework|ruby|katalon|appium)"
_AUTOMATION_WORD = r"(automation|automated|scripts?|python|playwright|code)"
_NOT_AUTOMATION = r"\b(test data|report|jmeter|performance|load test|stress test|defects?|test plan for|test cases? from)\b"
_ON = r"(enable|turn on|switch on|keep|always)"
_OFF = r"(disable|turn off|switch off|stop|no|don't|do not|without)"


def _number(t: str, unit_re: str) -> tuple[float, str] | None:
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*" + unit_re, t)
    return (float(m.group(1)), m.group(2)) if m else None


def _suite(t: str) -> str | None:
    for key, aliases in SUITE_ALIASES.items():
        if any(re.search(rf"\b{re.escape(a)}\b", t) for a in aliases):
            return key
    return None


def automation_intent(t: str):  # -> Intent | None
    """``t`` is normalized text. Returns None for anything that is not about automation scripts or their runs."""
    from .intents import Intent  # imported lazily to avoid a cycle

    # ---- run settings -----------------------------------------------------------------------
    if re.search(r"\b(what|which|read|tell me|show)\b.*\b(automation|run|execution|playwright) (settings|parameters|options|configuration)\b", t):
        return Intent("automation_settings", {})
    m = re.search(rf"\b(change|switch|set|use|choose|select|run (?:it|them|the tests?|this test)? ?(?:in|on|with))\b.*?\b{_BROWSER}\b", t)
    if m and not re.search(r"\b(open|go to|launch|start)\b", t) and re.search(r"\b(browser|use|switch|change|run|set)\b", t):
        return Intent("automation_settings", {"browser": BROWSERS[m.group(2)]})
    if re.search(r"\btime ?outs?\b", t) and re.search(r"\b(set|change|make|increase|decrease|raise|lower|use)\b", t):
        n = _number(t, r"(seconds?|secs?|s|minutes?|mins?|milliseconds?|ms)\b")
        if n:
            value, unit = n
            seconds = value / 1000 if unit.startswith("milli") or unit == "ms" else value * 60 if unit.startswith("min") else value
            return Intent("automation_settings", {"timeout_seconds": seconds})
        return Intent("automation_settings", {"timeout_seconds": None})
    if re.search(r"\bretr(y|ies|ied)\b", t) and re.search(r"\b(set|enable|turn on|use|allow|add|retry failed|\d)\b", t):
        n = _number(t, r"(times?|retr(?:y|ies))?")
        return Intent("automation_settings", {"retries": int(n[0]) if n else None})
    m = re.search(r"\bscreenshots?\b", t)
    if m and re.search(rf"\b({_ON}|{_OFF})\b", t):
        on = not re.search(rf"\b{_OFF}\b", t)
        if re.search(r"\b(on|after|for|of)\s+(a\s+|each\s+|every\s+)?(fail(ure|ures|ed)?|errors?)\b|\bwhen (it|a test|a step) fails\b", t):
            return Intent("automation_settings", {"failure_screenshots": on})
        return Intent("automation_settings", {"step_screenshots": on})
    m = re.search(r"\b(video|videos|recording of the run|trace|traces|tracing)\b", t)
    if m and re.search(rf"\b({_ON}|{_OFF}|record|save)\b", t) and not re.search(r"\b(start|stop) (the )?(manual |browser )?record", t):
        key = "video" if m.group(1).startswith(("video", "recording")) else "trace"
        return Intent("automation_settings", {key: not re.search(rf"\b{_OFF}\b", t)})
    if re.fullmatch(r"(?:please\s+)?(?:run|use|switch to|set)\s+(?:it\s+|the tests?\s+)?(?:in\s+)?(?:the\s+)?(headless|background|visible|headed|show(?:ing)? the browser)(?:\s+(?:mode|browser))?(?:\s+please)?", t) \
            or re.search(r"\b(headless mode|run headless|without showing the browser|show the browser while (it|they) runs?)\b", t):
        return Intent("automation_settings", {"headless": not re.search(r"\b(visible|headed|show)", t)})
    if re.search(rf"\b(generate|create|write|use|choose|select|make|prepare|build)\b.*\b{OTHER_TOOLS}\b", t) and \
            re.search(rf"\b{_AUTOMATION_WORD}\b|\b{OTHER_TOOLS}\b", t):
        return Intent("automation_settings", {"framework": re.search(rf"\b{OTHER_TOOLS}\b", t).group(1)})
    if re.fullmatch(r"(?:please\s+)?(?:choose|use|select|pick)\s+python(?:\s+(?:with|and)\s+playwright)?(?:\s+please)?", t):
        return Intent("automation_settings", {"framework": "python"})

    # ---- generate scripts for the current plan, suite or test cases -------------------------------------
    if re.search(r"\b(generate|create|prepare|build|write|make)\b", t) and re.search(rf"\b{_AUTOMATION_WORD}\b", t) \
            and not re.search(_NOT_AUTOMATION, t) and not re.search(r"\b(generate|create) (the |some )?test cases\b", t) \
            and not re.search(r"\b(test case|user story|story)\s+\d+", t):
        p: dict = {}
        if re.search(r"\b(these|those|the saved|saved|selected|the new|new) test cases\b|\bfor (these|them|those)\b", t):
            p["which"] = "these"
        elif re.search(r"\bsuites?\b", t) or _suite(t):
            p["which"] = "suite"
            if _suite(t):
                p["suite"] = _suite(t)
        elif re.search(r"\b(test )?plan\b", t):
            p["which"] = "plan"
        if re.search(r"\bopen (the )?(ai driven automation|automation|script studio)\b", t):
            p["open"] = True
        return Intent("automate_selection", p)

    # ---- one script: validate, show, run, failure, regenerate ---------------------------------------
    tc = re.search(r"\btest case (\d+)\b", t)
    base = {"test_case_id": int(tc.group(1))} if tc else {}
    if re.search(r"\b(validate|check|verify|lint|static check)\b.*\b(script|code|automation|test)\b", t) and \
            not re.search(r"\b(connection|tfs|ai|server)\b", t):
        return Intent("validate_script", base)
    if re.search(r"\b(show|display|let me see|open|read)\b.*\b(the |its |generated )?(code|python|source)\b", t) or \
            re.fullmatch(r"(?:please\s+)?(?:show|display|open)\s+(?:me\s+)?(?:the|this|that)\s+script", t):
        return Intent("open_script", {**base, "tab": "view"})
    m = re.search(r"\b(run|execute|play|start)\b.*?\b(?:in|on|against)\s+(?:the\s+)?([a-z]+(?:\s[a-z]+)?)\s+(?:environment|env)\b", t)
    if m:
        return Intent("run_test_case", {**base, "environment": m.group(2), "play": m.group(1) == "play"})
    if re.search(r"\b(show|tell|read|what)\b.*\b(the |that )?(failure|error|what failed)\b|\bwhy did (it|the test|this test) fail\b", t) and \
            not re.search(r"\b(failed tests|all failures|failures)\b", t):
        return Intent("show_failure", base)
    if re.search(r"\b(fix|heal|repair|update|correct)\b.*\b(locators?|selectors?|elements?)\b", t) or \
            re.search(r"\bregenerate\b", t) and re.search(r"\b(it|script|automation|test|code)\b|^regenerate$", t):
        return Intent("regenerate_script", base)
    return None
