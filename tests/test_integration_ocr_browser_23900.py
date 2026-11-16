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
