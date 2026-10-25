"""Stage 4: Playwright automation against a local test site with a real headless browser."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from qa_platform.automation import healing
from qa_platform.automation.dsl import Script, Step, Target, ValueResolver, new_script_id, to_playwright_python
from qa_platform.automation.recorder import merge_step
from tests.site_server import SITE


try:
    import playwright  # noqa: F401  (availability check)

    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

needs_browser = pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright is not installed")


@pytest.fixture()
def auto(rt):
    rt.settings.update({"playback": {"visual_delay_ms": 20}})
    rt.automation.creds.save("Shop", "admin", "alice", "s3cret-pass", None)
    return rt.automation


def _login_script(base: str, page: str = "login.html", button: Target | None = None) -> Script:
    button = button or Target(selector="#login-btn", tag="button", text="Sign in", role="button", name="Sign in",
                              element_id="login-btn", classes=["btn", "primary"], description="Sign in button")
    return Script(id=new_script_id(), name="Login works", mode="ai", base_url=base, application="Shop",
                  credential_role="admin", test_case_id="101", steps=[
                      Step(action="goto", value=f"{base}/{page}"),
                      Step(action="fill", target=Target(selector="#username"), value="{{CRED:admin.username}}"),
                      Step(action="fill", target=Target(selector="#password"), value="{{CRED:admin.password}}"),
                      Step(action="fill", target=Target(selector="#travel"), value="{{TODAY+3d:%d/%m/%Y}}"),
                      Step(action="select", target=Target(selector="#region"), value="Middle East"),
                      Step(action="check", target=Target(selector="#remember")),
                      Step(action="click", target=button),
                      Step(action="expect_text", target=Target(selector="#welcome"), value="Welcome alice"),
                  ])


# ---- pure units ------------------------------------------------------------------------


def test_resolver_dates_use_system_clock_and_mask_passwords():
    fixed = datetime(2026, 1, 31, 9, 30).astimezone()
    r = ValueResolver(data={"city": "Dubai"}, credentials=lambda role, f: "pw-123" if f == "password" else "bob",
                      clock=lambda: fixed)
    assert r.resolve("{{TODAY}}") == "2026-01-31"
    assert r.resolve("{{TODAY+1m:%d/%m/%Y}}") == "28/02/2026"
    assert r.resolve("{{TODAY-1d}}") == "2026-01-30"
    assert r.resolve("{{DATA:city}} {{CRED:admin.username}}") == "Dubai bob"
    assert r.resolve("{{CRED:admin.password}}") == "pw-123"
    assert r.mask("typed pw-123 into field") == "typed *** into field"
    with pytest.raises(KeyError):
        r.resolve("{{DATA:missing}}")


def test_healing_prefers_confident_unambiguous_match():
    target = Target(selector="#login-btn", tag="button", text="Sign in", role="button", name="Sign in",
                    element_id="login-btn")
    cands = [
        {"selector": "#cancel-btn", "tag": "button", "text": "Cancel", "role": "button", "name": "Cancel",
         "element_id": "cancel-btn"},
        {"selector": "#signin-btn", "tag": "button", "text": "Sign in", "role": "button", "name": "Sign in",
         "element_id": "signin-btn"},
    ]
    chosen = healing.choose(healing.rank(target, cands))
    assert chosen and chosen["selector"] == "#signin-btn" and chosen["confidence"] >= healing.THRESHOLD
    twins = [dict(cands[1], selector="#a"), dict(cands[1], selector="#b")]
    assert healing.choose(healing.rank(target, twins)) is None  # ambiguous -> no silent change


def test_merge_step_collapses_typing():
    steps = []
    merge_step(steps, {"action": "fill", "target": {"selector": "#u"}, "value": "a"})
    merge_step(steps, {"action": "fill", "target": {"selector": "#u"}, "value": "al"})
    merge_step(steps, {"action": "click", "target": {"selector": "#b"}})
    merge_step(steps, {"action": "click", "target": {"selector": "#b"}})
    assert [s["action"] for s in steps] == ["fill", "click", "click"] and steps[0]["value"] == "al"


def test_export_has_no_secret_values(site):
    py = to_playwright_python(_login_script(site))
    assert "s3cret" not in py and "{{CRED:admin.password}}" in py and "def run(page):" in py


def test_script_store_versions_and_restore(auto, site):
    s = auto.scripts.save(_login_script(site), reason="created")
    s.name = "Renamed"
    auto.scripts.save(s, reason="edit")
    assert auto.scripts.get(s.id).version == 2
    restored = auto.scripts.restore(s.id, 1)
    assert restored.name == "Login works" and restored.version == 3
    assert [v["reason"] for v in auto.scripts.versions(s.id)] == ["created", "edit", "restore v1"]


def test_credentials_api_never_returns_password(client):
    r = client.put("/api/automation/credentials", json={"application": "Shop", "role": "admin", "username": "alice",
                                                        "password": "s3cret-pass"})
    assert r.status_code == 200 and "s3cret" not in r.text
    items = client.get("/api/automation/credentials").json()["items"]
    assert items[0]["has_password"] is True and "s3cret" not in json.dumps(items)
    rt = client.app.state.runtime
    assert rt.secrets.get("appcred", "shop|admin") == "s3cret-pass"
    assert rt.secrets.get("tfs", "shop|admin") is None  # separate from TFS credentials


def test_api_validation_errors(client):
    r = client.get("/api/automation/scripts/not-an-id")
    assert r.status_code == 400 and r.json()["error_code"] == "SCRIPT_ID_INVALID"
    r = client.get("/api/automation/scripts/SCR-0000000000")
    assert r.status_code == 404 and r.json()["error_code"] == "SCRIPT_NOT_FOUND"
    r = client.post("/api/automation/discoveries", json={"base_url": "ftp://x"})
    assert r.json()["error_code"] == "URL_INVALID"
    r = client.get("/api/automation/evidence", params={"path": "/etc/passwd"})
    assert r.status_code == 404


# ---- real browser ------------------------------------------------------------------------


@needs_browser
def test_run_passes_with_credentials_dates_and_evidence(auto, rt, site):
    script = auto.scripts.save(_login_script(site))
    before = hashlib.sha256((SITE / "login.html").read_bytes()).hexdigest()
    record = auto.run_script(script.id, headless=True, background=False)
    assert record["status"] == "PASSED", record["error"]
    assert all(s["status"] == "PASSED" for s in record["steps"])
    assert Path(record["evidence"]["screenshots"][-1]).exists()
    # Visual playback is injected at runtime only: the application under test is unchanged.
    assert hashlib.sha256((SITE / "login.html").read_bytes()).hexdigest() == before
    # The password never reaches events, results or evidence.
    events = json.dumps(rt.bus.recent(500))
    stored = json.dumps(auto.runner.results(record["run_id"]))
    evidence = (Path(record["evidence"]["folder"]) / "execution.json").read_text()
    assert "s3cret-pass" not in events + stored + evidence
    assert rt.runs.get(record["run_id"])["status"] == "PASSED"


@needs_browser
def test_run_failure_records_step_and_screenshot(auto, site):
    script = _login_script(site)
    script.steps[-1].value = "Welcome bob"
    script.steps[-1].timeout_ms = 800
    script = auto.scripts.save(script)
    record = auto.run_script(script.id, headless=True, background=False)
    assert record["status"] == "FAILED" and record["failed_step"] == 7
    failed = record["steps"][-1]
    assert failed["status"] == "FAILED" and Path(failed["screenshot"]).exists()


@needs_browser
def test_self_healing_recovers_renamed_id_and_applies_only_after_validation(auto, rt, site):
    script = _login_script(site, page="login_v2.html")
    script.steps[6].timeout_ms = 800
    script = auto.scripts.save(script)
    record = auto.run_script(script.id, headless=True, background=False)
    assert record["status"] == "PASSED", record["error"]
    assert record["healed"][0]["healed"] == "#signin-btn"
    events = auto.runner.healing_events(script.id)
    assert events[0]["result"] == "validated" and Path(events[0]["screenshot"]).exists()
    # The script is not changed until someone applies the validated healing.
    assert auto.scripts.get(script.id).steps[6].target.selector == "#login-btn"
    updated = auto.runner.apply_healing(events[0]["id"])
    assert updated.steps[6].target.selector == "#signin-btn"
    assert "#login-btn" in updated.steps[6].target.alternates
    assert any("self-healing" in v["reason"] for v in auto.scripts.versions(script.id))


@needs_browser
def test_self_healing_refuses_low_confidence(auto, site):
    bad = Target(selector="#does-not-exist", tag="div", text="Totally different", description="Ghost")
    script = _login_script(site, button=bad)
    script.steps[6].timeout_ms = 500
    script = auto.scripts.save(script)
    record = auto.run_script(script.id, headless=True, background=False)
    assert record["status"] == "FAILED" and "self-healing found no confident match" in record["error"]
    assert auto.runner.healing_events(script.id)[0]["result"] == "failed"


@needs_browser
def test_recorder_captures_steps_and_hides_password(auto, site):
    run_id = auto.start_recording(f"{site}/login.html", name="Recorded login", headless=True,
                                  credential_role="admin", application="Shop")
    session = auto.recording(run_id)["session"]
    session.command("fill", ("#username", "alice"))
    session.command("fill", ("#password", "s3cret-pass"))
    session.command("select", ("#region", "me"))
    session.command("click", "#login-btn")
    deadline = time.time() + 10
    while time.time() < deadline and not any(s["action"] == "click" for s in session.steps):
        time.sleep(0.1)
    session.add_assertion("#welcome", "Welcome alice")
    script = auto.stop_recording(run_id)
    actions = [s.action for s in script.steps]
    assert actions[0] == "goto" and "fill" in actions and "select" in actions and "click" in actions
    assert actions[-1] == "expect_text"
    assert "s3cret" not in script.model_dump_json()
    assert any(s.value == "{{CRED:admin.password}}" for s in script.steps)
    assert script.mode == "guided"
    # The recorded script plays back.
    assert auto.run_script(script.id, headless=True, background=False)["status"] == "PASSED"


@needs_browser
def test_discovery_inventories_pages_elements_and_endpoints(auto, site):
    result = auto.discovery.crawl("DISC-T1", f"{site}/index.html", max_pages=6, max_depth=2, headless=True)
    urls = {p["url"] for p in result["pages"]}
    assert f"{site}/login.html" in urls and f"{site}/products.html" in urls
    assert not any("logout" in u for u in urls)
    login = next(p for p in result["pages"] if p["url"].endswith("/login.html"))
    assert any(e["selector"] == "#login-btn" for e in login["elements"])
    assert any(e["endpoint"].endswith("/api/items/{id}") and e["count"] >= 2 for e in result["endpoints"])
    assert result["diff"] is None
    again = auto.discovery.crawl("DISC-T2", f"{site}/index.html", max_pages=6, max_depth=2, headless=True)
    assert again["diff"]["compared_with"] == result["id"]
    script = auto.create_from_discovery(result["id"], login["url"], ["#username", "#login-btn"], "From discovery")
    assert [s.action for s in script.steps] == ["goto", "fill", "click"] and script.mode == "discovery"


@needs_browser
def test_run_through_api_and_results(client, site):
    rt = client.app.state.runtime
    rt.settings.update({"playback": {"visual_delay_ms": 0, "headless": True}})
    client.put("/api/automation/credentials", json={"application": "Shop", "role": "admin", "username": "alice",
                                                    "password": "s3cret-pass"})
    body = _login_script(site).model_dump()
    r = client.post("/api/automation/scripts", json={k: body[k] for k in body if k not in ("id", "version")})
    sid = r.json()["script"]["id"]
    run_id = client.post(f"/api/automation/scripts/{sid}/run", json={}).json()["run_id"]
    for _ in range(300):
        items = client.get("/api/automation/results", params={"run_id": run_id}).json()["items"]
        if items and items[0]["status"] != "RUNNING":
            break
        time.sleep(0.1)
    assert items[0]["status"] == "PASSED"
    shot = items[0]["evidence"]["screenshots"][-1]
    assert client.get("/api/automation/evidence", params={"path": shot}).headers["content-type"] == "image/png"
    detail = client.get(f"/api/automation/scripts/{sid}").json()
    assert "page.locator" in detail["python"] and detail["versions"]


@needs_browser
def test_ai_generation_uses_only_discovered_selectors(paths, site):
    from fastapi.testclient import TestClient

    from qa_platform.credentials import MemoryBackend
    from qa_platform.main import create_app
    from tests.fake_ado import PAT, FakeAzureDevOps
    from tests.fake_llm import FakeLLM

    ado, llm = FakeAzureDevOps(), FakeLLM()
    ado.items[1280]["Microsoft.VSTS.TCM.Steps"] = (
        '<steps id="0" last="2"><step id="1" type="ValidateStep"><parameterizedString isformatted="true">'
        "Sign in as admin</parameterizedString><parameterizedString isformatted=\"true\">Welcome message shown"
        "</parameterizedString></step></steps>")
    ado.relations[1280] = [("Microsoft.VSTS.Common.TestedBy-Reverse", 118)]
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport(), ai_transport=llm.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-7b-instruct-q4_k_m.gguf"})
        auto = c.app.state.runtime.automation
        disc = auto.discovery.crawl("DISC-G1", f"{site}/login.html", max_pages=1, max_depth=0, headless=True)
        run_id = c.post("/api/automation/generate", json={"test_case_id": 1280, "base_url": site,
                                                          "discovery_id": disc["id"], "credential_role": "admin"}).json()["run_id"]
        for _ in range(200):
            status = c.get(f"/api/automation/generate/{run_id}").json()
            if status["script_id"] or status["status"] == "FAILED":
                break
            time.sleep(0.05)
        script = auto.scripts.get(status["script_id"])
        assert script.mode == "ai" and script.test_case_id == "1280" and script.user_story_id == "118"
        assert [s.action for s in script.steps] == ["goto", "fill", "fill", "click", "expect_text", "click"]
        assert script.steps[3].target.element_id == "login-btn"  # fingerprint taken from discovery
        assert "#made-up-button" in script.notes and "teleport" in script.notes
        prompt = next(call for call in llm.calls if "Playwright automation engineer" in call["messages"][0]["content"])
        assert "Sign in as admin" in prompt["messages"][1]["content"]


# ---- Python test export (pytest + Playwright) ------------------------------------------------------


def _run_exported(folder: Path, *args: str, env_extra: dict[str, str]):
    import os
    import subprocess
    import sys

    env = {**os.environ, "HEADLESS": "1", **env_extra}
    return subprocess.run([sys.executable, *args], cwd=folder, env=env, capture_output=True, text=True, timeout=180)


def test_pytest_export_is_valid_python_without_secrets(site):
    import ast

    from qa_platform.automation import pytest_export

    script = _login_script(site)
    script.name = 'TC101 Login """works""" \\ fine'
    py = pytest_export.to_pytest(script, [{"name": "Gulf region", "values": {"region": "Middle East", "vip": True,
                                                                              "note": "true story", "n": None}}])
    ast.parse(py)
    assert "s3cret" not in py and "ADMIN_PASSWORD" in py and "{{CRED:admin.password}}" in py
    assert "('Gulf region', {'region': 'Middle East', 'vip': True, 'note': 'true story', 'n': None})" in py
    assert pytest_export.file_name(script) == "test_tc101_login_works_fine.py"
    assert "# Step 2: Enter value in #username" in py


@needs_browser
def test_exported_tests_pass_with_pytest_and_plain_python(client, site, tmp_path):
    import io
    import zipfile

    body = _login_script(site).model_dump()
    body["steps"][4]["value"] = "{{DATA:region}}"
    r = client.post("/api/automation/scripts", json={k: body[k] for k in body if k not in ("id", "version")})
    sid = r.json()["script"]["id"]
    rt = client.app.state.runtime
    for name, region in (("Gulf", "Middle East"), ("Europe", "Europe")):
        rt.db.execute("INSERT INTO test_data (user_story_id, test_case_ref, tfs_test_case_id, name, kind, dataset, source_run_id,"
                      " created_at, updated_at) VALUES ('118', 'TC1', 101, ?, 'valid', ?, 'R', 'x', 'x')",
                      (name, json.dumps({"region": region})))
    single = client.get(f"/api/automation/scripts/{sid}/export")
    assert single.headers["content-disposition"].endswith('filename="test_tc101_login_works.py"')
    assert "('Gulf', {'region': 'Middle East'})" in single.text
    assert "def run(page):" in client.get(f"/api/automation/scripts/{sid}/export?format=playwright").text

    r = client.post("/api/automation/export-bundle", json={"test_case_ids": ["101"]})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
        assert {"tests/test_tc101_login_works.py", "pytest.ini", "requirements.txt", "README.txt", "RUN_TESTS.cmd"} <= names
        assert all("s3cret" not in zf.read(n).decode() for n in names)
        assert "ADMIN_PASSWORD" in zf.read("README.txt").decode()
        zf.extractall(tmp_path)
    creds = {"ADMIN_USERNAME": "alice", "ADMIN_PASSWORD": "s3cret-pass"}
    run = _run_exported(tmp_path, "-m", "pytest", "-p", "no:cacheprovider", env_extra=creds)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "2 passed" in run.stdout and "[Gulf]" in run.stdout and "[Europe]" in run.stdout
    plain = _run_exported(tmp_path / "tests", "test_tc101_login_works.py", env_extra=creds)
    assert plain.returncode == 0 and plain.stdout.count("PASSED") == 2, plain.stdout + plain.stderr
    wrong = _run_exported(tmp_path, "-m", "pytest", "-p", "no:cacheprovider", env_extra={**creds, "ADMIN_PASSWORD": "nope"})
    assert wrong.returncode == 1 and "2 failed" in wrong.stdout
    missing = _run_exported(tmp_path, "-m", "pytest", "-p", "no:cacheprovider", env_extra={"ADMIN_USERNAME": "alice"})
    assert "Set the environment variable ADMIN_PASSWORD" in missing.stdout

    assert client.post("/api/automation/export-bundle", json={"user_story_id": "999"}).status_code == 404


@needs_browser
def test_ai_generation_for_every_test_case_of_a_story(paths, site):
    from fastapi.testclient import TestClient

    from qa_platform.credentials import MemoryBackend
    from qa_platform.main import create_app
    from tests.fake_ado import PAT, FakeAzureDevOps
    from tests.fake_llm import FakeLLM

    ado, llm = FakeAzureDevOps(), FakeLLM()
    ado.items[1280]["Microsoft.VSTS.TCM.Steps"] = (
        '<steps id="0" last="2"><step id="1" type="ValidateStep"><parameterizedString isformatted="true">'
        "Sign in as admin</parameterizedString><parameterizedString isformatted=\"true\">Welcome shown"
        "</parameterizedString></step></steps>")
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport(), ai_transport=llm.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-7b-instruct-q4_k_m.gguf"})
        assert c.post("/api/automation/generate", json={"base_url": site}).status_code == 400
        auto = c.app.state.runtime.automation
        disc = auto.discovery.crawl("DISC-G2", f"{site}/login.html", max_pages=1, max_depth=0, headless=True)
        r = c.post("/api/automation/generate", json={"user_story_id": 118, "base_url": site, "discovery_id": disc["id"]}).json()
        assert r["test_case_ids"] == [1280]
        for _ in range(200):
            status = c.get(f"/api/automation/generate/{r['run_id']}").json()
            if status["script_ids"]:
                break
            time.sleep(0.05)
        assert len(status["script_ids"]) == 1
        assert auto.scripts.get(status["script_ids"][0]).test_case_id == "1280"
        ado.relations[118] = []
        c.post("/api/tfs/cache/clear")
        assert c.post("/api/automation/generate", json={"user_story_id": 118, "base_url": site}).status_code == 409


@needs_browser
def test_voice_actions_during_recording_record_a_date_rule_not_a_literal_date(auto, rt, tmp_path):
    from datetime import date, timedelta

    from tests.test_visual_playback import PAGE

    page_file = tmp_path / "booking.html"
    page_file.write_text(PAGE.replace('<select id="s">', '<label for="s">Cabin</label><select id="s">'), encoding="utf-8")
    run_id = auto.start_recording(page_file.as_uri(), name="Voice booking", headless=True)
    session = auto.recording(run_id)["session"]
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            session.command("url", timeout=2)
            break
        except Exception:
            time.sleep(0.2)
    # Spoken through the Voice Command Agent, exactly as the person would say it.
    r = rt.voice.handle("Hami, select tomorrow in travel date", conversation_id="conv-recording-test", source="typed")
    assert r["status"] == "completed" and r["command"] == "browser_action", r
    assert "Current Date + 1 day" in r["say"]
    assert "Entered" in auto.voice_action(run_id, {"action": "fill", "target": "Plain", "value": "hello"})
    assert "Selected" in auto.voice_action(run_id, {"action": "select", "target": "Cabin", "value": "Business"})
    time.sleep(0.5)
    script = auto.stop_recording(run_id)
    pick = [s for s in script.steps if s.action == "pick_date"]
    assert len(pick) == 1 and pick[0].value == "{{TODAY+1d:%Y-%m-%d}}"
    assert pick[0].description == "Pick date Current Date + 1 day in Travel date"
    literal = (date.today() + timedelta(days=1)).strftime("%d/%m/%Y")
    assert literal not in script.model_dump_json()  # no hard-coded date and no recorded calendar clicks
    assert not any(s.action == "click" for s in script.steps)
    assert any(s.action == "fill" and s.value == "hello" for s in script.steps)
    assert any(s.action == "select" and s.value == "bus" for s in script.steps)
    # Playback picks the date of the day it runs through the page's calendar.
    record = auto.run_script(script.id, headless=True, background=False)
    assert record["status"] == "PASSED", record.get("error")
