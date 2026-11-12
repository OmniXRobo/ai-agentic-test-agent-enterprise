"""Final patch, defect 10: parameter scanner with dynamic dates, Excel import/export of performance data, and
deterministic error classification of failed samples."""

from __future__ import annotations

import base64
from datetime import date

import pytest

from qa_platform.comparison.reports import xlsx
from qa_platform.performance import jtl
from qa_platform.performance import script as S
from tests.test_perf_data_23325 import jmx


def test_scanner_finds_dates_users_ids_and_secrets_and_makes_dates_dynamic():
    text = jmx("1")
    # record a booking form with a check-in date 7 days after "today", a password and a booking id
    login = next(f for f in S.fields(text) if f["kind"] == "json")["node"]
    text = S.apply(text, [{"op": "set", "node": login,
                           "body": "checkin=2026-11-19&checkout=21/11/2026&password=pw1&booking_id=00123&csrf_token=abcdef0123456789"}])["script"]
    found = {s["key"]: s for s in S.scan_parameters(text, today=date(2026, 11, 12))}
    assert found["checkin"]["type"] == "date" and found["checkin"]["offset_days"] == 7
    assert found["checkin"]["expr"] == "${__timeShift(yyyy-MM-dd,,P7D,,)}"
    assert found["checkout"]["format"] == "dd/MM/yyyy" and found["checkout"]["offset_days"] == 9
    assert found["password"]["type"] == "secret" and found["password"]["value"] == "***"
    assert found["booking_id"]["type"] == "data"
    assert found["csrf_token"]["type"] == "correlation" and "0123456789" not in found["csrf_token"]["value"]
    c = found["checkin"]
    out = S.apply(text, [{"op": "bind", "node": c["node"], "kind": c["kind"], "key": c["key"], "expr": c["expr"]}])
    assert "checkin=${__urlencode(${__timeShift(yyyy-MM-dd,,P7D,,)})}" in out["script"]
    assert "checkin" not in {s["key"] for s in S.scan_parameters(out["script"], today=date(2026, 11, 12))}
    # only the date function is accepted as an expression (no arbitrary JMeter/Groovy code)
    with pytest.raises(Exception):
        S.apply(text, [{"op": "bind", "node": c["node"], "kind": c["kind"], "key": c["key"],
                        "expr": "${__groovy(System.exit(0))}"}])


def test_excel_import_keeps_text_and_export_round_trips(client):
    book = xlsx([("Users", ["username", "account_id", "city"], [["user001", "00123", "Dubai"], ["user002", "00456", "Abu Dhabi"]])])
    r = client.post("/api/performance/datasets/import",
                    json={"csv": "base64:" + base64.b64encode(book).decode(), "filename": "users.xlsx", "name": "Excel users"})
    assert r.status_code == 201, r.text
    ds = r.json()["dataset"]
    view = client.get(f"/api/performance/datasets/{ds['id']}").json()["dataset"]
    assert [c["name"] for c in view["columns"]] == ["username", "account_id", "city"]
    out = client.get(f"/api/performance/datasets/{ds['id']}/xlsx")
    assert out.status_code == 200 and out.content[:2] == b"PK"
    from qa_platform.automation.files import read_xlsx
    sheet = next(iter(read_xlsx(out.content).values()))
    assert sheet[0] == ["username", "account_id", "city"] and sheet[1][1] == "00123"
    bad = client.post("/api/performance/datasets/import", json={"csv": "base64:" + base64.b64encode(book).decode(),
                                                                  "filename": "users.xlsx", "sheet": "Nope"})
    assert bad.status_code == 422


def test_error_classification_is_deterministic():
    assert jtl.classify_error("401", "Unauthorized") == "authentication"
    assert jtl.classify_error("500", "Internal Server Error") == "server_error"
    assert jtl.classify_error("Non HTTP response code: java.net.SocketTimeoutException", "Read timed out") == "timeout"
    assert jtl.classify_error("200", "Test failed: text expected to contain /Welcome/") == "assertion"
    assert jtl.classify_error("200", "token_NOTFOUND in request") == "correlation"
    rows = [{"timeStamp": "1000", "elapsed": "10", "success": "false", "responseCode": "503", "label": "a",
             "failureMessage": "Service Unavailable"},
            {"timeStamp": "1100", "elapsed": "10", "success": "false", "responseCode": "401", "label": "a"},
            {"timeStamp": "1200", "elapsed": "10", "success": "true", "responseCode": "200", "label": "a"}]
    cats = {c["category"]: c["count"] for c in jtl.compute(rows)["overall"]["error_categories"]}
    assert cats == {"server_error": 1, "authentication": 1}
