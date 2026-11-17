"""23.11.0 pages in a real browser against the running app (Chromium through Playwright, acting as the tester).

* Guided Browser: the Python card shows the toolbar; Edit Script, a syntax error is listed with its line number,
  Save keeps version 1, Version History lists it, the download is the saved version, the recorded steps are unchanged.
* Wait settings and Insert waits change the script (saved with it) without duplicates.
* Running the saved (failing) Python makes a "Needs Review" draft; Defect Review shows it with its evidence, Approve
  needs a press, and nothing is sent to TFS by itself (no TFS is configured here, so Send to TFS must refuse).

Screenshots go to $SKYGUARD_UI_SHOTS when it is set (the release evidence folder), otherwise to the test's temp folder.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pw_api = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from qa_platform.auth import COOKIE  # noqa: E402
from tests import ajax_site  # noqa: E402
from tests.auth_helper import session_token  # noqa: E402

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1]))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def ajax():
    server, url = ajax_site.start()
    yield url
    server.shutdown()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    home = tmp_path_factory.mktemp("qahome")
    port = free_port()
    (home / "settings.json").write_text(json.dumps({
        "server": {"host": "127.0.0.1", "port": port},
        "playback": {"visual_delay_ms": 0, "headless": True, "step_screenshots": False},
    }))
    env = {**os.environ, "QA_HOME": str(home), "PYTHONPATH": str(APP)}
    proc = subprocess.Popen([sys.executable, "-m", "qa_platform"], cwd=APP, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("server did not start")
    yield base
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def api(server):
    token = session_token(server)
    with httpx.Client(base_url=server, timeout=60, cookies={COOKIE: token},
                      headers={"Origin": server, "Sec-Fetch-Site": "same-origin"}) as c:
        yield c, token


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    d = Path(os.environ.get("SKYGUARD_UI_SHOTS") or tmp_path_factory.mktemp("shots"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="module")
def page(server, api):
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 1000})
        ctx.add_cookies([{"name": COOKIE, "value": api[1], "url": server + "/"}])
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        yield pg
        b.close()


def _steps(base):
    return [
        {"action": "goto", "value": base + "/?delay=1200"},
        {"action": "select", "target": {"selector": "#country", "description": "Country"}, "value": "UAE"},
        {"action": "select", "target": {"selector": "#city", "description": "City"}, "value": "Dubai", "timeout_ms": 600},
        {"action": "click", "target": {"selector": "#apply", "description": "Apply filter"}},
        {"action": "expect_text", "target": {"selector": "#orders"}, "value": "Dubai", "timeout_ms": 600},
    ]


def test_python_editor_waits_and_defect_review(server, api, page, ajax, shots):
    c, _ = api
    r = c.post("/api/automation/scripts", json={"name": "Orders by city", "mode": "guided", "base_url": ajax,
                                                "steps": _steps(ajax)})
    assert r.status_code == 201, r.text
    sid = r.json()["script"]["id"]

    page.goto(f"{server}/#/guided-automation?script={sid}")
    card = page.locator("#pyCard")
    expect(card.locator('[data-a="edit"]')).to_be_visible(timeout=15000)
    for label in ("Validate", "Run", "Regenerate", "Version History (0)", "Download"):
        expect(card.get_by_role("button", name=label, exact=label != "Download").first).to_be_visible()
    card.scroll_into_view_if_needed()
    card.screenshot(path=str(shots / "ui-11-python-card.png"))

    # Wait settings: Smart, 20 s, all steps; saved with the script
    page.click("#waitSettings")
    form = page.locator("#wpForm")
    expect(form).to_be_visible()
    form.locator('[name="enabled"]').check()
    form.locator('[name="mode"]').select_option("smart")
    form.locator('[name="timeout"]').fill("20")
    page.screenshot(path=str(shots / "ui-12-wait-settings.png"))
    form.locator('button[type="submit"]').click()
    expect(page.locator("#waitSettings")).to_contain_text("(on)", timeout=10000)
    got = c.get(f"/api/automation/scripts/{sid}").json()
    assert got["script"]["wait_policy"]["enabled"] is True and got["script"]["wait_policy"]["timeout_ms"] == 20000

    # Insert waits after dropdowns, twice: the second time adds nothing
    for n in (1, 2):
        page.click("#bulkWaits")
        bw = page.locator("#bwForm")
        expect(bw).to_be_visible()
        bw.locator('[name="where"]').select_option("dropdowns")
        if n == 1:
            page.screenshot(path=str(shots / "ui-13-insert-waits.png"))
        bw.locator('button[data-op="insert"]').click()
        expect(bw).to_be_hidden(timeout=10000)
    steps = c.get(f"/api/automation/scripts/{sid}").json()["script"]["steps"]
    assert [s["action"] for s in steps].count("wait_until") == 2, [s["action"] for s in steps]

    # Edit the Python: a syntax error is listed with its line number and Save is refused
    page.goto(f"{server}/#/guided-automation?script={sid}")
    card = page.locator("#pyCard")
    card.locator('[data-a="edit"]').click()
    ta = card.locator("#pySrc")
    expect(ta).to_be_visible()
    good = ta.input_value()
    assert "SmartWait" in good and "def test_" in good  # the same Smart Wait the runner uses is embedded
    broken = good.rstrip("\n") + "\n\n\ndef broken(:\n    pass\n"
    bad_line = broken.split("\n").index("def broken(:") + 1
    ta.fill(broken)
    card.locator('[data-a="validate"]').click()
    errs = card.locator("#pyErrors")
    expect(errs).to_contain_text(f"Line {bad_line},", timeout=10000)
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    card.screenshot(path=str(shots / "ui-14-python-syntax-error.png"))

    # Save a version that expects a city that is never shown, so the run fails
    failing = good.replace("to_contain_text(value('Dubai', data)", "to_contain_text(value('Atlantis', data)")
    assert failing != good
    ta.fill(failing)
    card.locator('[data-a="save"]').click()
    expect(card.get_by_role("button", name="Version History (1)")).to_be_visible(timeout=15000)
    card.get_by_role("button", name="Version History (1)").click()
    expect(page.get_by_role("dialog").locator("[data-restore='1']")).to_be_visible()
    page.get_by_role("dialog").locator("[data-view='1']").click()
    expect(page.locator("#pyVer")).to_contain_text("Atlantis")
    page.screenshot(path=str(shots / "ui-15-python-saved-history.png"))
    page.keyboard.press("Escape")
    assert c.get(f"/api/automation/scripts/{sid}/export").text == failing  # the download is the saved version
    after = c.get(f"/api/automation/scripts/{sid}").json()
    assert after["python_state"]["edited"] is True and len(after["script"]["steps"]) == 7  # steps untouched

    # Run the saved Python; the failure becomes a draft that needs review
    r = c.post(f"/api/automation/scripts/{sid}/python/run", json={})
    assert r.status_code in (200, 202), r.text
    draft = None
    for _ in range(240):
        items = c.get("/api/defects/drafts").json()["items"]
        draft = next((d for d in items if d.get("script_id") == sid), None)
        if draft:
            break
        time.sleep(0.5)
    assert draft, "no draft was made for the failed Python run"
    assert draft["status"] in ("draft", "needs_review") and draft["source"] == "python"
    assert not draft.get("tfs_id")

    page.goto(f"{server}/#/defect-review")
    row = page.locator(f'[data-id="{draft["id"]}"]').first
    expect(row).to_be_visible(timeout=15000)
    page.screenshot(path=str(shots / "ui-16-defect-review-list.png"))
    row.click()
    expect(page.locator("#approve")).to_be_visible(timeout=10000)
    expect(page.locator("#evidence")).to_be_visible()
    page.screenshot(path=str(shots / "ui-17-defect-review-draft.png"), full_page=True)
    page.click("#approve")
    for _ in range(40):
        if c.get(f"/api/defects/drafts/{draft['id']}").json()["draft"]["status"] == "approved":
            break
        time.sleep(0.25)
    d = c.get(f"/api/defects/drafts/{draft['id']}").json()["draft"]
    assert d["status"] == "approved" and not d.get("tfs_id")  # approval alone never publishes
    r = c.post(f"/api/defects/drafts/{draft['id']}/publish", json={})
    assert r.status_code >= 400  # no TFS connection is configured here: refused, nothing claimed
    d = c.get(f"/api/defects/drafts/{draft['id']}").json()["draft"]
    assert not d.get("tfs_id") and d["status"] in ("approved", "publication_failed")
    page.goto(f"{server}/#/defect-review?id={draft['id']}")
    page.reload()
    expect(page.locator("#send, #retry").first).to_be_visible(timeout=10000)
    page.wait_for_timeout(500)
    page.screenshot(path=str(shots / "ui-18-defect-review-after-send.png"), full_page=True)
    assert not page.errors, page.errors
