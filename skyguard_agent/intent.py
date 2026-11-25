"""23.11.1: what the person wants, decided before any workflow is looked at.

The reported bug: with Online Store selected, "navigate to online store" produced an unrelated "Cancel lab" preview.
Nothing classified the request first. A word-overlap score picked any learned task that looked closest, the local
model was asked to choose "one of the given workflow ids" (it could not answer "none"), and an older unanswered
request was glued in front of the new one. This module is the first step of every command:

    classify(text)            -> Intent(kind, verb, target, ...)  deterministic, no model
    workflow_category(wf)     -> the kind of business operation a learned task performs
    compatible(intent, wf)    -> may this task serve this request at all? (navigation never maps to a task)
    relevance(intent, wf)     -> how well the task's entity, title and fields match the request (0..1)
    navigation_target(...)    -> the learned page (or the application's address) a navigation request means

Kinds: navigate, read, data_entry, create, edit, approve, authorize, authenticate, record, cancel_delete, explain,
unknown. Only create / edit / approve / authorize / authenticate / record / cancel_delete are business operations;
navigate, read and explain never change anything and never produce an execution preview.

Standard library only (the companion of the browser extension runs it too).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from .util import norm

OPERATIONS = ("create", "edit", "approve", "authorize", "authenticate", "record", "cancel_delete")
READ_ONLY = ("navigate", "read", "explain")

# canonical operation for a verb (of a request or of a learned task)
VERBS: dict[str, str] = {}
for _cat, _words in {
    "create": ("create", "add", "new", "make", "register", "book", "schedule", "plan", "raise", "insert", "place",
               "checkout", "check out", "order", "buy", "purchase", "issue", "generate", "open a new", "set up",
               "save", "store", "submit"),
    "edit": ("edit", "update", "change", "modify", "amend", "correct", "rename", "reschedule", "reassign", "assign"),
    "approve": ("approve", "approval", "sign off", "endorse"),
    "reject": ("reject", "decline", "deny", "refuse", "return"),
    "authorize": ("authorize", "authorise", "authorization", "authorisation", "authorized", "authorised", "release"),
    "authenticate": ("authenticate", "authentication", "verify", "accept", "acknowledge", "validate"),
    "record": ("record", "complete", "finish", "close", "finalize", "finalise", "log", "mark", "dispatch", "post"),
    "cancel_delete": ("cancel", "delete", "remove", "void", "discard", "archive", "erase", "drop", "revoke",
                      "terminate", "withdraw"),
}.items():
    for _w in _words:
        VERBS.setdefault(_w, _cat)
CATEGORY = {"reject": "approve"}  # a decision; still matched on its own verb, never as approve

NAV = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+|kindly\s+)?(?:navigate|go|take me|bring me|head|switch|"
                 r"jump|move|return|get me|browse|visit|launch|load|open)\b(?:\s+(?:back\s+)?(?:to|into|on|onto|at))?"
                 r"\s*(?P<target>.*)$", re.I)
PAGEISH = re.compile(r"\b(page|screen|form|list|website|web ?site|site|app|application|portal|home ?page|home|"
                     r"dashboard|menu|tab|window|module|section|report|inbox|queue|start page|main page|url|link)\b",
                     re.I)
READ = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:show|list|display|find|search|look ?up|view|get|"
                  r"fetch|filter|which|how many|count|give me|check the status|what is the status|status of|"
                  r"open the details?|details? of)\b", re.I)
QUESTION = re.compile(r"^\s*(how|what|what's|whats|why|who|whom|whose|where|when|explain|describe|tell me|teach me|"
                      r"is it|are there|can i|can a|could i|does|do i|should)\b", re.I)
DATA_ENTRY = re.compile(r"^\s*(?:please\s+)?(?:fill|enter|type|input|put|set|use)\b", re.I)
FIELD_VALUE = re.compile(r"^[\w /.-]{2,40}\s*(:|=|\bis\b)\s*\S", re.I)
STOP = {"the", "a", "an", "to", "of", "for", "in", "on", "at", "my", "me", "our", "your", "this", "that", "these",
        "those", "all", "any", "some", "please", "now", "then", "and", "or", "with", "from", "by", "page", "screen",
        "form", "list", "website", "site", "app", "application", "portal", "window", "tab", "menu", "section",
        "module", "url", "link", "go", "navigate", "open", "take", "bring", "show", "view", "new", "back", "it",
        "us", "is", "are", "be", "you", "can", "could", "would", "will", "i", "we", "there", "here", "up", "out"}
SITE_WORDS = re.compile(r"^\s*(?:the\s+)?(?:website|web ?site|site|app|application|portal|home ?page|home|start page|"
                        r"main page|login page|sign ?in page|landing page)?\s*$", re.I)


@dataclass
class Intent:
    kind: str                       # navigate, read, data_entry, create, ..., explain, unknown
    verb: str | None = None         # canonical operation verb ("approve", "reject", "create", ...)
    target: str = ""                # the words after the verb ("online store", "trip creation page")
    words: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""

    @property
    def is_operation(self) -> bool:
        return self.kind in OPERATIONS

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "verb": self.verb, "target": self.target, "confidence": self.confidence,
                "reason": self.reason}


def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", norm(text)) if w not in STOP and len(w) > 1]


def _verb_at(text: str) -> tuple[str | None, int]:
    """The first operation verb in the request and where it starts."""
    low = " " + norm(text) + " "
    best: tuple[str | None, int] = (None, 10 ** 6)
    for w, cat in VERBS.items():
        m = re.search(rf"(?<![a-z]){re.escape(w)}(?:e?s|e?d|ing|al|ion)?(?![a-z])", low)
        if m and m.start() < best[1]:
            best = (w, m.start())
    return best


def canonical_verb(word: str | None) -> str | None:
    if not word:
        return None
    w = norm(word)
    if w in VERBS:
        return VERBS[w] if VERBS[w] != "create" else "create"
    for k, cat in VERBS.items():
        if re.fullmatch(rf"{re.escape(k)}(?:e?s|e?d|ing|al|ion)?", w):
            return cat
    return None


def classify(text: str, *, app_names: list[str] | None = None) -> Intent:
    """Deterministic intent of one request. The first matching rule wins, in this order: navigation, explanation,
    read/search, a business operation (by its first verb), data entry, unknown."""
    raw = (text or "").strip()
    t = norm(raw)
    if not t:
        return Intent("unknown", reason="empty")
    nav = NAV.match(raw)
    if nav:
        target = nav.group("target").strip(" .!?")
        lead = norm(raw.split()[0]) if raw.split() else ""
        verb_word, _ = _verb_at(target)
        names = [norm(n) for n in app_names or [] if n]
        mentions_app = any(n and re.search(rf"\b{re.escape(n)}\b", norm(target)) for n in names)
        # "open trip T100" reads a record; "open a new trip" is a creation; "open the trip creation page" and
        # "navigate to online store" are navigation
        if lead not in ("open", "launch", "load") or PAGEISH.search(target) or mentions_app or SITE_WORDS.match(target):
            return Intent("navigate", target=target, words=_content_words(target), confidence=0.95,
                          reason=f"'{lead}' asks to go somewhere")
        if verb_word and canonical_verb(verb_word) == "create" and re.search(r"\b(new|create|add)\b", norm(target)):
            return Intent("create", verb="create", target=target, words=_content_words(target), confidence=0.8,
                          reason="'open a new ...' creates a record")
        return Intent("read", target=target, words=_content_words(target), confidence=0.8,
                      reason="'open' a record shows it")
    if QUESTION.match(raw) or (raw.endswith("?") and not READ.match(raw)):
        return Intent("explain", target=raw, words=_content_words(raw), confidence=0.85, reason="a question")
    if READ.match(raw):
        return Intent("read", target=raw, words=_content_words(raw), confidence=0.9, reason="show / list / find")
    verb_word, pos = _verb_at(raw)
    if verb_word:
        cat = VERBS[verb_word]
        kind = CATEGORY.get(cat, cat)
        rest = norm(raw)[pos + len(verb_word):]
        return Intent(kind, verb=cat, target=rest.strip(), words=_content_words(raw), confidence=0.9,
                      reason=f"the operation word '{verb_word}'")
    if DATA_ENTRY.match(raw) or FIELD_VALUE.match(raw) or ";" in raw:
        return Intent("data_entry", target=raw, words=_content_words(raw), confidence=0.6,
                      reason="values for a form")
    return Intent("unknown", target=raw, words=_content_words(raw), confidence=0.0, reason="no operation word")


def workflow_verb(wf: dict[str, Any]) -> str | None:
    """The canonical operation of a learned task (from its operation, else its title)."""
    d = wf.get("data") or wf
    for source in (d.get("operation"), (d.get("title") or wf.get("key") or "").split(" ")[0]):
        v = canonical_verb(source)
        if v:
            return v
    return None


def workflow_category(wf: dict[str, Any]) -> str | None:
    v = workflow_verb(wf)
    return CATEGORY.get(v, v) if v else None


def compatible(intent: Intent, wf: dict[str, Any]) -> bool:
    """A task may serve a request only when it performs the same kind of operation (and, for decisions, the same
    verb: "approve" never runs a "reject" task). Navigation, reading and questions never run a task."""
    if not intent.is_operation:
        return False
    v = workflow_verb(wf)
    if v is None:
        return False
    if intent.verb in ("approve", "reject") or v in ("approve", "reject"):
        return v == intent.verb
    if CATEGORY.get(v, v) == intent.kind:
        return True
    # 23.12.0: the name the person gave the task counts too. A task recorded as "buy item" whose last click is
    # "Finish" (an operation of another kind) is still the task for "buy item".
    d = wf.get("data") or wf
    tv = canonical_verb((d.get("title") or wf.get("key") or "").split(" ")[0])
    return bool(tv) and tv not in ("approve", "reject") and CATEGORY.get(tv, tv) == intent.kind


def relevance(intent: Intent, wf: dict[str, Any], *, other_entities: set[str] | None = None) -> float:
    """0..1: how well a compatible task fits the words of the request. A request that names another entity the
    application knows ("cancel the lab test" when the task cancels orders) scores 0."""
    d = wf.get("data") or {}
    entity = norm(d.get("entity"))
    text = " " + norm(intent.target or "") + " " + " ".join(intent.words) + " "
    fields = " ".join(norm(p.get("field") or p.get("name")) for p in d.get("parameters") or [])
    title = norm(d.get("title") or wf.get("key"))
    named_entity = bool(entity) and re.search(rf"\b{re.escape(entity)}(?:e?s)?\b", text) is not None
    others = {e for e in (other_entities or set()) if e and e != entity}
    names_other = any(re.search(rf"\b{re.escape(e)}(?:e?s)?\b", text) for e in others)
    if names_other and not named_entity:
        return 0.0
    score = 0.5  # the operation already matches
    if named_entity:
        score += 0.35
    hits = [w for w in intent.words if len(w) > 2 and (re.search(rf"\b{re.escape(w)}", title)
                                                       or re.search(rf"\b{re.escape(w)}", fields))]
    score += min(0.15, 0.05 * len(hits))
    return round(min(1.0, score), 2)


def navigation_target(intent: Intent, app: dict[str, Any], screens: list[dict[str, Any]],
                      workflows: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The address a navigation request means: the application itself ("navigate to online store", "go to the
    website", "home") or the best learned page ("open the trip creation page" -> the page where trips are created).
    None when no learned page fits; the caller then asks which page."""
    target = norm(intent.target)
    name = norm(app.get("name"))
    words = [w for w in intent.words if w not in set(re.findall(r"[a-z0-9]+", name))]
    if not target or SITE_WORDS.match(target) or (name and re.search(rf"\b{re.escape(name)}\b", target) and not words):
        return {"url": app["base_url"], "title": app["name"], "kind": "application", "score": 1.0}
    if name and re.search(rf"\b{re.escape(name)}\b", target):
        target = re.sub(rf"\b{re.escape(name)}\b", " ", target)
    wants_create = bool(re.search(r"\b(creat\w*|new|add\w*|entry|register\w*|book\w*)\b", target))
    best: tuple[float, dict[str, Any]] | None = None
    for s in screens:
        if (s.get("type") or "") in ("sign-in", "verification") or not s.get("path"):
            continue
        hay = " ".join(norm(x) for x in (s.get("name"), s.get("path"), s.get("entity"), s.get("type")))
        hits = [w for w in words if len(w) > 1 and re.search(rf"\b{re.escape(w)}", hay)]
        if not hits:
            continue
        score = len(hits) / max(1, len(words))
        if wants_create and (s.get("type") == "form" or re.search(r"\b(new|create|add)\b", hay)):
            score += 0.3
        if not wants_create and s.get("type") == "list":
            score += 0.1
        if best is None or score > best[0]:
            best = (score, s)
    for wf in workflows or []:  # "trip creation page": where the Create trip task starts
        d = wf.get("data") or {}
        if not wants_create or workflow_category(wf) != "create" or not d.get("start_url"):
            continue
        ent = norm(d.get("entity"))
        if ent and re.search(rf"\b{re.escape(ent)}", target):
            cand = {"path": d["start_url"], "name": f"{d.get('title') or wf.get('key')} page", "type": "form"}
            if best is None or best[0] < 1.2:
                best = (1.2, cand)
    if not best or best[0] < 0.5:
        return None
    s = best[1]
    return {"url": urljoin(app["base_url"], s.get("example_url") or s["path"]), "title": s.get("name") or s["path"],
            "kind": "page", "score": round(min(1.0, best[0]), 2)}
