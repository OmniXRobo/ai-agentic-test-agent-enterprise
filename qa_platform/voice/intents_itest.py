"""23.14.0: voice and typed commands for Security Testing, Red Hat Fuse Testing, ActiveMQ Testing and Email / SMTP.

Every command here only opens a screen (with a fixed filter or tab). Nothing is started, published, consumed or sent
by voice: scans, broker tests and emails start only from the page, after the tester confirms the authorized target.
"""

from __future__ import annotations

import re

from .intents import Intent

_V = r"(?:please\s+)?(?:open|show(?: me)?|go to|take me to|display|list|view|check|see)?\s*(?:the\s+|all\s+|my\s+)?"
_SEV = r"(critical|high|medium|low|info)"


def _open(route: str, query: str = "", note: str = "") -> Intent:
    params = {"route": route}
    if query:
        params["query"] = query
    if note:
        params["note"] = note
    return Intent("open_page", params)


# (pattern, route, query, note) - the first match wins
RULES: list[tuple[str, str, str, str]] = [
    (rf"{_V}{_SEV}\s+(?:severity\s+)?(?:security\s+)?(?:findings|vulnerabilities|issues)", "sec-findings", "severity={0}", ""),
    (rf"{_V}open\s+(?:security\s+)?(?:findings|vulnerabilities)", "sec-findings", "status=open,confirmed", ""),
    (rf"{_V}(?:findings|vulnerabilities)\s+(?:to|waiting for)\s+review", "sec-findings", "status=open", ""),
    (r"(?:please\s+)?(?:start|run|launch|begin|do)\s+(?:a\s+|the\s+)?(?:security|vulnerability|owasp|zap|passive)\s+(?:scan|test|testing|assessment|check)s?",
     "sec-scanning", "", "A scan starts only from Vulnerability Scanning, after you check the authorized target and press "
     "Start. I opened it for you."),
    (r"(?:please\s+)?(?:start|run|launch|begin)\s+(?:a\s+|the\s+)?(?:fuse|route|integration)\s+(?:tests?|testing|run)",
     "fuse-testing", "tab=execution", "Fuse tests start from Test Execution after you choose the approved tests. "
     "I opened it for you."),
    (r"(?:please\s+)?(?:start|run|launch|begin)\s+(?:a\s+|the\s+)?(?:broker|activemq|active mq|message|queue)\s+(?:tests?|testing|run)",
     "activemq-testing", "tab=publish", "Broker tests start from Publish & Verify after you choose the approved tests. "
     "Nothing is published by voice. I opened it for you."),
    (rf"{_V}failed\s+(?:fuse|route|integration)\s+(?:tests?|results)", "fuse-testing", "tab=results", ""),
    (rf"{_V}failed\s+(?:broker|activemq|active mq|message|queue)\s+(?:tests?|results)", "activemq-testing", "tab=results", ""),
    (rf"{_V}(?:fuse\s+)?route\s+monitoring|{_V}(?:fuse\s+)?routes?\s+(?:status|health)", "fuse-testing", "tab=monitoring", ""),
    (rf"{_V}(?:fuse\s+)?routes?(?:\s+discovery)?|{_V}fuse\s+routes", "fuse-testing", "tab=discovery", ""),
    (rf"{_V}route\s+mapping", "fuse-testing", "tab=mapping", ""),
    (rf"{_V}transformation\s+comparison|{_V}compare\s+(?:the\s+)?transformation", "fuse-testing", "tab=compare", ""),
    (rf"{_V}(?:broker|activemq|active mq)\s+(?:monitoring|health|status)|{_V}queue\s+(?:depths?|sizes?)", "activemq-testing",
     "tab=monitoring", ""),
    (rf"{_V}(?:dead[\s-]?letter|dlq|retry)(?:\s+queue)?\s*(?:tests?|testing)?", "activemq-testing", "tab=dlq", ""),
    (rf"{_V}(?:queues?|topics?|destinations)(?:\s+and\s+(?:queues?|topics?))?(?:\s+discovery)?", "activemq-testing",
     "tab=discovery", ""),
    (rf"{_V}consumer\s+verification", "activemq-testing", "tab=consumer", ""),
    (rf"{_V}(?:email|mail)\s+(?:send\s+)?history|{_V}sent\s+emails", "email-settings", "", ""),
]
_COMPILED = [(re.compile(rf"{p}(?:\s+please)?"), r, q, n) for p, r, q, n in RULES]


def itest_intent(t: str) -> Intent | None:
    """t is the normalized sentence (lower case, numbers as digits)."""
    for rx, route, query, note in _COMPILED:
        m = rx.fullmatch(t)
        if m:
            groups = [g for g in m.groups() if g]
            return _open(route, query.format(*groups) if "{0}" in query and groups else query.replace("{0}", ""), note)
    return None
