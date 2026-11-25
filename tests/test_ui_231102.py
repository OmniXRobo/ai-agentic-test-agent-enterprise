"""23.11.2 AI Agent page in a real browser against SkyGuard (served in-process, Chromium through Playwright acting as
the person): the reported Swag Labs conversation on the stand-in shop, typed into the chat box.

"checkout an order" -> "I still need: First Name, Last Name, Zip/Postal Code"; "First Name; muhammad" -> received,
two fields left; "pre pare it" -> the same two fields only; "last name Ali" -> Zip/Postal Code left; "54000" -> the
preview with the three values; Confirm & Execute -> the order is placed in the shop.

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
from tests import shop_site  # noqa: E402
from tests.auth_helper import session_token  # noqa: E402
from tests.passive_session import browse  # noqa: E402
from tests.test_ai_agent_repair_231101 import shop_person  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import uvicorn

    shop_site.STATE.reset()
    site_server, site = shop_site.start()
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
    # 23.15.0: these tests cover the follow-up questions asked when no value is known. With "reuse_values" on
    # (the default since 23.15.0) the values typed while teaching fill them instead, so it is off here;
    # tests/test_ai_agent_memory_231500.py covers the filled case.
    rt.settings.update({"opsagent": {"headless": True, "reuse_values": False}})
    svc = rt.opsagent
    svc.pilot.reuse_values = False
    application = svc.pilot.connect(site + "/", name="Online Store", by="admin", environment="Test")["application"]
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        browse(svc.agent, b, application["id"], site, shop_person(site))
        b.close()
    svc.agent.understanding.digest(application["id"])
    svc.pilot.trust(application["id"])
    for acc in svc.agent.kb.accounts(application["id"]):  # the shopper's saved password (secure form)
        if acc["login_id"] == "shopper":
            svc.agent.kb.set_credential(acc["id"], shop_site.USERS["shopper"], consent=True)
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
    expect(page.locator(".ai-msg.agent")).to_have_count(before + 1, timeout=30000)
    last = page.locator(".ai-msg.agent").last
    expect(last).not_to_contain_text("Working", timeout=30000)
    return last


def test_the_reported_conversation_in_the_page(page, world, shots):
    msg = say(page, "checkout an order")
    for f in ("First Name", "Last Name", "Zip/Postal Code"):
        expect(msg).to_contain_text(f)

    msg = say(page, "First Name; muhammad")                       # the reported answer
    expect(msg).to_contain_text("Got it: First Name = muhammad.")
    text = msg.inner_text()
    assert "Last Name" in text and "Zip/Postal Code" in text, text
    assert text.count("First Name") == 1, text                      # not asked again
    page.screenshot(path=str(shots / "231102-ai-first-name-received.png"))

    msg = say(page, "pre pare it")                                  # no value: only what is still missing
    text = msg.inner_text()
    assert "Last Name" in text and "Zip/Postal Code" in text and "First Name" not in text, text

    msg = say(page, "last name Ali")
    expect(msg).to_contain_text("Got it")
    text = msg.inner_text()
    assert "Zip/Postal Code" in text and "First Name" not in text, text

    before = len(shop_site.STATE.orders)
    msg = say(page, "54000")                                        # one field asked: the value alone
    card = msg.locator(".ai-run")
    expect(card.locator("[data-confirm]")).to_be_visible(timeout=90000)
    preview = card.locator(".ai-preview").inner_text()
    for word in ("muhammad", "Ali", "54000", "Online Store"):
        assert word in preview, (word, preview)
    page.screenshot(path=str(shots / "231102-ai-preview-values.png"))
    if card.locator("[data-ack]").count():
        card.locator("[data-ack]").check()
    card.locator("[data-confirm]").click()
    expect(card).to_contain_text("done", timeout=120000)
    assert len(shop_site.STATE.orders) == before + 1
    page.screenshot(path=str(shots / "231102-ai-order-done.png"))
    assert not page.errors, page.errors  # type: ignore[attr-defined]
