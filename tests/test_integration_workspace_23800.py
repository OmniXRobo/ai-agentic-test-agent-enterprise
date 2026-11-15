"""23.8.0 Integration Comparison Workspace: the 14 acceptance scenarios, through the API.

Data reaches the workspace the way a tester gives it: exported files (CSV), uploaded screenshots (read by the
offline OCR, or, where the OCR engine is not the point of the test, with the screenshot's reading stored as the
screen reader would produce it), and every result is decided by the deterministic comparison.
"""

from __future__ import annotations

import json
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from qa_platform.comparison import formula, screen_reader

W = "/api/comparison/workspace"
A_CSV = ("Customer ID,Customer Name,Total Sales,Completed Orders,Pending Orders,Cancelled Orders,Status\n"
         "1001,Alpha Trading,\"50,000\",100,20,5,02\n1002,Beta Foods,\"32,500\",45,5,0,01\n"
         "1003,Gamma Retail,\"18,750\",30,10,10,03\n1004,Delta Stores,\"12,000\",12,3,5,02\n")
B_CSV = ("Customer Reference,Sales Amount,Order Completion,Order Status\n1001,50000.00,83.33%,Completed\n"
         "1002,32500.00,90.00%,Pending\n1003,18750.00,75.00%,Cancelled\n1004,12000.00,80.00%,Completed\n")
C_CSV = ("Client Code,Revenue,Completion Rate,State\n1001,50000,80.00%,Successful\n1002,32500,90.00%,Waiting\n"
         "1003,18750,60.00%,Closed\n1004,12000,60.00%,Successful\n")
MONTHS_CSV = "Month,Sales\nJanuary,100\nFebruary,150\nMarch,200\n"
SITE = Path(__file__).parent / "site" / "integration"


def png(w=8, h=6):
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def new(client, *names):
    r = client.post(W, json={"name": "Orders", "applications": list(names)}).json()
    assert r["ok"], r
    return r["comparison"]["id"], r["applications"]


def upload_csv(client, app, text, name="export.csv"):
    r = client.post(f"{W}/applications/{app['id']}/file?name={name}", content=text.encode()).json()
    assert r["ok"], r
    return r


def screen(client, rt, app, parsed):
    """A screenshot whose reading (the screen reader's output) is given: tables, values, charts."""
    r = client.post(f"{W}/applications/{app['id']}/screenshots?name=shot.png", content=png()).json()
    assert r["ok"], r
    full = {"tables": [], "values": [], "charts": [], "texts": [], "low_confidence": 0, **parsed}
    rt.db.execute("UPDATE cmp_ws_screens SET parsed = ?, status = 'read' WHERE evidence_id = ?",
                  (json.dumps(full), r["screenshot"]["id"]))
    return r["screenshot"]["id"]


def cell(v, conf=0.96):
    return {"value": v, "confidence": conf, "method": "ocr", "box": [10, 10, 40, 12]}


def analyze(client, pid):
    r = client.post(f"{W}/{pid}/analyze", json={}).json()
    assert r["ok"], r
    return r["analysis"]


def approve_all(cfg, *, rules=True, statuses=True, charts=True):
    for f in cfg["fields"]:
        f["approve"] = len(f["members"]) > 1 or f.get("is_key")
    for r in cfg["rules"]:
        r["approve"] = rules
    for s in cfg["statuses"]:
        for g in s["groups"]:
            g["approve"] = statuses
    for c in cfg["charts"]:
        c["approve"] = charts
    return cfg


def save(client, pid, cfg):
    r = client.put(f"{W}/{pid}/mapping", json={"config": cfg}).json()
    assert r["ok"], r
    return r["saved"]


def run(client, pid):
    r = client.post(f"{W}/{pid}/run").json()
    assert r["ok"], r
    return r["run"]


def by_letter(apps):
    return {a["letter"]: a["id"] for a in apps}


def row_of(cfg, app_id, field):
    return next(r for r in cfg["fields"] if (r["members"].get(app_id) or {}).get("field") == field)


def rows(run_, field=None, result=None):
    return [r for r in run_["detail"]["rows"] if (field is None or r["field"] == field)
            and (result is None or r["result"] == result)]


# ---- TEST 1 ------------------------------------------------------------------------------------------------------

def test_01_table_vs_table(client):
    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV)
    upload_csv(client, apps[1], B_CSV)
    an = analyze(client, pid)
    L = by_letter(apps)
    cfg = an["config"]
    key = next(r for r in cfg["fields"] if r.get("is_key"))
    assert key["members"][L["A"]]["field"] == "Customer ID" and key["members"][L["B"]]["field"] == "Customer Reference"
    sales = row_of(cfg, L["A"], "Total Sales")
    assert sales["members"][L["B"]]["field"] == "Sales Amount" and sales["status"] == "auto_mapped"
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    s = r["detail"]["summary"]
    assert r["result"] == "PASS" and s["failed"] == 0 and s["records_checked"] == 4
    assert {x["result"] for x in rows(r, "Total Sales")} == {"PASS*"}  # 50,000 = 50000.00 after normalization


# ---- TEST 2 ------------------------------------------------------------------------------------------------------

def test_02_table_vs_calculated_percentages(client):
    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV)
    upload_csv(client, apps[1], B_CSV)
    cfg = analyze(client, pid)["config"]
    L = by_letter(apps)
    rule = next(r for r in cfg["rules"] if r["app"] == L["B"] and r["field"] == "Order Completion")
    assert rule["expression"] == "[A:Completed Orders] / ([A:Completed Orders] + [A:Pending Orders]) * 100"
    assert rule["review"] == "auto_mapped" and rule["status"] == "suggested"  # still needs the tester's approval
    # "Completed Orders" (a count) is never mapped to "Order Completion" (a percentage) because the names look alike
    assert L["B"] not in row_of(cfg, L["A"], "Completed Orders")["members"]
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    calc = rows(r, "Order Completion (calculated)")
    assert len(calc) == 4 and {x["result"] for x in calc} == {"PASS"}
    assert next(x for x in calc if x["record_key"] == "1001")["expected"] == "83.33"


def test_02b_unapproved_rule_is_a_warning(client):
    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV)
    upload_csv(client, apps[1], B_CSV)
    cfg = analyze(client, pid)["config"]
    save(client, pid, approve_all(cfg, rules=False))
    r = run(client, pid)
    assert rows(r, "Order Completion (calculated)", "WARNING") and r["result"] == "WARNING"


# ---- TEST 3 ------------------------------------------------------------------------------------------------------

def test_03_table_vs_graph(client, rt):
    pid, apps = new(client, "Order System", "Analytics")
    upload_csv(client, apps[0], MONTHS_CSV)
    screen(client, rt, apps[1], {"charts": [{"title": "Sales by Month", "categories": ["Jan", "Feb", "Mar"],
                                             "points": {"Jan": cell("100"), "Feb": cell("150"), "Mar": cell("210")},
                                             "extracted": True}]})
    cfg = analyze(client, pid)["config"]
    L = by_letter(apps)
    ch = cfg["charts"][0]
    assert ch["app"] == L["B"] and ch["source_app"] == L["A"] and ch["category_field"] == "Month" \
        and ch["value_field"] == "Sales" and ch["aggregate"] == "sum"
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    res = {x["record_key"]: x for x in rows(r, "Sales by Month (chart)")}
    assert res["Jan"]["result"] == "PASS" and res["Feb"]["result"] == "PASS"  # Jan = January
    assert res["Mar"]["result"] == "FAIL" and res["Mar"]["expected"] == "200" and res["Mar"]["category"] == "chart_mismatch"


# ---- TEST 4 and TEST 11 -------------------------------------------------------------------------------------------

def three_apps(client):
    pid, apps = new(client, "Order System", "Sales Portal", "Analytics")
    for a, text in zip(apps, (A_CSV, B_CSV, C_CSV)):
        upload_csv(client, a, text)
    return pid, apps


def test_04_three_apps_different_field_names(client):
    pid, apps = three_apps(client)
    cfg = analyze(client, pid)["config"]
    L = by_letter(apps)
    key = next(r for r in cfg["fields"] if r.get("is_key"))
    assert {m["field"] for m in key["members"].values()} == {"Customer ID", "Customer Reference", "Client Code"}
    sales = row_of(cfg, L["A"], "Total Sales")
    assert sales["members"][L["B"]]["field"] == "Sales Amount" and sales["members"][L["C"]]["field"] == "Revenue"
    assert sales["status"] == "auto_mapped"
    status = row_of(cfg, L["A"], "Status")
    assert status["members"][L["B"]]["field"] == "Order Status" and status["members"][L["C"]]["field"] == "State"
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    assert r["result"] == "PASS"
    pairs = {tuple(p["apps"]): p for p in r["detail"]["pairs"].values()}
    assert {(L["A"], L["B"]), (L["A"], L["C"]), (L["B"], L["C"])} <= set(pairs)
    assert all(p["failed"] == 0 for p in pairs.values())
    assert all(c["consistent"] for c in r["detail"]["consistency"].values())


def test_11_legitimate_business_rule_difference(client):
    pid, apps = three_apps(client)
    cfg = analyze(client, pid)["config"]
    L = by_letter(apps)
    rb = next(r for r in cfg["rules"] if r["app"] == L["B"])
    rc = next(r for r in cfg["rules"] if r["app"] == L["C"])
    assert rb["expression"] != rc["expression"]
    assert "[A:Cancelled Orders]" in rc["expression"] and "[A:Cancelled Orders]" not in rb["expression"]
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    # B (83.33%) and C (80.00%) differ for 1001, but each follows its own approved rule: not a defect
    bc = [x for x in rows(r) if x["field"] == "Order Completion" and x["record_key"] == "1001"]
    assert bc and bc[0]["result"] == "ACCEPTED" and bc[0]["category"] == "different_business_rules"
    assert not rows(r, result="FAIL")


# ---- TEST 5 ------------------------------------------------------------------------------------------------------

def test_05_status_codes_vs_descriptions(client):
    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV)
    upload_csv(client, apps[1], B_CSV.replace("1004,12000.00,80.00%,Completed", "1004,12000.00,80.00%,Pending"))
    cfg = analyze(client, pid)["config"]
    L = by_letter(apps)
    groups = {g["values"][L["A"]]: g for g in cfg["statuses"][0]["groups"]}
    assert groups["01"]["values"][L["B"]] == "Pending" and groups["03"]["values"][L["B"]] == "Cancelled"
    # 02 is Completed on 1001 and Pending on 1004: uncertain, so it must be reviewed
    assert groups["02"]["status"] != "auto_mapped"
    groups["02"]["values"][L["B"]] = "Completed"
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    st = {x["record_key"]: x for x in rows(r, "Status")}
    assert st["1001"]["result"] == "PASS*" and st["1002"]["result"] == "PASS*"
    assert st["1004"]["result"] == "FAIL" and st["1004"]["category"] == "status_mismatch"
    assert r["detail"]["summary"]["status_mismatches"] == 1


def test_05b_unapproved_status_mapping_never_passes(client):
    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV)
    upload_csv(client, apps[1], B_CSV)
    cfg = analyze(client, pid)["config"]
    save(client, pid, approve_all(cfg, statuses=False))
    r = run(client, pid)
    assert {x["result"] for x in rows(r, "Status")} == {"WARNING"}


# ---- TEST 6 ------------------------------------------------------------------------------------------------------

def test_06_table_vs_kpi_dashboard(client, rt):
    pid, apps = new(client, "Order System", "Sales Dashboard")
    upload_csv(client, apps[0], A_CSV)
    screen(client, rt, apps[1], {"values": [{"label": "Total Sales", **cell("113,250")},
                                            {"label": "Customers", **cell("4")},
                                            {"label": "Pending Orders", **cell("40")}]})
    client.patch(f"{W}/applications/{apps[0]['id']}", json={"screens_complete": True})
    cfg = analyze(client, pid)["config"]
    L = by_letter(apps)
    exprs = {r["field"]: r["expression"] for r in cfg["rules"] if r["app"] == L["B"]}
    assert exprs["Total Sales"] == "SUM([A:Total Sales])" and exprs["Customers"] == "COUNT([A:Customer ID])"
    assert exprs["Pending Orders"] == "SUM([A:Pending Orders])"  # suggested by name although the total differs
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    res = {x["field"]: x for x in rows(r) if x["field"].endswith("(calculated)")}
    assert res["Total Sales (calculated)"]["result"] == "PASS"
    assert res["Customers (calculated)"]["result"] == "PASS"
    assert res["Pending Orders (calculated)"]["result"] == "FAIL" and res["Pending Orders (calculated)"]["expected"] == "38"


# ---- TEST 7 ------------------------------------------------------------------------------------------------------

def _shots(tmp_path):
    from playwright.sync_api import sync_playwright

    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1100, "height": 700}, device_scale_factor=2)
        for n in ("app_a", "app_b", "app_c"):
            pg.goto((SITE / f"{n}.html").as_uri())
            out[n] = tmp_path / f"{n}.png"
            pg.screenshot(path=str(out[n]), full_page=True)
        b.close()
    return out


@pytest.mark.skipif(not shutil.which("tesseract"), reason="needs an offline OCR engine (Tesseract)")
def test_07_three_screenshots_ocr_and_compare(client, tmp_path):
    pytest.importorskip("playwright")
    shots = _shots(tmp_path)
    pid, apps = new(client, "Order System", "Sales Portal", "Analytics")
    L = by_letter(apps)
    for a, n in zip(apps, ("app_a", "app_b", "app_c")):
        r = client.post(f"{W}/applications/{a['id']}/screenshots?name={n}.png", content=shots[n].read_bytes()).json()
        assert r["ok"], r
        client.patch(f"{W}/applications/{a['id']}", json={"screens_complete": True})
    an = analyze(client, pid)
    prev = {a["letter"]: a for a in an["apps"]}
    assert prev["A"]["tables"][0]["headers"][:3] == ["Customer ID", "Customer Name", "Total Sales"]
    assert prev["A"]["tables"][0]["total_rows"] == 4
    assert {k: v["value"] for k, v in prev["B"]["values"].items()} == {"Sales Amount (summary)": "113,250",
                                                                        "Customers": "4", "Pending": "38"}
    chart = prev["C"]["charts"][0]
    assert chart["title"] == "Sales by Month" and chart["extracted"]
    assert {k: v["value"] for k, v in chart["points"].items()} == {"January": "100", "February": "150", "March": "200"}
    cfg = an["config"]
    assert row_of(cfg, L["A"], "Total Sales")["members"][L["C"]]["field"] == "Revenue"
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    assert r["result"] == "PASS", [x for x in r["detail"]["rows"] if x["result"] != "PASS"]
    assert all("screenshot" in x["sources"] for x in r["detail"]["rows"] if x["sources"])
    assert rows(r, "Sales by Month (chart)", "PASS")


def test_07b_screen_reader_layouts():
    def word(t, x, y, w=None, h=14, c=0.95):
        return {"text": t, "box": [x, y, w or 9 * len(t), h], "confidence": c}
    ocr = {"lines": [{"words": [
        word("Customer", 10, 10), word("Reference", 95, 10), word("Order", 300, 10), word("Completion", 352, 10),
        word("1001", 10, 40), word("83.33%", 300, 40), word("1002", 10, 70), word("90.00%", 300, 70, c=0.5),
        word("Status:", 10, 120), word("Completed", 80, 120),
        word("Revenue", 500, 300), word("120", 520, 280), word("90", 620, 260), word("Q1", 520, 340),
        word("Q2", 620, 340), word("Q3", 720, 340), word("75", 720, 300)]}]}
    out = screen_reader.read_screen(ocr)
    t = out["tables"][0]
    assert t["headers"] == ["Customer Reference", "Order Completion"] and len(t["rows"]) == 2
    assert t["rows"][1]["Order Completion"]["confidence"] == 0.5 and out["low_confidence"] >= 1
    assert {"label": "Status", "value": "Completed"}.items() <= next(v for v in out["values"]
                                                                    if v["label"] == "Status").items()


# ---- TEST 8 ------------------------------------------------------------------------------------------------------

def test_08_tester_corrects_a_mapping(client):
    pid, apps = new(client, "Order System", "Ledger")
    upload_csv(client, apps[0], A_CSV)
    upload_csv(client, apps[1], "Acct,Zeta Figure\n1001,50000\n1002,32500\n1003,18750\n1004,12001\n")
    cfg = analyze(client, pid)["config"]
    L = by_letter(apps)
    sales = row_of(cfg, L["A"], "Total Sales")
    assert sales["status"] != "auto_mapped"  # the AI cannot tell that "Zeta Figure" is the sales amount
    orphan = next((r for r in cfg["fields"] if (r["members"].get(L["B"]) or {}).get("field") == "Zeta Figure"), None)
    if orphan is not sales:
        cfg["fields"].remove(orphan)
    sales["members"][L["B"]] = {"field": "Zeta Figure", "kind": "column", "confidence": 1, "origin": "tester"}
    sales["name"] = "Sales"
    saved = save(client, pid, approve_all(cfg))
    row = next(r for r in saved["config"]["fields"] if r["name"] == "Sales")
    assert row["status"] == "approved" and row["members"][L["B"]]["origin"] == "tester"
    r = run(client, pid)
    res = {x["record_key"]: x["result"] for x in rows(r, "Sales")}
    assert res == {"1001": "PASS*", "1002": "PASS*", "1003": "PASS*", "1004": "FAIL"}


# ---- TEST 9 ------------------------------------------------------------------------------------------------------

def test_09_saved_mapping_is_reused(client):
    pid, apps = three_apps(client)
    cfg = analyze(client, pid)["config"]
    save(client, pid, approve_all(cfg))
    first = run(client, pid)
    an = analyze(client, pid)  # next time: the approved mapping comes back as approved
    assert an["saved_version"] == 1
    assert all(r["status"] == "approved" for r in an["config"]["fields"] if len(r["members"]) > 1)
    assert all(r["status"] == "approved" for r in an["config"]["rules"])
    second = run(client, pid)  # no new approval needed
    assert second["detail"]["mapping_version"] == 1
    assert first["detail"]["summary"] == second["detail"]["summary"]
    hist = client.get(f"{W}/{pid}/mapping/history").json()["items"]
    assert [h["version"] for h in hist] == [1]


# ---- TEST 10 -----------------------------------------------------------------------------------------------------

def test_10_calculation_mismatch_and_defect_report(client):
    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV)
    upload_csv(client, apps[1], B_CSV.replace("83.33%", "85.00%"))
    cfg = analyze(client, pid)["config"]
    L = by_letter(apps)
    rule = next(r for r in cfg["rules"] if r["app"] == L["B"] and r["field"] == "Order Completion")
    assert rule["expression"] == "[A:Completed Orders] / ([A:Completed Orders] + [A:Pending Orders]) * 100"
    assert rule["review"] == "needs_review" and rule["mismatches"] == [{"shown": "85.00%", "expected": "83.33"}]
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    bad = rows(r, "Order Completion (calculated)", "FAIL")
    assert len(bad) == 1 and bad[0]["record_key"] == "1001"
    assert bad[0]["expected"] == "83.33" and bad[0]["actual"] == "85.00%" and bad[0]["difference"] == "1.67"
    assert r["detail"]["summary"]["calculation_mismatches"] == 1
    d = client.post(f"{W}/runs/{r['id']}/rows/{bad[0]['id']}/defect").json()["defect"]
    assert "Order Completion" in d["title"] and "83.33" in d["title"] and "85.00%" in d["title"]
    data = d["data"]
    assert data["applications"] == ["Sales Portal", "Order System"] and data["difference"] == "1.67"
    assert any("Expected calculation" in s for s in data["steps"])
    again = client.post(f"{W}/runs/{r['id']}/rows/{bad[0]['id']}/defect").json()["defect"]
    assert again["id"] == d["id"]  # not created twice
    html = client.get(f"{W}/runs/{r['id']}/report.html").text
    assert "Calculation validation" in html and d["id"] in html and "Actual defect" in html
    x = client.get(f"{W}/runs/{r['id']}/report.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    ex = client.post(f"{W}/runs/{r['id']}/rows/{bad[0]['id']}/explain").json()
    assert ex["ok"] and "83.33" in ex["rules"]


# ---- TEST 11b: tester decisions ------------------------------------------------------------------------------------

def test_11b_tester_decision_accepts_a_known_difference(client):
    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV)
    upload_csv(client, apps[1], B_CSV.replace("18750.00", "18700.00"))
    save(client, pid, approve_all(analyze(client, pid)["config"]))
    r = run(client, pid)
    bad = [x for x in rows(r, "Total Sales", "FAIL") if x["record_key"] == "1003"][0]
    client.post(f"{W}/runs/{r['id']}/rows/{bad['id']}/decision",
                json={"decision": "valid_business_difference", "note": "B excludes a returned order"})
    r2 = run(client, pid)
    again = [x for x in rows(r2, "Total Sales") if x["record_key"] == "1003"][0]
    assert again["result"] == "ACCEPTED" and again["original_result"] == "FAIL"
    assert again["decision"]["label"] == "Valid business difference"
    hist = client.get(f"{W}/{pid}/decisions").json()["items"]
    assert hist[0]["decision"] == "valid_business_difference"
    # the approved mapping was not changed by the decision
    assert client.get(f"{W}/{pid}").json()["saved_mapping"]["version"] == 1


# ---- TEST 12 -----------------------------------------------------------------------------------------------------

def test_12_missing_duplicate_and_mismatched_records(client):
    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV + "1005,Epsilon,\"9,000\",9,1,0,02\n")
    b = B_CSV.replace("1003,18750.00", "1003,18000.00") + "1002,32500.00,90.00%,Pending\n"
    upload_csv(client, apps[1], b)
    save(client, pid, approve_all(analyze(client, pid)["config"]))
    r = run(client, pid)
    cats = {(x["category"], x["record_key"]) for x in rows(r, result="FAIL")}
    assert ("missing_record", "1005") in cats
    assert ("duplicate_record", "1002") in cats
    assert ("value_mismatch", "1003") in cats
    s = r["detail"]["summary"]
    assert s["missing_records"] == 1 and s["duplicate_records"] == 1 and s["field_mismatches"] >= 1
    assert r["result"] == "FAIL"


# ---- TEST 13 -----------------------------------------------------------------------------------------------------

def test_13_incomplete_screenshots_give_warnings_not_pass(client, rt):
    pid, apps = new(client, "Order System", "Sales Portal", "Analytics")
    upload_csv(client, apps[0], A_CSV)
    # B: a screenshot that shows only two of the four customers, one value read with low confidence
    screen(client, rt, apps[1], {"tables": [{"headers": ["Customer Reference", "Sales Amount"], "rows": [
        {"Customer Reference": cell("1001"), "Sales Amount": cell("50,000.00")},
        {"Customer Reference": cell("1002"), "Sales Amount": cell("32,800.00", 0.41)}]}]})
    # C: a chart without value labels
    screen(client, rt, apps[2], {"charts": [{"title": "Sales by Customer", "categories": ["Alpha", "Beta"],
                                             "points": {}, "extracted": False,
                                             "note": "The chart has no value labels"}]})
    an = analyze(client, pid)
    b = next(a for a in an["apps"] if a["letter"] == "B")
    assert not b["coverage"]["complete"] and b["coverage"]["low_confidence"] == 1
    cfg = an["config"]
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    assert r["result"] != "PASS"
    miss = [x for x in rows(r) if x["category"] == "not_on_screenshots"]
    assert {x["record_key"] for x in miss} == {"1003", "1004"} and {x["result"] for x in miss} == {"WARNING"}
    low = [x for x in rows(r, "Total Sales") if x["record_key"] == "1002"][0]
    assert low["result"] == "WARNING" and low["category"] == "ocr_uncertainty"
    assert rows(r, result="WARNING") and r["detail"]["summary"]["unverified_visual"] >= 1
    assert r["detail"]["coverage_note"] and "Sales Portal" in r["detail"]["coverage_note"]
    assert not rows(r, result="FAIL")


def test_13b_correcting_an_ocr_value(client, rt):
    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV)
    ev = screen(client, rt, apps[1], {"tables": [{"headers": ["Customer Reference", "Sales Amount"], "rows": [
        {"Customer Reference": cell("1001"), "Sales Amount": cell("50,000.00")},
        {"Customer Reference": cell("1002"), "Sales Amount": cell("32,800.00", 0.41)}]}]})
    analyze(client, pid)
    r = client.post(f"{W}/{pid}/correct", json={"evidence_id": ev, "value": "32,500.00",
                                                "where": {"kind": "table", "table": 0, "row": 1,
                                                          "field": "Sales Amount"}}).json()
    c = r["screen"]["tables"][0]["rows"][1]["Sales Amount"]
    assert c == {**c, "value": "32,500.00", "ocr_value": "32,800.00", "confidence": 1.0, "method": "tester"}
    an = analyze(client, pid)
    assert next(a for a in an["apps"] if a["letter"] == "B")["coverage"]["low_confidence"] == 0


# ---- TEST 14 and safety -------------------------------------------------------------------------------------------

def test_14_existing_functionality_still_answers(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/settings").json()["ok"]
    # TFS endpoints are still there (offline here: answered, not missing)
    assert client.get("/api/tfs/profiles").status_code != 404
    # the earlier comparison API still works and the workspace is built on it
    p = client.post("/api/comparison/projects", json={"name": "Old style"}).json()
    assert p["ok"] and client.get(f"/api/comparison/projects/{p['project']['id']}").json()["ok"]
    assert any(x["id"] == p["project"]["id"] for x in client.get(W).json()["items"])
    # the dashboard menu shows one comparison entry instead of 18
    app_js = (Path(__file__).resolve().parents[1] / "web" / "js" / "app.js").read_text()
    assert "integration-comparison" in app_js and "cmp-mapping" not in app_js


def test_security_formula_upload_and_password(client, rt):
    with pytest.raises(formula.FormulaError):
        formula.evaluate("__import__('os').system('x')", lambda *a: 1)
    with pytest.raises(formula.FormulaError):
        formula.validate("[A:X].__class__")
    pid, apps = new(client, "Order System", "Sales Portal")
    bad = client.post(f"{W}/applications/{apps[0]['id']}/screenshots?name=x.png", content=b"<script>alert(1)</script>")
    assert bad.status_code in (400, 415, 422)
    r = client.patch(f"{W}/applications/{apps[0]['id']}", json={"url": "https://erp.example.local/orders",
                                                               "auth_method": "form", "username": "qa",
                                                               "password": "S3cret-Pass"}).json()
    assert r["application"]["has_password"] and "S3cret" not in json.dumps(r)
    row = rt.db.query_one("SELECT config FROM cmp_applications WHERE id = ?", (apps[0]["id"],))
    assert "S3cret" not in row["config"]
    cfg = {"fields": [], "rules": [{"app": apps[1]["id"], "field": "X", "expression": "open('x')", "approve": True}]}
    assert client.put(f"{W}/{pid}/mapping", json={"config": cfg}).status_code == 422
    audit = json.dumps(rt.db.query("SELECT details FROM cmp_audit"))
    assert "S3cret" not in audit


def test_live_capture_and_pdf(client, site):
    """'Connect to application': the page is read in the browser (existing extraction framework), read-only."""
    pytest.importorskip("playwright")
    import time

    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV)
    client.patch(f"{W}/applications/{apps[1]['id']}", json={"url": f"{site}/integration/app_b.html"})
    jobs = client.post(f"{W}/applications/{apps[1]['id']}/capture", json={}).json()["jobs"]
    for j in jobs:
        for _ in range(300):
            if client.get(f"/api/comparison/jobs/{j['job']}").json()["status"] != "running":
                break
            time.sleep(0.2)
    an = analyze(client, pid)
    b = next(a for a in an["apps"] if a["letter"] == "B")
    assert b["coverage"]["live"] >= 1 and b["coverage"]["complete"]
    assert b["tables"][0]["total_rows"] == 4 and b["tables"][0]["source"] == "live"
    save(client, pid, approve_all(an["config"]))
    r = run(client, pid)
    assert rows(r, "Total Sales") and {x["result"] for x in rows(r, "Total Sales")} <= {"PASS", "PASS*"}
    assert "live" in rows(r, "Total Sales")[0]["sources"]
    pdf = client.get(f"{W}/runs/{r['id']}/report.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
