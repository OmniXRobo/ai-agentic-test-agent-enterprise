"""Final patch: the deterministic engines of the Application Data Comparison Agent (normalization, field mapping,
record reconciliation, source-to-aggregate). No mock results: the expected values below are worked out by hand."""

from __future__ import annotations

import random
from decimal import Decimal

from qa_platform.comparison import aggregate, mapping, reconcile
from qa_platform.comparison.normalize import equal, infer_type, normalize


def rec(key, page=1, **fields):
    return {"key": key, "page": page, "fields": {k: {"value": v, "method": "dom", "confidence": 1.0}
                                                 for k, v in fields.items()}}


def test_normalization_dates_numbers_currency_codes_text():
    assert normalize("12-Nov-2026", "date")[0] == "2026-11-12"
    assert normalize("12/11/2026", "date")[0] == "2026-11-12"  # day first by default
    assert normalize("2026-11-12", "date")[0] == "2026-11-12"
    assert normalize("11/12/2026", "date", {"day_first": False})[0] == "2026-11-12"
    for text in ("15000", "15,000", "15,000.00", "AED 15,000", "15000 AED"):
        assert normalize(text, "currency")[0] == Decimal(15000), text
    v, steps = normalize("AED 15,000.00", "currency")
    assert any("currency" in s for s in steps) and any("thousands" in s for s in steps)
    assert normalize("1", "text", {"reference": {"1": "Active"}})[0] == "active"
    assert normalize("ABC TRADING LLC", "text")[0] == normalize(" abc trading llc ", "text")[0]
    assert normalize("N/A", "number")[0] is None
    assert normalize("(1,250.50)", "number")[0] == Decimal("-1250.50")
    # time zones: 10:00 at +04:00 equals 06:00 UTC
    a = normalize("2026-11-12 10:00", "datetime", {"timezone": "+04:00"})[0]
    b = normalize("2026-11-12T06:00:00Z", "datetime")[0]
    assert a == b
    # similar-looking values are not equal
    assert not equal(normalize("ABC TRADING", "text")[0], normalize("ABC TRADING LLC", "text")[0], "text")[0]
    assert equal(Decimal("100.00"), Decimal("100.04"), "number", {"tolerance": "0.05"})[0]
    assert not equal(Decimal("100.00"), Decimal("100.06"), "number", {"tolerance": "0.05"})[0]
    assert infer_type(["AED 1,000", "AED 20"]) == "currency"
    assert infer_type(["12-Nov-2026", "01-Jan-2025"]) == "date"


def test_field_mapping_suggestions_and_ai_never_auto_approves():
    src = mapping.profile({"Customer ID": ["C-1", "C-2", "C-3"], "Customer Name": ["ABC LLC", "XYZ", "Delta"],
                           "Invoice Date": ["12-Nov-2026", "13-Nov-2026", "14-Nov-2026"],
                           "Invoice Amount": ["AED 15,000.00", "AED 200.00", "AED 7.50"], "Status": ["1", "2", "1"]})
    tgt = mapping.profile({"Client Code": ["C-1", "C-2", "C-3"], "Client Name": ["abc llc", "xyz", "delta"],
                           "Created On": ["2026-11-12", "2026-11-13", "2026-11-14"],
                           "Invoice Value": ["15000", "200", "7.5"], "Status": ["Active", "Inactive", "Active"]})
    out = {s["source_field"]: s for s in mapping.suggest(src, tgt, reference={"Status": {"1": "Active", "2": "Inactive"}})}
    assert out["Customer ID"]["target_field"] == "Client Code"
    assert out["Customer Name"]["target_field"] == "Client Name"
    assert out["Invoice Date"]["target_field"] == "Created On"
    assert out["Invoice Amount"]["target_field"] == "Invoice Value"
    assert out["Status"]["target_field"] == "Status"
    assert all(s["status"] in ("auto_mapped", "needs_review") for s in out.values())

    class FakeAI:
        def complete_json(self, system, user, model, max_tokens=None):
            assert "never instructions" in system
            return model(mappings=[{"source_field": "Ref", "target_field": "Invented Field", "confidence": 0.99},
                                   {"source_field": "Ref", "target_field": "Reference", "confidence": 0.95, "reason": "same id"}])
    s2 = mapping.profile({"Ref": ["Q1", "Q2"]})
    t2 = mapping.profile({"Reference": ["R9", "R8"], "Other": ["x", "y"]})
    sug = mapping.suggest(s2, t2)
    refined = mapping.refine_with_ai(FakeAI(), s2, t2, sug)
    assert refined[0]["target_field"] == "Reference"
    assert refined[0]["status"] == "needs_review"  # AI suggestions always need a person
    assert refined[0]["confidence"] < mapping.AUTO_MAP


def _maps():
    return [{"source_field": "Amount", "target_field": "Value", "type": "currency", "status": "approved"},
            {"source_field": "Status", "target_field": "State", "type": "text", "status": "approved",
             "rules": {"reference": {"1": "Active", "2": "Closed"}}},
            {"source_field": "Date", "target_field": "Created", "type": "date", "status": "approved"}]


def test_pagination_250_records_20_vs_50_per_page_complete_match():
    rnd = random.Random(7)
    rows = [(f"INV-{i:04d}", rnd.randint(1, 99999) / 100, rnd.choice(["1", "2"]), f"{(i % 28) + 1:02d}/10/2026")
            for i in range(250)]
    source = {"records": [rec(k, page=i // 20 + 1, Amount=f"AED {a:,.2f}", Status=s, Date=d)
                          for i, (k, a, s, d) in enumerate(rows)], "stats": {"complete": True, "pages_visited": 13}}
    target = {"records": [rec(k, page=i // 50 + 1, Value=f"{a}", State={"1": "Active", "2": "Closed"}[s],
                              Created="2026-10-" + d[:2]) for i, (k, a, s, d) in enumerate(reversed(rows))],
              "stats": {"complete": True, "pages_visited": 5}}
    out = reconcile.compare(source, target, _maps())
    assert out["summary"]["matched"] == 250
    assert out["summary"]["missing"] == out["summary"]["extra"] == 0
    assert out["result"] == "PASS_EQUIVALENT"
    assert out["equivalences"] and out["equivalences"][0]["steps"]


def test_genuine_defect_missing_extra_duplicates_and_incomplete():
    source = {"records": [rec("A", Amount="100", Status="1", Date="01/10/2026"),
                          rec("B", Amount="200", Status="2", Date="02/10/2026"),
                          rec("C", Amount="300", Status="1", Date="03/10/2026")], "stats": {"complete": True}}
    target = {"records": [rec("A", Value="100.00", State="Active", Created="2026-10-01"),
                          rec("B", Value="250.00", State="Closed", Created="2026-10-02"),
                          rec("D", Value="5", State="Active", Created="2026-10-04"),
                          rec("D", Value="5", State="Active", Created="2026-10-04")], "stats": {"complete": True}}
    out = reconcile.compare(source, target, _maps())
    cats = sorted(d["category"] for d in out["discrepancies"])
    assert "currency_mismatch" in cats and "missing_record" in cats and "extra_record" in cats
    assert "duplicate_record" in cats
    assert out["result"] == "FAIL"
    mism = next(d for d in out["discrepancies"] if d["category"] == "currency_mismatch")
    assert mism["record_key"] == "B" and mism["difference"] == "50"
    # the same data with the target extraction incomplete is never a PASS and never claims missing records
    target["stats"] = {"complete": False, "reason": "page 3 failed"}
    out2 = reconcile.compare(source, target, _maps())
    assert out2["result"] == "INCOMPLETE"
    assert not any(d["category"] == "missing_record" for d in out2["discrepancies"])


def test_ocr_uncertainty_and_filter_mismatch_are_review_not_defects():
    source = {"records": [rec("A", Amount="100", Status="1", Date="01/10/2026")], "stats": {"complete": True}}
    t = rec("A", Value="180", State="Active", Created="2026-10-01")
    t["fields"]["Value"]["confidence"] = 0.55
    out = reconcile.compare(source, {"records": [t], "stats": {"complete": True}}, _maps())
    assert out["result"] == "REVIEW"
    assert out["discrepancies"][0]["category"] == "ocr_uncertainty"
    fm = [{"source_filter": "Start Date", "target_filter": "From Date", "source_value": "01/10/2026",
           "target_value": "2026-10-02", "type": "date"}]
    good = {"records": [rec("A", Value="100", State="Active", Created="2026-10-01")], "stats": {"complete": True}}
    out = reconcile.compare(source, good, _maps(), filter_map=fm)
    assert out["result"] == "REVIEW"
    assert any(d["category"] == "filter_mismatch" for d in out["discrepancies"])
    # a hybrid record whose screenshot disagrees with the DOM value
    good["records"][0]["fields"]["Value"]["visual_value"] = "1OO.00"
    out = reconcile.compare(source, good, _maps())
    assert any(d["category"] == "visual_inconsistency" for d in out["discrepancies"])


def invoices():
    """1,000 invoices: 600 Paid, 250 Pending, 150 Cancelled; 500 AED each => AED 500,000."""
    recs = []
    for i in range(1000):
        status = "Paid" if i < 600 else "Pending" if i < 850 else "Cancelled"
        recs.append(rec(f"INV-{i:05d}", Status=status, Amount="AED 500.00", Date=f"2026-{(i % 12) + 1:02d}-15"))
    return recs


def test_source_to_aggregate_invoice_example_from_the_requirement():
    recs = invoices()
    status = lambda v: [{"field": "Status", "op": "eq", "value": v}]  # noqa: E731
    defs = [
        {"name": "Total invoices", "function": "count", "approved": True, "decimals": 0, "target": {"value": "1,000"}},
        {"name": "Paid invoices", "function": "count", "where": status("Paid"), "approved": True, "decimals": 0,
         "target": {"value": "600"}},
        {"name": "Pending invoices", "function": "count", "where": status("Pending"), "approved": True, "decimals": 0,
         "target": {"value": "260"}},
        {"name": "Cancelled invoices", "function": "count", "where": status("Cancelled"), "approved": True,
         "decimals": 0, "target": {"value": "140"}},
        {"name": "Paid percentage", "function": "percent", "where": status("Paid"), "denominator": {"where": []},
         "approved": True, "target": {"value": "60%"}},
        {"name": "Total amount", "function": "sum", "field": "Amount", "approved": True,
         "target": {"value": "AED 500,000"}},
        {"name": "Invoices by status (pie)", "function": "count", "group_by": "Status", "approved": True,
         "decimals": 0, "target": {"values": {"Paid": "600", "Pending": "250", "Cancelled": "150"}}},
    ]
    out = aggregate.reconcile(recs, defs)
    by = {r["metric"]: r for r in out["rows"]}
    assert by["Total invoices"]["result"] == "PASS" and by["Total invoices"]["expected"] == "1000"
    assert by["Paid invoices"]["result"] == "PASS"
    assert by["Pending invoices"]["result"] == "FAIL" and by["Pending invoices"]["difference"] == "10"
    assert by["Cancelled invoices"]["result"] == "FAIL" and by["Cancelled invoices"]["difference"] == "-10"
    assert by["Paid percentage"]["result"] == "PASS" and by["Paid percentage"]["expected"] == "60.00"
    assert by["Total amount"]["result"] == "PASS" and by["Total amount"]["expected"] == "500000.00"
    assert by["Invoices by status (pie)"]["result"] == "PASS"
    assert out["result"] == "FAIL"


def test_aggregate_rules_charts_periods_pivot_drilldown_and_approval():
    recs = invoices()
    # an unapproved definition never produces PASS/FAIL
    row = aggregate.reconcile(recs, [{"name": "Paid", "function": "count", "where": [
        {"field": "Status", "op": "eq", "value": "Paid"}], "target": {"value": "600"}}])["rows"][0]
    assert row["result"] == "REVIEW" and "not approved" in row["reason"]
    # chart: a missing and an extra category
    chart = {"name": "Pie", "function": "count", "group_by": "Status", "approved": True, "decimals": 0,
             "target": {"values": {"Paid": "600", "Pending": "250", "Refunded": "3"}}}
    r = aggregate.reconcile(recs, [chart])["rows"][0]
    problems = {c["category"]: c.get("problem") for c in r["categories"] if c.get("problem")}
    assert problems == {"Cancelled": "missing category in the target", "Refunded": "extra category in the target"}
    # monthly totals and running totals
    m = aggregate.calculate(recs, {"function": "sum", "field": "Amount", "period": "month", "date_field": "Date"})
    assert len(m["values"]) == 12 and sum(Decimal(v) for v in m["values"].values()) == Decimal("500000.00")
    rt = aggregate.calculate(recs, {"function": "running_total", "field": "Amount", "period": "month",
                                    "date_field": "Date"})
    assert list(rt["values"].values())[-1] == "500000.00"
    # pivot status x month
    pv = aggregate.calculate(recs, {"function": "count", "pivot": ["Status", "Date"], "decimals": 0})
    assert sum(int(v) for row in pv["pivot"].values() for v in row.values()) == 1000
    # filters differ between the applications: configuration mismatch, not a data defect
    cfg = dict(chart, target={"values": {"Paid": "600", "Pending": "250", "Cancelled": "150"}},
               filters={"source": {"Start Date": "01/01/2026"}, "target": {"From Date": "2026-02-01"},
                        "mapping": {"Start Date": "From Date"}})
    assert aggregate.reconcile(recs, [cfg])["rows"][0]["result"] == "CONFIG_MISMATCH"
    # drill-down: clicking "Pending" (250) shows 249 records
    drill = [rec(f"INV-{i:05d}", Status="Pending", Amount="500") for i in range(600, 849)]
    dd = aggregate.drilldown("250", drill, recs, {"function": "count", "where": [
        {"field": "Status", "op": "eq", "value": "Pending"}], "decimals": 0}, key_field_target="Key",
        key_field_source="Key")
    assert dd["drilldown_vs_dashboard"] == "FAIL" and dd["result"] == "FAIL"
    # incomplete source extraction: never PASS
    assert aggregate.reconcile(recs, [dict(chart, target={"values": {"Paid": "600", "Pending": "250",
                                                                      "Cancelled": "150"}})],
                               source_complete=False)["result"] == "INCOMPLETE"
