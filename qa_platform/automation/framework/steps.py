"""Reads manual Test Case steps into automation intents (23.7.2).

A manual step such as "Enter 'John Doe' in the First Name field." or "Add products to the cart" is turned into
one or more intents (fill First Name with 'John Doe'; add a product to the cart) and its expected result into
checks (the First Name field shows 'John Doe'; something on the page shows the cart changed). The Test Case
precondition ("The customer is logged in and is on the checkout page") becomes setup intents that run first.

Nothing here depends on an AI model: the patterns cover how test cases are written (by people and by the
Test Design Agent), so every step is understood the same way on every run, also with a small local model. A step
that matches no pattern is left for the AI (one step at a time) or flagged for review; it is never dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_Q = r"""(?:'(?P<q1>[^']*)'|"(?P<q2>[^"]*)"|‘(?P<q3>[^’]*)’|“(?P<q4>[^”]*)”)"""
_FIELD_SUFFIX = r"(?:\s+(?:field|box|textbox|text\s+box|input|input\s+field|textarea|area))?"
_CTRL_SUFFIX = r"(?:\s+(?:button|link|icon|tab|menu|menu\s+item|option))?"
_ART = r"(?:the\s+|a\s+|an\s+|your\s+)?"


@dataclass
class Intent:
    """One thing to do on the application.

    kind: goto, login, fill, select, check, uncheck, click, add_to_cart, open_cart, reach, complete, view
    target: the words that describe the element, page or process ("first name", "checkout")
    value: the literal value written in the step ('John Doe'), if any
    """

    kind: str
    target: str = ""
    value: str | None = None


@dataclass
class Check:
    """One expected result to verify. kind: value, navigated, changed, success, visible, text"""

    kind: str
    target: str = ""
    value: str | None = None


@dataclass
class PlannedStep:
    number: int  # 0 = precondition / setup
    action: str
    expected: str
    intents: list[Intent] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def understood(self) -> bool:
        return bool(self.intents)


def _quoted(m: re.Match) -> str | None:
    for g in ("q1", "q2", "q3", "q4"):
        if m.groupdict().get(g) is not None:
            return m.group(g)
    return None


def _clean_target(text: str) -> str:
    text = re.sub(r"[.;:!]+$", "", text.strip())
    text = re.sub(r"^(?:the|a|an|your)\s+", "", text, flags=re.I)
    text = re.sub(_FIELD_SUFFIX + r"$", "", text, flags=re.I)
    return text.strip()


def _clean_control(text: str) -> str:
    text = re.sub(r"[.;:!]+$", "", text.strip())
    text = re.sub(r"^(?:on\s+)?(?:the|a|an|your)\s+", "", text, flags=re.I)
    text = re.sub(_CTRL_SUFFIX + r"$", "", text, flags=re.I)
    return text.strip(" '\"")


# ---- action patterns (first match wins; each returns intents) ---------------------------------------------------

_FILL_VALUE_FIRST = re.compile(
    rf"^(?:enter|type|input|key\s+in|provide|insert|write|fill\s+in|populate)\s+{_Q}\s+(?:in|into|on|for|as)\s+(?P<t>.+)$",
    re.I)
_FILL_FIELD_FIRST = re.compile(
    rf"^(?:fill(?:\s+in)?|populate|set|complete)\s+{_ART}(?P<t>.+?){_FIELD_SUFFIX}\s+(?:with|to|as)\s+{_Q}\.?$", re.I)
_FILL_FIELD_IS = re.compile(rf"^(?:enter|type|input|provide)\s+{_ART}(?P<t>.+?){_FIELD_SUFFIX}\s*(?:as|:|=)\s*{_Q}\.?$",
                            re.I)
_FILL_NO_VALUE = re.compile(rf"^(?:enter|type|input|provide|fill\s+in|key\s+in)\s+{_ART}(?:valid\s+|a\s+valid\s+)?(?P<t>.+?)"
                            rf"{_FIELD_SUFFIX}\.?$", re.I)
_SELECT = re.compile(rf"^(?:select|choose|pick)\s+{_Q}\s+(?:from|in|on)\s+(?P<t>.+?)(?:\s+(?:dropdown|drop-down|list|"
                     rf"menu|field|combo\s*box))?\.?$", re.I)
_CHECK = re.compile(r"^(?P<verb>check|tick|uncheck|untick|clear)\s+(?:the\s+)?(?P<t>.+?)(?:\s+(?:checkbox|check\s+box|box|"
                    r"option))\.?$", re.I)
_LOGIN = re.compile(r"\b(?:log\s?-?in|sign\s?-?in|login|logon|log\s+on|authenticate)\b", re.I)
_CREDENTIALS = re.compile(r"\b(?:credentials|username\s+and\s+password|user\s*name\s*/\s*password)\b", re.I)
_NAVIGATE = re.compile(r"^(?:navigate|go|open|launch|browse|visit|access|load|start)\b(?:\s+to)?\s*(?P<t>.*)$", re.I)
_SITE_WORDS = re.compile(r"\b(?:url|site|website|web\s+site|store|shop|application|app|portal|home\s*page|login\s+page|"
                         r"sign[\s-]?in\s+page|landing\s+page|browser)\b|https?://", re.I)
_ADD_TO_CART = re.compile(r"\badd(?:s|ed|ing)?\b(?P<what>.*?)\bto\s+(?:the\s+|my\s+|your\s+)?(?:shopping\s+)?"
                          r"(?P<where>cart|basket|bag|trolley)\b", re.I)
_OPEN_CART = re.compile(r"\b(?:open|view|review|go\s+to|navigate\s+to|check|see|click(?:\s+on)?)\b.*?\b(?:shopping\s+)?"
                        r"(?P<where>cart|basket|bag|trolley)\b", re.I)
_REVIEW_ORDER = re.compile(r"^(?:review|verify|check|view)\s+(?:the\s+|your\s+)?(?:order|purchase|items?|selected\s+products?)"
                           r"(?:\s+summary)?\b", re.I)
_COMPLETE = re.compile(r"\b(?:complete|finish|place|submit|confirm|finali[sz]e|make)\b\s+(?:the\s+|an?\s+|your\s+)?"
                       r"(?:online\s+)?(?P<what>checkout|check-out|order|purchase|payment|booking|registration|"
                       r"application|form|transaction)\b", re.I)
_PROCEED = re.compile(r"\b(?:proceed|continue|go|move|navigate)\b\s+(?:on\s+)?(?:to|with)\s+(?:the\s+)?(?P<t>[\w\s-]+?)"
                      r"(?:\s+page)?\.?$", re.I)
_CLICK = re.compile(r"^(?:click|press|tap|hit|select|choose|push)\s+(?:on\s+)?(?P<t>.+)$", re.I)
_VIEW = re.compile(r"^(?:browse|view|verify|observe|see|ensure|validate|confirm|check\s+that|look\s+at|scroll\s+through|"
                   r"review)\b\s*(?P<t>.*)$", re.I)

# ---- expected-result patterns -----------------------------------------------------------------------------------

_SHOWS = re.compile(rf"\b(?:shows|displays|contains|has|holds|reads|is\s+populated\s+with|is\s+filled\s+with|"
                    rf"has\s+the\s+value|value\s+is)\s+{_Q}", re.I)
_NAVIGATED = re.compile(r"\b(?:logged\s+in|signed\s+in|redirected|navigat\w*|taken\s+to|lands?\s+on|landed|"
                        r"(?:page|website|site|dashboard|home\s*page)\s+(?:is\s+)?(?:displayed|shown|opened|loaded|loads|"
                        r"opens|appears)|is\s+displayed|opens)\b", re.I)
_CHANGED = re.compile(r"\b(?:added|appears?\s+in|in\s+the\s+(?:cart|basket|bag)|(?:cart|basket|bag)\s+(?:count|badge|"
                      r"icon|total)|updated|increase[sd]?|removed)\b", re.I)
_SUCCESS = re.compile(r"\b(?:success\w*|complete[ds]?|completion|confirm\w*|thank|order\s+(?:is\s+)?placed|"
                      r"purchase\s+(?:is\s+)?(?:made|done))\b", re.I)
_VISIBLE = re.compile(r"\b(?:view|browse|see|visible|displayed|listed|shown|list|details|review)\b", re.I)


def plan_action(text: str) -> list[Intent]:
    """Intents for one manual action (an empty list means: not understood)."""
    t = " ".join((text or "").split()).strip()
    if not t:
        return []
    low = t.lower().rstrip(".")
    for rx in (_FILL_VALUE_FIRST, _FILL_FIELD_FIRST, _FILL_FIELD_IS):
        m = rx.match(t)
        if m:
            return [Intent("fill", _clean_target(m.group("t")), _quoted(m))]
    m = _SELECT.match(t)
    if m:
        return [Intent("select", _clean_target(m.group("t")), _quoted(m))]
    m = _CHECK.match(t)
    if m:
        kind = "uncheck" if m.group("verb").lower() in ("uncheck", "untick", "clear") else "check"
        return [Intent(kind, _clean_target(m.group("t")))]
    m = _ADD_TO_CART.search(t)
    if m:
        what = _clean_control(m.group("what") or "")
        what = "" if re.fullmatch(r"(?:an?\s+|the\s+|one\s+|some\s+|any\s+)?(?:products?|items?|goods|articles?)?", what,
                                  re.I) else what
        q = re.search(_Q, t)
        return [Intent("add_to_cart", m.group("where").lower(), (_quoted(q) if q else None) or (what or None))]
    m = _NAVIGATE.match(t)
    url = re.search(r"https?://\S+", t)
    if m and url:
        return [Intent("goto", value=url.group(0).rstrip(".,;)"))]
    if _LOGIN.search(t) and not _CLICK.match(t) and not re.search(r"\b(?:fail|invalid|wrong|incorrect|locked|without)\b", low) and \
            not re.search(r"\b(?:log\s?out|sign\s?out)\b", low):
        return [Intent("login")]
    if _CREDENTIALS.search(t):
        return [Intent("login")]
    m = _COMPLETE.search(t)
    if m:
        return [Intent("complete", m.group("what").lower().replace("check-out", "checkout"))]
    m = _OPEN_CART.search(t)
    if m:
        return [Intent("open_cart", m.group("where").lower())]
    if _REVIEW_ORDER.match(t):
        return [Intent("open_cart", "cart")]
    m = _PROCEED.search(t)
    if m and not _SITE_WORDS.search(m.group("t")):
        return [Intent("reach", _clean_control(m.group("t")))]
    m = _NAVIGATE.match(t)
    if m:
        rest = m.group("t")
        url = re.search(r"https?://\S+", rest)
        if url:
            return [Intent("goto", value=url.group(0).rstrip(".,;)"))]
        if not rest or _SITE_WORDS.search(rest):
            return [Intent("goto")]
        if re.match(r"^(?:browse|view)\b", low):
            return [Intent("view", _clean_control(rest))]
        return [Intent("reach", _clean_control(re.sub(r"\s+page$", "", rest.strip(" ."), flags=re.I)))]
    m = _CLICK.match(t)
    if m:
        target = m.group("t")
        target = re.split(r"\s+(?:to|in order to|so that)\s+", target, maxsplit=1)[0]
        q = re.fullmatch(rf"\s*{_Q}(?P<rest>.*)", target)
        target = _clean_control(_quoted(q) if q else target)
        if not q and (re.search(r"\b(?:and|then|or)\b|,", target) or len(target.split()) > 5):
            return []  # several things in one sentence: left for the AI, one step at a time
        return [Intent("click", target)]
    m = _VIEW.match(t)
    if m:
        return [Intent("view", _clean_control(m.group("t")))]
    m = _FILL_NO_VALUE.match(t)
    if m and not re.search(r"\b(?:and|then)\b", m.group("t")):
        return [Intent("fill", _clean_target(m.group("t")))]
    return []


def plan_checks(expected: str, intents: list[Intent]) -> list[Check]:
    """Checks for one expected result, given what the step does."""
    e = " ".join((expected or "").split())
    if not e:
        return []
    out: list[Check] = []
    m = _SHOWS.search(e)
    if m:
        target = intents[0].target if intents and intents[0].kind in ("fill", "select") else ""
        out.append(Check("value" if target else "text", target, _quoted(m)))
        return out
    kinds = {i.kind for i in intents}
    if _SUCCESS.search(e) and (kinds & {"complete", "click", "reach"} or re.search(r"\b(?:purchase|order|checkout)\b", e,
                                                                                    re.I)):
        out.append(Check("success"))
    elif _CHANGED.search(e) and kinds & {"add_to_cart", "click", "check", "uncheck", "select"}:
        out.append(Check("changed", e))
    elif _NAVIGATED.search(e) or kinds & {"goto", "login", "open_cart", "reach"}:
        out.append(Check("navigated", e))
    elif _VISIBLE.search(e):
        out.append(Check("visible", e))
    return out


_PRE_LOGIN = re.compile(r"\b(?:logged\s+in|signed\s+in|authenticated|has\s+(?:a\s+)?valid\s+(?:account|credentials))\b",
                        re.I)
_PRE_ON_PAGE = re.compile(r"\b(?:on|at|viewing|opened)\s+(?:the\s+)?(?P<t>[\w/-]+(?:\s+[\w/-]+){0,3}?)\s+(?:page|screen|step|"
                          r"form|view)\b", re.I)
_PRE_CART = re.compile(r"\b(?:has|have|with)\s+(?:at\s+least\s+)?(?:one|an?|some|\d+)?\s*(?:products?|items?)\s+in\s+"
                       r"(?:the\s+|their\s+|his\s+|her\s+)?(?:shopping\s+)?(?:cart|basket|bag)\b", re.I)


def plan_precondition(text: str | None) -> list[Intent]:
    """Setup intents for a Test Case precondition (empty when there is none or it needs nothing on screen)."""
    t = " ".join((text or "").split())
    if not t or re.fullmatch(r"(?:none|n/?a|-|no\s+precondition\.?)", t, re.I):
        return []
    out: list[Intent] = []
    if _PRE_LOGIN.search(t):
        out.append(Intent("login"))
    if _PRE_CART.search(t):
        out.append(Intent("add_to_cart", "cart"))
    m = _PRE_ON_PAGE.search(t)
    if m:
        target = re.sub(r"^(?:the|a|an)\s+", "", m.group("t").strip(), flags=re.I)
        if target and not re.fullmatch(r"(?:login|log\s?in|sign\s?in|home|landing|start)", target, re.I):
            out.append(Intent("reach", target))
    return out


def extract_precondition(description: str | None) -> str | None:
    """The 'Precondition:' line of a Test Case description (plain text or HTML)."""
    if not description:
        return None
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", description, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    m = re.search(r"pre-?conditions?\s*[:\-]\s*(?P<p>[^\n]+)", text, re.I)
    return " ".join(m.group("p").split()).strip() if m else None


def plan_steps(steps: list[dict[str, str]], precondition: str | None = None) -> list[PlannedStep]:
    """The precondition (number 0, when it needs setup) and every manual step, in order."""
    out: list[PlannedStep] = []
    pre = plan_precondition(precondition)
    first = plan_action(steps[0]["action"]) if steps else []
    if pre or (steps and not any(i.kind == "goto" for i in first)):
        checks = [Check("navigated", precondition or "")] if any(i.kind == "reach" for i in pre) else []
        out.append(PlannedStep(0, precondition or "Open the application", "", [Intent("goto"), *pre], checks))
    for n, s in enumerate(steps, start=1):
        intents = plan_action(s.get("action") or "")
        out.append(PlannedStep(n, s.get("action") or "", s.get("expected") or "", intents,
                               plan_checks(s.get("expected") or "", intents)))
    return out


# ---- matching words to elements ---------------------------------------------------------------------------------

_STOP = {"the", "a", "an", "to", "of", "on", "in", "for", "and", "or", "with", "field", "button", "link", "input", "box",
         "page", "icon", "your", "my", "this", "that", "is", "are", "be", "user", "customer", "valid", "enter", "click",
         "select", "from", "at", "by", "it", "its", "should", "can", "will"}
_SYNONYMS = {"zip": "postal", "zipcode": "postal", "postcode": "postal", "surname": "last", "lastname": "last",
             "firstname": "first", "forename": "first", "given": "first", "family": "last", "mail": "email",
             "e": "email", "telephone": "phone", "mobile": "phone", "tel": "phone", "username": "user",
             "userid": "user", "login": "login", "signin": "login", "sign": "login", "log": "login", "pwd": "password",
             "pass": "password", "basket": "cart", "bag": "cart", "trolley": "cart", "qty": "quantity",
             "products": "product", "items": "product", "item": "product", "inventory": "product",
             "check-out": "checkout"}


def tokens(text: str | None) -> list[str]:
    """Lower-case words of a label, id or test id (camelCase, kebab-case and snake_case split)."""
    if not text:
        return []
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text))
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        w = _SYNONYMS.get(w, w)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = _SYNONYMS.get(w[:-1], w[:-1])
        out.append(w)
    return out


def key_words(text: str | None) -> set[str]:
    return {w for w in tokens(text) if w not in _STOP}
