"""23.3.30: product name, tagline and logo from Preferences (header, reports, PDFs, emails), report PDFs that are
printed from a file with a browser fallback and can be made again, and acceptance criteria kept as work items
counted on the dashboard."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qa_platform import branding
from qa_platform.credentials import MemoryBackend
from qa_platform.errors import QAError
from qa_platform.main import create_app
from qa_platform.reports import service as report_service
from tests.fake_ado import PAT, FakeAzureDevOps
from tests.test_automation import HAVE_PLAYWRIGHT

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.fixture()
def env(paths):
    ado = FakeAzureDevOps()
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        yield c, ado, c.app.state.runtime


def test_default_name_and_preferences(client, tmp_path):
    b = client.get("/api/branding").json()
    assert b["name"] == "SkyGuard QA" and b["tagline"] == "Every release, cleared for takeoff." and b["logo"] is False
    assert client.get("/api/branding/logo").status_code == 404

    # name and tagline are checked; a missing or unsupported logo file is refused with the reason
    assert client.patch("/api/settings", json={"ui": {"brand_name": "  "}}).status_code == 400
    assert client.patch("/api/settings", json={"ui": {"brand_name": "x" * 41}}).status_code == 400
    r = client.patch("/api/settings", json={"ui": {"logo_path": str(tmp_path / "missing.png")}})
    assert r.status_code == 422 and "not found" in r.json()["message"]
    (tmp_path / "logo.bmp").write_bytes(b"BM")
    assert client.patch("/api/settings", json={"ui": {"logo_path": str(tmp_path / "logo.bmp")}}).status_code == 422
    (tmp_path / "big.png").write_bytes(b"\0" * (branding.MAX_LOGO_BYTES + 1))
    assert "2 MB" in client.patch("/api/settings", json={"ui": {"logo_path": str(tmp_path / "big.png")}}).json()["message"]

    logo = tmp_path / "Company Logo.png"
    logo.write_bytes(PNG)
    r = client.patch("/api/settings", json={"ui": {"brand_name": "Falcon QA", "brand_tagline": "Fly safe.",
                                                   "logo_path": f'"{logo}"'}})  # pasted with quotes from Explorer
    assert r.status_code == 200, r.text
    b = client.get("/api/branding").json()
    assert b["name"] == "Falcon QA" and b["tagline"] == "Fly safe." and b["logo"] and b["logo_url"].startswith("/api/branding/logo")
    img = client.get(b["logo_url"])
    assert img.status_code == 200 and img.content == PNG and img.headers["content-type"] == "image/png"
    assert "default-src 'none'" in img.headers["content-security-policy"]

    # the logo file removed later: the emblem is shown again and Preferences says why
    logo.unlink()
    b = client.get("/api/branding").json()
    assert b["logo"] is False and "not found" in b["logo_problem"]

    # an empty tagline hides it; an empty path goes back to the emblem
    assert client.patch("/api/settings", json={"ui": {"brand_tagline": "", "logo_path": ""}}).status_code == 200
    b = client.get("/api/branding").json()
    assert b["tagline"] == "" and b["logo_problem"] is None


def test_name_in_messages_and_emails(client):
    from qa_platform.mailer import _sender_name
    client.patch("/api/settings", json={"ui": {"brand_name": "Falcon QA"}})
    assert _sender_name({"sender_name": ""}) == "Falcon QA"
    assert _sender_name({"sender_name": "QA Enterprise AI"}) == "Falcon QA"  # the old default follows the name
    assert _sender_name({"sender_name": "QA Team"}) == "QA Team"
    host = client.get("/api/performance/schedules").json()["host"]
    assert "only while Falcon QA runs" in host["explain"]


def test_pages_carry_the_branding():
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="brandTitle">SkyGuard QA<' in index and 'id="brandLogoImg"' in index and "QA Enterprise AI" not in index
    app = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    assert "loadBrand()" in app and "QA Enterprise AI" not in app
    prefs = (ROOT / "web" / "js" / "pages" / "settings.js").read_text(encoding="utf-8")
    assert all(n in prefs for n in ('name="brand_name"', 'name="brand_tagline"', 'name="logo_path"'))
    for css in ("app.css", "hud.css"):  # the voice pages are navy and cyan, not green
        text = (ROOT / "web" / "css" / css).read_text(encoding="utf-8")
        assert "93, 255, 122" not in text and "#5dff7a" not in text
    wf = (ROOT / "web" / "js" / "pages" / "workflow.js").read_text(encoding="utf-8")
    assert "wf-hero" in wf and "wf-flight" in wf


def test_report_carries_name_logo_and_pdf_can_be_made_again(env, tmp_path):
    c, _ado, rt = env
    logo = tmp_path / "logo.png"
    logo.write_bytes(PNG)
    c.patch("/api/settings", json={"ui": {"brand_name": "Falcon QA", "brand_tagline": "Fly safe.", "logo_path": str(logo)}})
    r = rt.reports.start({"scope": "functional", "days": 7}, background=False)
    page = c.get(f"/api/reports/{r['id']}/html").text
    assert "Falcon QA" in page and "Fly safe." in page and "data:image/png;base64," in page
    assert "AI Agentic Test Agent Enterprise" not in page
    assert not list((rt.paths.reports / r["id"]).glob("*.print.html"))  # the print file is removed
    if not HAVE_PLAYWRIGHT:
        return
    assert r["has_pdf"], r["summary"]
    from qa_platform.reports.perf_report import pdf_text
    assert "Falcon QA" in pdf_text(rt.paths.reports / r["id"] / "report.pdf")
    # the PDF went missing: Make PDF prints it again from the saved report
    (rt.paths.reports / r["id"] / "report.pdf").unlink()
    again = c.post(f"/api/reports/{r['id']}/pdf", json={}).json()["report"]
    assert again["has_pdf"] and "pdf_error" not in again["summary"]
    assert c.get(f"/api/reports/{r['id']}/pdf").content[:4] == b"%PDF"


def test_pdf_tries_the_next_browser_and_says_why(monkeypatch, tmp_path):
    if not HAVE_PLAYWRIGHT:
        pytest.skip("Playwright is not installed")
    target = tmp_path / "r.pdf"
    monkeypatch.setattr(report_service, "PDF_BROWSERS", (
        ("the included Chromium", {"executable_path": str(tmp_path / "no-chromium.exe")}),
        ("a second browser", {})))
    report_service.print_pdf("<h1>Report</h1>", target)  # the first cannot start; the second prints it
    assert target.read_bytes()[:4] == b"%PDF"
    monkeypatch.setattr(report_service, "PDF_BROWSERS", (
        ("the included Chromium", {"executable_path": str(tmp_path / "no-chromium.exe")}),
        ("Microsoft Edge", {"executable_path": str(tmp_path / "no-edge.exe")})))
    with pytest.raises(QAError) as err:
        report_service.print_pdf("<h1>Report</h1>", tmp_path / "x.pdf")
    assert "the included Chromium could not start" in err.value.message and "Microsoft Edge could not start" in err.value.message
    assert not list(tmp_path.glob("*.print.html"))


def test_dashboard_counts_criteria_kept_as_work_items(env):
    c, ado, rt = env
    ado.extra_types = ["Acceptance Criteria"]
    ado.items.update({
        119: {"System.Title": "Guest checkout", "System.WorkItemType": "User Story", "System.State": "Active"},
        120: {"System.Title": "Wish list", "System.WorkItemType": "User Story", "System.State": "New"},
        501: {"System.Title": "Guest can pay by card", "System.WorkItemType": "Acceptance Criteria", "System.State": "New"},
        502: {"System.Title": "Guest gets a receipt", "System.WorkItemType": "Acceptance Criteria", "System.State": "New"},
        503: {"System.Title": "Saved card is masked", "System.WorkItemType": "Acceptance Criteria", "System.State": "New"},
    })
    child, parent = "System.LinkTypes.Hierarchy-Forward", "System.LinkTypes.Hierarchy-Reverse"
    ado.relations.update({501: [(parent, 119)], 502: [(parent, 119)], 503: [(parent, 118)]})
    rt.tfs.invalidate()
    ac = c.get("/api/analytics/summary").json()["acceptance_criteria"]
    # story 118: 1 criterion in its field + 1 linked work item; story 119: 2 linked work items; story 120: none
    assert ac == {"available": True, "criteria": 4, "in_field": 1, "work_items": 3, "stories_with_criteria": 2,
                  "stories": 3}, ac
