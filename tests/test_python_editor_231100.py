"""23.11.0: editable Python test (Pytest + Playwright) of a Guided Browser script: validation with line numbers,
safety policy, versions and restore, the saved version is the one run and downloaded, regenerate warns, revert,
permissions, and a failed run becomes a Defect Review draft with the traceback, screenshot and (when no secret was
typed) the Playwright trace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import ajax_site
from tests.test_automation import HAVE_PLAYWRIGHT, _login_script

needs_browser = pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright is not installed")


@pytest.fixture(scope="module")
def ajax_url():
    server, url = ajax_site.start()
    yield url
    server.shutdown()


@pytest.fixture()
def prt(client, rt):
    rt.settings.update({"playback": {"visual_delay_ms": 0, "headless": True}})
    rt.automation.creds.save("Shop", "admin", "alice", "s3cret-pass", None)
    return client, rt


def _save(c, sid, source, note=""):
    return c.put(f"/api/automation/scripts/{sid}/python", json={"source": source, "note": note})


def test_validate_reports_line_numbers_and_blocks_unsafe_code(prt, site):
    c, rt = prt
    script = rt.automation.scripts.save(_login_script(site))
    v = c.post(f"/api/automation/scripts/{script.id}/python/validate", json={"source": "def f(:\n    pass\n"}).json()
    assert not v["ok"] and v["errors"][0]["line"] == 1 and v["errors"][0]["kind"] == "syntax"
    r = _save(c, script.id, "import os\n\nx = 1\nos.system('calc')\n")
    assert r.status_code == 422 and r.json()["error_code"] == "PYTHON_INVALID" and "Line 4" in r.json()["message"]
    r = _save(c, script.id, "import subprocess\n")
    assert r.status_code == 422 and "subprocess" in r.json()["message"]
    f = c.post(f"/api/automation/scripts/{script.id}/python/format", json={"source": "x = 1\t \n\n\n\n\ny = 2"}).json()
    assert f["source"] == "x = 1\n\n\ny = 2\n" and f["ok"]
    assert not rt.automation.python.is_edited(script.id)  # nothing was saved


@needs_browser
def test_edited_python_is_what_runs_and_downloads_with_versions(prt, site):
    c, rt = prt
    script = rt.automation.scripts.save(_login_script(site))
    sid = script.id
    got = c.get(f"/api/automation/scripts/{sid}").json()
    assert got["python_state"]["edited"] is False and "def test_login_works" in got["python"]
    original = got["python"]
    failing = original.replace("'Welcome alice'", "'Welcome nobody'")
    assert failing != original
    r = _save(c, sid, failing, "expect another name")
    assert r.status_code == 200 and r.json()["python_state"] == {**r.json()["python_state"], "edited": True, "version": 1}
    assert c.get(f"/api/automation/scripts/{sid}/export").text == failing  # the download is the saved version
    assert [s.action for s in rt.automation.scripts.get(sid).steps][-1] == "expect_text"  # steps untouched
    run = rt.automation.run_script(sid, headless=True, background=False)  # the main Run runs the saved Python
    assert run["status"] == "FAILED" and run["python_version"] == 1, run.get("output")
    log = Path(run["evidence"]["log"]).read_text()
    assert "s3cret-pass" not in log and "Welcome nobody" in log
    assert run["evidence"]["screenshots"] and not run["evidence"]["trace"]  # a password was typed: no trace
    assert rt.defects.wait_for_automatic(run["run_id"], 10)
    row = rt.db.query_one("SELECT id FROM defect_drafts WHERE source_run_id = ?", (run["run_id"],))
    d = rt.defects.get(row["id"])
    assert d["source"] == "python" and d["status"] in ("draft", "needs_review")
    assert d["facts"]["python_line"] and "Welcome nobody" in (d["facts"]["python_code"] or "") + d["facts"]["error"]
    assert d["facts"]["failed_step_description"].startswith("Verify") and "s3cret-pass" not in json.dumps(d)
    assert d["evidence"][0]["kind"] == "screenshot" and d["facts"]["trace_note"]
    # back to passing, then restore the failing version as a new version
    assert _save(c, sid, original, "fixed").json()["python_state"]["version"] == 2
    assert rt.automation.run_script(sid, headless=True, background=False)["status"] == "PASSED"
    r = c.post(f"/api/automation/scripts/{sid}/python/restore?version=1").json()
    assert r["python_state"]["version"] == 3 and r["python"] == failing
    assert [v["version"] for v in r["python_versions"]] == [3, 2, 1]
    # regenerating replaces manual edits only after confirmation
    r = c.post(f"/api/automation/scripts/{sid}/python/regenerate", json={})
    assert r.status_code == 409 and r.json()["error_code"] == "PYTHON_EDITED"
    r = c.post(f"/api/automation/scripts/{sid}/python/regenerate", json={"confirm": True}).json()
    assert r["python_state"]["edited"] is False and r["python"] == original
    # permissions: a role without the Python permission can neither save nor run
    rt.settings.update({"automation": {"python_roles": ["tester"]}})
    assert _save(c, sid, original).status_code == 403
    assert c.post(f"/api/automation/scripts/{sid}/python/run", json={}).status_code == 403


@needs_browser
def test_failed_python_without_secrets_keeps_the_playwright_trace(prt):
    c, rt = prt
    server, base = ajax_site.start()
    try:
        from qa_platform.automation.dsl import Script, Step, Target

        script = rt.automation.scripts.save(Script(id="SCR-PYTRACE001", name="Orders", base_url=base, steps=[
            Step(action="goto", value=base + "/?delay=200"),
            Step(action="expect_text", target=Target(selector="#status"), value="ready")]))
        src = c.get(f"/api/automation/scripts/{script.id}").json()["python"].replace("'ready'", "'gone'")
        src = src.replace("timeout=10000", "timeout=1000")
        assert _save(c, script.id, src).status_code == 200
        run = rt.automation.python.run(script.id, headless=True, background=False)
        assert run["status"] == "FAILED" and run["evidence"]["trace"] and Path(run["evidence"]["trace"]).is_file()
    finally:
        server.shutdown()


@needs_browser
def test_data_driven_run_of_edited_python_runs_every_row(prt, ajax_url):
    """Run data rows on an edited script: the saved Python runs each Excel / CSV row as its own test."""
    from qa_platform.automation.dsl import DataTable, Script, Step, Target

    c, rt = prt
    steps = [Step(action="goto", value=ajax_url + "/?delay=200"),
             Step(action="select", target=Target(selector="#country"), value="UAE"),
             Step(action="wait_until", wait={"kind": "ajax", "timeout_ms": 15000}),
             Step(action="select", target=Target(selector="#city"), value="{{DATA:city}}"),
             Step(action="click", target=Target(selector="#apply")),
             Step(action="wait_until", wait={"kind": "ajax", "timeout_ms": 15000}),
             Step(action="expect_text", target=Target(selector="#orders"), value="{{DATA:city}}", timeout_ms=3000)]
    script = rt.automation.scripts.save(Script(id="SCR-PYDATA0001", name="Orders per city", base_url=ajax_url,
                                               steps=steps, data_table=DataTable(columns=["city"], rows=[
                                                   {"city": "Dubai"}, {"city": "Atlantis"}])))
    src = c.get(f"/api/automation/scripts/{script.id}").json()["python"]
    assert _save(c, script.id, src + "\n# reviewed\n", "reviewed").status_code == 200
    batch = rt.automation.run_data_rows(script.id, [0])
    assert batch["rows"][0]["row"] is None and "edited Python" in batch["rows"][0]["label"] and batch["note"]
    run_id = batch["rows"][0]["run_id"]
    import time as _t
    for _ in range(240):
        row = rt.db.query_one("SELECT status, steps FROM execution_results WHERE run_id = ?", (run_id,))
        if row and row["status"] not in ("RUNNING",):
            break
        _t.sleep(0.5)
    assert row["status"] == "FAILED"  # Atlantis is never shown
    cases = json.loads(row["steps"])
    assert [c_["status"] for c_ in cases] == ["PASSED", "FAILED"], cases
