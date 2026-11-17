"""23.11.1 AI Agent page in a real browser against SkyGuard (served in-process, Chromium through Playwright acting as
the person). Test E of the repair request, through the page itself:

* the chat has dynamic suggestions from what was learned, and no fixed example list or permanent password warning;
* "navigate to transport" answers with the page and opens it (no plan, no preview);
* "Create a trip tomorrow ... for Aslam" says "Missing information: Vehicle" instead of making a preview;
* "Approve trip X": the preview shows Action / Application / Trip No / Role / Status; the confirmation checkbox stays
  ticked while the card refreshes (the reported "Tick the confirmation box first" after ticking); Confirm & Execute
  runs it and the card shows the status the application reports;
* "Approve trip Y" for a trip the application shows as Recorded: no Confirm button, the reason is shown, and the
  server refuses a confirmation sent anyway.

Screenshots go to $SKYGUARD_UI_SHOTS when it is set (the release evidence folder), otherwise to the test's temp folder.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

pw_api = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from qa_platform.auth import COOKIE  # noqa: E402
from qa_platform.config import Paths  # noqa: E402
from qa_platform.credentials import MemoryBackend  # noqa: E402
from qa_platform.main import create_app  # noqa: E402
from tests import transport_flow_site as T  # noqa: E402
from tests.auth_helper import session_token  # noqa: E402
from tests.passive_session import browse  # noqa: E402
from tests.test_ai_agent_repair_231101 import seed, transport_person, trip  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import uvicorn

    T.STATE.reset()
    site_server, site = T.start()
    paths = Paths(tmp_path_factory.mktemp("qahome")).ensure()
    app = create_app(paths, secret_backend=MemoryBackend())
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.25)
    rt = app.state.runtime
    rt.settings.update({"opsagent": {"headless": True}})
    svc = rt.opsagent
    application = svc.pilot.connect(site + "/", name="Transport", by="admin", environment="Test")["application"]
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        browse(svc.agent, b, application["id"], site, transport_person(site))
        b.close()
    svc.agent.understanding.digest(application["id"])
    svc.pilot.trust(application["id"])
    for acc in svc.agent.kb.accounts(application["id"]):
        svc.agent.kb.set_credential(acc["id"], T.USERS[acc["label"]][0], consent=True)
    yield {"base": base, "svc": svc, "app": application, "site": site}
    server.should_exit = True
    th.join(10)
    site_server.shutdown()


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    d = Path(os.environ.get("SKYGUARD_UI_SHOTS") or tmp_path_factory.mktemp("shots"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="module")
def page(world):
    token = session_token(world["base"])
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1400, "height": 1000})
        ctx.add_cookies([{"name": COOKIE, "value": token, "url": world["base"] + "/"}])
        p = ctx.new_page()
        errors: list[str] = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.goto(world["base"] + "/#/ai-agent")
        p.wait_for_selector("#chatText", timeout=20000)
        p.errors = errors  # type: ignore[attr-defined]
        yield p
        b.close()


def say(page, text: str):
    before = page.locator(".ai-msg.agent").count()
    page.fill("#chatText", text)
    page.press("#chatText", "Enter")
    expect(page.locator(".ai-msg.agent")).to_have_count(before + 1, timeout=30000)  # "Working…", then the reply
    last = page.locator(".ai-msg.agent").last
    expect(last).not_to_contain_text("Working", timeout=30000)
    return last


def test_chat_has_learned_suggestions_and_no_fixed_examples(page, world, shots):
    expect(page.locator("#suggest [data-sug]").first).to_be_visible(timeout=20000)
    texts = page.locator("#suggest [data-sug]").all_inner_texts()
    assert texts and all("trip" in t.lower() or "page" in t.lower() for t in texts), texts
    body = page.inner_text("#aiBody")
    assert "Never type passwords" not in body and "For example" not in body
    page.screenshot(path=str(shots / "231101-ai-chat-suggestions.png"))


def test_navigation_opens_the_page_without_a_plan(page, world, shots):
    runs = len(world["svc"].agent.engine.runs(app_id=world["app"]["id"]))
    msg = say(page, "navigate to transport")
    expect(msg).to_contain_text("Opening")
    assert world["site"] in msg.inner_text()
    assert "Not opened" not in msg.inner_text(), msg.inner_text()
    assert msg.locator(".ai-run").count() == 0
    assert len(world["svc"].agent.engine.runs(app_id=world["app"]["id"])) == runs
    page.screenshot(path=str(shots / "231101-ai-navigate.png"))


def test_missing_information_is_asked_not_previewed(page, shots):
    msg = say(page, "Create a trip tomorrow from Dubai to Abu Dhabi for Aslam")
    expect(msg).to_contain_text("Missing information")
    expect(msg).to_contain_text("Vehicle")
    assert msg.locator(".ai-run").count() == 0
    page.screenshot(path=str(shots / "231101-ai-missing-vehicle.png"))


def test_checkbox_stays_ticked_and_confirm_executes(page, world, shots):
    no = seed("Pending Approval")
    msg = say(page, f"Approve trip {no}")
    card = msg.locator(".ai-run")
    expect(card.locator("[data-confirm]")).to_be_visible(timeout=90000)
    preview = card.locator(".ai-preview").inner_text()
    for word in ("Action", "Approve trip", "Application", "Transport", no, "Role", "manager", "Status"):
        assert word in preview, (word, preview)
    card.locator("[data-ack]").check()
    page.wait_for_timeout(6000)  # three refreshes of the card
    assert card.locator("[data-ack]").is_checked()
    page.screenshot(path=str(shots / "231101-ai-preview-ticked.png"))
    card.locator("[data-confirm]").click()
    expect(card).to_contain_text("status Approved", timeout=120000)
    assert trip(no)["status"] == "Approved"
    page.screenshot(path=str(shots / "231101-ai-approved.png"))


def test_nothing_to_run_has_no_confirm(page, world, shots):
    no = seed("Recorded")
    msg = say(page, f"Approve trip {no}")
    card = msg.locator(".ai-run")
    expect(card.locator("[data-edit]")).to_be_visible(timeout=90000)
    assert card.locator("[data-confirm]").count() == 0
    expect(card).to_contain_text("Cannot run")
    expect(card).to_contain_text("Recorded")
    run_id = world["svc"].agent.engine.runs(app_id=world["app"]["id"], limit=1)[0]["id"]
    token = session_token(world["base"])
    r = httpx.post(f"{world['base']}/api/ops/simple/runs/{run_id}/confirm",
                   json={"preview_hash": world["svc"].agent.engine.run(run_id)["preview_hash"], "acknowledge": True,
                         "app_id": world["app"]["id"]},
                   cookies={COOKIE: token}, headers={"Origin": world["base"], "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 409 and r.json()["error_code"] == "NOTHING_TO_RUN", r.text
    assert trip(no)["status"] == "Recorded"
    page.screenshot(path=str(shots / "231101-ai-nothing-to-run.png"))
    assert not page.errors, page.errors  # type: ignore[attr-defined]
