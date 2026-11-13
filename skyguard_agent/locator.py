"""Adaptive DOM locator engine and self-healing.

A learned control is a *fingerprint* (accessible role and name, label, placeholder, stable id / name / test id,
nearby text, section, dialog, table row, tag, type, classes, ancestors), never a brittle XPath or a coordinate. To act,
the engine matches the fingerprint against the live page snapshot using this hierarchy:

  1 accessibility role + accessible name    2 label / placeholder          3 stable id and attributes
  4 nearby text and semantic relationships  5 parent / sibling structure   6 form / table structure
  7 visible text and context                8 screenshot + OCR (not bundled: the agent pauses instead)
  9 coordinates (never automatic: a person clicks, and the click becomes the new fingerprint)

A candidate is used only when it is unique (clearly ahead of the runner-up), visible, enabled for the action, and in
the expected context (dialog / section). Two near-equal candidates mean *ambiguous*: the agent pauses rather than
guess. The model may suggest a control, but only this deterministic check decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .util import norm

THRESHOLD = 0.60
MIN_MARGIN = 0.08
HEAL_REVIEW_FLOOR = 0.42

FIELD_TAGS = {"input", "select", "textarea"}
CLICK_ROLES = {"button", "link", "menuitem", "tab", "checkbox", "radio", "switch", "option"}

WEIGHTS = {
    "name": 0.22, "label": 0.20, "placeholder": 0.08, "test_id": 0.30, "id": 0.18, "name_attr": 0.16,
    "nearby": 0.14, "section": 0.06, "row": 0.08, "role": 0.08, "tag": 0.05, "type": 0.05, "text": 0.10,
    "href": 0.05, "classes": 0.03, "ancestors": 0.03,
}
LIST_FIELDS = {"classes", "ancestors"}


def _sim(a: Any, b: Any) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) > 3 and len(b) > 3 and (a in b or b in a):
        return 0.85 * min(len(a), len(b)) / max(len(a), len(b)) + 0.1
    return SequenceMatcher(None, a, b).ratio()


def _value(fp: dict[str, Any], key: str) -> Any:
    if key == "row":
        return (fp.get("table") or {}).get("row") if isinstance(fp.get("table"), dict) else None
    return fp.get(key)


def kind_of(fp: dict[str, Any]) -> str:
    if fp.get("tag") in FIELD_TAGS and fp.get("type") not in ("submit", "button", "reset", "image", "checkbox", "radio"):
        return "field"
    return "control"


def score(target: dict[str, Any], cand: dict[str, Any]) -> tuple[float, dict[str, float], int]:
    parts: dict[str, float] = {}
    total = 0.0
    for key, weight in WEIGHTS.items():
        want = _value(target, key)
        if want in (None, "", []):
            continue
        have = _value(cand, key)
        if key in LIST_FIELDS:
            want_set = set(want)
            value = len(want_set & set(have or [])) / len(want_set) if want_set else 0.0
        else:
            value = _sim(want, have)
        parts[key] = round(value, 3)
        total += weight
    if not total:
        return 0.0, parts, 9
    raw = sum(parts[k] * WEIGHTS[k] for k in parts) / total
    if target.get("tag") and cand.get("tag") and target["tag"] != cand["tag"]:
        same_role = bool(target.get("role")) and target.get("role") == cand.get("role")
        raw *= 0.85 if same_role or {target["tag"], cand["tag"]} <= {"button", "input", "a"} else 0.6
    if target.get("type") and cand.get("type") and target["type"] != cand["type"] and target.get("tag") == "input":
        raw *= 0.8
    if bool(target.get("dialog")) != bool(cand.get("dialog")):
        raw *= 0.8
    tier = _tier(parts)
    return round(raw, 3), parts, tier


def _tier(parts: dict[str, float]) -> int:
    """The strongest exact signal that matched (1 = role + accessible name ... 7 = visible text)."""
    if parts.get("test_id") == 1.0 or (parts.get("role") == 1.0 and parts.get("name") == 1.0):
        return 1
    if parts.get("label") == 1.0 or parts.get("placeholder") == 1.0:
        return 2
    if parts.get("id") == 1.0 or parts.get("name_attr") == 1.0:
        return 3
    if parts.get("nearby") == 1.0:
        return 4
    if parts.get("ancestors", 0) >= 0.66 or parts.get("section") == 1.0:
        return 5
    if parts.get("row", 0) >= 0.9:
        return 6
    return 7


@dataclass
class Resolution:
    status: str  # found | ambiguous | not_found | blocked
    element: dict[str, Any] | None = None
    confidence: float = 0.0
    tier: int = 9
    healed: bool = False
    reason: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "confidence": self.confidence, "tier": self.tier, "healed": self.healed,
                "reason": self.reason, "element": _brief(self.element) if self.element else None,
                "candidates": [{"confidence": c["confidence"], "tier": c["tier"], "element": _brief(c["element"])}
                               for c in self.candidates[:5]]}


def _brief(el: dict[str, Any]) -> dict[str, Any]:
    return {k: el.get(k) for k in ("h", "tag", "type", "role", "name", "label", "nearby", "text", "section") if el.get(k)}


def _exact_match(target: dict[str, Any], el: dict[str, Any]) -> bool:
    """The stored fingerprint still identifies this element by its strongest stable signals."""
    for key in ("test_id", "id", "name_attr"):
        if target.get(key) and norm(target.get(key)) == norm(el.get(key)):
            return True
    return bool(target.get("name") and target.get("role") and norm(target["name"]) == norm(el.get("name"))
                and target["role"] == el.get("role"))


def _id_exact(target: dict[str, Any], el: dict[str, Any]) -> bool:
    return any(target.get(k) and norm(target.get(k)) == norm(el.get(k)) for k in ("test_id", "id", "name_attr"))


def context_of(el: dict[str, Any]) -> str:
    """Words that tell look-alike controls apart for a person: the row, card or section they belong to."""
    for key in ("row", "nearby", "section", "dialog", "table"):
        v = el.get(key)
        if isinstance(v, dict):
            v = v.get("row") or ""
        if isinstance(v, list):
            v = " ".join(str(x) for x in v if x)
        if v and norm(str(v)) not in (norm(el.get("name")), norm(el.get("label"))):
            return str(v)[:80]
    return str(el.get("test_id") or el.get("id") or el.get("text") or "")[:80]


def pick(fingerprint: dict[str, Any], snapshot: dict[str, Any], *, action: str = "click") -> Resolution:
    """The control the person chose in chat among look-alikes: the single best match of its full fingerprint
    (row / nearby text included), or ambiguous again when two still tie."""
    elements = [e for e in snapshot.get("elements", []) if e.get("visible", True)]
    ranked = sorted(({"element": e, "confidence": round(score(fingerprint, e)[0], 3), "tier": score(fingerprint, e)[2]}
                     for e in elements), key=lambda c: c["confidence"], reverse=True)
    if not ranked or ranked[0]["confidence"] < HEAL_REVIEW_FLOOR:
        return Resolution("not_found", reason="The control you chose is no longer on the page.", candidates=ranked[:5])
    exact = [c for c in ranked if _id_exact(fingerprint, c["element"])]
    top = exact[0] if len(exact) == 1 else ranked[0]
    if len(exact) != 1 and len(ranked) > 1 and ranked[1]["confidence"] >= top["confidence"]:
        return Resolution("ambiguous", reason="The controls still look the same.", candidates=ranked[:5],
                          confidence=top["confidence"])
    if top["element"].get("disabled"):
        return Resolution("blocked", element=top["element"], reason="The control is disabled.")
    return Resolution("found", element=top["element"], confidence=top["confidence"], tier=top["tier"])


def resolve(target: dict[str, Any], snapshot: dict[str, Any], *, action: str = "click",
            alternatives: list[dict[str, Any]] | None = None, section: str | None = None) -> Resolution:
    """Find the element a fingerprint describes on the current page."""
    elements = [e for e in snapshot.get("elements", []) if e.get("visible", True)]
    want_field = action in ("fill", "select") or kind_of(target) == "field"
    pool = []
    for e in elements:
        is_field = e.get("tag") in FIELD_TAGS and e.get("type") not in ("submit", "button", "reset", "image",
                                                                       "checkbox", "radio")
        if action in ("fill", "select") and not is_field:
            continue
        if action == "select" and e.get("tag") != "select" and not e.get("options"):
            continue
        if action == "click" and want_field and not is_field:
            continue
        pool.append(e)
    ranked = []
    fingerprints = [target, *(alternatives or [])]
    for e in pool:
        best = max((score(fp, e) for fp in fingerprints), key=lambda s: s[0])
        conf, parts, tier = best
        if section and e.get("section") and norm(section) != norm(e.get("section")):
            conf *= 0.9
        ranked.append({"element": e, "confidence": round(conf, 3), "tier": tier, "signals": parts,
                       "exact": _exact_match(target, e)})
    ranked.sort(key=lambda c: (c["confidence"], -c["tier"]), reverse=True)
    if not ranked or ranked[0]["confidence"] < HEAL_REVIEW_FLOOR:
        return Resolution("not_found", reason="No control on this page matches what was learned.",
                          candidates=ranked[:5])
    top = ranked[0]
    # 23.6.0: a unique id that matches exactly (data-test / id / name) decides between look-alike controls, e.g. six
    # "Add to cart" buttons that differ only in data-test="add-to-cart-<product>". Similar ids are not enough.
    by_id = [c for c in ranked if c["confidence"] >= HEAL_REVIEW_FLOOR and _id_exact(target, c["element"])]
    if len(by_id) == 1:
        top = by_id[0]
        close: list[dict[str, Any]] = []
    else:
        close = [c for c in ranked[1:] if top["confidence"] - c["confidence"] < MIN_MARGIN
                 and c["confidence"] >= THRESHOLD]
    if close:
        return Resolution("ambiguous", reason=f"{len(close) + 1} controls match equally well.",
                          candidates=[top, *close][:5], confidence=top["confidence"], tier=top["tier"])
    if top["confidence"] < THRESHOLD:
        return Resolution("not_found", reason="The closest control is too different to use safely.",
                          candidates=ranked[:5], confidence=top["confidence"], tier=top["tier"])
    el = top["element"]
    if action in ("click", "fill", "select", "check") and el.get("disabled"):
        return Resolution("blocked", element=el, confidence=top["confidence"], tier=top["tier"],
                          reason="The control is disabled.", candidates=ranked[:3])
    if action in ("fill", "select") and el.get("readonly"):
        return Resolution("blocked", element=el, confidence=top["confidence"], tier=top["tier"],
                          reason="The field is read-only.", candidates=ranked[:3])
    healed = not top["exact"]
    return Resolution("found", element=el, confidence=top["confidence"], tier=top["tier"], healed=healed,
                      candidates=ranked[:3])


def fingerprint_of(element: dict[str, Any]) -> dict[str, Any]:
    """What is remembered about an element (no handle, value, coordinates or secrets)."""
    keep = ("tag", "type", "role", "name", "label", "placeholder", "id", "name_attr", "test_id", "classes", "text",
            "nearby", "section", "dialog", "table", "ancestors", "href")
    fp = {k: element.get(k) for k in keep if element.get(k) not in (None, "", [])}
    if element.get("secret"):
        fp["secret"] = True
        fp.pop("text", None)
    return fp


def describe_target(fp: dict[str, Any]) -> str:
    return (fp.get("label") or fp.get("name") or fp.get("nearby") or fp.get("placeholder") or fp.get("text")
            or fp.get("name_attr") or fp.get("id") or "the control")


def resolve_label(label: str, snapshot: dict[str, Any], *, action: str = "click") -> Resolution:
    """No fingerprint learned yet: find the control a person would call ``label`` (exact label, accessible name,
    nearby text, placeholder or button text), unique or nothing."""
    want = norm(label)
    order = [("label", 2), ("name", 1), ("placeholder", 2), ("nearby", 4), ("text", 7)]
    elements = [e for e in snapshot.get("elements", []) if e.get("visible", True)]
    fields = action in ("fill", "select")
    for key, tier in order:
        hits = [e for e in elements if norm(e.get(key)) == want
                and ((e.get("tag") in FIELD_TAGS and e.get("type") not in ("submit", "button", "reset", "image"))
                     if fields else True)]
        if action == "select":
            hits = [e for e in hits if e.get("tag") == "select" or e.get("options")]
        if action == "click":
            hits = [e for e in hits if e.get("role") in CLICK_ROLES or e.get("tag") in ("button", "a", "input")
                    or e.get("role") is None]
        if len(hits) == 1:
            el = hits[0]
            if el.get("disabled"):
                return Resolution("blocked", element=el, confidence=0.9, tier=tier, reason="The control is disabled.")
            return Resolution("found", element=el, confidence=0.9, tier=tier)
        if len(hits) > 1:
            return Resolution("ambiguous", confidence=0.9, tier=tier, reason=f"{len(hits)} controls are called "
                              f"'{label}'.", candidates=[{"element": h, "confidence": 0.9, "tier": tier} for h in hits[:5]])
    guess = {"label": label, "name": label, "nearby": label}
    return resolve(guess, snapshot, action=action)
