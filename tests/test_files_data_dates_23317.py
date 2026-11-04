"""23.3.17: file upload / download steps (with PDF and Excel content checks), data-driven runs from Excel or CSV
(one run per row) and date / time rules (Current system date +/- days, current system time +/- hours / minutes).

The browser tests run a real headless Chromium against a small local web application that only accepts today or a
later date, takes a file upload and offers a PDF and an Excel download."""
import base64
import io
import json
import os
import socket
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).parent))

from qa_platform.automation import date_rules, files as filekit  # noqa: E402
from qa_platform.automation.dsl import DataTable, Script, Step, Target, ValueResolver  # noqa: E402

CLOCK = lambda: datetime(2026, 11, 3, 8, 10)  # noqa: E731  the day the script was recorded


# ---- test files made here (no extra packages) --------------------------------------------------------------


def make_pdf(lines: list[str]) -> bytes:
    text = "BT /F1 14 Tf 72 720 Td " + " ".join(f"({ln}) Tj 0 -20 Td" for ln in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    def cell(ref, v):
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        names = list(sheets)
        zf.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                    + "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1))
                    + "</sheets></workbook>")
        zf.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    + "".join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml" Type="x"/>' for i in range(1, len(names) + 1))
                    + "</Relationships>")
        for i, n in enumerate(names, 1):
            rows = "".join(f'<row r="{r}">' + "".join(cell(f"{filekit.col_letters(c)}{r}", v) for c, v in enumerate(row))
                           + "</row>" for r, row in enumerate(sheets[n], 1))
            zf.writestr(f"xl/worksheets/sheet{i}.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                        f"<sheetData>{rows}</sheetData></worksheet>")
    return buf.getvalue()


# ---- 10: date / time rules ---------------------------------------------------------------------------------


@pytest.mark.parametrize("typed,rule,label", [
    ("03/11/2026", "{{TODAY:%d/%m/%Y}}", "Current system date (dd/mm/yyyy)"),
    ("05/11/2026", "{{TODAY+2d:%d/%m/%Y}}", "Current system date + 2 days (dd/mm/yyyy)"),
    ("2026-11-01", "{{TODAY-2d:%Y-%m-%d}}", "Current system date - 2 days (yyyy-mm-dd)"),
    ("3 Nov 2026", "{{TODAY:%d %b %Y}}", "Current system date (dd Mon yyyy)"),
    ("09:10", "{{NOW+1h:%H:%M}}", "Current system time + 1 hour (HH:MM)"),
    ("12:10", "{{NOW+4h:%H:%M}}", "Current system time + 4 hours (HH:MM)"),
    ("06:40", "{{NOW-1h-30min:%H:%M}}", "Current system time - 1 hour - 30 minutes (HH:MM)"),
    ("08:11", "{{NOW:%H:%M}}", "Current system time (HH:MM)"),
    ("2026-11-04T10:10", "{{NOW+1d+2h:%Y-%m-%dT%H:%M}}", None),
])
def test_recorded_dates_and_times_become_rules(typed, rule, label):
    found = date_rules.detect(typed, CLOCK)
    assert found["rule"] == rule
    if label:
        assert found["label"] == label
    assert date_rules.preview(rule, CLOCK) in (typed, "08:10", "03 Nov 2026")  # same value on the recording day


@pytest.mark.parametrize("value", ["15/05/1985", "12345", "Blue mug", "", None, "{{TODAY}}", "2026", "10.5"])
def test_other_values_stay(value):
    assert date_rules.detect(value, CLOCK) is None


def test_rule_moves_with_the_clock_on_the_replay_day():
    """Recorded on 3 Nov, played on 5 Nov: the date is 5 Nov (+ N days), never the recorded 3 Nov."""
    replay = lambda: datetime(2026, 11, 5, 14, 0)  # noqa: E731
    r = ValueResolver(clock=replay)
    assert r.resolve("{{TODAY:%d/%m/%Y}}") == "05/11/2026"
    assert r.resolve("{{TODAY+1d:%d/%m/%Y}}") == "06/11/2026"
    assert r.resolve("{{TODAY-3d:%d/%m/%Y}}") == "02/11/2026"
    assert r.resolve("{{NOW+1h:%H:%M}}") == "15:00"        # hours were ignored before 23.3.17
    assert r.resolve("{{NOW+2h+30min:%H:%M}}") == "16:30"
    assert r.resolve("{{NOW-4h:%H:%M}}") == "10:00"
    assert r.resolve("{{NOW-45min:%I:%M %p}}") == "01:15 PM"
    assert r.resolve("{{TODAY + 1d : %d/%m/%Y}}") in ("06/11/2026", " 06/11/2026")
    # a data cell may hold a rule
    assert ValueResolver(data={"d": "{{TODAY+1d:%d/%m/%Y}}"}, clock=replay).resolve("{{DATA:d}}") == "06/11/2026"


def test_recorder_session_converts_typed_dates(tmp_path):
    from qa_platform.automation.recorder import RecordingSession

    class Agent:
        def emit(self, *a, **k):
            pass
    s = RecordingSession(Agent(), "REC-1", "http://x/", files_dir=tmp_path)
    at = datetime(2026, 11, 3, 8, 10).isoformat()
    s.steps += [{"action": "fill", "target": {"selector": "#d", "name": "Travel date"}, "value": "04/11/2026", "_at": at},
                {"action": "fill", "target": {"selector": "#t", "name": "Time"}, "value": "12:10", "_at": at},
                {"action": "fill", "target": {"selector": "#n", "name": "Name"}, "value": "Ali", "_at": at},
                {"action": "expect_text", "target": {"selector": "#m"}, "value": "03/11/2026", "_at": at}]
    steps = s.to_steps()
    assert steps[1].value == "{{TODAY+1d:%d/%m/%Y}}" and steps[1].original_value == "04/11/2026"
    assert "Current system date + 1 day" in steps[1].description
    assert steps[2].value == "{{NOW+4h:%H:%M}}"
    assert steps[3].value == "Ali" and steps[4].value == "03/11/2026"  # names and checks stay
    assert [c["index"] for c in s.converted] == [1, 2]


def test_ai_driven_test_data_dates_become_rules():
    from qa_platform.automation.framework.studio import _dynamic_dates
    today = datetime.now()
    data = {"combinations": [{"name": "c1", "values": {"travel_date": (today + timedelta(days=2)).strftime("%d/%m/%Y"),
                                                       "name": "Ali", "dob": "01/01/1980"}}]}
    changes = {"test_data/x.json": json.dumps(data)}
    found = _dynamic_dates(changes, "test_data/x.json")
    assert [f for f, _ in found] == ["c1.travel_date"]
    values = json.loads(changes["test_data/x.json"])["combinations"][0]["values"]
    assert values["travel_date"] == "{{TODAY+2d:%d/%m/%Y}}" and values["dob"] == "01/01/1980"
    # the framework's own resolver reads the rule
    sys.path.insert(0, str(APP / "qa_platform" / "automation" / "framework" / "template"))
    from utilities import runtime_values
    assert runtime_values.resolve(values["travel_date"]) == (today + timedelta(days=2)).strftime("%d/%m/%Y")


def test_classic_ai_generation_converts_dates():
    from qa_platform.automation import generator
    src = Path(generator.__file__).read_text(encoding="utf-8")
    assert "detect(value)" in src and "original_value=original" in src


# ---- 1 and 5: files and tables -------------------------------------------------------------------------------


def test_read_tables_and_file_text(tmp_path):
    xlsx = make_xlsx({"Data": [["Name", "Amount"], ["Ali", 10], ["Sara", 12.5]], "Summary": [["x"], ["Total", 22.5]]})
    t = filekit.read_table("people.xlsx", xlsx)
    assert t["columns"] == ["Name", "Amount"] and t["rows"][1] == {"Name": "Sara", "Amount": "12.5"}
    assert filekit.read_table("people.xlsx", xlsx, "summary")["columns"] == ["x"]
    c = filekit.read_table("p.csv", "Name;Date\r\nAli;03/11/2026\r\n\r\nSara;04/11/2026\r\n".encode("cp1252"))
    assert c["rows"] == [{"Name": "Ali", "Date": "03/11/2026"}, {"Name": "Sara", "Date": "04/11/2026"}]
    assert filekit.excel_cell(xlsx, "B2", "Summary") == "22.5"
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(make_pdf(["Invoice 42", "Total 22.50 AED"]))
    assert "Invoice 42" in filekit.file_text(pdf)
    with pytest.raises(Exception):
        filekit.read_table("old.xls", b"x")


# ---- the web application under test --------------------------------------------------------------------------

PAGE = """<!doctype html><html><body>
<label for="d">Travel date</label><input id="d" type="date">
<label for="n">Name</label><input id="n">
<input id="f" type="file" multiple>
<button id="save" onclick="save()">Save</button>
<p id="msg"></p><p id="file"></p>
<a id="pdf" href="/report.pdf" download>Download PDF</a>
<a id="xlsx" href="/report.xlsx" download>Download Excel</a>
<script>
document.getElementById('f').addEventListener('change', (e) => {
  document.getElementById('file').textContent = [...e.target.files].map(f => f.name + ' ' + f.size).join(', ');
});
function save() {
  const v = document.getElementById('d').value;
  const t = new Date(); const today = t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0');
  document.getElementById('msg').textContent = !v ? 'Enter a date' : v < today ? 'Date must be today or later'
    : 'Saved for ' + document.getElementById('n').value + ' on ' + v;
}
</script></body></html>"""


@pytest.fixture(scope="module")
def site():
    pdf = make_pdf(["Invoice 42", "Customer Ali"])
    xlsx = make_xlsx({"Report": [["Item", "Qty"], ["Mug", 10]]})

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body, kind, extra = PAGE.encode(), "text/html", {}
            if self.path == "/report.pdf":
                body, kind, extra = pdf, "application/pdf", {"Content-Disposition": 'attachment; filename="invoice-42.pdf"'}
            elif self.path == "/report.xlsx":
                body, kind = xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                extra = {"Content-Disposition": 'attachment; filename="report.xlsx"'}
            self.send_response(200)
            self.send_header("Content-Type", kind)
            for k, v in extra.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/"
    server.shutdown()


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.main import create_app
    app = create_app()
    with TestClient(app) as client:
        app.state.runtime.client = client
        yield app.state.runtime


def T(sel):
    return Target(selector=sel)


def test_upload_download_and_file_checks_in_a_real_browser(rt, site, tmp_path):
    a = rt.automation
    s = a.scripts.save(Script(id="SCR-FILES00001", name="Files", base_url=site, mode="guided", steps=[
        Step(action="goto", value=site),
        Step(action="fill", target=T("#d"), value="{{TODAY+1d:%Y-%m-%d}}"),
        Step(action="fill", target=T("#n"), value="Ali"),
        Step(action="click", target=T("#save")),
        Step(action="expect_text", target=T("#msg"), value="Saved for Ali on {{TODAY+1d:%Y-%m-%d}}"),
        Step(action="upload_file", target=T("#f"), value="passport.pdf; photo.png"),
        Step(action="expect_text", target=T("#file"), value="passport.pdf"),
        Step(action="download_file", target=T("#pdf")),
        Step(action="expect_file_name", value=".pdf"),
        Step(action="expect_file_text", value="Invoice 42||customer ali"),
        Step(action="download_file", target=T("#xlsx"), value="weekly.xlsx"),
        Step(action="expect_excel_cell", value="Report!B2=10"),
        Step(action="expect_file_text", value="Mug"),
    ]))
    a.scripts.save_file(s.id, "passport.pdf", make_pdf(["P"]))
    a.scripts.save_file(s.id, "photo.png", b"\x89PNG....")
    r = a.run_script(s.id, headless=True, background=False)
    assert r["status"] == "PASSED", (r["error"], [st for st in r["steps"] if st["status"] != "PASSED"])
    downloads = r["evidence"]["downloads"]
    assert [Path(d).name for d in downloads] == ["invoice-42.pdf", "weekly.xlsx"]

    # a wrong expectation fails with what the file really holds
    bad = s.model_copy(deep=True)
    bad.steps[11] = Step(action="expect_excel_cell", value="Report!B2=11")
    a.scripts.save(bad)
    r = a.run_script(s.id, headless=True, background=False)
    assert r["status"] == "FAILED" and r["failed_step"] == 11 and "is '10', expected '11'" in r["error"]

    # a past date is refused by the application: what a hard-coded recording date does on a later day
    past = s.model_copy(deep=True)
    past.steps = past.steps[:5]
    past.steps[1].value = "{{TODAY-1d:%Y-%m-%d}}"
    a.scripts.save(past)
    r = a.run_script(s.id, headless=True, background=False)
    assert r["status"] == "FAILED" and r["failed_step"] == 4

    # an upload file that is not attached
    missing = s.model_copy(deep=True)
    missing.steps = missing.steps[:1] + [Step(action="upload_file", target=T("#f"), value="nope.pdf")]
    a.scripts.save(missing)
    r = a.run_script(s.id, headless=True, background=False)
    assert r["status"] == "FAILED" and "nope.pdf is not attached" in r["error"]


def test_data_driven_runs_one_per_row(rt, site):
    a, c = rt.automation, rt.client
    s = a.scripts.save(Script(id="SCR-DATA000001", name="Rows", base_url=site, mode="guided", steps=[
        Step(action="goto", value=site),
        Step(action="fill", target=T("#d"), value="{{DATA:Travel date}}"),
        Step(action="fill", target=T("#n"), value="{{DATA:Name}}"),
        Step(action="click", target=T("#save")),
        Step(action="expect_text", target=T("#msg"), value="{{DATA:Expected}}"),
    ]))
    today = datetime.now()
    rows = [["Name", "Travel date", "Expected"],
            ["Ali", (today + timedelta(days=1)).strftime("%Y-%m-%d"), "Saved for Ali"],
            ["Sara", "{{TODAY+3d:%Y-%m-%d}}", "Saved for Sara"],
            ["Omar", (today - timedelta(days=2)).strftime("%Y-%m-%d"), "today or later"],
            ["Bad", (today - timedelta(days=2)).strftime("%Y-%m-%d"), "Saved for Bad"]]
    content = base64.b64encode(make_xlsx({"People": rows})).decode()
    pre = c.post(f"/api/automation/scripts/{s.id}/data-table", json={"name": "people.xlsx", "content_base64": content,
                                                                       "preview": True}).json()
    assert pre["row_count"] == 4 and pre["fields_missing"] == [] and pre["dates_converted"] == 3
    assert a.scripts.get(s.id).data_table is None  # a preview saves nothing
    done = c.post(f"/api/automation/scripts/{s.id}/data-table", json={"name": "people.xlsx", "content_base64": content}).json()
    table = a.scripts.get(s.id).data_table
    assert done["ok"] and table.rows[0]["Travel date"] == "{{TODAY+1d:%Y-%m-%d}}" and table.source == "people.xlsx"
    # editing the steps keeps the imported data
    body = {**a.scripts.get(s.id).model_dump(exclude={"id", "version", "created_at", "updated_at", "data_table"})}
    assert c.put(f"/api/automation/scripts/{s.id}", json=body).status_code == 200
    assert a.scripts.get(s.id).data_table.rows == table.rows

    batch = c.post(f"/api/automation/scripts/{s.id}/data-runs", json={"headless": True}).json()
    assert [r["label"] for r in batch["rows"]] == ["row 1 of 4", "row 2 of 4", "row 3 of 4", "row 4 of 4"]
    for _ in range(240):
        items = c.get(f"/api/automation/scripts/{s.id}/data-runs").json()["items"]
        if not items[0]["active"]:
            break
        time.sleep(0.5)
    rows_out = items[0]["rows"]
    assert [r["status"] for r in rows_out] == ["PASSED", "PASSED", "PASSED", "FAILED"], rows_out
    assert items[0]["counts"] == {"PASSED": 3, "FAILED": 1}

    # only the chosen rows
    batch = c.post(f"/api/automation/scripts/{s.id}/data-runs", json={"rows": [1], "headless": True}).json()
    assert [r["row"] for r in batch["rows"]] == [1]
    for _ in range(120):
        if not c.get(f"/api/automation/scripts/{s.id}/data-runs").json()["items"][0]["active"]:
            break
        time.sleep(0.5)

    # the Python export has one test per row
    src = c.get(f"/api/automation/scripts/{s.id}/export").text
    assert "'row 4 of 4'" in src and "{{TODAY+1d:%Y-%m-%d}}" in src
    compile(src, "export.py", "exec")

    # CSV, a missing column, removal
    csv_b64 = base64.b64encode(b"Name,Expected\nA,Saved\n").decode()
    r = c.post(f"/api/automation/scripts/{s.id}/data-table", json={"name": "x.csv", "content_base64": csv_b64, "preview": True}).json()
    assert r["fields_missing"] == ["Travel date"]
    assert c.delete(f"/api/automation/scripts/{s.id}/data-table").json()["script"]["data_table"] is None
    assert c.post(f"/api/automation/scripts/{s.id}/data-runs", json={}).status_code == 409


def test_recording_captures_uploads_downloads_and_dates(rt, site, tmp_path):
    a = rt.automation
    picked = tmp_path / "contract.pdf"
    picked.write_bytes(make_pdf(["Contract"]))
    run_id = a.start_recording(site, name="Rec", headless=True)
    session = a.recording(run_id)["session"]
    for _ in range(100):
        try:
            session.command("fingerprint", "#d", timeout=5)
            break
        except Exception:
            time.sleep(0.2)
    session.command("fill", ("#d", datetime.now().strftime("%Y-%m-%d")))
    session.command("upload", ("#f", [str(picked)]))
    time.sleep(1.0)
    session.command("click", "#pdf")
    time.sleep(1.5)
    script = a.stop_recording(run_id)
    actions = [s.action for s in script.steps]
    assert "upload_file" in actions and "download_file" in actions and "expect_file_name" in actions, actions
    date_step = next(s for s in script.steps if s.action == "fill")
    assert date_step.value == "{{TODAY:%Y-%m-%d}}" and date_step.original_value
    up = next(s for s in script.steps if s.action == "upload_file")
    assert up.value == "contract.pdf"
    assert [f["name"] for f in a.scripts.list_files(script.id)] == ["contract.pdf"]
    assert next(s for s in script.steps if s.action == "expect_file_name").value == ".pdf"
    # the recorded script plays back
    r = a.run_script(script.id, headless=True, background=False)
    assert r["status"] == "PASSED", r["error"]


def test_files_api_suggestions_and_bundle(rt):
    a, c = rt.automation, rt.client
    s = a.scripts.save(Script(id="SCR-API0000001", name="Api", steps=[
        Step(action="goto", value="http://x/"),
        Step(action="fill", target=T("#d"), value=(datetime.now() + timedelta(days=5)).strftime("%d/%m/%Y")),
        Step(action="upload_file", target=T("#f"), value="a.txt"),
    ]))
    r = c.post(f"/api/automation/scripts/{s.id}/files", json={"name": "..\\evil/a.txt", "content_base64": base64.b64encode(b"hi").decode()})
    assert r.status_code == 201 and r.json()["file"]["name"] == "a.txt"
    got = c.get(f"/api/automation/scripts/{s.id}").json()
    assert got["files"][0]["name"] == "a.txt"
    assert got["date_suggestions"]["1"]["rule"] == "{{TODAY+5d:%d/%m/%Y}}"
    z = c.post("/api/automation/export-bundle", json={"script_ids": [s.id]})
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "tests/files/scr_api0000001/a.txt" in names
    assert "pypdf" in zipfile.ZipFile(io.BytesIO(z.content)).read("requirements.txt").decode()
    p = c.post("/api/automation/date-rules/preview", json={"values": ["{{TODAY+1d:%d/%m/%Y}}"]}).json()["items"][0]
    assert p["now"] == (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y") and p["label"].startswith("Current system date + 1 day")
    assert c.delete(f"/api/automation/scripts/{s.id}/files/a.txt").json()["items"] == []
