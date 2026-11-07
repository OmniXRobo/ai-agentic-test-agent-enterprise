"""23.3.31: a screenshot after every step, and AI analysis reports (functional run, performance run, dashboard) that
the tester edits, prints as a PDF, downloads and emails with a written subject and body and the PDF attached."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from qa_platform.reports.perf_report import pdf_text as _pdf_text
from tests.fake_llm import FakeLLM
from tests.test_automation import _login_script, needs_browser


def pdf_text(path: Path) -> str:
    """The PDF's text with line breaks as spaces: where a line wraps depends on the fonts installed."""
    return " ".join(_pdf_text(path).split())

RUN_REPLY = {
    "summary": "The login test passed all 8 steps in Chromium.",
    "observations": ["Step 7 (Click Sign in) took the longest", "No self-healing was needed"],
    "issues": [],
    "recommendations": ["Add a check that the session cookie is set"],
    "undetermined": ["Whether the test is stable over time: only one run"],
}
DASH_REPLY = {
    "summary": "Automation coverage is low.",
    "observations": ["0 of the test cases are automated"],
    "issues": ["Test cases are not automated"],
    "recommendations": ["Automate the high-priority test cases first"],
    "undetermined": [],
}


@pytest.fixture()
def app_llm(paths):
    llm = FakeLLM()
    llm.extra["reviewing one automated browser test run"] = RUN_REPLY
    llm.extra["QA manager reviewing a QA dashboard"] = DASH_REPLY
    app = create_app(paths, secret_backend=MemoryBackend(), ai_transport=llm.transport())
    with TestClient(app) as c:
        c.app.state.runtime.settings.update({"performance": {"auto_report": False}, "playback": {"visual_delay_ms": 20}})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-7b-instruct-q4_k_m.gguf"})
        yield c, llm


def _mail(c):
    rt = c.app.state.runtime
    sent = []
    rt.mailer.transport = lambda cfg, msg: sent.append(msg)
    c.put("/api/email/settings", json={"mode": "smtp", "preset": "office365", "username": "qa@contoso.com",
                                       "sender": "qa@contoso.com", "default_to": "lead@contoso.com", "secret": "app-pass"})
    return sent


def test_unknown_kinds_ids_and_runs_are_refused(app_llm):
    c, _ = app_llm
    assert c.get("/api/ai-reports/other/x").status_code == 422
    assert c.get("/api/ai-reports/run/..%2F..%2Fsecrets").status_code in (404, 422)
    assert c.get("/api/ai-reports/run/RUN-NOPE").json()["error_code"] == "RUN_NOT_FOUND"
    # nothing to make a PDF or an email from before the AI wrote an analysis
    assert c.post("/api/ai-reports/dashboard/current/pdf").json()["error_code"] == "AI_REPORT_EMPTY"
    assert c.get("/api/ai-reports/dashboard/current/email").json()["error_code"] == "AI_REPORT_EMPTY"


@needs_browser
def test_step_screenshots_and_the_run_report_pdf_and_email(app_llm, site):
    c, llm = app_llm
    rt = c.app.state.runtime
    auto = rt.automation
    auto.creds.save("Shop", "admin", "alice", "s3cret-pass", None)
    script = auto.scripts.save(_login_script(site))
    record = auto.run_script(script.id, headless=True, background=False)
    assert record["status"] == "PASSED", record["error"]
    # a screenshot after every step, kept with the step and not repeated in the run's other evidence
    shots = [s.get("screenshot") for s in record["steps"]]
    assert all(shots) and all(Path(p).is_file() for p in shots)
    assert [Path(p).name for p in shots] == [f"step-{i:02d}.png" for i in range(1, 9)]
    assert not set(shots) & set(record["evidence"]["screenshots"])
    stored = auto.runner.results(record["run_id"])[0]
    assert stored["steps"][0]["screenshot"] == shots[0]

    run_id = record["run_id"]
    st = c.get(f"/api/ai-reports/run/{run_id}").json()
    assert st["analysis"] is None and st["has_pdf"] is False

    st = c.post(f"/api/ai-reports/run/{run_id}/analyze").json()
    assert st["analysis"]["summary"] == RUN_REPLY["summary"] and st["analysis"]["model"]
    prompt = next(x for x in llm.calls if "reviewing one automated browser test run" in x["messages"][0]["content"])
    facts = json.loads(prompt["messages"][1]["content"])
    assert facts["result"] == "PASSED" and len(facts["steps"]) == 8 and facts["script"] == "Login works"
    assert "s3cret-pass" not in prompt["messages"][1]["content"]

    # the tester edits the wording before making the PDF
    st = c.patch(f"/api/ai-reports/run/{run_id}", json={"summary": "Login works in Chromium (reviewed).",
                                                         "recommendations": ["Run it in Edge too", " "]}).json()
    assert st["analysis"]["edited"] and st["analysis"]["recommendations"] == ["Run it in Edge too"]

    st = c.post(f"/api/ai-reports/run/{run_id}/pdf").json()
    assert st["has_pdf"], st
    pdf = c.get(f"/api/ai-reports/run/{run_id}/pdf?download=true")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert f'{run_id}-ai-analysis.pdf' in pdf.headers["content-disposition"]
    folder = rt.ai_reports.folder("run", run_id)
    text = pdf_text(folder / "report.pdf")
    assert "Login works in Chromium (reviewed)." in text and "Run it in Edge too" in text
    page = (folder / "report.html").read_text(encoding="utf-8")
    assert page.count("data:image/png;base64,") >= 8  # every step's screenshot in the PDF

    # an edit after the PDF makes it stale; the email makes it again
    c.patch(f"/api/ai-reports/run/{run_id}", json={"issues": ["None found"]})
    assert c.get(f"/api/ai-reports/run/{run_id}").json()["has_pdf"] is False

    sent = _mail(c)
    d = c.get(f"/api/ai-reports/run/{run_id}/email").json()
    assert d["to"] == "lead@contoso.com" and d["subject"].startswith("[PASSED] Test run analysis: Login works")
    assert "Login works in Chromium (reviewed)." in d["body"] and "8 of 8 steps passed" in d["body"]
    r = c.post(f"/api/ai-reports/run/{run_id}/email", json={"to": "lead@contoso.com; qa@contoso.com", "cc": "",
                                                          "subject": d["subject"] + " (edited)", "body": "Hi team,\n\nSee the PDF."}).json()
    assert r["sent"] and r["to"] == ["lead@contoso.com", "qa@contoso.com"]
    msg = sent[-1]
    assert msg["Subject"].endswith("(edited)")
    files = [p for p in msg.iter_attachments()]
    assert [p.get_filename() for p in files] == [f"{run_id}-ai-analysis.pdf"]
    assert files[0].get_content()[:4] == b"%PDF"
    assert "See the PDF." in msg.get_body(("plain",)).get_content()
    assert c.get(f"/api/ai-reports/run/{run_id}").json()["has_pdf"] is True
    assert "None found" in pdf_text(folder / "report.pdf")


def test_step_screenshots_can_be_switched_off(rt):
    # a setting, read when the run starts (default on)
    assert rt.settings.section("playback").get("step_screenshots", True) is True


def test_email_needs_outlook_set_up_and_a_recipient(app_llm):
    c, _ = app_llm
    c.post("/api/ai-reports/dashboard/current/analyze")
    d = c.get("/api/ai-reports/dashboard/current/email").json()
    assert d["email_configured"] is False and d["to"] == ""
    r = c.post("/api/ai-reports/dashboard/current/email", json={"to": "lead@contoso.com", "subject": "x", "body": "y"})
    assert r.status_code in (409, 500) and r.json()["error_code"] in ("EMAIL_NOT_CONFIGURED", "REPORT_PDF_FAILED",
                                                                     "PLAYWRIGHT_MISSING")


@needs_browser
def test_dashboard_analysis_pdf_and_email(app_llm):
    c, llm = app_llm
    st = c.post("/api/ai-reports/dashboard/current/analyze").json()
    assert st["analysis"]["issues"] == DASH_REPLY["issues"]
    facts = st["analysis"]["facts"]
    assert "automation_coverage" in facts and facts["tfs"]["connected"] is False
    prompt = next(x for x in llm.calls if "QA manager reviewing a QA dashboard" in x["messages"][0]["content"])
    assert "functional_latest_results" in prompt["messages"][1]["content"]
    assert c.post("/api/ai-reports/dashboard/current/pdf").json()["has_pdf"]
    text = pdf_text(c.app.state.runtime.ai_reports.folder("dashboard", "current") / "report.pdf")
    assert "Dashboard analysis" in text and "Automation coverage is low." in text and "Automation coverage" in text
    sent = _mail(c)
    d = c.get("/api/ai-reports/dashboard/current/email").json()
    assert d["subject"].startswith("QA dashboard analysis") and "Automate the high-priority" in d["body"]
    c.post("/api/ai-reports/dashboard/current/email", json={"to": d["to"], "subject": d["subject"], "body": d["body"]})
    assert sent and sent[-1]["To"] == "lead@contoso.com"
    assert [p.get_filename() for p in sent[-1].iter_attachments()][0].startswith("dashboard-ai-analysis-")


@needs_browser
def test_performance_analysis_edit_pdf_and_email(app_llm, tmp_path):
    from qa_platform.clock import now_iso
    from qa_platform.storage.db import dumps

    c, _ = app_llm
    rt = c.app.state.runtime
    overall = {"label": "ALL REQUESTS", "samples": 6718, "errors": 0, "error_pct": 0, "avg_ms": 17.2, "min_ms": 7,
               "max_ms": 1248, "p90_ms": 19, "p95_ms": 27, "p99_ms": 68, "throughput": 111.886, "avg_latency_ms": 17.2,
               "avg_connect_ms": 0.3, "received_kb_s": 10, "response_codes": {"200": 6718}, "top_errors": []}
    metrics = {"duration_s": 60, "peak_threads": 1, "overall": overall,
               "requests": [{**overall, "label": "Home"}], "timeline": [{"t": 0, "throughput": 110, "avg_ms": 17, "errors": 0, "threads": 1}]}
    rt.db.execute("INSERT INTO perf_runs (run_id, plan_id, kind, name, status, state, verdict, started_at, finished_at, folder,"
                  " metrics) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  ("PERF-20261106-202028-A2C97F", "PERF-NOPLAN", "load", "Checkout", "PASSED", "COMPLETED", "NOT_SPECIFIED",
                   now_iso(), now_iso(), str(tmp_path), dumps(metrics)))
    rid = "PERF-20261106-202028-A2C97F"
    st = c.post(f"/api/ai-reports/perf/{rid}/analyze").json()
    assert st["analysis"]["summary"] == "All requests stayed fast under the tested load."
    # the run page shows the same analysis
    assert c.get(f"/api/performance/runs/{rid}").json()["run"]["analysis"]["summary"] == st["analysis"]["summary"]
    st = c.patch(f"/api/ai-reports/perf/{rid}", json={"suspected_bottlenecks": ["Home (medium confidence): max 1248 ms"]}).json()
    assert st["analysis"]["suspected_bottlenecks"] == [{"area": "Home", "confidence": "medium", "evidence": "max 1248 ms"}]
    assert c.post(f"/api/ai-reports/perf/{rid}/pdf").json()["has_pdf"]
    text = pdf_text(rt.ai_reports.folder("perf", rid) / "report.pdf")
    assert "AI performance analysis" in text and "Home (medium confidence): max 1248 ms" in text and "6,718" in text
    sent = _mail(c)
    d = c.get(f"/api/ai-reports/perf/{rid}/email").json()
    assert d["subject"] == f"[Analysis] Performance analysis: Checkout ({rid})" and "6,718 samples" in d["body"]
    c.post(f"/api/ai-reports/perf/{rid}/email", json={"to": "lead@contoso.com", "subject": d["subject"], "body": d["body"]})
    assert [p.get_filename() for p in sent[-1].iter_attachments()] == [f"{rid}-ai-performance-analysis.pdf"]


def test_pages_carry_the_report_panel():
    web = Path(__file__).resolve().parents[1] / "web" / "js"
    panel = (web / "ai-report.js").read_text(encoding="utf-8")
    for text in ("Generate PDF", "Download PDF", "Email PDF", "Open PDF", "Edit", "Send"):
        assert text in panel
    assert 'kind: "run"' in (web / "pages" / "automation.js").read_text(encoding="utf-8")
    assert "data-step-shot" in (web / "pages" / "automation.js").read_text(encoding="utf-8")
    assert 'kind: "perf"' in (web / "pages" / "performance.js").read_text(encoding="utf-8")
    assert 'kind: "dashboard"' in (web / "pages" / "dashboard.js").read_text(encoding="utf-8")
