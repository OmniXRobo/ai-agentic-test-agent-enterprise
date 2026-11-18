"""The Chrome/Edge extension end to end, loaded unpacked into Playwright's Chromium (new headless), with the companion
running in-process and the stand-in transport application.

Pairing, first admin, sign-in, the separate credential page (with consent), the chat's own secret check, a request
that becomes a preview in the side panel, confirmation, execution by the extension's service worker in a normal tab,
and the verified result. Real Chrome / Edge builds and Windows were not available here; see the test report.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser-extension" / "companion"))

from skyguard_agent.vault import MemoryBackend, Vault  # noqa: E402
from skyguard_companion.config import Paths  # noqa: E402
from skyguard_companion.server import Companion, serve  # noqa: E402
from tests import transport_site  # noqa: E402
from tests.ops_agent_fixtures import day, setup_fleetops  # noqa: E402
from tests.test_companion_security import Off, free_port  # noqa: E402

EXT = ROOT / "browser-extension" / "extension"


def extension_copy(tmp: Path) -> Path:
    """The extension as shipped, plus a host permission for the test site (granted at install instead of through the
    permission prompt, which a headless browser cannot answer). Same key, so the same extension ID."""
    import shutil

    dest = tmp / "extension"
    shutil.copytree(EXT, dest)
    manifest = json.loads((dest / "manifest.json").read_text())
    manifest["host_permissions"] = ["http://localhost/*"]
    (dest / "manifest.json").write_text(json.dumps(manifest))
    return dest


@pytest.fixture()
def world(tmp_path):
    transport_site.STATE.reset()
    server, site = transport_site.start()
    site = site.replace("127.0.0.1", "localhost")  # the companion stays on 127.0.0.1 (no host permission: CORS)
    ext = extension_copy(tmp_path)
    port = free_port()
    paths = Paths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text(json.dumps({"port": port}))
    comp = Companion(paths, vault=Vault(MemoryBackend()), llm=Off(), whisper=Off())
    httpd = serve(comp)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="chromium", headless=True,
                                                        args=[f"--disable-extensions-except={ext}",
                                                              f"--load-extension={ext}"])
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium with extensions is not available: {exc}")
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        base = sw.url.rsplit("/", 1)[0]
        yield {"ctx": ctx, "sw": sw, "base": base, "comp": comp, "site": site, "port": port}
        ctx.close()
    httpd.shutdown()
    httpd.server_close()
    comp.close()
    server.shutdown()


def wait_text(page, selector, text, timeout=30.0):
    """Polls an element's text (the extension's CSP forbids Playwright's evaluated wait functions)."""
    import time

    end = time.time() + timeout
    last = ""
    while time.time() < end:
        try:
            last = page.inner_text(selector, timeout=1000)
            if text.lower() in last.lower():
                return last
        except Exception:  # noqa: BLE001 - re-rendered while reading
            pass
        page.wait_for_timeout(300)
    raise AssertionError(f"{selector!r} never showed {text!r}; last: {last[:500]!r}")


def options_sign_in(w):
    p = w["ctx"].new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.goto(w["base"] + "/options.html")
    # Let the page finish its own first check (old port; ~2 s to fail on Windows) before changing the port, as a
    # person would; options.js also keeps a typed port and ignores a stale check (the CI race seen on Windows).
    # Locator assertions, not wait_for_function: the extension page's CSP forbids evaluating strings.
    expect(p.locator("#conn")).not_to_have_text(re.compile(r"^\s*(checking)?\s*$"), timeout=15000)
    p.fill("#port", str(w["port"]))
    expect(p.locator("#port")).to_have_value(str(w["port"]), timeout=5000)
    p.click("#savePort")
    try:
        p.wait_for_selector("#pairCard:not(.hidden)", timeout=10000)
    except Exception:
        raise AssertionError(p.inner_text("#connErr") + " / " + p.inner_text("#conn") + str(errors))
    code = w["comp"].pairing.new_code()
    p.fill("#code", code)
    p.click("#pairBtn")
    p.wait_for_selector("#setupCard:not(.hidden)", timeout=10000)
    p.fill("#suUser", "admin1")
    p.fill("#suPass", "Admin-pass-2026")
    p.click("#setupBtn")
    wait_text(p, "#conn", "admin1")
    assert not errors, errors
    return p


def test_pair_sign_in_credential_page_and_operation(world):
    w = world
    assert w["base"].endswith("fmiiaecpdgbhhhbbkkfhnjfkbmllladb")  # the fixed ID the companion trusts
    options = options_sign_in(w)
    assert "admin1 (admin)" in options.inner_text("#conn")
    agent = w["comp"].agent
    setup = setup_fleetops(agent, w["site"])
    acc = setup["accounts"]["operator"]
    agent.kb.revoke_credential(acc["id"])  # entered again through the extension's credential page below

    cred = w["ctx"].new_page()
    cred.goto(w["base"] + f"/credential.html?account={acc['id']}")
    wait_text(cred, "#who", "operator")
    cred.fill("#pw", transport_site.USERS["operator"][0])
    cred.fill("#pw2", transport_site.USERS["operator"][0])
    cred.click("button[type=submit]")  # consent not ticked: refused by the page (and by the companion)
    assert not agent.kb.account(acc["id"])["has_credential"]
    cred.check("#consent")
    cred.click("button[type=submit]")
    wait_text(cred, "#who", "saved")
    assert agent.kb.account(acc["id"])["has_credential"]

    panel = w["ctx"].new_page()
    errors = []
    panel.on("pageerror", lambda e: errors.append(str(e)))
    panel.goto(w["base"] + "/sidepanel.html")
    panel.wait_for_selector("#say")
    # a password typed into the chat never leaves the page
    panel.fill("#say", "log in as operator, password is Op#2026x")
    panel.click("#sendBtn")
    panel.wait_for_selector(".msg.warn")
    assert "not sent" in panel.inner_text("#chatLog")
    assert agent.store.query("SELECT COUNT(*) AS n FROM chat")[0]["n"] == 0

    panel.fill("#say", f"create 2 trips on {day()} from 07:00 every 240 minutes, route: Dubai to Sharjah, bus: BUS-101, "
                       "driver: Dina Yousef, trip numbers starting at EXT100 as Entry Operator (test)")
    panel.click("#sendBtn")
    panel.wait_for_selector("#confirmBtn", timeout=60000)  # inspected by the extension, preview shown
    assert transport_site.STATE.posts == 0
    assert "EXT100" in panel.inner_text("#runBody") and "EXT101" in panel.inner_text("#runBody")
    panel.click("#confirmBtn")
    wait_text(panel, "#runBody", "completed", timeout=90)
    assert [t["trip_no"] for t in transport_site.STATE.trips] == ["EXT100", "EXT101"]
    assert transport_site.STATE.posts == 2
    run = agent.engine.runs(limit=1)[0]
    assert run["status"] == "completed" and run["interface"] == "extension"
    items = agent.engine.items(run["id"])
    assert all(i["status"] == "confirmed_success" for i in items)
    dump = json.dumps([agent.engine.events(run["id"]), agent.audit.list(2000)])
    assert transport_site.USERS["operator"][0] not in dump
    assert not errors, errors


def test_record_for_integration_comparison_in_the_extension(world, client):
    """1.2.0: the person records the way to a report in their own browser tab; the side panel saves the converted
    recording as a file, and SkyGuard's Integration Comparison imports it."""
    from tests import report_site

    w = world
    server, rsite = report_site.start()
    rsite = rsite.replace("127.0.0.1", "localhost")
    try:
        options_sign_in(w)
        app_page = w["ctx"].new_page()
        app_page.goto(rsite + "/login")
        panel = w["ctx"].new_page()
        errors = []
        panel.on("pageerror", lambda e: errors.append(str(e)))
        panel.goto(w["base"] + "/sidepanel.html")
        panel.wait_for_selector("#say")
        tab_id = w["sw"].evaluate("u => chrome.tabs.query({}).then(ts => ts.find(t => t.url.startsWith(u)).id)", rsite)
        # in the product the side panel records the active tab; here the panel is a tab itself, so name the tab
        assert panel.evaluate("id => chrome.runtime.sendMessage({ type: 'cmp-record-start', tabId: id })",
                              tab_id)["ok"]
        panel.click("#tabs button[data-tab=learn]")
        panel.wait_for_selector("#cmpStop")
        p = app_page
        p.wait_for_timeout(500)
        p.fill("input[name=user]", report_site.USER)
        p.fill("input[name=pw]", report_site.PASSWORD)
        p.click("button:has-text('Sign in')")
        p.wait_for_url(rsite + "/")
        p.wait_for_timeout(500)
        p.click("ul >> text=Reports")
        p.wait_for_url("**/reports")
        p.wait_for_timeout(500)
        p.click("text=Orders Summary")
        p.wait_for_url("**/reports/orders")
        p.wait_for_timeout(500)
        p.select_option("#rg", label="North")
        p.click("text=Search")
        p.wait_for_url("**region=North**")
        p.wait_for_timeout(500)
        p.click(".pagination >> text=2")
        p.wait_for_url("**page=2")
        p.wait_for_timeout(500)
        p.click("text=SO-1020")
        p.wait_for_url("**/orders/SO-1020")
        p.wait_for_timeout(800)
        panel.bring_to_front()
        with panel.expect_download() as dl:
            panel.click("#cmpStop")
        path = dl.value.path()
        rec = json.loads(Path(path).read_text())
        assert dl.value.suggested_filename == "skyguard-navigation-localhost.json"
        assert [s.get("text") or s.get("label") for s in rec["steps"]] == ["Reports", "Orders Summary", "Region",
                                                                            "Search"], rec["steps"]
        assert rec["filters"][0]["label"] == "Region" and rec["filters"][0]["value"] == "North"
        assert rec["pagination_seen"] and rec["detail"]["url_template"] == rsite + "/orders/{key}"
        assert report_site.PASSWORD not in json.dumps(rec) and report_site.USER not in json.dumps(rec["steps"])
        wait_text(panel, "#main", "Recording saved")
        assert not errors, errors
        W = "/api/comparison/workspace"
        pid = client.post(W, json={"name": "Orders", "applications": ["Sales reports", "ERP"]}).json()
        aid = pid["applications"][0]["id"]
        client.patch(f"{W}/applications/{aid}", json={"url": rsite + "/", "auth_method": "none"})
        r = client.post(f"{W}/applications/{aid}/guided/import", json={"recording": rec})
        assert r.status_code == 200, r.text
        assert r.json()["steps_text"] == "click Reports\nclick Orders Summary\nselect Region = North\nclick Search"
        assert report_site.DELETES == []
    finally:
        server.shutdown()
