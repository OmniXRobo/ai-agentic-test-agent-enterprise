"""Grammar for Guided Browser Automation by voice (23.3.4, milestone 7).

``launch_intent`` is valid anywhere ("launch my application" starts the guided, recorded browser session).
``guided_action`` is only used while a guided session runs, before the older browser grammar (intents.browser_action),
for the sentences it did not understand: credentials, a field or choice named without its value, checkboxes and
radio buttons, links to another page, checks of what the page shows, and "where am I".
"""

from __future__ import annotations

import re

_FIELD_END = r"(?:\s+(?:field|box|input|text box))?"


def launch_intent(t: str):  # -> Intent | None
    from .intents import Intent
    if re.fullmatch(r"(?:please\s+)?(?:launch|open|start|load|bring up)\s+(?:my|the|our)\s+(?:application|app|web app|"
                    r"website|site|application under test|system under test)(?:\s+(?:in the browser|please))?", t):
        return Intent("start_recording", {"launch": True})
    # a configured third-party application by name: "open my e-commerce application", "launch the CRM test portal"
    m = re.fullmatch(r"(?:please\s+)?(?:launch|open|start|load|bring up)\s+(?:my|the|our)\s+(.+?)\s+(?:test\s+)?"
                     r"(?:application|app|web app|website|site|portal|system|environment)(?:\s+(?:in the browser|please))?", t)
    if m and not re.search(r"\b(voice|command center|settings|automation|guided|tfs|ai)\b", m.group(1)):
        return Intent("start_recording", {"launch": True, "application": m.group(1)})
    return None


def guided_action(t: str):  # -> Intent | None
    from .intents import Intent

    def act(action: str, **p) -> Intent:
        return Intent("browser_action", {"action": action, **{k: v for k, v in p.items() if v}})

    t = re.sub(r"^(?:please|now|then|and|next)\s+", "", t)
    t = re.sub(r"\s+please$", "", t)
    if re.fullmatch(r"where am i|what page (?:is this|am i on)|which page (?:is this|am i on)|what(?:'s| is) on (?:the )?screen", t):
        return act("where")
    if re.fullmatch(r"what(?:'s| is| can i do) (?:on|here|on this page|in this page)(?: page)?|describe (?:this|the) page|"
                    r"what(?:'s| is) on this page|inspect (?:this|the) page|read (?:this|the) page", t):
        return act("inspect")
    if re.fullmatch(r"(?:submit|send)(?: the| this)?(?: form| page| registration| request)?", t):
        return act("submit")
    if re.fullmatch(r"(?:close|dismiss|cancel)(?: the| this)? (?:dialog|modal|popup|pop up|window)", t):
        return act("close_dialog")
    # credentials: "enter the username", "enter the password from the configured test credentials"
    m = re.fullmatch(r"(?:enter|type|fill in|fill|put in|use)\s+(?:in\s+)?(?:the\s+|my\s+|our\s+)?(?:saved\s+|test\s+|configured\s+)?"
                     r"(user ?name|user id|login name|login|password)" + _FIELD_END + r"(?:\s+(?:from|of|using)\s+.+)?", t)
    if m:
        field = "password" if m.group(1) == "password" else "username"
        return act("fill_credential", field=field)
    if re.search(r"\b(enter|type|say|use)\b.*\bpassword\b", t) and not re.search(r"\b(saved|configured|stored|credentials?)\b", t):
        return act("fill_credential", field="password")  # a password said aloud is never typed: the saved one is used
    # checks: "verify the confirmation message", "verify the page shows Product saved"
    m = re.fullmatch(r"(?:verify|check|confirm|make sure|assert|see if)\s+(?:that\s+)?(?:the\s+page\s+|it\s+|the\s+screen\s+)?"
                     r"(?:shows|displays|says|contains|has)\s+(.+)", t)
    if m:
        return act("verify", text=m.group(1))
    m = re.fullmatch(r"(?:verify|check|confirm|make sure|assert)\s+(?:that\s+)?(?:the\s+|a\s+|an\s+)?(.+?)"
                     r"(?:\s+(?:is|appears|is shown|is displayed|is visible|shows|appeared))?", t)
    if m and re.search(r"\b(message|alert|notification|banner|toast|text|confirmation|error|success|title|heading|label)\b", m.group(1)):
        return act("verify", target=m.group(1))
    # checkboxes and radio buttons
    m = re.fullmatch(r"(check|tick|uncheck|untick|clear|deselect)\s+(?:the\s+)?(.+?)(?:\s+(?:checkbox|check box|box|option))?", t)
    if m:
        return act("check" if m.group(1) in ("check", "tick") else "uncheck", target=m.group(2))
    m = re.fullmatch(r"(?:select|choose|pick|tick)\s+(?:the\s+)?(.+?)\s+(?:option|radio button|radio|choice)", t)
    if m:
        return act("choose", value=m.group(1))
    # another page of the application under test: "open the products page"
    m = re.fullmatch(r"(?:open|go to|navigate to|show|click on|switch to)\s+(?:the\s+|my\s+)?(.+?)\s+(page|tab|menu|section|screen)", t)
    if m:
        return act("open", target=m.group(1))
    # a value for a named field: "set the zip code to 90210", "type 90210 as the zip code"
    m = re.fullmatch(r"(?:set|change)\s+(?:the\s+)?(.+?)\s+to\s+(.+)", t)
    if m:
        return act("fill", target=m.group(1), value=m.group(2))
    m = re.fullmatch(r"(?:enter|type|put|fill in|set)\s+(?:the|my|a|an|your)\s+(.+?)\s+(?:as|with|to)\s+(.+)", t)
    if m:  # "enter the customer name as John Smith"
        return act("fill", target=m.group(1), value=m.group(2))
    m = re.fullmatch(r"(?:enter|type|put)\s+(?:the\s+)?([a-z][a-z ]*?)\s+(\S*\d[\S]*)", t)
    if m and not re.search(r"\b(in|into|on|as)\b", m.group(1)):  # "enter ZIP code 00000"
        return act("fill", target=m.group(1), value=m.group(2))
    m = re.fullmatch(r"(?:enter|type|put)\s+(.+?)\s+as\s+(?:the\s+)?(.+?)" + _FIELD_END, t)
    if m:
        return act("fill", target=m.group(2), value=m.group(1))
    # a field or a choice named without its value: the agent asks for it ("enter the product name", "choose the country")
    m = re.fullmatch(r"(?:enter|type|fill in|fill|add)\s+(?:the|a|an|my|your)\s+(.+?)" + _FIELD_END, t)
    if m and not re.search(r"\b(in|into|on)\b", m.group(1)):
        return act("fill", target=m.group(1))
    m = re.fullmatch(r"(?:select|choose|pick)\s+(?:the|a|an|your)\s+(.+?)", t)
    if m:
        return act("select", target=m.group(1))
    return None
