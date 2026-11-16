"""Business meaning of a field, for mapping, calculations, charts and filters (23.9.0).

A field's meaning is decided from its label, its unit, its data type and its values, with fixed rules a tester can
read in Preferences > Comparison > Business vocabulary (``concept_words``) and extend there. It is used to:

* recognise equivalent fields with different names (City / Territory / Region / Location / Branch; Order Volume /
  Orders / Sales Quantity; Total / Final Amount / Sales Amount; Contribution % / Share % / Percentage; Record State /
  Status / Current Status) while keeping fields of different meaning apart (a quantity is never an amount);
* keep the ROLE of an amount: a unit price, a subtotal, a discount, a tax and a final amount are all money but not the
  same value. "Total" and "Final Amount" are suggested as the same field only when their values agree on matching
  records; otherwise the mapping needs review, because one may include tax and the other not;
* refuse arithmetic between fields that cannot be combined (adding a city's sales to a percentage, an identifier to
  an amount) before any calculation is suggested.

Nothing here is AI: the local AI may suggest more, but a suggestion is checked against these rules and the data.
"""

from __future__ import annotations

import re
from typing import Any

from .normalize import CURRENCY, clean_text, parse_number

CONCEPTS: dict[str, list[str]] = {
    "id": ["id", "code", "ref", "reference", "no", "number", "key", "identifier", "#", "invoice", "order id",
           "order no", "transaction id", "receipt"],
    "location": ["city", "town", "territory", "region", "location", "branch", "emirate", "area", "zone", "country",
                 "site", "store", "outlet", "district", "province", "market", "office"],
    "date": ["date", "period", "month", "year", "day", "time", "created", "posted", "issued", "timestamp", "week",
             "quarter", "reporting period", "from", "to"],
    "status": ["status", "state", "stage", "condition", "record state", "order state", "current status"],
    "quantity": ["quantity", "qty", "units", "volume", "orders", "order volume", "sales quantity", "count", "items",
                 "pcs", "pieces", "number of", "no of", "transactions", "order count"],
    "money": ["amount", "total", "final amount", "sales amount", "sales", "revenue", "price", "unit price", "subtotal",
              "sub total", "discount", "tax", "vat", "cost", "value", "net", "gross", "fee", "charge", "balance",
              "turnover", "income", "payment", "paid", "due", "line total"],
    "percent": ["%", "percent", "percentage", "share", "contribution", "rate", "ratio", "margin", "pct",
                "completion"],
    "customer": ["customer", "client", "buyer", "account", "party", "consumer", "customer name"],
    "product": ["product", "item", "sku", "article", "product category", "category", "model"],
    "employee": ["employee", "staff", "agent", "salesperson", "sales rep", "representative", "user", "owner"],
    "department": ["department", "dept", "division", "unit", "team", "business unit"],
}
# roles of an amount: equal names are not enough for equal values
MONEY_ROLES: dict[str, list[str]] = {
    "unit_price": ["unit price", "price", "rate", "unit cost"],
    "discount": ["discount", "rebate", "reduction"],
    "tax": ["tax", "vat", "gst"],
    "subtotal": ["subtotal", "sub total", "gross", "line total", "line amount", "before tax"],
    "final": ["final", "net", "grand total", "amount due", "total amount", "invoice total", "after tax"],
    "sales": ["sales", "revenue", "turnover", "sales amount", "total sales"],
}
STATUS_WORDS = {"completed", "complete", "pending", "cancelled", "canceled", "open", "closed", "active", "inactive",
                "approved", "rejected", "paid", "unpaid", "draft", "new", "in progress", "shipped", "delivered",
                "successful", "failed", "waiting", "on hold", "void"}
ARITH_OK = {("money", "money"), ("quantity", "quantity"), ("percent", "percent")}


def _words(label: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", clean_text(label))
    return [w for w in re.split(r"[^a-z0-9%#]+", text.lower()) if w]


def _has(label: str, phrase: str) -> bool:
    words = _words(label)
    pw = _words(phrase) if phrase not in ("%", "#") else [phrase]
    if not pw:
        return False
    if phrase in ("%", "#"):
        return phrase in label
    n = len(pw)
    return any(words[i:i + n] == pw for i in range(len(words) - n + 1)) or \
        any(w.rstrip("s") == pw[0].rstrip("s") for w in words if n == 1 and len(pw[0]) > 3)


def unit_of(label: str, samples: list[Any]) -> str | None:
    m = re.search(r"\((?P<u>[A-Z]{3}|%)\)|\bin\s+(?P<u2>[A-Z]{3})\b", label or "")
    if m:
        return m.group("u") or m.group("u2")
    if "%" in (label or ""):
        return "%"
    texts = [clean_text(v) for v in samples if v not in (None, "")][:30]
    if texts and sum(1 for t in texts if t.endswith("%")) >= 0.8 * len(texts):
        return "%"
    cur = [CURRENCY.search(t) for t in texts]
    found = {c.group(0).upper().replace("DHS", "AED").replace("DH", "AED") for c in cur if c}
    if len(found) == 1 and sum(1 for c in cur if c) >= 0.5 * len(texts):
        return found.pop()
    return None


def concept(label: str, ftype: str | None = None, samples: list[Any] | None = None,
            unit: str | None = None, extra: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """{'concept': id|location|date|status|quantity|money|percent|customer|product|employee|department|text,
        'role': money role or None, 'unit': unit, 'why': short reason}."""
    samples = samples or []
    vocab = {k: list(v) + list((extra or {}).get(k, [])) for k, v in CONCEPTS.items()}
    unit = unit or unit_of(label, samples)
    texts = [clean_text(v).lower() for v in samples if v not in (None, "")][:50]
    numeric = bool(texts) and sum(1 for t in texts if parse_number(t) is not None) >= 0.8 * len(texts)
    hits = {c: max((len(p) for p in phrases if _has(label, p)), default=0) for c, phrases in vocab.items()}
    best = max(hits, key=lambda c: hits[c]) if any(hits.values()) else None
    role = None
    why = ""
    if unit == "%" or (ftype == "percent"):
        c = "percent"
        why = "percent unit/values"
    elif best == "status" and texts and not numeric and sum(1 for t in texts if t in STATUS_WORDS) < 0.3 * len(texts) \
            and hits.get("location"):
        c = "location"
    elif best == "status" and texts and not numeric and sum(1 for t in texts if t in STATUS_WORDS) == 0 and \
            _has(label, "state") and len(_words(label)) == 1:
        c = "location"  # "State" holding California, Dubai ...: a place, not a status
        why = "values are not status words"
    elif best in ("money", "quantity", "percent") and texts and not numeric:
        c = "text"
        why = "values are not numbers"
    elif best == "id" and hits.get("money", 0) > hits.get("id", 0):
        c = "money"
    elif best == "id" and numeric and hits.get("quantity") and not _has(label, "id"):
        c = "quantity"
    elif best:
        c = best
        why = f"label '{label}'"
    elif unit and unit != "%":
        c = "money"
        why = f"currency {unit}"
    elif ftype in ("date", "datetime"):
        c = "date"
    elif ftype in ("number", "currency") or numeric:
        c = "money" if ftype == "currency" else "number"
    elif texts and sum(1 for t in texts if t in STATUS_WORDS) >= 0.6 * len(texts):
        c = "status"
        why = "status words in the values"
    else:
        c = "text"
    if c == "money" or (unit and unit != "%" and c in ("number", "quantity") and not _has(label, "qty")
                        and not _has(label, "quantity")):
        if c != "quantity":
            c = "money"
        scores = {r: max((len(p) for p in ps if _has(label, p)), default=0) for r, ps in MONEY_ROLES.items()}
        if any(scores.values()):
            role = max(scores, key=lambda r: scores[r])
        elif _has(label, "total") or _has(label, "amount"):
            role = "total"
    if c == "quantity" and unit and unit != "%" and not (_has(label, "qty") or _has(label, "quantity")):
        c = "money"
    return {"concept": c, "role": role, "unit": unit, "why": why}


def same_meaning(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, str]:
    """How well two fields' meanings agree: (score 0..1, reason). 1 = same concept and compatible role and unit."""
    ca, cb = (a or {}).get("concept"), (b or {}).get("concept")
    if not ca or not cb:
        return 0.5, "meaning unknown"
    if ca != cb:
        loose = {ca, cb} <= {"money", "number"} or {ca, cb} <= {"quantity", "number"} or \
            {ca, cb} <= {"text", "customer"} or {ca, cb} <= {"text", "product"} or {ca, cb} <= {"text", "location"} \
            or {ca, cb} <= {"text", "status"} or {ca, cb} <= {"id", "text"}
        return (0.55, f"{ca} / {cb}") if loose else (0.0, f"different meaning: {ca} / {cb}")
    ua, ub = a.get("unit"), b.get("unit")
    if ua and ub and ua != ub:
        return 0.1, f"different units: {ua} / {ub}"
    ra, rb = a.get("role"), b.get("role")
    if ca == "money" and ra and rb and ra != rb:
        compatible = {ra, rb} <= {"total", "final", "sales"} or {ra, rb} <= {"total", "subtotal", "sales"}
        if compatible:
            return 0.8, f"amounts ({ra} / {rb}): equal only if both use the same calculation"
        return 0.25, f"different kinds of amount: {ra} / {rb}"
    return 1.0, f"same meaning ({ca}{', ' + ra if ra and ra == rb else ''})"


def can_combine(concepts: list[dict[str, Any]], op: str) -> bool:
    """May these fields be added / subtracted / multiplied / divided? (+, -: same kind; *: quantity x price;
    /: a ratio of the same kind)."""
    kinds = [(c or {}).get("concept") for c in concepts]
    if any(k in (None, "id", "date", "text", "status", "location", "customer", "product", "employee", "department")
           for k in kinds):
        return False
    if op in ("+", "-"):
        return len(set(kinds)) == 1 and kinds[0] in ("money", "quantity", "percent", "number")
    if op == "*":
        return sorted(kinds) in (["money", "quantity"], ["money", "percent"], ["number", "number"],
                                 ["money", "number"], ["number", "quantity"])
    if op == "/":
        return len(set(kinds)) == 1 or "number" in kinds
    return False


def result_kind(expr_kind: str, operands: list[dict[str, Any]]) -> str:
    kinds = [(c or {}).get("concept") for c in operands]
    if expr_kind == "ratio":
        return "percent"
    if expr_kind == "product":
        return "money" if "money" in kinds else "number"
    return kinds[0] if kinds else "number"


def compatible_target(target: dict[str, Any], kind: str) -> bool:
    t = (target or {}).get("concept")
    if t == kind:
        return True
    return {t, kind} <= {"money", "number"} or {t, kind} <= {"quantity", "number"} or \
        (t == "percent" and kind == "percent")


def aggregate_allowed(label: str, fn: str, source: dict[str, Any] | None = None) -> bool:
    """Which aggregate may produce a displayed summary value: MIN/MAX only for 'lowest/highest' style labels, AVG
    only for averages, COUNT for counts of records, SUM for totals. A grand total is never a MAX."""
    low = clean_text(label).lower()
    src = (source or {}).get("concept")
    if fn in ("MIN", "MAX"):
        return bool(re.search(r"\b(min|max|minimum|maximum|lowest|highest|smallest|largest|top|peak|best|worst)\b", low))
    if fn == "AVG":
        return bool(re.search(r"\b(avg|average|mean|per)\b", low))
    if fn == "COUNT":
        return bool(re.search(r"\b(count|number|no\.?|customers|clients|orders|records|transactions|invoices|items|"
                              r"entries|accounts|employees)\b", low)) and src in (None, "id", "text", "customer",
                                                                                    "product", "number")
    if fn == "SUM":
        return src not in ("id", "percent", "date", "text") and not re.search(r"\b(average|avg|mean|rate|ratio)\b", low)
    return False
