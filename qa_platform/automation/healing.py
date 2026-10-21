"""Self-Healing Locator scoring.

Activated only after the original selector (and its recorded alternates) fail.
Candidates come from the live DOM; each is scored against the fingerprint
recorded with the step. Only a candidate with confidence >= threshold that is
unambiguous (clearly ahead of the runner-up) is used.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from .dsl import Target

THRESHOLD = 0.62
MIN_MARGIN = 0.08

WEIGHTS = {
    "test_id": 0.30,
    "element_id": 0.18,
    "name": 0.20,
    "text": 0.16,
    "label": 0.10,
    "placeholder": 0.08,
    "role": 0.08,
    "tag": 0.05,
    "input_type": 0.05,
    "href": 0.06,
    "classes": 0.04,
}


def _sim(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    a, b = a.strip().lower(), b.strip().lower()
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def score(target: Target, cand: dict[str, Any]) -> tuple[float, dict[str, float]]:
    parts: dict[str, float] = {}
    total_weight = 0.0
    for field, weight in WEIGHTS.items():
        want = getattr(target, field, None)
        if field == "classes":
            if not want:
                continue
            have = set(cand.get("classes") or [])
            value = len(set(want) & have) / len(set(want)) if want else 0.0
        else:
            if want in (None, ""):
                continue
            value = _sim(str(want), cand.get(field))
        parts[field] = round(value, 3)
        total_weight += weight
    if total_weight == 0:
        return 0.0, parts
    raw = sum(parts[f] * WEIGHTS[f] for f in parts) / total_weight
    # A different element type is a strong signal the candidate is wrong.
    if target.tag and cand.get("tag") and target.tag != cand.get("tag"):
        compatible = {target.tag, cand.get("tag")} <= {"button", "input", "a"}
        raw *= 0.85 if compatible else 0.6
    return round(raw, 3), parts


def rank(target: Target, candidates: list[dict[str, Any]], exclude: set[str] | None = None) -> list[dict[str, Any]]:
    exclude = exclude or set()
    scored = []
    for cand in candidates:
        if cand.get("selector") in exclude:
            continue
        s, parts = score(target, cand)
        scored.append({"selector": cand.get("selector"), "alternates": cand.get("alternates", []),
                       "confidence": s, "signals": parts, "fingerprint": cand})
    scored.sort(key=lambda c: c["confidence"], reverse=True)
    return scored


def choose(ranked: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not ranked or ranked[0]["confidence"] < THRESHOLD:
        return None
    if len(ranked) > 1 and ranked[0]["confidence"] - ranked[1]["confidence"] < MIN_MARGIN \
            and ranked[1]["confidence"] >= THRESHOLD:
        return None  # ambiguous: two elements look equally right
    return ranked[0]
