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
    copy = _upgrade_suggestions(copy, changes)
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


# ---- 23.6.1: suggestion clicks in recordings made before 23.6.1 ----------------------------------------------------

_WORD = re.compile(r"[^\W_]+", re.U)
_INDEX_TESTID = re.compile(r'^\[data-testid="\d+(?:-\d+)?"\]$')


def _words(text: str | None) -> list[str]:
    return [w.lower() for w in _WORD.findall(str(text or ""))]


def _text_field(step: Step) -> bool:
    t = step.target
    if t is None or t.input_type == "password" or "{{CRED:" in str(step.value or ""):
        return False
    sel = (t.selector or "").lower()
    return bool(t.input_type in (None, "", "text", "search") and (
        sel.startswith("input") or "input" in sel or "combobox" in sel or "textbox" in sel or t.placeholder
        or t.role in ("combobox", "textbox", "searchbox") or t.tag in ("input", "textarea")))


def option_text(step: Step) -> str:
    t = step.target
    if t is None:
        return ""
    for v in (t.text, t.name, t.description, t.label):
        v = " ".join(str(v or "").split())
        if v and len(v) <= 160:
            return stable_text(v)
    return ""


def suggestion_context(script: Script, index: int) -> dict[str, Any] | None:
    """A plain click that chose a suggestion of a text field, in a recording made before 23.6.1.

    Sites such as Trip.com report the typed text only when the field loses focus, so the recording can hold the
    suggestion click *before* the typing ("click New Delhi - All airports India", then "fill New Delhi"); playing it
    in that order clicks a list that is not open yet. A click counts as a suggestion when a neighbouring step (only
    hovers in between) fills a text field and the clicked text starts with what was typed. Returns the field, what
    was typed and the option, or None."""
    steps = script.steps
    step = steps[index]
    if step.action != "click" or step.semantic or step.target is None:
        return None
    text = option_text(step)
    tw = _words(text)
    if not tw:
        return None
    for j in (index + 1, index - 1, index + 2, index - 2):
        if j < 0 or j >= len(steps):
            continue
        between = steps[min(j, index) + 1:max(j, index)]
        if any(s.action != "hover" for s in between):
            continue
        fill = steps[j]
        if fill.action not in ("fill", "type") or not fill.value or not _text_field(fill):
            continue
        fw = _words(fill.value)
        if not fw or "{{" in str(fill.value):
            continue
        # what was typed starts the option ("New Delhi" -> "New Delhi - All airports India"), or the field was
        # given the option's leading words ("New Delhi (DEL)")
        if tw.count(fw[0]) > 1:
            continue  # the text of several rows at once ("Paris France Paris, Texas ..."): a click on the whole list
        starts = tw[:len(fw)] == fw or fw[0] == tw[0] and len(set(fw) & set(tw)) >= max(1, min(len(fw), 2))
        looks = bool(_INDEX_TESTID.match(step.target.selector or "")) or (step.target.role or "") in (
            "option", "listitem", "menuitem") or "li" in (step.target.selector or "").lower()
        if starts or (looks and set(fw) & set(tw)):
            return {"field": fill.target, "typed": str(fill.value), "option": text, "fill_index": j,
                    "after": j > index}
    return None


def _upgrade_suggestions(script: Script, changes: list[dict[str, Any]]) -> Script:
    """23.6.1: a suggestion click becomes "choose <option> in <field> (typed ...)", and typing recorded after it moves
    in front of it, so the script reads (and plays) in the order the person did it."""
    found = [(i, ctx) for i in range(len(script.steps)) if (ctx := suggestion_context(script, i))]
    moves: list[tuple[int, int]] = []
    for i, ctx in found:
        step = script.steps[i]
        old_selector = step.target.selector if step.target else ""
        step.action = "choose_option"
        step.target = ctx["field"].model_copy(deep=True)
        step.value = ctx["option"]
        step.semantic = {"intent": "choose_option", "option": ctx["option"], "typed": ctx["typed"],
                         "list": "suggestion", "option_selector": old_selector}
        name = step.target.name or step.target.label or step.target.placeholder or "the field"
        step.description = f"Choose '{ctx['option'][:60]}' in {str(name)[:60]} (typed '{ctx['typed'][:40]}')"
        changes.append({"step": i + 1, "change": "suggestion click -> choose_option", "option": ctx["option"],
                        "typed": ctx["typed"]})
        if ctx["after"]:
            moves.append((ctx["fill_index"], i))
    for src, dst in sorted(moves, reverse=True):
        fill = script.steps.pop(src)
        script.steps.insert(dst, fill)
        changes.append({"step": dst + 1, "change": "typing moved before the suggestion it produced"})
    return script


def same_text(shown: str | None, recorded: str | None) -> bool:
    """The element's text is the recorded one (prices and spacing aside): the same words, or one is the start of the
    other."""
    a, b = _words(stable_text(shown)), _words(stable_text(recorded))
    if not a or not b:
        return not b
    return a == b or a[:len(b)] == b or b[:len(a)] == a
