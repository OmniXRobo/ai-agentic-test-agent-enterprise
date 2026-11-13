"""What a recorded step *means* (23.6.0).

Recordings made before 23.6.0 store low-level clicks: a click on ``div[aria-label="Sunday, November 15, 2026, Select
the start date you want"]`` is really "choose 15 November 2026 as the start date"; a click on ``html > body`` is
really "close the open pop-up". ``infer`` reads that meaning from the step's own recorded labels, so playback can use
the component handlers (calendar, dropdown, dismiss) even for old recordings, and ``upgrade`` writes the meaning into
the script as a new version (the previous version is kept, as for every save).

Nothing here is site-specific: the rules read ARIA labels, roles and selectors, never a particular website.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .components import date_in_text, date_role, stable_text
from .dsl import Script, Step

_ARIA = re.compile(r'aria-label="([^"]+)"')
_ROLE_NAME = re.compile(r'^role=(\w+)\[name="(.+)"\]$')
PAGE_SELECTORS = {"html > body", "body", "html", "html>body"}


def _labels(step: Step) -> list[str]:
    t = step.target
    if t is None:
        return [step.description or ""]
    out = [t.name or "", t.description or "", t.label or "", t.text or "", step.description or ""]
    for sel in [t.selector, *t.alternates]:
        m = _ARIA.search(sel or "")
        if m:
            out.append(m.group(1))
        m = _ROLE_NAME.match(sel or "")
        if m:
            out.append(m.group(2))
    return [x for x in out if x]


def _role_of(step: Step) -> str | None:
    t = step.target
    if t is None:
        return None
    if t.role:
        return t.role
    for sel in [t.selector, *t.alternates]:
        m = _ROLE_NAME.match(sel or "")
        if m:
            return m.group(1)
    return None


def infer(step: Step) -> dict[str, Any]:
    """The step's meaning: {"intent": "pick_date" | "dismiss" | "prepare" | "choose_option" | ...}; {} if plain."""
    if step.semantic:
        return dict(step.semantic)
    t = step.target
    if step.action == "click" and t is not None:
        if (t.selector or "").strip().lower() in PAGE_SELECTORS and not t.alternates:
            return {"intent": "dismiss", "reason": "a click on the page background closes the open pop-up"}
        for label in _labels(step):
            iso = date_in_text(label)
            if iso and len(label) < 200:
                return {"intent": "pick_date", "component": "calendar", "date": iso, "role": date_role(label),
                        "label": stable_text(label)[:120], "inferred": True}
        if _role_of(step) == "option":
            name = next((x for x in _labels(step)), "")
            return {"intent": "choose_option", "option": stable_text(name)[:120], "inferred": True}
    if step.action == "hover":
        return {"intent": "prepare", "optional": True,
                "reason": "a hover only opens what the next step uses"}
    return {}


def upgrade(script: Script, today: date | None = None) -> tuple[Script, list[dict[str, Any]]]:
    """Write the inferred meaning into the steps (a copy). Returns the copy and what changed, step by step.

    A recorded calendar click becomes ``pick_date`` with the recorded date (dates in the past are reported, not
    changed: the tester decides the new date); a page-background click becomes ``dismiss``; hovers become optional."""
    copy = script.model_copy(deep=True)
    changes: list[dict[str, Any]] = []
    for i, step in enumerate(copy.steps):
        sem = infer(step)
        if not sem or step.semantic:
            continue
        intent = sem.get("intent")
        if intent == "pick_date":
            past = today is not None and date.fromisoformat(sem["date"]) < today
            step.semantic = {k: v for k, v in sem.items() if k != "inferred"}
            if past:
                changes.append({"step": i + 1, "change": "date in the past", "date": sem["date"],
                                "note": "Set a new date or a rule (Current date + N days) for this step."})
                continue
            step.action = "pick_date"
            step.original_value = sem["date"]
            step.value = sem["date"]
            step.description = (f"Choose {date.fromisoformat(sem['date']):%d %B %Y}"
                                + (f" as the {'start' if sem.get('role') == 'start' else 'end'} date"
                                   if sem.get("role") else "") + " in the calendar")
            changes.append({"step": i + 1, "change": "calendar click -> pick_date", "date": sem["date"],
                            "role": sem.get("role")})
        elif intent == "dismiss":
            step.action = "dismiss"
            step.semantic = {"intent": "dismiss"}
            step.description = "Close the open pop-up"
            changes.append({"step": i + 1, "change": "page click -> dismiss"})
        elif intent == "prepare":
            step.optional = True
            step.semantic = {"intent": "prepare"}
            changes.append({"step": i + 1, "change": "hover -> optional"})
        elif intent == "choose_option":
            step.semantic = {k: v for k, v in sem.items() if k != "inferred"}
            changes.append({"step": i + 1, "change": "option click recognised", "option": sem.get("option")})
    return copy, changes
