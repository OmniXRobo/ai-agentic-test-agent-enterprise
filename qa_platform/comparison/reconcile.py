"""Data Reconciliation and Comparison Engine: record, field and visual level (deterministic code only).

The language model never decides a comparison. This module matches records by their business key (never by page
position), compares every approved field mapping on normalized values, and classifies the outcome:

  PASS             exact match (every compared value identical as displayed, after trimming only)
  PASS_EQUIVALENT  equal after approved normalization (formats, currency, reference mappings, tolerances)
  FAIL             a confirmed data discrepancy on complete, comparable data
  REVIEW           inconclusive: filters differ, values were read with low confidence (OCR), mappings unapproved
  INCOMPLETE       extraction or coverage incomplete: never reported as a PASS

Each discrepancy carries its category, both original and normalized values, the normalization steps, the pages,
extraction methods, confidence and evidence references, so a person can check it.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from .normalize import equal, normalize, to_json

LOW_CONFIDENCE = 0.80
USABLE = ("auto_mapped", "manually_mapped", "approved")


def _rules_for(field_type: str, mapping: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    merged = dict(rules or {})
    merged.update((rules or {}).get("fields", {}).get(mapping.get("source_field") or "", {}))
    merged.update(mapping.get("rules") or {})
    merged.pop("fields", None)
    return merged


def _cell(record: dict[str, Any], field: str) -> dict[str, Any] | None:
    cell = (record.get("fields") or {}).get(field)
    if cell is None:
        return None
    return cell if isinstance(cell, dict) else {"value": cell}


def _key(record: dict[str, Any], key_field: str | None, rules: dict[str, Any]) -> str | None:
    if record.get("key") not in (None, ""):
        return str(normalize(record["key"], "text", rules)[0])
    if key_field:
        cell = _cell(record, key_field)
        if cell and cell.get("value") not in (None, ""):
            return str(normalize(cell["value"], "text", rules)[0])
    return None


def category_for(field: str, ftype: str, used_reference: bool) -> str:
    low = field.lower()
    if used_reference:
        return "reference_data_mismatch"
    if "status" in low or "state" in low:
        return "status_mismatch"
    if ftype in ("date", "datetime"):
        return "date_time_mismatch"
    if ftype == "currency" or any(w in low for w in ("amount", "price", "total", "value", "cost")):
        return "currency_mismatch" if ftype in ("currency", "number") else "value_mismatch"
    return "value_mismatch"


def filters_check(filter_map: list[dict[str, Any]], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """Equivalent business conditions on both sides? Each mapped filter's applied values are compared after
    normalization. An unmapped filter applied on one side only is a difference too."""
    issues: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []
    for f in filter_map or []:
        ftype = f.get("type") or "text"
        r = {**(rules or {}), **(f.get("rules") or {})}
        sv, tv = f.get("source_value"), f.get("target_value")
        if f.get("target_filter") is None or f.get("source_filter") is None:
            if (sv not in (None, "") if f.get("target_filter") is None else tv not in (None, "")):
                issues.append({**f, "problem": "applied on one application only"})
            continue
        ns, nt = normalize(sv, ftype, r)[0], normalize(tv, ftype, r)[0]
        same, how = equal(ns, nt, ftype, r)
        checked.append({**f, "equivalent": same, "how": how})
        if not same:
            issues.append({**f, "problem": f"different values ({sv!r} vs {tv!r})"})
    return {"equivalent": not issues, "issues": issues, "checked": checked}


def compare(source: dict[str, Any], target: dict[str, Any], mappings: list[dict[str, Any]], *,
            key: dict[str, str] | None = None, rules: dict[str, Any] | None = None,
            filter_map: list[dict[str, Any]] | None = None, names: dict[str, str] | None = None,
            max_examples: int = 2000) -> dict[str, Any]:
    """Compare two extracted datasets. ``source`` / ``target``: {"records": [...], "stats": {...}}."""
    rules = rules or {}
    names = names or {"source": "Application A", "target": "Application B"}
    key = key or {}
    usable = [m for m in mappings if m.get("status") in USABLE and m.get("source_field") and m.get("target_field")]
    not_used = [m for m in mappings if m not in usable]
    s_stats, t_stats = source.get("stats") or {}, target.get("stats") or {}
    complete = bool(s_stats.get("complete", True)) and bool(t_stats.get("complete", True))
    fcheck = filters_check(filter_map or [], rules)

    shown: dict[str, str] = {}  # normalized key -> the key as the application displays it

    def index(ds: dict[str, Any], key_field: str | None) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        by: dict[str, list[dict[str, Any]]] = {}
        no_key: list[dict[str, Any]] = []
        for r in ds.get("records") or []:
            k = _key(r, key_field, rules)
            if k is None:
                no_key.append(r)
            else:
                by.setdefault(k, []).append(r)
                raw = r.get("key") if r.get("key") not in (None, "") else (_cell(r, key_field or "") or {}).get("value")
                shown.setdefault(k, str(raw).strip())
        return by, no_key

    s_by, s_nokey = index(source, key.get("source"))
    t_by, t_nokey = index(target, key.get("target"))
    discrepancies: list[dict[str, Any]] = []
    equivalences: list[dict[str, Any]] = []
    counts = Counter()

    def disc(category: str, **kw: Any) -> None:
        if kw.get("record_key") is not None:
            kw["record_key"] = shown.get(kw["record_key"], kw["record_key"])
        base = f"{category}|{kw.get('record_key')}|{kw.get('source_field')}|{kw.get('target_field')}"
        discrepancies.append({"id": "D-" + hashlib.sha1(base.encode()).hexdigest()[:10].upper(),
                              "category": category, **kw})
        counts[category] += 1

    for k, rows in s_by.items():
        if len(rows) > 1:
            disc("duplicate_record", record_key=k, side="source", application=names["source"],
                 pages=[r.get("page") for r in rows], count=len(rows))
    for k, rows in t_by.items():
        if len(rows) > 1:
            disc("duplicate_record", record_key=k, side="target", application=names["target"],
                 pages=[r.get("page") for r in rows], count=len(rows))
    matched = sorted(set(s_by) & set(t_by))
    for k in sorted(set(s_by) - set(t_by)):
        r = s_by[k][0]
        disc("missing_record" if t_stats.get("complete", True) else "incomplete_comparison", record_key=k,
             application=names["target"], present_in=names["source"], page=r.get("page"),
             note=None if t_stats.get("complete", True) else f"{names['target']} was not extracted completely")
    for k in sorted(set(t_by) - set(s_by)):
        r = t_by[k][0]
        disc("extra_record" if s_stats.get("complete", True) else "incomplete_comparison", record_key=k,
             application=names["target"], absent_from=names["source"], page=r.get("page"),
             note=None if s_stats.get("complete", True) else f"{names['source']} was not extracted completely")
    record_results: list[dict[str, Any]] = []
    exact_records = equivalent_records = mismatched_records = review_records = 0
    for k in matched:
        sr, tr = s_by[k][0], t_by[k][0]
        fields_out = []
        rec_state = "exact"
        for m in usable:
            ftype = m.get("type") or "text"
            r = _rules_for(ftype, m, rules)
            sc, tc = _cell(sr, m["source_field"]), _cell(tr, m["target_field"])
            if sc is None or tc is None:
                fields_out.append({"field": m["source_field"], "result": "not_extracted"})
                rec_state = "review" if rec_state != "fail" else rec_state
                continue
            ns, s_steps = normalize(sc.get("value"), ftype, r)
            nt, t_steps = normalize(tc.get("value"), ftype, r)
            same, how = equal(ns, nt, ftype, r)
            raw_same = str(sc.get("value") or "").strip() == str(tc.get("value") or "").strip()
            conf = min(float(sc.get("confidence", 1.0) or 1.0), float(tc.get("confidence", 1.0) or 1.0))
            entry = {"field": m["source_field"], "target_field": m["target_field"], "source": sc.get("value"),
                     "target": tc.get("value"), "source_normalized": to_json(ns), "target_normalized": to_json(nt),
                     "result": "exact" if raw_same else "equivalent" if same else "different", "how": how,
                     "confidence": conf}
            # hybrid: the structured value and the screenshot (OCR) value of the same cell must agree
            for side, cell, norm in (("source", sc, ns), ("target", tc, nt)):
                if cell.get("visual_value") not in (None, ""):
                    nv = normalize(cell["visual_value"], ftype, r)[0]
                    if not equal(norm, nv, ftype, r)[0]:
                        disc("visual_inconsistency", record_key=k, source_field=m["source_field"],
                             target_field=m["target_field"], side=side,
                             application=names[side], structured_value=cell.get("value"),
                             visual_value=cell["visual_value"], screenshot=cell.get("screenshot"),
                             note="The extracted value and the screenshot disagree; neither is chosen silently.")
                        rec_state = "review" if rec_state != "fail" else rec_state
            if same:
                if not raw_same:
                    rec_state = "equivalent" if rec_state == "exact" else rec_state
                    if len(equivalences) < max_examples:
                        equivalences.append({"record_key": shown.get(k, k), "field": m["source_field"], "target_field": m["target_field"],
                                             "source": sc.get("value"), "target": tc.get("value"), "how": how,
                                             "steps": {"source": s_steps, "target": t_steps}})
            else:
                low_conf = conf < LOW_CONFIDENCE
                used_ref = any("reference mapping" in s for s in s_steps + t_steps)
                cat = "ocr_uncertainty" if low_conf else category_for(m["source_field"], ftype, used_ref)
                diff = None
                try:
                    if ftype in ("number", "currency", "percent") and ns is not None and nt is not None:
                        diff = to_json(nt - ns)
                except TypeError:
                    diff = None
                disc(cat, record_key=k, source_field=m["source_field"], target_field=m["target_field"],
                     source_value=sc.get("value"), target_value=tc.get("value"),
                     source_normalized=to_json(ns), target_normalized=to_json(nt), difference=diff,
                     normalization={"source": s_steps, "target": t_steps}, how=how,
                     source_page=sr.get("page"), target_page=tr.get("page"),
                     source_method=sc.get("method") or sr.get("method"), target_method=tc.get("method") or tr.get("method"),
                     confidence=conf, evidence=[x for x in (sc.get("screenshot") or sr.get("screenshot"),
                                                             tc.get("screenshot") or tr.get("screenshot")) if x])
                rec_state = "review" if low_conf and rec_state != "fail" else "fail"
            fields_out.append(entry)
        if rec_state == "exact":
            exact_records += 1
        elif rec_state == "equivalent":
            equivalent_records += 1
        elif rec_state == "fail":
            mismatched_records += 1
        else:
            review_records += 1
        if len(record_results) < max_examples:
            record_results.append({"record_key": shown.get(k, k), "state": rec_state, "fields": fields_out,
                                   "source_page": sr.get("page"), "target_page": tr.get("page")})
    s_total, t_total = len(source.get("records") or []), len(target.get("records") or [])
    coverage = {
        "source": {**{k: s_stats.get(k) for k in ("pages_visited", "complete", "failures", "expected_total",
                                                   "method")}, "records": s_total, "unique_keys": len(s_by),
                   "without_key": len(s_nokey)},
        "target": {**{k: t_stats.get(k) for k in ("pages_visited", "complete", "failures", "expected_total",
                                                   "method")}, "records": t_total, "unique_keys": len(t_by),
                   "without_key": len(t_nokey)},
    }
    if s_nokey or t_nokey:
        disc("pagination_extraction_problem", record_key=None,
             note=f"{len(s_nokey)} source and {len(t_nokey)} target records have no value for the record key; they "
                  "could not be matched and are not counted as missing or extra.")
    for side, stats in (("source", s_stats), ("target", t_stats)):
        if not stats.get("complete", True):
            disc("incomplete_comparison", record_key=None, side=side, application=names[side],
                 note=f"{names[side]} extraction is incomplete ({stats.get('reason') or 'not every page was read'}).")
    if not fcheck["equivalent"]:
        for issue in fcheck["issues"]:
            disc("filter_mismatch", record_key=None, filter=issue)
    hard = [d for d in discrepancies if d["category"] in (
        "value_mismatch", "missing_record", "extra_record", "duplicate_record", "date_time_mismatch",
        "currency_mismatch", "status_mismatch", "reference_data_mismatch")]
    soft = [d for d in discrepancies if d["category"] in ("ocr_uncertainty", "visual_inconsistency",
                                                          "pagination_extraction_problem")]
    if not complete or any(d["category"] == "incomplete_comparison" for d in discrepancies):
        result = "INCOMPLETE"
    elif not fcheck["equivalent"]:
        result = "REVIEW"  # configuration mismatch: never a data defect
    elif hard:
        result = "FAIL"
    elif soft or review_records or not usable:
        result = "REVIEW"
    elif equivalent_records:
        result = "PASS_EQUIVALENT"
    else:
        result = "PASS"
    return {
        "result": result,
        "summary": {"records_source": s_total, "records_target": t_total, "matched": len(matched),
                    "exact": exact_records, "equivalent": equivalent_records, "mismatched": mismatched_records,
                    "review": review_records, "missing": counts["missing_record"], "extra": counts["extra_record"],
                    "duplicates": counts["duplicate_record"], "visual_mismatches": counts["visual_inconsistency"],
                    "ocr_uncertain": counts["ocr_uncertainty"], "fields_mapped": len(usable),
                    "fields_unmapped": len(not_used), "discrepancies": len(discrepancies)},
        "coverage": coverage, "filters": fcheck, "mappings_used": usable, "mappings_not_used": not_used,
        "discrepancies": discrepancies, "equivalences": equivalences, "records": record_results,
        "complete": complete,
    }
