"""Extension and companion 1.3.0 (SkyGuard 23.11.1), test G of the AI Agent repair request.

Static: versions, no fixed example prompts, no permanent password warning, the new messages and routes.
Companion: dynamic suggestions (view-only accounts get no operations), Stop & save proves retrieval, the short preview.
End to end in Chromium with the extension loaded: Start learning (visible status), the person browses the store, the
companion is stopped and started again in the middle (the extension keeps the actions and sends them when it is back),
Stop & save, and the learned task is retrieved; "navigate to online store" opens the store.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser-extension" / "companion"))

from skyguard_agent.vault import MemoryBackend, Vault  # noqa: E402
from skyguard_companion.config import Paths  # noqa: E402
from skyguard_companion.server import Companion, serve  # noqa: E402
from tests import shop_site  # noqa: E402
from tests.test_companion_security import Off, call, comp, free_port, paired  # noqa: E402,F401
from tests.test_extension_e2e import extension_copy, options_sign_in, wait_text  # noqa: E402

EXT = ROOT / "browser-extension" / "extension"


def test_versions_are_130():
    from skyguard_agent import __version__ as core
    from skyguard_companion import VERSION
    # 1.3.1 (SkyGuard 23.11.2) keeps every 1.3.0 feature and adds the missing-information follow-up
    # 1.4.0 (23.12.0): installs without pairing; 1.4.1 (23.13.0): Arabic commands, public sites, changing addresses; 1.4.2 (23.13.2): spoken replies
    assert VERSION == "1.4.2" and core == "1.4.2"
    assert json.loads((EXT / "manifest.json").read_text())["version"] == "1.4.2"


def test_side_panel_has_no_fixed_examples_or_permanent_warning():
    js = (EXT / "sidepanel.js").read_text()
    assert "create 3 trips on 2026-12-01" not in js and "Never type passwords or codes here" not in js
    assert "Say 'help' for examples" not in js
    for needle in ("/v1/simple/suggestions", "/v1/simple/learn/", "/v1/simple/runs/", "learning-start",
                   "learning-stop", "open-url", "Provide missing data", "Confirm &amp; Execute", "S.ack", "app_id"):
        assert needle in js, needle
    bg = (EXT / "background.js").read_text()
    for needle in ("duplicateEvent", "MAX_QUEUED", "retryMs", "learningBadge", "\"REC\"", "case \"open-url\"",
                   "appOrigins()).has(origin)"):
        assert needle in bg, needle


def test_least_privilege_is_unchanged():
    m = json.loads((EXT / "manifest.json").read_text())
    assert sorted(m["permissions"]) == ["notifications", "scripting", "sidePanel", "storage", "tabs"]
    assert "host_permissions" not in m and m["optional_host_permissions"] == ["http://*/*", "https://*/*"]


def test_companion_suggestions_save_and_preview(comp):  # noqa: F811
    c, base, _ = comp
    auth, dev = paired(comp)
    shop_site.STATE.reset()
    server, site = shop_site.start()
    try:
        app = c.pilot.connect(site + "/", name="Online Store", by="admin1")["application"]
        aid = app["id"]
        st, s = call(base, "GET", f"/v1/simple/suggestions?app_id={aid}", headers=auth)
        assert st == 200 and s["learned"] is False and s["suggestions"] == []
        # learning arrives as the extension sends it: the person's events and page snapshots
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            from tests.passive_session import browse
            from tests.test_ai_agent_repair_231101 import shop_person
            browse(c.agent, b, aid, site, shop_person(site))
            b.close()
        st, saved = call(base, "POST", f"/v1/simple/learn/{aid}/save", {}, headers=auth)
        assert st == 200 and saved["verified"]["ok"], saved
        assert [t["name"] for t in saved["verified"]["checks"] if t["kind"] == "task"] == ["Checkout order"]
        st, s = call(base, "GET", f"/v1/simple/suggestions?app_id={aid}&page={site}/inventory", headers=auth)
        assert st == 200 and s["learned"] and s["suggestions"], s
        assert not any("password" in x["text"].lower() for x in s["suggestions"])
        # a page of another site is ignored
        assert call(base, "GET", f"/v1/simple/suggestions?app_id={aid}&page=http://evil.local/x",
                    headers=auth)[0] == 200
        st, r = call(base, "POST", "/v1/chat", {"text": "navigate to online store", "app_id": aid}, headers=auth)
        assert st == 200 and r["reply"]["type"] == "navigate" and r["reply"]["url"] == site + "/", r
        # viewers: suggestions without operations, and no saving
        call(base, "POST", "/v1/users", {"username": "viewer4", "password": "Viewer-pass-4", "role": "viewer"},
             headers=auth)
        tok = call(base, "POST", "/v1/auth/login", {"username": "viewer4", "password": "Viewer-pass-4"},
                   headers=dev)[1]
        viewer = {**dev, "Authorization": "Bearer " + tok["token"]}
        st, vs = call(base, "GET", f"/v1/simple/suggestions?app_id={aid}", headers=viewer)
        assert st == 200
        assert not any(x["text"].lower().startswith(("checkout", "create", "approve")) for x in vs["suggestions"]), vs
        assert call(base, "POST", f"/v1/simple/learn/{aid}/save", {}, headers=viewer)[0] == 403
    finally:
        server.shutdown()


# ---- G: the extension end to end ----------------------------------------------------------------------------------

@pytest.fixture()
def shopworld(tmp_path):
    shop_site.STATE.reset()
    server, site = shop_site.start()
    site = site.replace("127.0.0.1", "localhost")
    ext = extension_copy(tmp_path)
    port = free_port()
    paths = Paths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text(json.dumps({"port": port}))
    comp_ = Companion(paths, vault=Vault(MemoryBackend()), llm=Off(), whisper=Off())
    holder = {"httpd": serve(comp_)}
    threading.Thread(target=holder["httpd"].serve_forever, daemon=True).start()

    def restart_companion(down):
        if down:
            holder["httpd"].shutdown()
            holder["httpd"].server_close()
        else:
            holder["httpd"] = serve(comp_)
            threading.Thread(target=holder["httpd"].serve_forever, daemon=True).start()

    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="chromium", headless=True,
                                                        args=[f"--disable-extensions-except={ext}",
                                                              f"--load-extension={ext}"])
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium with extensions is not available: {exc}")
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        base = sw.url.rsplit("/", 1)[0]
        yield {"ctx": ctx, "sw": sw, "base": base, "comp": comp_, "site": site, "port": port,
               "companion": restart_companion}
        ctx.close()
    holder["httpd"].shutdown()
    holder["httpd"].server_close()
    comp_.close()
    server.shutdown()


def test_g_start_learning_browse_disconnect_stop_save_and_use(shopworld):
    w = shopworld
    options_sign_in(w)
    c = w["comp"]
    app = c.pilot.connect(w["site"] + "/", name="Online Store", by="admin1")["application"]
    aid = app["id"]
    shop = w["ctx"].new_page()
    shop.goto(w["site"] + "/")
    panel = w["ctx"].new_page()
    errors: list[str] = []
    panel.on("pageerror", lambda e: errors.append(str(e)))
    panel.goto(w["base"] + "/sidepanel.html")
    panel.wait_for_selector("#say")
    tab_id = w["sw"].evaluate("u => chrome.tabs.query({}).then(ts => ts.find(t => t.url.startsWith(u)).id)", w["site"])
    # Start learning (in the product the button asks for the site permission in its click; here it is pre-granted)
    started = panel.evaluate("([a, t]) => chrome.runtime.sendMessage({ type: 'learning-start', appId: a, tabId: t })",
                             [aid, tab_id])
    assert started["ok"], started
    panel.click("#tabs button[data-tab=learn]")
    panel.wait_for_selector("#lwStop", timeout=15000)
    wait_text(panel, "#main", "Learning since")
    assert w["sw"].evaluate("() => chrome.action.getBadgeText({})") == "REC"

    p = shop
    p.bring_to_front()
    step = lambda: p.wait_for_timeout(700)  # noqa: E731 - a person's pace
    p.fill("#user-name", "shopper"); step()
    p.fill("#password", shop_site.USERS["shopper"]); step()
    p.click("#login-button"); step()
    p.get_by_role("button", name="Add to cart").first.click(); step()
    # the companion is unreachable for a while (the extension is pointed at a port nobody listens on; stopping the
    # in-process server is not enough because the browser keeps its open connections): the extension keeps what the
    # person does and retries
    w["sw"].evaluate("p => chrome.storage.local.set({ port: p })", free_port())
    p.get_by_role("link", name="Cart (1)").click(); step()
    p.click("#checkout"); step()
    p.fill("#first-name", "Aslam"); step()
    p.fill("#last-name", "Khan"); step()
    p.fill("#postal-code", "123"); step()
    p.wait_for_timeout(2500)
    st = panel.evaluate("a => chrome.runtime.sendMessage({ type: 'learning-status', appId: a })", aid)
    assert st["learning"] and st["offline"] and st["queued"] > 0, st
    w["sw"].evaluate("p => chrome.storage.local.set({ port: p })", w["port"])
    p.click("#continue"); step()
    p.get_by_role("button", name="Finish").click()
    p.wait_for_timeout(1500)
    end = 0
    while end < 40:  # the retry backoff brings the queued actions in
        st = panel.evaluate("a => chrome.runtime.sendMessage({ type: 'learning-status', appId: a })", aid)
        if not st["offline"] and st["queued"] == 0:
            break
        p.wait_for_timeout(1000)
        end += 1
    assert not st["offline"] and st["queued"] == 0, st
    kinds = [e.get("kind") for e in c.agent.journal.entries(aid) if e.get("_t") == "event"]
    assert kinds.count("submit") + kinds.count("click") >= 4, kinds
    journal = json.dumps(c.agent.journal.entries(aid))
    assert shop_site.USERS["shopper"] not in journal  # the password was never sent

    panel.bring_to_front()
    panel.click("#tabs button[data-tab=learn]")
    panel.wait_for_selector("#lwStop")
    panel.click("#lwStop")
    wait_text(panel, "#main", "Learning saved and tested", timeout=60)
    assert w["sw"].evaluate("() => chrome.action.getBadgeText({})") == ""
    titles = [f["title"] for f in c.agent.understanding.model(aid)["flows"]]
    # the checkout (three fields, Continue, Finish) is learned whole, with nothing named after its Cancel button;
    # "Add to cart" may be learned too, as the person did it
    assert "Checkout order" in titles and not any("cancel" in t.lower() for t in titles), titles

    # retrieve: suggestions and a navigation command from the chat
    panel.click("#tabs button[data-tab=chat]")
    panel.select_option("#appSel", aid)
    try:
        panel.wait_for_selector("#suggest .chip", timeout=20000)
    except Exception:
        raise AssertionError(panel.inner_text("#main") + str(errors) + str(c.pilot.suggestions(aid)))
    panel.fill("#say", "navigate to online store")
    panel.click("#sendBtn")
    wait_text(panel, "#chatLog", "Opened in the browser")
    assert not errors, errors
