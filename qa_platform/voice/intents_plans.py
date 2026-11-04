"""Grammar for test plans, test suites and follow-ups that refer to the current item (23.3.1).

Called by intents.parse() on the normalized sentence before the older rules, and returns None when the
sentence is not about test plans or suites, so every earlier command keeps its meaning ("create the test plan"
for the current user story, "select the second one", "open test plans").
"""

from __future__ import annotations

import re
from typing import Any

from ..design import testing_types

# Spoken names of the 11 testing types (the suites a plan can have). Longest alias wins.
SUITE_ALIASES: dict[str, tuple[str, ...]] = {
    "functional": ("functional testing", "functional"),
    "smoke": ("smoke testing", "smoke"),
    "sanity": ("sanity testing", "sanity"),
    "regression": ("regression testing", "regression"),
    "integration": ("integration testing", "integration"),
    "end_to_end": ("end to end testing", "end to end", "e2e", "end 2 end"),
    "api": ("api testing", "api", "a p i"),
    "database": ("database testing", "database", "db testing"),
    "ui_usability": ("ui and usability testing", "ui and usability", "usability testing", "usability", "ui testing",
                     "user interface", "ui"),
    "performance": ("performance testing", "performance"),
    "security": ("security testing", "security"),
}
assert set(SUITE_ALIASES) == set(testing_types.BY_KEY), "every testing type needs spoken aliases"

_ALIAS_RE = re.compile(r"\b(" + "|".join(sorted({re.escape(a) for v in SUITE_ALIASES.values() for a in v}, key=len, reverse=True)) + r")\b")
_ALIAS_KEY = {a: k for k, v in SUITE_ALIASES.items() for a in v}

_ON = r"select|check|tick|choose|pick|add|include|enable|keep"
_OFF = r"uncheck|un check|untick|deselect|de select|unselect|remove|exclude|drop|disable|clear|skip|leave out|but not|without|except"
_VERB_RE = re.compile(rf"\b({_OFF}|{_ON})\b")
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "last": -1}
_REF = r"(?:it|that|this|that one|this one|the one i just created|the plan i just created|the test plan i just created|" \
       r"the new (?:test )?plan|my new (?:test )?plan|the current (?:test )?plan|the selected (?:test )?plan)"


def suite_keys(text: str) -> list[str]:
    """Testing types named in a phrase, in the order they were said."""
    return list(dict.fromkeys(_ALIAS_KEY[m.group(1)] for m in _ALIAS_RE.finditer(text)))


def suite_selection(t: str) -> dict[str, list[str]] | None:
    """'select functional, integration and regression testing, and uncheck performance' -> check/uncheck lists."""
    verbs = list(_VERB_RE.finditer(t))
    if not verbs or not _ALIAS_RE.search(t):
        return None
    check: list[str] = []
    uncheck: list[str] = []
    for i, v in enumerate(verbs):
        end = verbs[i + 1].start() if i + 1 < len(verbs) else len(t)
        keys = suite_keys(t[v.end():end])
        target = uncheck if re.fullmatch(_OFF, v.group(1)) else check
        target.extend(keys)
    check = [k for k in dict.fromkeys(check) if k not in uncheck]
    uncheck = list(dict.fromkeys(uncheck))
    if not check and not uncheck:
        return None
    return {"check": check, "uncheck": uncheck}


def _sprint(rest: str) -> str | None:
    m = re.search(r"\b(sprint|iteration)\s+([a-z0-9][\w.]*)", rest)
    return f"{m.group(1)} {m.group(2)}" if m else None


def plan_intent(t: str):  # -> Intent | None (imported lazily to avoid a cycle)
    from .intents import Intent

    # ---- test data ("generate test data for test cases 105, 106 and 107", "generate five more combinations") ------
    data = re.search(r"\b(?:generate|create|make|build|prepare|produce|give me)\b.*?\b(?:test data|combinations?|data rows?|data sets?)\b", t)
    if data and not re.search(r"\b(?:write|save|push|delete|remove|clear)\b", t):
        p: dict[str, Any] = {}
        ids = re.search(r"\btest cases?\s+((?:\d{1,9}(?:\s+|$)(?:and\s+)?)+)", t + " ")
        if ids:
            p["test_case_ids"] = [int(x) for x in re.findall(r"\d{1,9}", ids.group(1))]
        elif re.search(r"\b(?:these|those|the saved|the generated|the new)\s+test cases\b", t):
            p["which"] = "these"
        n = re.search(r"\b(\d{1,2})\s+(?:more\s+)?(?:combinations?|rows?|data sets?|records?)\b", t)
        if n:
            p["rows"] = int(n.group(1))
        only = re.search(r"\b(?:only\s+)?(negative|positive|boundary|invalid|valid|edge)\b(?=.*\b(?:data|combinations?|rows?)\b)", t)
        if only:
            p["only"] = only.group(1)
        return Intent("generate_test_data", p)

    # ---- test cases ("generate test cases for Sprint 25 using the acceptance criteria", "now generate test cases") --
    gen = re.search(r"\b(?:generate|create|design|write|make|prepare|build)\b.*?\btest ?cases?\b", t)
    if gen and not re.search(r"\bautomat|\bscripts?\b|\b(?:user story|story)\s+\d|\btest data\b", t):
        p = {}
        sprint = _sprint(t)
        if sprint:
            p["sprint"] = sprint
        return Intent("generate_suite_cases", p)
    m = re.search(r"\b(?:explain|describe|read|summari[sz]e)\b\s+(?:me\s+)?(?:this|that|the|the selected|the current)?\s*test case(?:\s+(\d{1,9}))?\b", t)
    if m:
        return Intent("explain_test_case", {"test_case_id": int(m.group(1))} if m.group(1) else {})

    # ---- create a test plan ("create a new test plan for Sprint 25", "create a test plan called Checkout") --------
    m = re.search(r"\b(?:create|make|add|set up|setup|prepare|build|generate|start)\b\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?(new\s+)?test plan\b(.*)$", t)
    if m and not re.search(r"\b(?:user story|story)\s+\d", t):
        rest = m.group(2)
        p: dict[str, Any] = {}
        sprint = _sprint(rest)
        if sprint:
            p["sprint"] = sprint
        nm = re.search(r"\b(?:called|named|with the name|titled)\s+(.+?)(?:\s+(?:for|in|with)\s+(?:sprint|iteration)\b.*)?$", rest)
        if nm:
            p["name"] = nm.group(1).strip()
        if re.search(r"\bdefault suites?\b|\bwith (?:the )?suites\b", rest):
            p["default_suites"] = True
        if p or m.group(1):
            return Intent("create_test_plan", p)
        return None  # "create the test plan": the user-story command

    # ---- change a test plan (asks for confirmation) ----------------------------------------------------------------
    m = re.search(r"\brename\b\s+(?:the\s+|this\s+|my\s+)?(?:test\s+)?plan\s+(?:to|as)\s+(.+)$", t)
    if m:
        return Intent("update_test_plan", {"name": m.group(1).strip()})
    m = re.search(r"\b(?:set|mark|make|change)\b\s+(?:the\s+|this\s+)?(?:test\s+)?plan(?:'s)?\s+(?:state\s+)?(?:to\s+|as\s+)?(active|inactive)\b", t)
    if m:
        return Intent("update_test_plan", {"state": m.group(1).capitalize()})
    m = re.search(r"\b(?:move|assign|change|set)\b\s+(?:the\s+|this\s+)?(?:test\s+)?plan\s+(?:to|into|sprint to)\s+((?:sprint|iteration)\s+[\w.]+)", t)
    if m:
        return Intent("update_test_plan", {"sprint": m.group(1)})

    # ---- suites ------------------------------------------------------------------------------------------------
    m = re.search(r"\b(?:delete|remove)\b\s+(?:the\s+)?(.+?)\s+(?:test\s+)?suite\b", t)
    if m and suite_keys(m.group(1)):
        return Intent("delete_suite", {"testing_type": suite_keys(m.group(1))[0]})
    if re.search(r"\b(update|save|apply|commit|submit)\b\s+(?:the\s+|my\s+)?(?:test\s+)?suites?(?:\s+selections?)?\b|\bsave (?:the |my |these )?(?:suite )?selections?\b", t):
        return Intent("save_suites")
    if re.search(r"\b(?:show|list|read|which|what)\b.*\b(?:test\s+)?suites\b", t) and not re.search(r"\b(?:open|go to|navigate)\b", t) \
            and re.search(r"\b(?:does|do|has|have|in|of|for)\b.*\b(?:it|plan|this)\b|\bthe suites\b|^(?:list|show|read) (?:the |its )?(?:test )?suites$", t):
        return Intent("list_suites")
    sel = suite_selection(t)
    if sel and (re.search(r"\b(suites?|testing)\b", t) or not re.search(r"\b(?:click|enter|type|fill)\b", t)):
        return Intent("select_suites", sel)

    # ---- find / open a test plan ---------------------------------------------------------------------------------
    m = re.search(r"\b(find|search|look for|look up|open|show|load|select|get|bring up|display|go to)\b\s+(?:me\s+)?(?:for\s+)?"
                  r"(?:the\s+|a\s+|my\s+)?(last|latest|newest|current|selected|new|previous|active|inactive)?\s*test plans?\b(.*)$", t)
    if m:
        which, rest = m.group(2), m.group(3).strip()
        p = {}
        idm = re.match(r"^(\d{1,9})\b", rest) or re.search(r"\b(?:id|number)\s+(\d{1,9})\b", rest)
        if idm:
            p["id"] = int(idm.group(1))
        sprint = _sprint(rest)
        if sprint:
            p["sprint"] = sprint
        st = re.search(r"\b(active|inactive)\b", rest + " " + (which or ""))
        if st:
            p["state"] = st.group(1).capitalize()
        nm = re.search(r"\b(?:called|named|about|for|matching|with)\s+(?!sprint\b|iteration\b)(?:the\s+)?(.+?)(?:\s+(?:project|plan))?$", rest)
        if nm and not p.get("sprint") and not p.get("id"):
            p["text"] = nm.group(1).strip()
        if re.search(r"\bi just created\b|\bjust made\b", rest) or which in ("current", "selected", "new"):
            p["which"] = "current"
        elif which in ("last", "latest", "newest", "previous"):
            p["which"] = "last"
        if p:
            return Intent("find_test_plan", p)
        return None  # "open test plans": the page
    if re.fullmatch(rf"(?:open|show|load|select|use|pick)\s+{_REF}(?:\s+again)?", t):
        return Intent("open_current", {})
    if re.fullmatch(r"(?:please\s+)?(?:show|give|tell)\s+(?:me\s+)?(?:some\s+|the\s+)?more(?:\s+details|\s+information|\s+info|\s+about (?:it|that|this))?"
                    r"|(?:show|give)\s+(?:me\s+)?(?:the\s+)?details(?:\s+of\s+(?:it|that|this|the (?:test )?plan))?|more details|details", t):
        return Intent("show_details", {})
    m = re.fullmatch(r"(?:the\s+)?(first|second|third|fourth|fifth|sixth|last)(?:\s+(?:one|1))?|(?:number|option)\s+(\d{1,2})", t)
    if m:
        return Intent("select_item", {"position": _ORDINALS[m.group(1)] if m.group(1) else int(m.group(2))})
    return None
