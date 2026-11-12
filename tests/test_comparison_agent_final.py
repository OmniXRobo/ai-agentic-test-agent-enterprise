"""Final patch: the Data Comparison Agent end to end against two real web pages (tests/site/cmp), through the API.

System A shows 250 invoices, 20 per page, after a form sign-in; Portal B shows the same invoices 50 per page in a
role=grid, in other formats (plain amounts, ISO dates, status words), with two real defects: INV-0100's amount
is 10 higher and INV-0200 is missing. B also shows KPI cards and a chart that must add up to its own records.
"""

from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib

import pytest

pytest.importorskip("playwright")


def _wait(client, job):
    import time

    for _ in range(600):
        j = client.get(f"/api/comparison/jobs/{job}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.2)
    raise AssertionError("extraction did not finish")


def _png(w=4, h=3, text=b"Author\x00secret-person"):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", text) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_web_comparison_end_to_end(client, rt, site):
    p = client.post("/api/comparison/projects", json={"name": "Invoices A vs B"}).json()["project"]
    pid = p["id"]
    client.patch(f"/api/comparison/projects/{pid}", json={"settings": {"reference": {"Status": {"1": "Active", "2": "Open"}}}})
    a = client.post(f"/api/comparison/projects/{pid}/applications", json={
        "name": "Finance A", "app_type": "web", "base_url": f"{site}/cmp/a.html", "auth_method": "form",
        "username": "tester", "password": "Cmp-Pass-1"}).json()["application"]
    b = client.post(f"/api/comparison/projects/{pid}/applications", json={
        "name": "Portal B", "app_type": "web", "base_url": f"{site}/cmp/b.html", "auth_method": "form",
        "username": "tester@corp.local", "password": "Cmp-Pass-1"}).json()["application"]
    # the password is in the credential store only
    assert "password" not in json.dumps(a["config"]) and a["has_password"]
    row = rt.db.query_one("SELECT config FROM cmp_applications WHERE id = ?", (a["id"],))
    assert "Cmp-Pass-1" not in row["config"]

    t = client.post(f"/api/comparison/applications/{a['id']}/test").json()
    assert t["status"] == "ok", t
    r = client.post(f"/api/comparison/applications/{b['id']}/discover", json={}).json()
    assert "screen" in r, r
    scr = r["screen"]
    assert scr["tables"] and len(scr["charts"]) >= 1 and len(scr["kpis"]) == 3

    ja = client.post(f"/api/comparison/applications/{a['id']}/extract",
                     json={"kind": "table", "key_field": "Invoice No", "screenshots": False}).json()
    jb = client.post(f"/api/comparison/applications/{b['id']}/extract",
                     json={"kind": "table", "key_field": "Client Code", "screenshots": False}).json()
    jk = client.post(f"/api/comparison/applications/{b['id']}/extract", json={"kind": "dashboard"}).json()
    for j in (ja, jb, jk):
        assert _wait(client, j["job"])["status"] == "done", _wait(client, j["job"])
    da = client.get(f"/api/comparison/datasets/{ja['dataset_id']}").json()["dataset"]
    db_ = client.get(f"/api/comparison/datasets/{jb['dataset_id']}").json()["dataset"]
    dk = client.get(f"/api/comparison/datasets/{jk['dataset_id']}").json()["dataset"]
    assert da["stats"]["records"] == 250 and da["stats"]["pages_visited"] == 13 and da["stats"]["complete"]
    assert da["stats"]["expected_total"] == 250
    assert db_["stats"]["records"] == 249 and db_["stats"]["pages_visited"] == 5 and db_["stats"]["complete"]
    kpis = {k["label"]: k["value"] for k in dk["aggregates"]["kpis"]}
    assert kpis["Total Invoices"] == "249"
    assert dk["aggregates"]["charts"][0]["series_values"][0]["values"] == {"Paid": "200", "Pending": "49"}
    # navigation log never holds the password
    nav = json.dumps(rt.db.query("SELECT step, details FROM cmp_navigation"))
    assert "Cmp-Pass-1" not in nav and "typed username and password" in nav

    sug = client.post("/api/comparison/mapping/suggest",
                      json={"source_dataset": ja["dataset_id"], "target_dataset": jb["dataset_id"]}).json()
    by = {s["source_field"]: s for s in sug["suggestions"]}
    assert by["Invoice No"]["target_field"] == "Client Code"
    assert by["Invoice Amount"]["target_field"] == "Value"
    assert by["Invoice Date"]["target_field"] == "Created On"
    assert by["Status"]["target_field"] == "State"
    assert sug["key_fields"] == {"source": "Invoice No", "target": "Client Code"}
    maps = [{**s, "status": "approved"} for s in sug["suggestions"] if s["target_field"]]
    saved = client.put(f"/api/comparison/projects/{pid}/mapping", json={
        "source_app": a["id"], "target_app": b["id"], "mappings": maps, "key_fields": sug["key_fields"]}).json()
    assert saved["mapping"]["version"] == 1

    out = client.post(f"/api/comparison/projects/{pid}/run", json={
        "source_dataset": ja["dataset_id"], "target_dataset": jb["dataset_id"]}).json()["execution"]
    d = out["detail"]
    assert out["result"] == "FAIL"
    assert d["summary"]["matched"] == 249 and d["summary"]["missing"] == 1
    cats = {(x["category"], x["record_key"]) for x in d["discrepancies"]}
    assert ("missing_record", "INV-0200") in cats
    amount = next(x for x in d["discrepancies"] if x["record_key"] == "INV-0100")
    assert amount["category"] == "currency_mismatch" and amount["difference"] == "10"
    assert not any(x["record_key"] not in ("INV-0100", "INV-0200") for x in d["discrepancies"])

    # source-to-aggregate: B's KPIs against A's records (definitions must be approved first)
    defs = [{"name": "Total invoices", "function": "count", "decimals": 0, "target": {"kpi": "Total Invoices"}},
            {"name": "Pending invoices", "function": "count", "decimals": 0,
             "where": [{"field": "Status", "op": "eq", "value": "2"}], "target": {"kpi": "Pending Invoices"}},
            {"name": "By status", "function": "count", "group_by": "Status", "decimals": 0,
             "target": {"chart": "Invoices by status"}}]
    ids = [client.post(f"/api/comparison/projects/{pid}/definitions", json={"definition": x}).json()["definition"]["id"]
           for x in defs]
    agg = client.post(f"/api/comparison/projects/{pid}/run", json={
        "source_dataset": ja["dataset_id"], "target_dataset": jk["dataset_id"], "mode": "aggregate"}).json()["execution"]
    assert all(r["result"] == "REVIEW" for r in agg["detail"]["aggregates"]["rows"])  # not approved yet
    for i in ids:
        client.post(f"/api/comparison/projects/{pid}/definitions/{i}/approve", json={"approved": True})
    agg = client.post(f"/api/comparison/projects/{pid}/run", json={
        "source_dataset": ja["dataset_id"], "target_dataset": jk["dataset_id"], "mode": "aggregate"}).json()["execution"]
    rows = {r["metric"]: r for r in agg["detail"]["aggregates"]["rows"]}
    assert rows["Total invoices"]["result"] == "FAIL" and rows["Total invoices"]["expected"] == "250"
    assert rows["Total invoices"]["difference"] == "-1"
    assert rows["Pending invoices"]["result"] == "FAIL" and rows["Pending invoices"]["expected"] == "50"
    # the chart is labelled with descriptions while A stores codes: categories do not match -> FAIL with reasons
    assert rows["By status"]["result"] == "FAIL"

    # defect review center: drafts were created from the record run, with steps taken from the navigation log
    items = client.get(f"/api/comparison/projects/{pid}/defects").json()["items"]
    assert {x["data"]["record_key"] for x in items if x["data"].get("record_key")} >= {"INV-0100", "INV-0200"}
    one = next(x for x in items if x["data"].get("record_key") == "INV-0100")
    steps = client.get(f"/api/comparison/defects/{one['id']}").json()["defect"]["repro_steps"]
    assert any("open page" in s for s in steps) and any("INV-0100" in s for s in steps)
    inv = client.post(f"/api/comparison/defects/{one['id']}/investigate").json()["defect"]["investigation"]
    assert inv["cause_category"] in ("calculation_logic", "data_transformation", "rounding_or_format", "unknown",
                                     "missing_synchronisation", "reference_data", "time_zone", "extraction_or_ocr",
                                     "pagination", "duplicate_load", "filter_or_configuration")
    upd = client.patch(f"/api/comparison/defects/{one['id']}", json={"status": "confirmed", "comment": "seen"}).json()
    assert upd["defect"]["status"] == "confirmed" and upd["defect"]["history"][-1]["status"] == "confirmed"
    assert client.patch(f"/api/comparison/defects/{one['id']}", json={"status": "created_in_tfs"}).status_code == 422

    # reports
    html = client.get(f"/api/comparison/executions/{out['id']}/report.html").text
    assert "INV-0100" in html and "Cmp-Pass-1" not in html
    x = client.get(f"/api/comparison/executions/{out['id']}/report.xlsx")
    assert x.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(x.content))
    assert "INV-0100" in z.read("xl/worksheets/sheet2.xml").decode()
    pkg = zipfile.ZipFile(io.BytesIO(client.get(f"/api/comparison/executions/{out['id']}/evidence.zip").content))
    manifest = json.loads(pkg.read("MANIFEST.json"))
    assert {m["file"] for m in manifest} >= {"report.html", "results.json", "audit.json"}

    # audit trail: hash chain intact, and the database refuses to change it
    au = client.get(f"/api/comparison/projects/{pid}/audit").json()
    assert au["chain"]["ok"] and len(au["items"]) > 5
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError):
        rt.db.execute("UPDATE cmp_audit SET actor = 'x'")
    with pytest.raises(sqlite3.DatabaseError):
        rt.db.execute("DELETE FROM cmp_audit")


def test_ssrf_blocked_and_uploads_validated(client, rt, site):
    from qa_platform.comparison.extract_web import allowed_hosts, host_allowed

    allowed = allowed_hosts({"base_url": "https://erp.example.local/app", "allowed_hosts": ["metadata", "cdn.example.local"]})
    assert allowed == {"erp.example.local", "cdn.example.local"}
    assert host_allowed("https://erp.example.local/x", allowed)
    assert not host_allowed("http://169.254.169.254/latest/meta-data", allowed | {"169.254.169.254"})
    assert not host_allowed("file:///c:/windows/win.ini", allowed)
    assert not host_allowed("https://evil.example.com/", allowed)

    pid = client.post("/api/comparison/projects", json={"name": "Uploads"}).json()["project"]["id"]
    app = client.post(f"/api/comparison/projects/{pid}/applications",
                      json={"name": "Screens", "app_type": "screenshot"}).json()["application"]
    url = f"/api/comparison/applications/{app['id']}/screenshot?name=../../evil.png"
    assert client.post(url, content=b"GIF89a....").status_code == 415
    assert client.post(url, content=b"<svg onload=alert(1)>").status_code == 415
    huge = _png(w=20000, h=2)
    assert client.post(url, content=huge).status_code == 422
    r = client.post(url, content=_png())
    assert r.status_code == 200, r.text  # stored even when no OCR engine is available (then: needs_manual)
    ev = r.json()["evidence"]
    stored = client.get(f"/api/comparison/evidence/{ev['id']}")
    assert stored.status_code == 200 and b"secret-person" not in stored.content and b"tEXt" not in stored.content
    row = rt.db.query_one("SELECT original_name, file FROM cmp_evidence WHERE id = ?", (ev["id"],))
    assert row["file"] == f"{ev['id']}.png" and ".." not in row["file"]
    # a changed original is detected
    path = rt.comparison.evidence.path(pid, row["file"])
    import os, stat
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    path.write_bytes(path.read_bytes() + b"x")
    assert client.get(f"/api/comparison/evidence/{ev['id']}").status_code == 409

    # file import (CSV) with a formula-looking value stays text in Excel
    csv = "Id,Amount\nA1,=HYPERLINK(\"http://x\")\nA2,10\n".encode()
    ds = client.post(f"/api/comparison/applications/{app['id']}/import?name=x.csv&key_field=Id", content=csv).json()
    assert ds["dataset"]["stats"]["records"] == 2


def test_project_access_is_limited_to_members(client, rt):
    from qa_platform import auth

    pid = client.post("/api/comparison/projects", json={"name": "Private"}).json()["project"]["id"]
    svc = rt.comparison
    outsider = auth.User(99, "outsider", "Out Sider", "tester")
    token = auth.set_current_user(outsider)
    try:
        assert all(p["id"] != pid for p in svc.projects())
        with pytest.raises(Exception) as e:
            svc.project(pid)
        assert getattr(e.value, "status", None) == 404
    finally:
        auth.reset_current_user(token)
    svc.update_project(pid, members=["outsider"])
    token = auth.set_current_user(outsider)
    try:
        assert svc.project(pid)["name"] == "Private"
        with pytest.raises(Exception):
            svc.update_project(pid, members=["outsider", "friend"])  # only the owner or an admin
    finally:
        auth.reset_current_user(token)


def test_ocr_table_reconstruction_and_kpis():
    from qa_platform.comparison import ocr

    def w(t, x, y, c=0.95):
        return {"text": t, "box": [x, y, 8 * len(t), 14], "confidence": c}
    words = [w("Invoice", 10, 10), w("No", 70, 10), w("Amount", 200, 10), w("Status", 320, 10),
             w("INV-1", 10, 40), w("1,000.00", 200, 40), w("Paid", 320, 40),
             w("INV-2", 10, 70), w("2O0.00", 200, 70, 0.55), w("Pending", 320, 70)]
    t = ocr.table_from_words(words)
    assert t["headers"] == ["Invoice No", "Amount", "Status"]
    assert t["rows"][0]["Amount"]["value"] == "1,000.00"
    assert t["rows"][1]["Amount"]["confidence"] == 0.55
    assert ocr._text_confidence("2O0.00") < 0.8 and ocr._text_confidence("200.00") > 0.8
    lines = [{"text": "Total Invoices", "box": [0, 0, 50, 10], "confidence": 0.9},
             {"text": "1,000", "box": [0, 12, 50, 10], "confidence": 0.9},
             {"text": "Paid: 600", "box": [0, 40, 50, 10], "confidence": 0.9}]
    k = {x["label"]: x["value"] for x in ocr.kpis_from_lines(lines)}
    assert k == {"Total Invoices": "1,000", "Paid": "600"}
    tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" \
          "5\t1\t1\t1\t1\t1\t10\t10\t30\t12\t91.5\tTotal\n5\t1\t1\t1\t1\t2\t45\t10\t30\t12\t40\t1OO\n"
    parsed = ocr.parse_tesseract_tsv(tsv)
    assert parsed["lines"][0]["text"] == "Total 1OO" and parsed["lines"][0]["confidence"] == 0.4
