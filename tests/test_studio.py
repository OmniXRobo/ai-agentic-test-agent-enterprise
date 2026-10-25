"""AI-Driven Automation Script Studio: generate -> validate -> save -> edit -> Play -> evidence, end to end.

The application under test is the local site in tests/site, TFS is the Azure DevOps
double and the AI is the scripted LLM double; the generated framework runs for real
with pytest + Playwright in a separate process, exactly as on a user's PC.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from datetime import date, timedelta
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from tests.fake_ado import PAT, FakeAzureDevOps
from tests.fake_llm import FakeLLM
from tests.site_server import SITE, SiteHandler

try:
    import playwright  # noqa: F401

    HAVE_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    HAVE_PLAYWRIGHT = False

needs_browser = pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright is not installed")

MARKER = "Automation Engineering Agent building a Page Object Model"


def _steps_xml(*steps: tuple[str, str]) -> str:
    body = "".join(
        f'<step id="{n}" type="ValidateStep"><parameterizedString isformatted="true">{a}</parameterizedString>'
        f'<parameterizedString isformatted="true">{e}</parameterizedString></step>' for n, (a, e) in enumerate(steps, 2))
    return f'<steps id="0" last="{len(steps) + 1}">{body}</steps>'


LOGIN = [
    {"action": "goto", "value": "login.html", "description": "Open the sign-in page", "manual_step": 1,
     "business_flow": "login"},
    {"action": "fill", "selector": "#username", "value": "{{CRED:admin.username}}", "description": "Enter the user name",
     "manual_step": 1, "business_flow": "login", "element_name": "username"},
    {"action": "fill", "selector": "#password", "value": "{{CRED:admin.password}}", "description": "Enter the password",
     "manual_step": 1, "business_flow": "login", "element_name": "password"},
    {"action": "click", "selector": "#login-btn", "description": "Click Sign in", "manual_step": 1,
     "business_flow": "login", "element_name": "sign in"},
]


def mapping(body: dict) -> dict:
    """The Automation Engineering Agent's answer for the Test Case named in the prompt."""
    user = body["messages"][1]["content"]
    tc = re.search(r"Test Case: TC(\d+)", user).group(1)
    if tc == "1280":
        return {"steps": LOGIN + [
            {"action": "expect_text", "selector": "#welcome", "value": "Welcome alice", "description": "Welcome is shown",
             "manual_step": 1, "element_name": "welcome message"}],
            "covered_acceptance_criteria": ["AC118-1"]}
    return {"steps": LOGIN + [
        {"action": "select", "selector": "#region", "value": "{{DATA:region}}", "description": "Choose the region",
         "manual_step": 2, "element_name": "region"},
        {"action": "fill", "selector": "#travel", "value": "{{DATA:travel_date}}", "description": "Enter the travel date",
         "manual_step": 2, "element_name": "travel date"},
        {"action": "click", "selector": "#login-btn", "description": "Submit again", "manual_step": 2,
         "element_name": "sign in"},
        {"action": "expect_text", "selector": "#welcome", "value": "{{DATA:travel_date}}",
         "description": "The travel date is confirmed", "manual_step": 2, "element_name": "welcome message"}],
        "test_data": {"travel_date": "{{TODAY+7d:%d/%m/%Y}}"}, "covered_acceptance_criteria": ["AC118-1"]}


@pytest.fixture()
def studio_env(paths, site):
    ado, llm = FakeAzureDevOps(), FakeLLM()
    llm.extra[MARKER] = mapping
    ado.items[1280]["Microsoft.VSTS.TCM.Steps"] = _steps_xml(("Sign in as admin", "Welcome message shown"))
    ado.items[1281] = {"System.Title": "Book travel for a region", "System.WorkItemType": "Test Case",
                       "System.State": "Design", "System.IterationPath": "Shop\\Sprint 4",
                       "Microsoft.VSTS.TCM.Steps": _steps_xml(("Sign in as admin", "Welcome shown"),
                                                              ("Choose a region and a travel date a week ahead",
                                                               "The travel date is confirmed"))}
    ado.relations[118].append(("Microsoft.VSTS.Common.TestedBy-Forward", 1281))
    ado.relations[1280] = [("Microsoft.VSTS.Common.TestedBy-Reverse", 118)]
    ado.relations[1281] = [("Microsoft.VSTS.Common.TestedBy-Reverse", 118)]
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport(), ai_transport=llm.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-7b-instruct-q4_k_m.gguf"})
        rt = c.app.state.runtime
        rt.settings.update({"playback": {"visual_delay_ms": 20}})
        for name, region in (("Gulf", "Middle East"), ("Europe", "Europe")):
            rt.db.execute("INSERT INTO test_data (user_story_id, test_case_ref, tfs_test_case_id, name, kind, dataset,"
                          " source_run_id, created_at, updated_at) VALUES ('118', 'TC2', 1281, ?, 'valid', ?, 'R', 'x', 'x')",
                          (name, json.dumps({"region": region})))
        yield {"c": c, "rt": rt, "ado": ado, "llm": llm, "site": site}


def wait_job(c: TestClient, job_id: str, timeout: float = 120) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = c.get(f"/api/automation/studio/jobs/{job_id}").json()["job"]
        if job["status"] in ("COMPLETED", "FAILED", "STOPPED"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish: {job}")


def generate(env: dict, **extra) -> dict:
    c, rt, site = env["c"], env["rt"], env["site"]
    if "disc" not in env:
        env["disc"] = rt.automation.discovery.crawl("DISC-S1", f"{site}/login.html", max_pages=1, max_depth=0,
                                                    headless=True)
    disc = env["disc"]
    body = {"selections": [{"user_story_id": 118}], "base_url": site, "application": "Shop", "credential_role": "admin",
            "discovery_id": disc["id"], **extra}
    r = c.post("/api/automation/studio/jobs", json=body)
    assert r.status_code == 202, r.text
    return wait_job(c, r.json()["job_id"])


@needs_browser
def test_story_generates_pom_framework_with_reuse_and_verified_files(studio_env):
    c, rt = studio_env["c"], studio_env["rt"]
    ctx = c.get("/api/automation/studio/context").json()
    assert ctx["project"] == "Shop" and ctx["ai_runtime"] == "On-Premise AI"
    stories = c.get("/api/automation/studio/stories", params={"sprint": "Shop\\Sprint 4"}).json()["items"]
    assert [s["id"] for s in stories] == [118] and stories[0]["sprint"] == "Sprint 4"
    cases = c.get("/api/automation/studio/stories/118/test-cases").json()
    assert [t["id"] for t in cases["test_cases"]] == [1280, 1281]
    assert cases["acceptance_criteria"] == [{"id": "AC118-1", "title": "Pay with saved card"}]

    job = generate(studio_env)
    assert job["status"] == "COMPLETED", job
    assert job["summary"] == {"total": 2, "completed": 2, "running": 0, "waiting": 0, "failed": 0, "stopped": 0,
                              "progress": 100}
    assert job["stories"][0]["user_story_id"] == "118" and job["stories"][0]["generated"] == 2
    assert all(i["status"] == "GENERATED" and i["details"]["file_verified"] for i in job["items"])

    ws = rt.studio.ws.root
    # One Page Object and one login flow, shared by both tests.
    assert sorted(p.name for p in (ws / "page_objects").glob("*_page.py")) == ["login_page.py"]
    assert sorted(p.name for p in (ws / "flows").glob("*_flow.py")) == ["login_flow.py"]
    tests = sorted(p.name for p in (ws / "tests").glob("test_*.py"))
    assert tests == ["test_tc1280_checkout_proceeds_to_payment.py", "test_tc1281_book_travel_for_a_region.py"]
    flow = (ws / "flows" / "login_flow.py").read_text()
    assert flow.count("def login(") == 1 and "def login_2" not in flow
    page = (ws / "page_objects" / "login_page.py").read_text()
    assert page.count("def click_sign_in(") == 1 and page.count('Loc(\n        "role", "button", name="Sign in"') == 1
    second = job["items"][1]["details"]
    assert {(comp["name"], comp["change"]) for comp in second["components"]} >= {("login", "reused")}
    assert 'PASSWORD = Loc(\n        "label", "Password"' in page  # password inputs have no textbox role

    test2 = (ws / "tests" / tests[1]).read_text()
    assert "# User Story: US118 - Checkout with saved card" in test2
    assert "# Acceptance Criteria: AC118-1 - Pay with saved card" in test2
    assert "# Test Case: TC1281 - Book travel for a region" in test2 and "# Sprint: Sprint 4" in test2
    assert 'pytest.mark.test_case("1281")' in test2 and "from flows.login_flow import login" in test2
    assert "login(page, credentials.username, credentials.password)" in test2
    data = json.loads((ws / "test_data" / "tc1281.json").read_text())
    assert [x["name"] for x in data["combinations"]] == ["Gulf", "Europe"]
    assert data["combinations"][0]["values"]["travel_date"] == "{{TODAY+7d:%d/%m/%Y}}"
    assert data["credentials"] == {"application": "Shop", "environment": None, "role": "admin"}
    # No secrets and no absolute paths of this PC in anything generated.
    for path in ws.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".json", ".ini", ".cmd", ".txt"):
            text = path.read_text(encoding="utf-8")
            assert "s3cret" not in text and str(ws) not in text and str(Path.home()) not in text, path

    inv = c.get("/api/automation/studio/inventory").json()["items"]
    mine = [i for i in inv if i["framework"]]
    assert [i["test_case_id"] for i in mine] == ["1281", "1280"]  # newest first
    assert all(i["status"] == "GENERATED" and i["file_verified"] and i["method_name"] == "AI-Driven Automation"
               for i in mine)
    assert mine[0]["warnings"] and "credentials missing" in mine[0]["warnings"][0]

    # Generating again skips existing automation unless asked otherwise.
    again = generate(studio_env)
    assert {i["status"] for i in again["items"]} == {"SKIPPED"}
    existing = c.post("/api/automation/studio/existing", json={"test_case_ids": [1280, 999]}).json()
    assert list(existing["existing"]) == ["1280"]

    # A missing file is reported, never shown as generated.
    (ws / "tests" / tests[0]).rename(ws / "tests" / "moved.py.bak")
    inv = {i["test_case_id"]: i for i in c.get("/api/automation/studio/inventory").json()["items"] if i["framework"]}
    assert inv["1280"]["status"] == "FILE MISSING" and not inv["1280"]["file_verified"]
    r = c.post(f"/api/automation/studio/scripts/{inv['1280']['id']}/run", json={})
    assert r.status_code == 409 and r.json()["error_code"] == "SCRIPT_FILE_MISSING"


@needs_browser
def test_edit_data_credentials_play_and_evidence(studio_env):
    c, rt = studio_env["c"], studio_env["rt"]
    generate(studio_env)
    inv = {i["test_case_id"]: i for i in c.get("/api/automation/studio/inventory").json()["items"] if i["framework"]}
    sid = inv["1281"]["id"]
    base = f"/api/automation/studio/scripts/{sid}"

    # Play without credentials: the real reason is given.
    r = c.post(f"{base}/run", json={})
    assert r.status_code == 409 and r.json()["error_code"] == "APP_CREDENTIALS_MISSING"

    # Application credentials (shared) and a script-specific override that wins.
    rt.automation.creds.save("Shop", "admin", "mallory", "wrong-pass", None)
    detail = c.put(f"{base}/credentials", json={"scope": "script", "username": "alice", "password": "s3cret-pass"}).json()
    assert detail["credentials"]["source"] == "script" and detail["credentials"]["username"] == "alice"
    assert rt.automation.creds.find("Shop", "admin", None)["username"] == "mallory"  # shared one untouched

    # View: metadata, source, Flow Logic.
    detail = c.get(base).json()
    assert detail["script"]["file_verified"] and detail["source"].startswith('"""TC1281')
    titles = [s["title"] for s in detail["flow_logic"]]
    assert titles[0] == "Login" and "Choose the region" in titles
    assert any("click sign in" in a.lower() for a in detail["flow_logic"][0]["actions"])

    # Play: real pytest + Playwright run, two data combinations, evidence on disk.
    result = rt.studio.run(sid, headless=True, background=False)
    assert result["status"] == "PASSED", result["error"]
    assert len(result["tests"]) == 2 and all(t["status"] == "PASSED" for t in result["tests"])
    week = (date.today() + timedelta(days=7)).strftime("%d/%m/%Y")
    checked = [a["description"] for s in result["steps"] for a in s.get("actions", []) if a["action"] == "expect_text"]
    assert checked and all(week in d for d in checked), checked
    assert all("{{" not in a["description"] for s in result["steps"] for a in s.get("actions", []))
    assert result["evidence"]["screenshots"] and all(Path(p).is_file() for p in result["evidence"]["screenshots"])
    assert result["traceability"]["user_story_id"] == "118" and result["traceability"]["test_case_id"] == "1281"
    # A screenshot for every step, the expected result of the TFS step and the actual result.
    folder = Path(result["evidence"]["folder"])
    shots = sorted(p.name for p in (folder / "screenshots").glob("*.png"))
    assert "Gulf-step_001_login.png" in shots and "Europe-step_005_the_travel_date_is_confirmed.png" in shots
    assert len(shots) == 10 and all(s.get("screenshot") for s in result["steps"])
    last = result["steps"][-1]
    assert last["expected"] == "The travel date is confirmed" and last["actual"].startswith("As expected: Verify")
    assert result["steps"][0]["expected"] == "Welcome shown" and result["steps"][0]["started_at"]
    assert result["execution_mode"] == "Background / Headless" and result["duration_ms"] > 0
    saved = json.loads((folder / "result.json").read_text())
    assert saved["status"] == "PASSED" and (folder / "execution.log").is_file()
    assert not (folder / "failure-analysis.json").exists()
    log = Path(result["evidence"]["log"]).read_text()
    assert "s3cret-pass" not in log
    assert "s3cret-pass" not in Path(result["evidence"]["progress"]).read_text()
    logs = c.get(f"{base}/logs").json()
    assert logs["run"]["status"] == "PASSED" and logs["generation"]
    results = c.get(f"{base}/results").json()["items"]
    assert results[0]["status"] == "PASSED"
    assert c.get("/api/automation/evidence", params={"path": result["evidence"]["screenshots"][0]}).status_code == 200
    inv = {i["id"]: i for i in c.get("/api/automation/studio/inventory").json()["items"]}
    assert inv[sid]["last_result"] == "PASSED"

    # Test Data: change one combination, keep only it, run again.
    data = c.get(base).json()["data"]
    combos = [dict(data["combinations"][1], name="Europe next month")]
    combos[0]["values"] = {**combos[0]["values"], "travel_date": "{{TODAY+1m:%d/%m/%Y}}"}
    c.put(f"{base}/data", json={"combinations": combos}).raise_for_status()
    bad = c.put(f"{base}/data", json={"combinations": [{"name": "x", "values": {"region": "Europe"}}]})
    assert bad.status_code == 422 and bad.json()["error_code"] == "TEST_DATA_MISSING"
    result = rt.studio.run(sid, headless=True, background=False)
    assert result["status"] == "PASSED" and len(result["tests"]) == 1, result["error"]

    # The application shows another result: diagnosed as an application problem, and a defect draft is recorded.
    combos[0]["values"]["travel_date"] = "not-a-date"
    c.put(f"{base}/data", json={"combinations": combos}).raise_for_status()
    detail = c.get(base).json()
    source = detail["source"].replace('login_page.expect_welcome_message_text(data["travel_date"])',
                                      'login_page.expect_welcome_message_text("Welcome bob")')
    c.put(f"{base}/source", json={"source": source}).raise_for_status()
    failed = rt.studio.run(sid, headless=True, background=False)
    assert failed["status"] == "FAILED" and failed["failed_step"] == 4, failed["error"]
    analysis = failed["failure_analysis"]
    assert analysis["classification"] == "Expected Result Mismatch" and analysis["likely_application_problem"]
    assert analysis["expected"] == "The travel date is confirmed" and "Welcome bob" in analysis["actual"]
    assert analysis["failed_step_title"].endswith("The travel date is confirmed")
    folder = Path(failed["evidence"]["folder"])
    assert json.loads((folder / "failure-analysis.json").read_text())["classification"] == "Expected Result Mismatch"
    names_ = {p.name for p in (folder / "screenshots").glob("*.png")}
    assert {"Europe next month-step_005_the_travel_date_is_confirmed_FAILED.png".replace(" ", "_"),
            "Europe_next_month-step_005_the_travel_date_is_confirmed_FAILED_full.png"} <= names_, names_
    assert failed["diagnostics"]["url"].endswith("/login.html")
    assert rt.defects.wait_for_automatic(failed["run_id"], 10)
    drafts = rt.db.query("SELECT * FROM defect_drafts WHERE source_run_id = ?", (failed["run_id"],))
    assert len(drafts) == 1 and "Expected Result Mismatch" in drafts[0]["repro_steps"]
    events = [e["name"] for e in rt.db.query("SELECT name FROM events WHERE run_id = ? AND agent_id = ?",
                                             (failed["run_id"], "failure_analysis"))]
    assert "agent.started" in events and "agent.completed" in events

    # Wrong password: an authentication problem, not an application defect.
    c.put(f"{base}/source", json={"source": detail["source"]}).raise_for_status()
    c.put(f"{base}/credentials", json={"scope": "script", "password": "nope"}).raise_for_status()
    failed = rt.studio.run(sid, headless=True, background=False)
    assert failed["status"] == "FAILED" and failed["error"] and failed["failed_step"] is not None
    assert "nope" not in Path(failed["evidence"]["log"]).read_text()
    analysis = failed["failure_analysis"]
    assert analysis["classification"] == "Authentication Failure", analysis
    assert any("Invalid credentials" in o for o in analysis["observed"]) and not analysis["likely_application_problem"]
    time.sleep(0.5)
    assert not rt.db.query("SELECT id FROM defect_drafts WHERE source_run_id = ?", (failed["run_id"],))


@needs_browser
def test_edit_validation_versions_compare_restore(studio_env):
    c, rt = studio_env["c"], studio_env["rt"]
    generate(studio_env)
    sid = next(i["id"] for i in c.get("/api/automation/studio/inventory").json()["items"] if i["test_case_id"] == "1280")
    base = f"/api/automation/studio/scripts/{sid}"
    original = c.get(base).json()
    path = rt.studio.ws.path(original["script"]["file"])

    # Invalid edits are rejected with the real reason, and the working file is untouched.
    for broken, code in ((original["source"].replace("def test_", "def test_(", 1), "PYTHON_SYNTAX_INVALID"),
                         (original["source"].replace("login_page.", "nowhere_page.", 1), "PAGE_OBJECT_REFERENCE_INVALID"),
                         (original["source"].replace("from flows.login_flow import login",
                                                     "from flows.missing import login"), "IMPORT_VALIDATION_FAILED")):
        r = c.put(f"{base}/source", json={"source": broken})
        assert r.status_code == 422 and r.json()["error_code"] == code, (code, r.json())
        assert path.read_text() == original["source"]

    edited = original["source"].replace('"Welcome is shown"', '"The welcome message is shown"')
    r = c.put(f"{base}/source", json={"source": edited, "reason": "Clearer step name"}).json()
    assert r["saved"] and path.read_text() == edited
    # Flow Logic edit: rename a step, then move it.
    r = c.post(f"{base}/flow", json={"operations": [{"op": "rename", "index": 1, "title": "Welcome message checked"}]})
    assert r.status_code == 200, r.text
    assert 'with step("Welcome message checked"' in path.read_text()

    versions = c.get(base).json()["versions"]
    assert [v["version"] for v in versions] == [1, 2, 3] and versions[1]["reason"] == "Clearer step name"
    diff = c.get(f"{base}/compare", params={"a": 1}).json()
    assert any(line.startswith("+") and "Welcome message checked" in line for line in diff["script_diff"])
    restored = c.post(f"{base}/restore/1").json()
    assert restored["source"] == original["source"] and path.read_text() == original["source"]
    assert restored["versions"][-1]["reason"] == "Restored version 1"


@needs_browser
def test_failed_regeneration_keeps_the_last_working_script(studio_env):
    c, rt, llm = studio_env["c"], studio_env["rt"], studio_env["llm"]
    generate(studio_env)
    rec = next(i for i in c.get("/api/automation/studio/inventory").json()["items"] if i["test_case_id"] == "1280")
    before = rt.studio.ws.path(rec["file"]).read_text()
    llm.extra[MARKER] = {"steps": [{"action": "fill", "selector": "#username", "value": "{{DATA:unknown}}",
                                    "description": "Uses data nobody provided"}]}
    job_id = c.post(f"/api/automation/studio/scripts/{rec['id']}/regenerate", json={}).json()["job_id"]
    job = wait_job(c, job_id)
    item = job["items"][0]
    assert item["status"] == "FAILED" and item["error_code"] == "TEST_DATA_MISSING" and "unknown" in item["error"]
    assert rt.studio.ws.path(rec["file"]).read_text() == before
    assert c.get(f"/api/automation/studio/scripts/{rec['id']}").json()["script"]["version"] == 1

    llm.down = True
    retry = wait_job(c, c.post(f"/api/automation/studio/job-items/{item['id']}/retry").json()["job_id"])
    assert retry["items"][0]["error_code"] == "AI_UNAVAILABLE"
    assert retry["items"][0]["error"].startswith("On-Premise AI unavailable")
    assert rt.studio.ws.path(rec["file"]).read_text() == before

    # Regenerating with the same mapping changes nothing; files stay as they are.
    llm.down = False
    llm.extra[MARKER] = mapping
    job = wait_job(c, c.post(f"/api/automation/studio/scripts/{rec['id']}/regenerate", json={}).json()["job_id"])
    assert job["items"][0]["status"] in ("UNCHANGED", "UPDATED")


@needs_browser
def test_self_healing_is_recorded_and_applied_only_on_request(studio_env, tmp_path):
    c, rt = studio_env["c"], studio_env["rt"]
    generate(studio_env)
    rec = next(i for i in c.get("/api/automation/studio/inventory").json()["items"] if i["test_case_id"] == "1280")
    rt.studio.save_credentials(rec["id"], username="alice", password="s3cret-pass", scope="script")
    # Make the Page Object depend on the button id only (no role locator, no alternates).
    comp = c.get("/api/automation/studio/components", params={"path": "page_objects/login_page.py"}).json()
    assert comp["affected_tests"] == ["tests/test_tc1280_checkout_proceeds_to_payment.py",
                                      "tests/test_tc1281_book_travel_for_a_region.py"]
    src = re.sub(r'SIGN_IN = Loc\(\n.*?\n    \)', 'SIGN_IN = Loc(\n        "css", "#login-btn",\n'
                 '        fingerprint={"tag": "button", "text": "Sign in", "role": "button", "name": "Sign in",'
                 ' "classes": ["btn", "primary"]},\n    )', comp["source"], flags=re.S)
    assert src != comp["source"]
    saved = c.put("/api/automation/studio/components", json={"path": "page_objects/login_page.py", "source": src})
    assert saved.status_code == 200, saved.text
    broken = src.replace("def click_sign_in(self):", "def click_sign_in(self, extra):")
    r = c.put("/api/automation/studio/components", json={"path": "page_objects/login_page.py", "source": broken})
    assert r.status_code == 422 and r.json()["error_code"] == "PAGE_OBJECT_REFERENCE_INVALID", r.json()
    assert "flows/login_flow.py" in r.json()["message"] and "click_sign_in() takes 1 argument(s), 0 given" in r.json()["message"]

    # The application changes: the button id is now signin-btn.
    site2 = tmp_path / "site2"
    shutil.copytree(SITE, site2)
    (site2 / "login.html").write_text((SITE / "login_v2.html").read_text())
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SiteHandler, directory=str(site2)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        rt.db.execute("UPDATE framework_scripts SET base_url = ? WHERE id = ?",
                      (f"http://127.0.0.1:{server.server_address[1]}", rec["id"]))
        before = rt.studio.ws.read("page_objects/login_page.py")
        result = rt.studio.run(rec["id"], headless=True, background=False)
        assert result["status"] == "PASSED", result["error"]
        assert rt.studio.ws.read("page_objects/login_page.py") == before  # never changed silently
        heal = rt.db.query_one("SELECT * FROM healing_events WHERE run_id = ?", (result["run_id"],))
        assert heal["original_selector"] == "#login-btn" and heal["healed_selector"] == "#signin-btn"
        assert heal["result"] == "validated" and heal["component"] == "LoginPage.SIGN_IN" and heal["confidence"] >= 0.62
        applied = c.post(f"/api/automation/healing/{heal['id']}/apply").json()
        assert applied["framework"]["component"] == "LoginPage.SIGN_IN"
        page = rt.studio.ws.read("page_objects/login_page.py")
        assert '"css", "#signin-btn"' in page and '"#login-btn"' in page  # the old one is kept as an alternate
        again = rt.studio.run(rec["id"], headless=True, background=False)
        assert again["status"] == "PASSED" and not again["healed"]
        versions = c.get(f"/api/automation/studio/scripts/{rec['id']}").json()["versions"]
        assert versions[-1]["kind"] == "healed"
    finally:
        server.shutdown()


def test_stop_job_and_error_reasons(studio_env):
    c, rt, llm = studio_env["c"], studio_env["rt"], studio_env["llm"]
    bad = c.post("/api/automation/studio/jobs", json={"selections": [{"user_story_id": 118}], "base_url": "ftp://x"})
    assert bad.status_code == 400 and bad.json()["error_code"] == "URL_INVALID"
    assert c.get("/api/automation/studio/jobs/nope").status_code == 400
    assert c.get("/api/automation/studio/scripts/FWS-0000000000").status_code == 404

    gate = threading.Event()
    def slow(body):
        gate.wait(10)
        return mapping(body)
    llm.extra[MARKER] = slow
    rt.automation.discovery.latest_for = lambda url, application=None: {"id": "D", "pages": [{"url": f"{url}/login.html", "title": "Sign in",
        "elements": [{"selector": "#username", "tag": "input", "element_id": "username"},
                     {"selector": "#password", "tag": "input", "element_id": "password"},
                     {"selector": "#login-btn", "tag": "button", "role": "button", "name": "Sign in"},
                     {"selector": "#welcome", "tag": "p", "element_id": "welcome"}]}]}
    job_id = c.post("/api/automation/studio/jobs", json={"selections": [{"user_story_id": 118}],
                                                         "base_url": studio_env["site"]}).json()["job_id"]
    for _ in range(100):
        if c.get(f"/api/automation/studio/jobs/{job_id}").json()["job"]["summary"]["running"]:
            break
        time.sleep(0.05)
    assert c.post(f"/api/automation/studio/jobs/{job_id}/stop").json()["ok"]
    gate.set()
    job = wait_job(c, job_id)
    assert job["status"] == "STOPPED"
    statuses = [i["status"] for i in job["items"]]
    assert statuses[1] == "STOPPED" and statuses[0] in ("GENERATED", "STOPPED")
    if statuses[0] == "GENERATED":  # scripts finished before Stop are kept
        assert rt.studio.ws.exists(job["items"][0]["details"]["file"])


def test_runtime_values_resolve_from_the_system_clock():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rv", Path(__file__).parent.parent / "qa_platform/automation/framework/template/utilities/runtime_values.py")
    rv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rv)
    today = date.today()
    assert rv.resolve("{{TODAY}}") == today.isoformat()
    assert rv.resolve("{{TODAY+7d:%Y-%m-%d}}") == (today + timedelta(days=7)).isoformat()
    assert rv.resolve("{{TODAY-1d:%Y-%m-%d}}") == (today - timedelta(days=1)).isoformat()
    assert rv.resolve("on {{TODAY+2w:%Y-%m-%d}}!") == f"on {(today + timedelta(days=14)).isoformat()}!"
    assert re.fullmatch(r"\d\d:\d\d", rv.resolve("{{NOW+2h:%H:%M}}"))
    assert rv.resolve("plain") == "plain"


def test_cloud_ai_mode_uses_api_key_from_credential_store(client):
    import httpx

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "cloud-model"}]})

    rt = client.app.state.runtime
    rt.ai.transport = httpx.MockTransport(handler)
    r = client.put("/api/ai/settings", json={"mode": "cloud", "cloud_base_url": "https://ai.example.com/v1",
                                             "cloud_model": "m", "api_key": "sk-test-123"}).json()
    assert r["mode"] == "cloud" and r["label"] == "Cloud AI" and r["base_url"] == "https://ai.example.com/v1"
    assert r["cloud"]["has_api_key"] and "sk-test-123" not in json.dumps(r)
    assert "sk-test-123" not in rt.paths.settings_file.read_text()
    assert client.get("/api/ai/models").json()["models"] == ["cloud-model"]
    assert seen[-1].headers["authorization"] == "Bearer sk-test-123"
    client.put("/api/ai/settings", json={"clear_api_key": True})
    r = client.get("/api/ai/models")
    assert r.status_code == 409 and "Cloud AI API key" in r.json()["message"]
    back = client.put("/api/ai/settings", json={"mode": "on_premise"}).json()
    assert back["label"] == "On-Premise AI" and back["base_url"] == "http://127.0.0.1:8080/v1"


@pytest.fixture()
def display(monkeypatch):
    """A virtual screen for Visible Browser runs on a machine without one (CI)."""
    import os
    import subprocess

    if os.environ.get("DISPLAY"):
        yield os.environ["DISPLAY"]
        return
    if not shutil.which("Xvfb"):
        pytest.skip("no display and no Xvfb for a visible browser")
    proc = subprocess.Popen(["Xvfb", ":97", "-screen", "0", "1400x900x24"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    monkeypatch.setenv("DISPLAY", ":97")
    yield ":97"
    proc.terminate()


@needs_browser
def test_visible_browser_run_and_stop(studio_env, display):
    import os

    c, rt = studio_env["c"], studio_env["rt"]
    generate(studio_env)
    sid = next(i["id"] for i in c.get("/api/automation/studio/inventory").json()["items"] if i["test_case_id"] == "1280")
    rt.studio.save_credentials(sid, username="alice", password="s3cret-pass", scope="script")

    # Visible Browser: same script, cursor, highlight and typing; same result and evidence as headless.
    visible = rt.studio.run(sid, headless=False, background=False)
    assert visible["status"] == "PASSED", visible["error"]
    assert visible["execution_mode"] == "Visible Browser"
    assert len([s for s in visible["steps"] if s.get("screenshot")]) == len(visible["steps"]) == 2
    headless = rt.studio.run(sid, headless=True, background=False)
    assert headless["status"] == "PASSED"
    assert [s["description"] for s in headless["steps"]] == [s["description"] for s in visible["steps"]]

    # Stop: the run ends as STOPPED, evidence so far is kept and no browser or Python process is left behind.
    rt.settings.update({"playback": {"visual_delay_ms": 1500}})
    run_id = c.post(f"/api/automation/studio/scripts/{sid}/run", json={"headless": False}).json()["run_id"]
    for _ in range(200):
        if rt.studio.runner.processes.get(run_id) and (rt.paths.evidence / run_id / "progress.jsonl").exists():
            break
        time.sleep(0.05)
    pid = rt.studio.runner.processes[run_id].pid
    time.sleep(1.0)
    assert c.post(f"/api/automation/studio/runs/{run_id}/stop").json()["ok"]
    for _ in range(200):
        rows = rt.automation.runner.results(run_id=run_id)
        if rows and rows[0]["status"] != "RUNNING":
            break
        time.sleep(0.1)
    assert rows[0]["status"] == "STOPPED"
    assert json.loads((rt.paths.evidence / run_id / "result.json").read_text())["status"] == "STOPPED"
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)  # the whole process group (pytest and its browser) is gone
    assert run_id not in rt.studio.runner.running()
    assert c.post(f"/api/automation/studio/runs/{run_id}/stop").status_code == 409


def _serve_through(page, c: TestClient) -> str:
    """Serve the web UI and API to a real browser page from the in-process app (no port needed)."""
    origin = "http://studio.test"

    def handle(route):
        req = route.request
        path = req.url[len(origin):] or "/"
        if path.startswith("/api/events/stream"):
            return route.abort()  # the live stream never ends; pages fall back to polling
        r = c.request(req.method, path, content=req.post_data_buffer, headers={k: v for k, v in req.headers.items()
                                                                              if k.lower() == "content-type"})
        route.fulfill(status=r.status_code, body=r.content,
                      headers={k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-disposition")})

    page.route(f"{origin}/**", handle)
    return origin


@needs_browser
def test_script_studio_page_inventory_play_dialog_result_and_evidence(studio_env):
    from playwright.sync_api import expect, sync_playwright

    c, rt = studio_env["c"], studio_env["rt"]
    generate(studio_env)
    sid = next(i["id"] for i in c.get("/api/automation/studio/inventory").json()["items"] if i["test_case_id"] == "1280")
    rt.studio.save_credentials(sid, username="alice", password="s3cret-pass", scope="script")
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        origin = _serve_through(page, c)

        # Inventory: the spec's columns, newest first, the actions of each script.
        page.goto(f"{origin}/#/ai-automation")
        inv = page.locator("#inv")
        expect(inv.locator("tbody tr")).to_have_count(2)
        heads = inv.locator("thead th").all_inner_texts()
        assert heads[1:] == ["Method", "User Story", "User Story Title", "Test Case", "Test Case Title", "Sprint",
                             "Script", "Generated", "Status", "Last Result", "Actions"]
        row = inv.locator("tbody tr", has_text="TC1280")
        for action in ("View Script", "Edit Script", "Flow Logic", "Play", "Regenerate / Update"):
            expect(row.get_by_text(action, exact=True)).to_be_visible()

        # View Script: highlighted Python with traceability.
        row.get_by_text("View Script", exact=True).click()
        expect(page.locator(".codeview pre.code.py")).to_contain_text("TC1280")
        expect(page.locator(".py-k").first).to_be_visible()

        # Play asks how to run; Cancel runs nothing.
        page.locator("#play").click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_contain_text("How would you like to run this automation?")
        expect(dialog.get_by_text("Visible Browser Execution")).to_be_visible()
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_have_count(0)
        assert not rt.automation.runner.results(script_id=sid)

        # Headless: live status, then the result with steps, then the evidence and logs.
        page.locator("#play").click()
        dialog.get_by_text("Background / Headless Execution").click()
        dialog.get_by_role("button", name="Run Test").click()
        live = page.locator("#live")
        expect(live).to_contain_text("Test Execution Agent - RUNNING")
        expect(live).to_contain_text("Background / Headless")
        expect(page.locator("#lastResult")).to_contain_text("PASSED", timeout=90_000)
        pane = page.locator("#tabPane")
        expect(pane).to_contain_text("Welcome message shown", timeout=10_000)
        expect(pane.locator("table.data").first).to_contain_text("PASSED")
        page.get_by_role("tab", name="Evidence").click()
        expect(page.locator(".evidence-list .shot")).to_have_count(2)
        page.locator(".evidence-list .shot").first.click()
        expect(page.get_by_role("dialog")).to_contain_text("1 of 2")
        page.keyboard.press("Escape")
        page.get_by_role("tab", name="Logs").click()
        expect(pane).to_contain_text("passed")
        expect(pane).not_to_contain_text("s3cret-pass")

        # Inventory shows the last result and offers Result / Logs / Evidence.
        page.goto(f"{origin}/#/ai-automation?tab=scripts")
        row = page.locator("#inv tbody tr", has_text="TC1280")
        expect(row).to_contain_text("PASSED")
        for action in ("Result", "Logs", "Evidence"):
            expect(row.get_by_text(action, exact=True)).to_be_visible()

        # Play Selected asks once and recommends Headless for several tests.
        page.locator("#all").check()
        page.locator("#playSel").click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_contain_text("Recommended for several tests")
        assert dialog.locator("input[value=headless]").is_checked()
        dialog.get_by_role("button", name="Cancel").click()

        # Generate wizard loads Sprint, User Stories and the existing-automation badge.
        page.get_by_role("tab", name="Generate Automation").click()
        expect(page.locator("#stories")).to_contain_text("US118")
        browser.close()
    assert not errors, errors
