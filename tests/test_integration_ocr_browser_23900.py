"""23.9.0 SkyGuard Integration Comparison, OCR and Browser final patch: the acceptance tests of the patch.

The screenshots are real: each fixture page of tests/site/integration_ocr is rendered by Chromium, saved as PNG or
JPEG, uploaded like a tester uploads it and read by the bundled offline OCR engine (RapidOCR, ONNX models in the
wheel, no download). Expected values come from truth.json, which the fixture builder computes with Decimal; the
application never sees it.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from qa_platform.comparison import ocr

W = "/api/comparison/workspace"
FIX = Path(__file__).parent / "site" / "integration_ocr"
TRUTH = json.loads((FIX / "truth.json").read_text())
HAS_OCR = bool(ocr.engines({}).get("rapidocr"))
needs_ocr = pytest.mark.skipif(not HAS_OCR, reason="the bundled offline OCR runtime is not installed here")


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    """Every fixture page rendered to a PNG (and a JPEG of the dark table), as a browser screenshot."""
    from playwright.sync_api import sync_playwright

    out = tmp_path_factory.mktemp("shots")
    files = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
        for html in sorted(FIX.glob("*.html")):
            pg.goto(html.as_uri())
            target = out / f"{html.stem}.png"
            pg.screenshot(path=str(target), full_page=True)
            files[html.stem] = target
        pg.goto((FIX / "app_b_detailed.html").as_uri())
        jpg = out / "app_b_detailed.jpg"
        pg.screenshot(path=str(jpg), full_page=True, type="jpeg", quality=88)
        files["app_b_detailed_jpg"] = jpg
        b.close()
    return files


def new(client, *names):
    r = client.post(W, json={"name": "Sales lineage", "applications": list(names)}).json()
    assert r["ok"], r
    return r["comparison"]["id"], r["applications"]


def upload(client, app, path: Path):
    r = client.post(f"{W}/applications/{app['id']}/screenshots?name={path.name}", content=path.read_bytes()).json()
    assert r["ok"], r
    return r["screenshot"]["id"]


def all_records(client, app):
    r = client.patch(f"{W}/applications/{app['id']}", json={"screens_complete": True}).json()
    assert r["ok"], r


def analyze(client, pid):
    r = client.post(f"{W}/{pid}/analyze", json={}).json()
    assert r["ok"], r
    return r["analysis"]


def approve_all(cfg):
    for f in cfg["fields"]:
        f["approve"] = len(f["members"]) > 1 or f.get("is_key")
    for r in cfg["rules"]:
        r["approve"] = True
    for s in cfg["statuses"]:
        for g in s["groups"]:
            g["approve"] = True
    for c in cfg["charts"]:
        c["approve"] = True
    return cfg


def save(client, pid, cfg):
    r = client.put(f"{W}/{pid}/mapping", json={"config": cfg}).json()
    assert r["ok"], r
    return r["saved"]


def run(client, pid):
    r = client.post(f"{W}/{pid}/run").json()
    assert r["ok"], r
    return r["run"]


def rows(run_, field=None, result=None):
    return [r for r in run_["detail"]["rows"] if (field is None or r["field"] == field)
            and (result is None or r["result"] == result)]


def num(v) -> Decimal:
    return Decimal(str(v).replace(",", "").replace("%", "").replace("AED", "").strip())


def read_shot(path: Path) -> dict:
    from qa_platform.comparison import screen_reader

    res = ocr.read_for_layout(str(path), {})
    g = res.pop("graphics", None) or ocr.graphics(str(path), {})
    return screen_reader.read_screen(res, g)


# ---- Test 1 / 2: tables from screenshots ----------------------------------------------------------------------------

@needs_ocr
@pytest.mark.parametrize("shot", ["app_a_table", "app_b_detailed", "app_b_detailed_jpg"])
def test_01_table_screenshot_values_are_exact(shots, shot):
    """Light and dark tables, PNG and JPEG: every cell equals the true value; totals are totals, not records;
    '(AED)' under a header is the unit, not a field."""
    parsed = read_shot(shots[shot])
    t = parsed["tables"][0]
    assert len(t["rows"]) == 8, t["headers"]
    truth = {o["Order ID"]: o for o in TRUTH["orders"]}
    money_cols = [h for h in t["headers"] if h in ("Unit Price", "Line Total", "Subtotal", "Discount", "Tax",
                                                   "Final Amount")]
    assert "AED" not in " ".join(t["headers"]) and "(AED)" not in t["headers"]
    if shot.startswith("app_b"):
        assert all((t.get("units") or {}).get(h) == "AED" for h in money_cols)
    for r in t["rows"]:
        o = truth[r["Order ID"]["value"]]
        for h in ("Customer", "City", "Product"):
            assert r[h]["value"] == o[h]
        assert num(r["Quantity"]["value"]) == num(o["Quantity"])
        for h in money_cols:
            assert num(r[h]["value"]) == num(o[h]), (h, r[h], o[h])
        assert all("raw" in c or "ocr_value" in c or "value" in c for c in r.values())
    labels = {x["label"].upper() for x in t.get("totals") or []}
    assert labels & {"TOTAL", "GRAND TOTAL"}
    assert parsed["status"] in ("COMPLETE", "NEEDS_REVIEW")  # NEEDS_REVIEW only for low-confidence cells


@needs_ocr
def test_01b_ocr_engine_diagnostics_offline(shots):
    """The engine is found without the network and explains itself (Part 1 diagnostics)."""
    d = ocr.diagnose({}, Path(shots["app_a_table"]))
    rap = next(e for e in d["engines"] if e["engine"] == "rapidocr")
    assert rap["status"] == "ready" and d["selected"] == "rapidocr" and d["offline"]
    assert d["sample"]["engine"] == "rapidocr" and d["sample"]["lines"] >= 9


# ---- Tests 3 / 4 / scenario E: tables, bar chart, pie chart, summary table - four applications ----------------------

@needs_ocr
def test_02_four_application_lineage_finds_only_the_real_defect(client, shots):
    pid, apps = new(client, "Sales Transactions", "Detailed Sales", "Sales by City", "Sales Distribution",
                    "City Summary")
    for a, k in zip(apps, ["app_a_table", "app_b_detailed", "app_c_bar", "app_d_pie", "summary"]):
        upload(client, a, shots[k])
        all_records(client, a)  # the tester declares that the screenshots show every record
    an = analyze(client, pid)
    cfg = an["config"]
    by = {a["letter"]: a["app_id"] for a in an["apps"]}
    # semantic mapping: City ~ Territory; the key is the order id
    city = next(r for r in cfg["fields"] if (r["members"].get(by["A"]) or {}).get("field") == "City")
    assert city["members"][by["E"]]["field"] == "Territory"
    assert next(r for r in cfg["fields"] if r.get("is_key"))["members"][by["B"]]["field"] == "Order ID"
    # the bar chart is the SUM of a sales amount by city, never the quantity
    bar = next(c for c in cfg["charts"] if c["chart"] == "Total Sales by City")
    assert bar["value_field"] in ("Line Total", "Subtotal") and bar["aggregate"] == "sum"
    assert bar["category_field"] == "City"
    share = next(c for c in cfg["charts"] if c["chart"].endswith("share %"))
    assert share["aggregate"] == "share"
    # Final Amount = Subtotal - Discount + Tax on the detailed screen; no absurd rules (discount from a final amount)
    rules = {r["field"]: r["expression"] for r in cfg["rules"]}
    assert rules.get("Final Amount") == "[B:Subtotal] - [B:Discount] + [B:Tax]"
    assert "Discount" not in rules and "Tax" not in rules
    assert not any("MAX(" in e or "MIN(" in e for e in rules.values())
    save(client, pid, approve_all(cfg))
    r = run(client, pid)
    fails = rows(r, result="FAIL")
    assert len(fails) == 1, [(x["field"], x["record_key"], x["explanation"]) for x in fails]
    f = fails[0]
    assert f["record_key"] == "Dubai" and f["category"] == "chart_mismatch"
    assert num(f["expected"]) == Decimal(TRUTH["city_line_total"]["Dubai"]) and num(f["actual"]) == Decimal("8950")
    assert f["source_app"] and f["suggested_classification"] == "actual_defect" and f["rule"]
    # pie shares (city sales / total x 100, rounded as shown) and the summary table pass
    pie = rows(r, "Sales Distribution — share % (chart)")
    assert len(pie) == 4 and {x["result"] for x in pie} == {"PASS"}
    summ = rows(r, "Share % by Territory (summary table)")
    assert len(summ) == 4 and {x["result"] for x in summ} == {"PASS"}
    final = rows(r, "Final Amount (calculated)")
    assert len(final) == 8 and {x["result"] for x in final} == {"PASS"}
    assert r["result"] == "FAIL"


@needs_ocr
def test_03_unlabeled_bar_chart_is_approximate_not_pass(client, shots):
    pid, apps = new(client, "Sales Transactions", "Sales chart")
    upload(client, apps[0], shots["app_a_table"])
    upload(client, apps[1], shots["bar_unlabeled"])
    for a in apps:
        all_records(client, a)
    an = analyze(client, pid)
    ch = an["apps"][1]["charts"][0]
    assert all(p.get("value") for p in ch["points"].values())
    save(client, pid, approve_all(an["config"]))
    r = run(client, pid)
    res = {x["record_key"]: x for x in r["detail"]["rows"] if x["field"].endswith("(chart)")}
    assert len(res) == 4
    assert {x["result"] for x in res.values()} <= {"WARNING", "NEEDS_REVIEW"}  # never PASS or FAIL on estimates
    assert any(x["category"] == "approximate_match" for x in res.values())


@needs_ocr
@pytest.mark.parametrize("shot,kind", [("hbar", "bar"), ("donut", "donut")])
def test_04_horizontal_bar_and_donut(shots, shot, kind):
    parsed = read_shot(shots[shot])
    ch = parsed["charts"][0]
    vals = {k: num(v["value"]) for k, v in ch["points"].items()}
    truth = {k: Decimal(v) for k, v in TRUTH["city_line_total"].items()}
    assert vals == truth, vals
    if kind == "donut":
        assert ch["type"] in ("donut", "doughnut") and ch["status"] == "COMPLETE"


# ---- Test 5 / 6: live URL capture ------------------------------------------------------------------------------------

def _extract(site, page, **cfg):
    from qa_platform.comparison.extract_web import WebExtractor

    conf = {"base_url": f"{site}/integration_live/{page}", "auth_method": "none", **cfg}
    with WebExtractor(conf) as ex:
        ex.login()
        ex.wait_for_data()
        r = ex.table({})
        return r, ex.charts(), list(ex.other_tables), int(ex.canvases), list(ex.blocked)


def _values(r, field):
    return [x["fields"][field]["value"] for x in r["records"]]


@pytest.mark.parametrize("page,method,count", [
    ("js_table.html", "html table", 25),            # JavaScript table, library from another host, 3 pages + Next
    ("grid.html", "aria grid", 25),                 # virtualized ARIA grid: only 9 rows exist at a time
    ("frames.html", "html table", 12),              # report inside an iframe, rowspan/colspan headers, tfoot total
    ("shadow.html", "html table", 8),               # web component (open shadow root)
    ("divgrid.html", "div grid", 15),               # div layout without table or ARIA roles
    ("datatables.html", "datatables api", 25),      # DataTables API: every filtered row, 5 drawn
])
def test_05_live_capture_layers(site, page, method, count):
    pytest.importorskip("playwright")
    r, _, _, _, blocked = _extract(site, page)
    st = r["stats"]
    assert st["records"] == count and st["complete"], st
    assert st["method"].startswith(method)
    ids = _values(r, "Invoice No")
    assert len(set(ids)) == count and ids[0] == "INV-001"  # no duplicates from scrolling or pages
    if page == "frames.html":
        assert st["headers"] == ["Invoice No", "Billing City", "Billing Status", "Amount"]
        assert st["totals"][0]["values"]["Amount"] == "10,725.00"  # the total row is a total, not a record
    if page == "js_table.html":
        assert st["expected_total"] == 25 and st["pages_visited"] == 3


def test_05b_old_host_rule_gave_zero_records(site):
    """The 23.8.0 cause, reproduced: with only the application's own host allowed, the table library (served from
    another host, as from a CDN) is blocked and the table never renders."""
    pytest.importorskip("playwright")
    r, _, _, _, blocked = _extract(site, "js_table.html", strict_hosts=True, data_timeout_ms=3000)
    assert r["stats"]["records"] == 0 and any("localhost" in b for b in blocked)


def test_05c_blocked_addresses_and_redirects():
    from qa_platform.comparison import extract_web as xw

    assert xw.site_of("www.shop.example.com") == "example.com" and xw.site_of("a.b.co.uk") == "b.co.uk"
    assert xw.private_host("127.0.0.1") and xw.private_host("10.1.2.3") and xw.private_host("intranet.local")
    assert not xw.host_allowed("http://169.254.169.254/latest/meta-data", {"169.254.169.254"})


def test_06_chart_libraries_and_canvas_fallback(site):
    pytest.importorskip("playwright")
    r, charts, _, canvases, _ = _extract(site, "charts.html")
    by = {c["title"]: c["series_values"][0]["values"] for c in charts}
    assert by["Sales by City (d3)"] == {"Dubai": 12512.5, "Sharjah": 9900, "Abu Dhabi": 10725, "Ajman": 11550}
    assert by["Invoices by Status"] == {"Paid": "9", "Pending": "8", "Cancelled": "8"}
    assert canvases == 1 and r["stats"]["records"] == 0  # the canvas chart has no data in the page code


def _wait_jobs(client, jobs):
    import time

    for j in jobs:
        for _ in range(600):
            if client.get(f"/api/comparison/jobs/{j['job']}").json()["status"] != "running":
                break
            time.sleep(0.2)


@needs_ocr
def test_06b_workspace_capture_reads_canvas_chart_by_ocr(client, site):
    """'Connect' a page whose chart is a canvas: one browser session; the library charts come from the page code,
    the canvas chart from the offline OCR of the page screenshot (marked as such)."""
    pytest.importorskip("playwright")
    pid, apps = new(client, "Charts page", "Other")
    client.patch(f"{W}/applications/{apps[0]['id']}", json={"url": f"{site}/integration_live/charts.html"})
    jobs = client.post(f"{W}/applications/{apps[0]['id']}/capture", json={}).json()["jobs"]
    assert len(jobs) == 1  # one session for table, KPI cards and charts
    _wait_jobs(client, jobs)
    an = analyze(client, pid)
    a = an["apps"][0]
    titles = {c["title"]: c for c in a["charts"]}
    assert "Sales by City (d3)" in titles
    canvas = next(c for t, c in titles.items() if "Amount by City" in t)
    vals = {k: num(v["value"]) for k, v in canvas["points"].items()}
    assert vals == {"Dubai": Decimal("12512.50"), "Sharjah": Decimal("9900.00"), "Abu Dhabi": Decimal("10725.00"),
                    "Ajman": Decimal("11550.00")}


def test_06c_workspace_capture_paginated_js_table(client, site):
    pytest.importorskip("playwright")
    pid, apps = new(client, "Invoices", "Other")
    client.patch(f"{W}/applications/{apps[0]['id']}", json={"url": f"{site}/integration_live/js_table.html"})
    jobs = client.post(f"{W}/applications/{apps[0]['id']}/capture", json={}).json()["jobs"]
    _wait_jobs(client, jobs)
    an = analyze(client, pid)
    a = an["apps"][0]
    assert a["coverage"]["complete"] and a["coverage"]["scope"] == "verified"
    assert a["tables"][0]["total_rows"] == 25
    inp = a["coverage"]["inputs"][0]
    assert inp["records"] == 25 and inp["expected_total"] == 25 and inp["status"] == "COMPLETE"


# ---- Part 14: filters ------------------------------------------------------------------------------------------------

def _filters_app(client, site, page, name):
    pid, apps = new(client, name, "Sales portal")
    client.patch(f"{W}/applications/{apps[0]['id']}", json={"url": f"{site}/integration_live/{page}"})
    client.patch(f"{W}/applications/{apps[1]['id']}", json={"url": f"{site}/integration_live/filters_b.html"})
    return pid, apps


def test_14_discover_classify_and_fetch_filtered(client, site):
    pytest.importorskip("playwright")
    pid, apps = _filters_app(client, site, "filters.html", "Invoices")
    a = apps[0]["id"]
    r = client.post(f"{W}/applications/{a}/filters/discover", json={}).json()
    assert r["ok"], r
    ctl = {c["label"]: c for c in r["filters"]["controls"]}
    is_f = {k: v["is_filter"] for k, v in ctl.items()}
    assert is_f["Region"] and is_f["City"] and is_f["Status"] and is_f["Date From"] and is_f["Date To"] and is_f["Search"]
    assert not is_f["Language"] and not any(v for k, v in is_f.items() if "entries" in k.lower() or k == "Show")
    assert ctl["City"]["depends_on"] == ctl["Region"]["id"]          # cascading dropdown found
    assert ctl["Date From"].get("range", {}).get("role") == "from"
    assert r["filters"]["buttons"]["apply"]["text"].startswith("Apply")
    vals = {ctl["Region"]["id"]: "North", ctl["City"]["id"]: "Dubai", ctl["Status"]["id"]: "Paid"}
    j = client.post(f"{W}/applications/{a}/filters/apply", json={"values": vals}).json()
    import time

    for _ in range(600):
        st = client.get(f"/api/comparison/jobs/{j['job']}").json()
        if st["status"] != "running":
            break
        time.sleep(0.2)
    assert st["status"] == "done", st
    want = [x for x in TRUTH_INVOICES() if x["city"] == "Dubai" and x["status"] == "Paid"]
    ds = client.get(f"/api/comparison/datasets/{st['dataset_id']}").json()
    assert st["run"]["records"] == len(want) and st["run"]["complete"], ds.get("dataset", ds).get("stats")
    assert st["run"]["filters"] == {"Region": "North", "City": "Dubai", "Status": "Paid"}
    assert all(v["ok"] for v in st["verified"].values())
    fs = client.get(f"{W}/applications/{a}/filters").json()
    assert fs["applied"]["City"] == "Dubai" and fs["runs"][-1]["dataset"] == st["dataset_id"]
    client.post(f"{W}/applications/{a}/filters/close")


def TRUTH_INVOICES():
    cities, status = ["Dubai", "Sharjah", "Abu Dhabi", "Ajman"], ["Paid", "Pending", "Cancelled"]
    return [{"id": f"INV-{n:03d}", "city": cities[(n - 1) % 4], "status": status[(n - 1) % 3],
             "amount": Decimal(n) * Decimal("137.5")} for n in range(1, 26)]


def _wait(client, job):
    import time

    for _ in range(900):
        st = client.get(f"/api/comparison/jobs/{job}").json()
        if st["status"] != "running":
            return st
        time.sleep(0.2)
    return st


def test_14b_equivalent_filters_sync_scope_and_filter_sets(client, site):
    pytest.importorskip("playwright")
    pid, apps = _filters_app(client, site, "filters.html", "Invoices")
    a, b = apps[0]["id"], apps[1]["id"]
    fa = {c["label"]: c for c in client.post(f"{W}/applications/{a}/filters/discover", json={}).json()["filters"]["controls"]}
    fb = {c["label"]: c for c in client.post(f"{W}/applications/{b}/filters/discover", json={}).json()["filters"]["controls"]}
    assert fb["Territory"]["is_filter"] and fb["Order State"]["is_filter"]
    links = client.get(f"{W}/{pid}/filter-links").json()["links"]
    by = {l_["name"]: l_ for l_ in links}
    # Region ~ Territory (same meaning and the same options); Status ~ Order State; City has no equivalent
    assert by["Region"]["members"][b]["label"] == "Territory"
    assert by["Status"]["members"][b]["label"] == "Order State"
    assert b not in by["City"]["members"]
    # the tester approves Region and Status; the portal has no City filter: filter its data locally on City
    approved = [{**by["Region"], "approve": True}, {**by["Status"], "approve": True},
                {"name": "City", "members": {a: {"filter": fa["City"]["id"], "label": "City"},
                                             b: {"local_field": "City"}}, "approve": True}]
    saved = client.put(f"{W}/{pid}/filter-links", json={"links": approved}).json()["links"]
    assert {x["status"] for x in saved} == {"approved"}
    va = {fa["Region"]["id"]: "North", fa["City"]["id"]: "Dubai", fa["Status"]["id"]: "Paid"}
    sync = client.post(f"{W}/{pid}/filters/sync", json={"source_app": a, "values": va}).json()
    assert sync["values"][b] == {fb["Territory"]["id"]: "North", fb["Order State"]["id"]: "Paid", "local:City": "Dubai"}
    # scope mismatch first: the portal filtered on another state -> NOT COMPARABLE, never PASS/FAIL
    assert _wait(client, client.post(f"{W}/applications/{a}/filters/apply", json={"values": va}).json()["job"])["status"] == "done"
    wrong = {fb["Territory"]["id"]: "North", fb["Order State"]["id"]: "Pending"}
    assert _wait(client, client.post(f"{W}/applications/{b}/filters/apply", json={"values": wrong}).json()["job"])["status"] == "done"
    an = analyze(client, pid)
    # the two scopes hold different invoices: the identifier is suggested by name, for review, not auto-approved
    kf = next(f for f in an["config"]["fields"] if f.get("is_key"))
    assert kf["name"] == "Invoice No" and kf["status"] == "needs_review" and "by name" in kf["evidence"][0]
    unkeyed = [dict(f, is_key=False) if f.get("is_key") else f for f in an["config"]["fields"]]
    save(client, pid, approve_all({**an["config"], "fields": unkeyed}))
    nokey = run(client, pid)
    assert rows(nokey, "Record key")[0]["result"] == "INCOMPLETE" and nokey["result"] != "PASS"
    save(client, pid, approve_all(an["config"]))
    r = run(client, pid)
    scope = rows(r, "Filter scope: Status")
    assert scope and scope[0]["result"] == "NOT_COMPARABLE" and scope[0]["category"] == "scope_mismatch"
    # synced values: same scope; the portal's City is filtered locally, so both hold the 3 Dubai/Paid invoices
    assert _wait(client, client.post(f"{W}/applications/{b}/filters/apply", json={"values": sync["values"][b]}).json()["job"])["status"] == "done"
    r = run(client, pid)
    assert not rows(r, "Filter scope: Status") and not rows(r, result="FAIL")
    assert r["detail"]["filters"][b]["local:City"] == "Dubai"
    assert any(x.get("record_key") and x["result"] == "PASS" for x in r["detail"]["rows"])
    assert {x["record_key"] for x in r["detail"]["rows"] if x.get("record_key")} <= {"inv-001", "inv-013", "inv-025"}
    # filter sets: only the approved ones run
    sets = [{"name": "North paid", "approved": True, "values": {a: va, b: sync["values"][b]}},
            {"name": "Not approved", "approved": False, "values": {a: {fa["Region"]["id"]: "South"}}}]
    client.put(f"{W}/{pid}/filter-sets", json={"sets": sets})
    j = client.post(f"{W}/{pid}/filter-sets/run").json()
    assert j["sets"] == 1
    done = _wait(client, j["job"])
    assert done["status"] == "done" and [x["set"] for x in done["results"]] == ["North paid"]
    for x in (a, b):
        client.post(f"{W}/applications/{x}/filters/close")


# ---- Windows OCR readings (found on the Windows CI runners) -------------------------------------------------------

def _word(t, x, y, c=0.9):
    return {"text": t, "box": [x, y, 9 * len(t), 14], "confidence": c}


def _orders_page(cancelled_1002="0", pending_1003="10"):
    words = [_word("Customer", 10, 10), _word("ID", 90, 10), _word("Completed", 200, 10), _word("Pending", 320, 10),
             _word("Cancelled", 440, 10)]
    rows = [("1001", "100", "20", "5"), ("1002", "45", "5", cancelled_1002), ("1003", "30", pending_1003, "10"),
            ("1004", "12", "3", "5")]
    for i, (k, c, p, x) in enumerate(rows):
        y = 40 + 30 * i
        words += [_word(k, 10, y), _word(c, 200, y), _word(p, 320, y)]
        if x:
            words.append(_word(x, 440, y))
    return {"engine": "windows", "lines": [{"words": words}]}


def test_digits_read_as_letters_in_a_number_column_are_corrected_and_flagged():
    from qa_platform.comparison import screen_reader

    t = screen_reader.read_screen(_orders_page(pending_1003="IO"))["tables"][0]
    cell = t["rows"][2]["Pending"]
    assert cell["value"] == "10" and cell["read_as"] == "IO"
    assert cell["confidence"] <= screen_reader.LOW and "letters read as digits" in cell["method"]
    assert t["status"] == "NEEDS_REVIEW" and any("letters in place of digits" in x for x in t["issues"])
    assert "Pending" in t["numeric_columns"]
    # a text column is left alone
    page = _orders_page()
    page["lines"][0]["words"] += [_word("Name", 560, 10)] + [_word(n, 560, 40 + 30 * i)
                                                             for i, n in enumerate(["Io", "Ola", "Il", "Lo"])]
    t = screen_reader.read_screen(page)["tables"][0]
    assert [r["Name"]["value"] for r in t["rows"]] == ["Io", "Ola", "Il", "Lo"]


def test_an_empty_number_cell_is_read_again_and_only_a_number_inside_it_is_used():
    from qa_platform.comparison import ocr, screen_reader

    page = _orders_page(cancelled_1002="")
    gaps = screen_reader.empty_number_cells(screen_reader.read_screen(page))
    assert len(gaps) == 1
    x, y, w, h = gaps[0]
    assert x <= 440 <= x + w and y <= 70 <= y + h  # the Cancelled cell of 1002
    seen = []

    def reader(path, rects, settings=None):
        seen.append(rects)
        return [_word("0", 441, 70), _word("Total", 20, 400), _word("abc", 442, 71)]

    out = ocr._fill_empty_cells(Path("shot.png"), page, None, reader=reader)
    assert seen == [gaps] and out["cell_rereads"] == {"cells": 1, "read": 1}
    t = screen_reader.read_screen(out)["tables"][0]
    assert t["rows"][1]["Cancelled"]["value"] == "0"
    assert t["rows"][1]["Cancelled"]["confidence"] <= screen_reader.LOW
    # nothing read: the cell stays empty, nothing is invented
    out = ocr._fill_empty_cells(Path("shot.png"), _orders_page(cancelled_1002=""), None, reader=lambda *a, **k: [])
    assert screen_reader.read_screen(out)["tables"][0]["rows"][1].get("Cancelled", {}).get("value") in (None, "")
    # an engine failure keeps the first reading
    def broken(*a, **k):
        raise OSError("powershell missing")
    out = ocr._fill_empty_cells(Path("shot.png"), _orders_page(cancelled_1002=""), None, reader=broken)
    assert out["cell_rereads"]["read"] == 0 and "powershell missing" in out["cell_rereads"]["error"]


def test_a_cell_read_again_counts_only_when_the_copies_agree():
    from qa_platform.comparison import ocr

    rect = [100, 200, 40, 20]  # x, y, w, h on the screenshot
    cw, gap, pad = 40 * ocr.CELL_SCALE, 20 * ocr.CELL_SCALE, ocr.CELL_PAD

    def at(k, text, dx=30):
        return {"text": text, "box": [pad + k * (cw + gap) + dx, pad + 6, 30, 40]}

    got = ocr._cell_number(rect, [at(0, "0"), at(1, "0"), at(2, "0")])
    assert got["text"] == "0" and got["copies"] == 3
    assert got["box"][0] == 110 and got["box"][1] == 202  # mapped back onto the screenshot
    assert ocr._cell_number(rect, [at(0, "0"), at(2, "0")])["text"] == "0"  # one copy missed
    assert ocr._cell_number(rect, [at(1, "0")]) is None  # one copy only: not enough
    assert ocr._cell_number(rect, [at(0, "0"), at(1, "8"), at(2, "0")]) is None  # the copies disagree
    assert ocr._cell_number(rect, [at(0, "O"), at(1, "O"), at(2, "O")]) is None  # not a number
    run = {"text": "000", "box": [pad + 30, pad + 6, 2 * (cw + gap) + 40, 40]}  # copies read as one word
    assert ocr._cell_number(rect, [run])["text"] == "0"
    assert ocr._cell_number(rect, [dict(run, text="080")]) is None


def test_a_failed_cell_reread_is_reported_not_hidden():
    from qa_platform.comparison import ocr
    from qa_platform.errors import QAError

    def failing(*a, **k):
        raise QAError("OCR_FAILED", "Windows OCR could not read the table cells again.", details="Add-Type failed")
    out = ocr._fill_empty_cells(Path("shot.png"), _orders_page(cancelled_1002=""), None, reader=failing)
    assert out["cell_rereads"]["read"] == 0 and "Add-Type failed" in out["cell_rereads"]["error"]
