"""Locators with a priority strategy and self-healing.

A ``Loc`` is declared once in a Page Object and reused by every test:

    SIGN_IN = Loc("role", "button", name="Sign in", alternates=["#login-btn"], fingerprint={...})

Strategies, strongest first: test_id, role (+ accessible name), label, placeholder,
id, css, text, xpath. ``alternates`` are other selectors for the same element that
were verified when the element was discovered. ``fingerprint`` describes the element
(tag, role, name, text, id, label, placeholder, classes) for the Self-Healing
Locator Agent.

Resolution order: primary locator, then each alternate, then self-healing, which
scores the elements on the live page against the fingerprint and uses a candidate
only when it is confident (>= 0.62) and clearly ahead of the runner-up. Every healing
attempt is recorded (original, replacement, confidence, page, screenshot); source
code is never changed by a run.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from . import config, healing, reporting

STRATEGIES = ("test_id", "role", "label", "placeholder", "id", "css", "text", "xpath")
FINGERPRINT_FIELDS = ("tag", "text", "role", "name", "element_id", "test_id", "label", "placeholder", "input_type",
                      "classes", "href")


class LocatorNotFound(AssertionError):
    pass


class Loc:
    def __init__(self, strategy: str, value: str, *, name: str | None = None, exact: bool = True,
                 alternates: list[str] | tuple[str, ...] = (), description: str = "",
                 fingerprint: dict[str, Any] | None = None) -> None:
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown locator strategy '{strategy}'. Use one of: {', '.join(STRATEGIES)}.")
        self.strategy = strategy
        self.value = value
        self.name = name
        self.exact = exact
        self.alternates = list(alternates)
        self.description = description
        self.fingerprint = dict(fingerprint or {})

    def __repr__(self) -> str:
        return f"Loc({self.selector()!r})"

    def selector(self) -> str:
        """The primary locator as a readable selector (used in reports and healing records)."""
        s, v = self.strategy, self.value
        if s == "test_id":
            return f"[data-testid={json.dumps(v)}]"
        if s == "role":
            return f"role={v}[name={json.dumps(self.name)}]" if self.name else f"role={v}"
        if s == "label":
            return f"label={json.dumps(v)}"
        if s == "placeholder":
            return f"placeholder={json.dumps(v)}"
        if s == "text":
            return f"text={json.dumps(v)}"
        if s == "xpath":
            return v if v.startswith(("xpath=", "//", "(")) else f"xpath={v}"
        return v  # id / css

    def build(self, page: Any) -> Any:
        s, v = self.strategy, self.value
        if s == "test_id":
            return page.get_by_test_id(v)
        if s == "role":
            return page.get_by_role(v, name=self.name, exact=self.exact) if self.name else page.get_by_role(v)
        if s == "label":
            return page.get_by_label(v, exact=self.exact)
        if s == "placeholder":
            return page.get_by_placeholder(v, exact=self.exact)
        if s == "text":
            return page.get_by_text(v, exact=self.exact)
        if s == "xpath":
            return page.locator(v if v.startswith(("xpath=", "//", "(")) else f"xpath={v}")
        return page.locator(v)

    def label(self) -> str:
        return self.description or self.name or self.value


def _wait(locator: Any, state: str, timeout: int) -> Any | None:
    try:
        locator.first.wait_for(state=state, timeout=timeout)
    except Exception:
        return None
    try:
        count = locator.count()
    except Exception:
        count = 1
    if count > 1 and state != "hidden":
        return None  # ambiguous: another locator must identify the element exactly
    return locator.first


def resolve(page: Any, loc: Loc, state: str = "visible", timeout: int | None = None) -> Any:
    """The element for ``loc`` on the current page, healing the locator only when every known locator fails."""
    timeout = timeout or config.TIMEOUT_MS
    primary = loc.build(page)
    found = _wait(primary, state, timeout)
    if found is not None:
        return found
    if state == "hidden":
        return primary.first
    for alt in loc.alternates:
        found = _wait(page.locator(alt), state, 1500)
        if found is not None:
            reporting.emit("locator", step=reporting.state.step_index, original=loc.selector(), used=alt,
                           note="Primary locator failed; recorded alternate worked")
            return found
    try:
        if primary.count() > 1:
            reporting.emit("locator", step=reporting.state.step_index, original=loc.selector(), used=loc.selector(),
                           note="Primary locator matched several elements; the first one was used")
            return primary.first
    except Exception:
        pass
    healed = heal(page, loc)
    if healed is None:
        raise LocatorNotFound(f"Element not found: {loc.label()} (locator {loc.selector()}); "
                              "self-healing found no confident match.")
    return healed


def heal(page: Any, loc: Loc) -> Any | None:
    """Self-Healing Locator Agent: inspect the DOM, score candidates, validate the best one, record the attempt."""
    if not loc.fingerprint:
        reporting.emit("healing", step=reporting.state.step_index, original=loc.selector(), healed=None, confidence=0.0,
                       candidates=[], page_url=_url(page), result="failed", element=loc.label(),
                       reason="No element fingerprint was recorded for this locator.")
        return None
    reporting.emit("healing_started", step=reporting.state.step_index, original=loc.selector(), element=loc.label())
    try:
        candidates = page.evaluate("window.__qaFp ? window.__qaFp.candidates() : []")
    except Exception:
        candidates = []
    target = SimpleNamespace(**{f: None for f in FINGERPRINT_FIELDS})
    for key, value in loc.fingerprint.items():
        setattr(target, key, value)
    if target.classes is None:
        target.classes = []
    ranked = healing.rank(target, candidates, exclude={loc.selector(), *loc.alternates})
    chosen = healing.choose(ranked)
    verified = None
    if chosen:
        candidate = page.locator(chosen["selector"])
        try:
            candidate.first.wait_for(state="visible", timeout=2000)
            if candidate.count() == 1:
                verified = candidate.first
        except Exception:
            verified = None
    if verified is not None and config.VISUAL:
        try:
            box = verified.bounding_box()
            if box:
                page.evaluate("b => window.__qaOverlay && window.__qaOverlay.point(b.x, b.y, b.width, b.height, 'heal', "
                              "'Self-healed locator')", box)
        except Exception:
            pass
    shot = reporting.screenshot(f"healing-step-{reporting.state.step_index + 1:02d}")
    best = chosen or (ranked[0] if ranked else None)
    reporting.emit("healing", step=reporting.state.step_index, original=loc.selector(),
                   healed=chosen["selector"] if verified is not None else None,
                   confidence=best["confidence"] if best else 0.0,
                   candidates=[{k: c[k] for k in ("selector", "confidence", "signals")} for c in ranked[:5]],
                   page_url=_url(page), screenshot=shot, result="healed" if verified is not None else "failed",
                   element=loc.label(), threshold=healing.THRESHOLD)
    return verified


def _url(page: Any) -> str:
    try:
        return page.url
    except Exception:
        return ""
