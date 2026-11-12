"""Source-to-Aggregate Reconciliation Engine (deterministic).

Application A shows detailed records; application B shows what should be calculated from them: KPI cards, totals,
subtotals, percentages, chart segments and bars, pivot cells, drill-down lists. This module calculates the expected
values from A's extracted records with plain Decimal arithmetic and compares them with B's displayed values.

The AI may *suggest* a calculation definition (which field, which function, which filter, which denominator); the
definition is shown to the tester and only an approved definition can produce PASS or FAIL. With an unapproved
definition the row is REVIEW. Nothing here assumes that a KPI is a SUM or that a percentage uses the grand total:
the definition says so, explicitly.

Definition (dict):
    name             what the target shows, e.g. "Paid invoices"
    function         count | distinct_count | sum | avg | min | max | percent | running_total
    field            the source field (sum, avg, min, max, distinct_count, running_total)
    where            [{field, op, value, type}]  ops: eq ne in not_in gt ge lt le between contains is_null not_null
    group_by         field for a chart / grouped table (one value per category)
    pivot            [row field, column field]
    period           day | month | year (with date_field): date-based aggregation
    date_field       the date the period is taken from
    denominator      {"where": [...]} for percent: the records the percentage is "of" (default: the base filter)
    distinct_by      the field that identifies a record for distinct_count (default: field)
    null_policy      ignore (default) | zero | review   what an empty numeric value means
    decimals         rounding of the expected value before comparing (default 2; percent default 2)
    tolerance        absolute tolerance; tolerance_pct relative tolerance (percent)
    approved         True when a tester approved the definition
    filters          {"source": {...}, "target": {...}}  the filters each application had (checked for equivalence)
"""

from __future__ import annotations

from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .normalize import is_null, normalize, parse_datetime, parse_number, to_json

FUNCTIONS = ("count", "distinct_count", "sum", "avg", "min", "max", "percent", "running_total")
OPS = ("eq", "ne", "in", "not_in", "gt", "ge", "lt", "le", "between", "contains", "is_null", "not_null")


def _value(record: dict[str, Any], field: str) -> Any:
    cell = (record.get("fields") or {}).get(field)
    if isinstance(cell, dict):
        return cell.get("value")
    return cell


def _confidence(record: dict[str, Any], field: str) -> float:
    cell = (record.get("fields") or {}).get(field)
    return float(cell.get("confidence", 1.0) or 1.0) if isinstance(cell, dict) else 1.0


def _num(value: Any, rules: dict[str, Any]) -> Decimal | None:
    if is_null(value, rules):
        return None
    n = parse_number(str(value), bool(rules.get("decimal_comma")))
    return n


def matches(record: dict[str, Any], cond: dict[str, Any], rules: dict[str, Any] | None = None) -> bool:
    """One structured condition. Never evaluates code: only the listed operators exist."""
    rules = rules or {}
    op = cond.get("op", "eq")
    if op not in OPS:
        raise ValueError(f"Unknown operator {op}")
    ftype = cond.get("type") or "text"
    raw = _value(record, cond["field"])
    if op == "is_null":
        return is_null(raw, rules)
    if op == "not_null":
        return not is_null(raw, rules)
    have = normalize(raw, ftype, rules)[0]
    want = cond.get("value")

    def n(v: Any) -> Any:
        return normalize(v, ftype, rules)[0]

    if op == "eq":
        return have == n(want)
    if op == "ne":
        return have != n(want)
    if op == "in":
        return have in {n(v) for v in (want or [])}
    if op == "not_in":
        return have not in {n(v) for v in (want or [])}
    if op == "contains":
        return have is not None and str(n(want)) in str(have)
    if have is None:
        return False
    try:
        if op == "between":
            lo, hi = n(want[0]), n(want[1])
            return lo <= have <= hi
        w = n(want)
        return {"gt": have > w, "ge": have >= w, "lt": have < w, "le": have <= w}[op]
    except TypeError:
        return False


def select(records: list[dict[str, Any]], where: list[dict[str, Any]] | None, rules: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in records if all(matches(r, c, rules) for c in (where or []))]


def _round(value: Decimal, decimals: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def _period(record: dict[str, Any], field: str, period: str, rules: dict[str, Any]) -> str | None:
    parsed = parse_datetime(str(_value(record, field) or ""), rules)
    if parsed is None:
        return None
    d = parsed[0]
    return {"day": d.strftime("%Y-%m-%d"), "month": d.strftime("%Y-%m"), "year": d.strftime("%Y")}.get(period)


def _reduce(rows: list[dict[str, Any]], d: dict[str, Any], rules: dict[str, Any],
            notes: list[str]) -> Decimal | None:
    fn = d["function"]
    if fn == "count":
        return Decimal(len(rows))
    if fn == "distinct_count":
        by = d.get("distinct_by") or d.get("field")
        vals = {str(normalize(_value(r, by), "text", rules)[0]) for r in rows if not is_null(_value(r, by), rules)}
        return Decimal(len(vals))
    nums: list[Decimal] = []
    empties = 0
    for r in rows:
        v = _num(_value(r, d["field"]), rules)
        if v is None:
            empties += 1
            if d.get("null_policy") == "zero":
                nums.append(Decimal(0))
            continue
        nums.append(v)
    if empties:
        notes.append(f"{empties} record(s) had no number in '{d['field']}' "
                     f"({'counted as 0' if d.get('null_policy') == 'zero' else 'left out'})")
    if not nums:
        return None
    if fn in ("sum", "running_total"):
        return sum(nums, Decimal(0))
    if fn == "avg":
        return sum(nums, Decimal(0)) / Decimal(len(nums))
    if fn == "min":
        return min(nums)
    if fn == "max":
        return max(nums)
    raise ValueError(f"Unknown function {fn}")


def calculate(records: list[dict[str, Any]], d: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """Expected value(s) of one definition. Returns {value | values, records_used, notes, trace}."""
    rules = rules or {}
    fn = d.get("function")
    if fn not in FUNCTIONS:
        raise ValueError(f"Unknown function '{fn}'. Use one of: {', '.join(FUNCTIONS)}.")
    if fn in ("sum", "avg", "min", "max", "running_total") and not d.get("field"):
        raise ValueError(f"'{fn}' needs a field.")
    decimals = int(d.get("decimals", 2))
    notes: list[str] = []
    base = select(records, d.get("where"), rules)
    trace = {"source_records": len(records), "after_filter": len(base),
             "filter": d.get("where") or [], "function": fn, "field": d.get("field")}
    low_conf = sum(1 for r in base if d.get("field") and _confidence(r, d["field"]) < 0.8)
    if low_conf:
        notes.append(f"{low_conf} value(s) were read with low confidence (OCR)")
    if d.get("null_policy") == "review" and d.get("field"):
        empties = sum(1 for r in base if is_null(_value(r, d["field"]), rules))
        if empties:
            notes.append(f"{empties} empty value(s) in '{d['field']}': the definition asks for review")
            trace["needs_review"] = True

    def finish(v: Decimal | None) -> str | None:
        return None if v is None else to_json(_round(v, decimals))

    if fn == "percent":
        denom_rows = select(records, (d.get("denominator") or {}).get("where", d.get("denominator_where")), rules) \
            if d.get("denominator") is not None else select(records, d.get("base_where"), rules)
        num_fn = d.get("of_function", "count")
        sub = dict(d, function=num_fn)
        if d.get("group_by"):
            groups = _group(base, d["group_by"], rules)
            denom = _reduce(denom_rows, sub, rules, notes)
            values = OrderedDict()
            for g, rows in groups.items():
                v = _reduce(rows, sub, rules, notes)
                values[g] = finish(v * 100 / denom) if (v is not None and denom) else None
            trace["denominator"] = to_json(denom) if denom is not None else None
            return {"values": values, "records_used": len(base), "notes": notes, "trace": trace}
        num = _reduce(base, sub, rules, notes)
        denom = _reduce(denom_rows, sub, rules, notes)
        trace.update({"numerator": to_json(num) if num is not None else None,
                      "denominator": to_json(denom) if denom is not None else None,
                      "denominator_filter": (d.get("denominator") or {}).get("where", [])})
        if not denom:
            notes.append("the denominator is 0: no percentage can be calculated")
            return {"value": None, "records_used": len(base), "notes": notes, "trace": trace}
        return {"value": finish(num * 100 / denom), "records_used": len(base), "notes": notes, "trace": trace}
    if d.get("pivot"):
        rf, cf = d["pivot"][0], d["pivot"][1]
        out: dict[str, dict[str, Any]] = OrderedDict()
        for rk, rows in _group(base, rf, rules).items():
            out[rk] = OrderedDict((ck, finish(_reduce(cr, d, rules, notes))) for ck, cr in _group(rows, cf, rules).items())
        return {"pivot": out, "records_used": len(base), "notes": notes, "trace": trace}
    if d.get("period"):
        if not d.get("date_field"):
            raise ValueError("A period needs a date field.")
        buckets: dict[str, list[dict[str, Any]]] = {}
        undated = 0
        for r in base:
            p = _period(r, d["date_field"], d["period"], rules)
            if p is None:
                undated += 1
                continue
            buckets.setdefault(p, []).append(r)
        if undated:
            notes.append(f"{undated} record(s) had no readable date in '{d['date_field']}' and were left out")
        values = OrderedDict()
        running = Decimal(0)
        for p in sorted(buckets):
            v = _reduce(buckets[p], d, rules, notes)
            if fn == "running_total" and v is not None:
                running += v
                v = running
            values[p] = finish(v)
        return {"values": values, "records_used": len(base), "notes": notes, "trace": trace}
    if d.get("group_by"):
        values = OrderedDict((g, finish(_reduce(rows, d, rules, notes)))
                             for g, rows in _group(base, d["group_by"], rules).items())
        return {"values": values, "records_used": len(base), "notes": notes, "trace": trace}
    return {"value": finish(_reduce(base, d, rules, notes)), "records_used": len(base), "notes": notes,
            "trace": trace}


def _group(rows: list[dict[str, Any]], field: str, rules: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = OrderedDict()
    ref = (rules.get("reference") or {})
    for r in rows:
        raw = _value(r, field)
        label = "(empty)" if is_null(raw, rules) else str(ref.get(str(raw).strip(), str(raw).strip()))
        groups.setdefault(label, []).append(r)
    return groups


def _target_number(text: Any, rules: dict[str, Any]) -> Decimal | None:
    if text is None:
        return None
    return parse_number(str(text), bool(rules.get("decimal_comma")))


def _cmp(expected: str | None, displayed: Any, d: dict[str, Any], rules: dict[str, Any]) -> tuple[str, Any]:
    e = Decimal(expected) if expected is not None else None
    t = _target_number(displayed, rules)
    if e is None or t is None:
        return "REVIEW", None
    decimals = int(d.get("decimals", 2))
    diff = _round(t, decimals) - _round(e, decimals)
    tol = Decimal(str(d.get("tolerance") or 0))
    pct = d.get("tolerance_pct")
    ok = abs(diff) <= tol or (pct and e and abs(diff) / abs(e) * 100 <= Decimal(str(pct)))
    return ("PASS" if ok else "FAIL"), to_json(diff)


def _filters_equivalent(d: dict[str, Any], rules: dict[str, Any]) -> tuple[bool, list[str]]:
    f = d.get("filters") or {}
    s, t = f.get("source") or {}, f.get("target") or {}
    issues = []
    mapping = f.get("mapping") or {}
    for sk, sv in s.items():
        tk = mapping.get(sk, sk)
        if tk not in t:
            issues.append(f"'{sk}' = {sv!r} is applied on the source only")
            continue
        ftype = "date" if any(w in sk.lower() for w in ("date", "from", "to", "period")) else "text"
        if normalize(sv, ftype, rules)[0] != normalize(t[tk], ftype, rules)[0]:
            issues.append(f"'{sk}' = {sv!r} on the source but '{tk}' = {t[tk]!r} on the target")
    for tk, tv in t.items():
        if tk not in {mapping.get(k, k) for k in s}:
            issues.append(f"'{tk}' = {tv!r} is applied on the target only")
    return not issues, issues


def reconcile(records: list[dict[str, Any]], definitions: list[dict[str, Any]], *, rules: dict[str, Any] | None = None,
              source_complete: bool = True) -> dict[str, Any]:
    """Every definition calculated and compared with its target. Rows for the Source-to-Aggregate report."""
    rules = rules or {}
    rows: list[dict[str, Any]] = []
    for d in definitions:
        base = {"metric": d.get("name") or d.get("function"), "definition_id": d.get("id"),
                "approved": bool(d.get("approved")), "function": d.get("function"), "field": d.get("field"),
                "target": d.get("target") or {}}
        try:
            calc = calculate(records, d, rules)
        except (ValueError, KeyError, TypeError) as exc:
            rows.append({**base, "result": "REVIEW", "reason": f"definition error: {exc}"})
            continue
        same_filters, issues = _filters_equivalent(d, rules)
        tgt = d.get("target") or {}
        conf = float(tgt.get("confidence", 1.0) or 1.0)
        reason = None
        if "value" in calc:
            result, diff = _cmp(calc["value"], tgt.get("value"), d, rules)
            row = {**base, "expected": calc["value"], "displayed": tgt.get("value"), "difference": diff,
                   "records_used": calc["records_used"], "notes": calc["notes"], "trace": calc["trace"]}
        else:
            expected = calc.get("values") or calc.get("pivot") or {}
            displayed = tgt.get("values") or {}
            cats = _categories(expected, displayed, d, rules)
            result = "FAIL" if any(c["result"] == "FAIL" for c in cats) else \
                "REVIEW" if any(c["result"] == "REVIEW" for c in cats) else "PASS"
            if d.get("check_order") and list(displayed) and [c for c in displayed if c in expected] != \
                    [c for c in expected if c in displayed]:
                result, reason = "FAIL", "categories are not in the expected order"
            row = {**base, "expected": expected, "displayed": displayed, "categories": cats,
                   "records_used": calc["records_used"], "notes": calc["notes"], "trace": calc["trace"]}
        if not source_complete:
            result, reason = "INCOMPLETE", "the source records were not extracted completely"
        elif not same_filters:
            result, reason = "CONFIG_MISMATCH", "; ".join(issues)
        elif not d.get("approved"):
            if result in ("PASS", "FAIL"):
                reason = f"would be {result}, but the calculation definition is not approved yet"
            result = "REVIEW"
        elif conf < 0.8 and result == "FAIL":
            result, reason = "REVIEW", "the displayed value was read with low confidence (OCR)"
        elif (calc.get("trace") or {}).get("needs_review"):
            result, reason = "REVIEW", "empty values need review (null policy)"
        rows.append({**row, "result": result, "reason": reason})
    counts = {k: sum(1 for r in rows if r["result"] == k) for k in ("PASS", "FAIL", "REVIEW", "CONFIG_MISMATCH",
                                                                   "INCOMPLETE")}
    overall = ("INCOMPLETE" if counts["INCOMPLETE"] else "FAIL" if counts["FAIL"] else
               "REVIEW" if counts["REVIEW"] or counts["CONFIG_MISMATCH"] else "PASS" if rows else "REVIEW")
    return {"result": overall, "counts": counts, "rows": rows}


def _categories(expected: dict[str, Any], displayed: dict[str, Any], d: dict[str, Any],
                rules: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    norm = {str(k).strip().lower(): k for k in displayed}
    for cat, val in expected.items():
        if isinstance(val, dict):  # pivot row
            for col, v in val.items():
                shown = (displayed.get(cat) or {}).get(col) if isinstance(displayed.get(cat), dict) else None
                res, diff = _cmp(v, shown, d, rules) if shown is not None else ("FAIL", None)
                out.append({"category": f"{cat} / {col}", "expected": v, "displayed": shown, "difference": diff,
                            "result": res if shown is not None else "FAIL",
                            "problem": None if shown is not None else "missing in the target"})
            continue
        key = norm.get(str(cat).strip().lower())
        if key is None:
            out.append({"category": cat, "expected": val, "displayed": None, "difference": None, "result": "FAIL",
                        "problem": "missing category in the target"})
            continue
        res, diff = _cmp(val, displayed[key], d, rules)
        out.append({"category": cat, "expected": val, "displayed": displayed[key], "difference": diff, "result": res})
    exp_keys = {str(k).strip().lower() for k in expected}
    for k, v in displayed.items():
        if str(k).strip().lower() not in exp_keys:
            out.append({"category": k, "expected": None, "displayed": v, "difference": None, "result": "FAIL",
                        "problem": "extra category in the target"})
    return out


def drilldown(dashboard_value: Any, drill_records: list[dict[str, Any]], source_records: list[dict[str, Any]],
              d: dict[str, Any], *, key_field_target: str | None = None, key_field_source: str | None = None,
              rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """A chart segment or KPI clicked in B: does its drill-down list add up to the figure B showed, and does it hold
    the same records as A filtered the same way?"""
    rules = rules or {}
    sub = dict(d)
    sub.pop("group_by", None)
    drill_calc = calculate(drill_records, {**sub, "where": []}, rules)
    source_calc = calculate(source_records, sub, rules)
    agrees_dashboard, diff_dash = _cmp(drill_calc["value"], dashboard_value, d, rules)
    agrees_source, diff_src = _cmp(source_calc["value"], drill_calc["value"], d, rules)
    out = {"dashboard_value": dashboard_value, "drilldown_value": drill_calc["value"],
           "source_value": source_calc["value"], "drilldown_vs_dashboard": agrees_dashboard,
           "difference_dashboard": diff_dash, "drilldown_vs_source": agrees_source, "difference_source": diff_src}
    if key_field_target and key_field_source:
        dk = {str(normalize(_value(r, key_field_target), "text", rules)[0]) for r in drill_records}
        sk = {str(normalize(_value(r, key_field_source), "text", rules)[0]) for r in select(source_records, d.get("where"), rules)}
        out["records_missing_in_drilldown"] = sorted(sk - dk)[:200]
        out["records_extra_in_drilldown"] = sorted(dk - sk)[:200]
    out["result"] = "PASS" if (agrees_dashboard == "PASS" and agrees_source == "PASS"
                               and not out.get("records_missing_in_drilldown") and not out.get("records_extra_in_drilldown")) \
        else "FAIL" if "FAIL" in (agrees_dashboard, agrees_source) or out.get("records_missing_in_drilldown") \
        or out.get("records_extra_in_drilldown") else "REVIEW"
    return out


def suggest_definitions(records: list[dict[str, Any]], targets: list[dict[str, Any]],
                        field_types: dict[str, str]) -> list[dict[str, Any]]:
    """Candidate definitions for displayed KPIs (never approved). A label "Total ... amount" suggests SUM of the
    numeric field whose name shares a word; "... count / number of / total X" suggests COUNT; "%" suggests a
    percentage with a filter on the category whose value appears in the label. The tester confirms each."""
    out = []
    numeric = [f for f, t in field_types.items() if t in ("number", "currency")]
    category_fields = [f for f, t in field_types.items() if t == "text"]
    values_by_field = {f: {str(_value(r, f)).strip().lower() for r in records if _value(r, f)} for f in category_fields}
    for t in targets:
        label = str(t.get("label") or "")
        low = label.lower()
        shown = str(t.get("value") or "")
        cond = None
        for f, vals in values_by_field.items():
            hit = next((v for v in vals if v and len(v) > 2 and v in low), None)
            if hit and len(vals) <= 50:
                cond = {"field": f, "op": "eq", "value": hit, "type": "text"}
                break
        if "%" in shown or "percent" in low or "share" in low:
            d = {"name": label, "function": "percent", "of_function": "count", "where": [cond] if cond else [],
                 "denominator": {"where": []}, "decimals": 2}
            why = "the displayed value is a percentage; denominator assumed ALL records: please confirm"
        elif any(w in low for w in ("amount", "value", "revenue", "sales", "sum")) and numeric:
            field = next((f for f in numeric if set(low.split()) & set(f.lower().split())), numeric[0])
            d = {"name": label, "function": "sum", "field": field, "where": [cond] if cond else [], "decimals": 2}
            why = f"label mentions an amount; SUM of '{field}' suggested"
        elif "average" in low or "avg" in low:
            field = next((f for f in numeric if set(low.split()) & set(f.lower().split())), numeric[0] if numeric else None)
            d = {"name": label, "function": "avg", "field": field, "where": [cond] if cond else [], "decimals": 2}
            why = "label says average"
        else:
            d = {"name": label, "function": "count", "where": [cond] if cond else [], "decimals": 0}
            why = "a whole number next to a category label; COUNT of records suggested"
        out.append({**d, "approved": False, "origin": "suggested", "why": why,
                    "target": {k: t.get(k) for k in ("value", "values", "confidence", "screenshot", "method",
                                                     "screen", "application")}})
    return out
