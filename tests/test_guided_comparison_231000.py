"""23.10.0 Integration Comparison: Guided Browser Automation instead of typed navigation steps.

A tester (Playwright acting as the person, in the live session's own browser) signs in, opens Reports > Orders
Summary, chooses Region North, presses Search, goes to page 2 and opens one order. Stop & Save must give: the
navigation steps without the sign-in, the Region filter kept apart, pagination noticed but not replayed, the detail
page as /orders/{key}. Replay & Verify must read all 40 North orders (15 + 15 + 10) and the item lines of every one
of them, each with its parent Order No. Nothing in the recording may change data.
"""

from __future__ import annotations

import time

import pytest

from qa_platform.comparison.guided_nav import events_to_recording, parse_steps_text, steps_text
from qa_platform.errors import QAError
from tests import report_site

W = "/api/comparison/workspace"


def ev(kind, url, **el):
    value = el.pop("value", None)
    secret = el.pop("secret", False)
    return {"kind": kind, "url": url, "value": value, "secret": secret, "element": {"secret": secret, **el}}


def test_conversion_drops_sign_in_pagination_and_changes():
    b = "http://rep.local"
    events = [
        ev("fill", f"{b}/login", tag="input", label="User", value="analyst"),
        ev("fill", f"{b}/login", tag="input", label="Password", value="[secret]", secret=True),
        ev("click", f"{b}/login", tag="button", name="Sign in"),
        ev("click", f"{b}/", tag="a", name="Reports", href="/reports"),
        ev("click", f"{b}/reports", tag="a", name="Orders Summary", href="/reports/orders"),
        ev("select", f"{b}/reports/orders", tag="select", label="Region", id="rg", value="North"),
        ev("click", f"{b}/reports/orders", tag="button", name="Search"),
        ev("click", f"{b}/reports/orders?region=North&page=1", tag="a", name="2"),
        ev("click", f"{b}/reports/orders?region=North&page=2", tag="a", name="Next"),
        ev("click", f"{b}/reports/orders?region=North&page=3", tag="button", name="Delete selected"),
        ev("click", f"{b}/reports/orders?region=North&page=3", tag="a", name="SO-1031", href="/orders/SO-1031",
           table={"cells": ["SO-1031", "Customer 31", "North", "123"], "headers": ["Order No", "Customer", "Region",
                                                                               "Total"]}),
        {"kind": "navigate", "url": f"{b}/orders/SO-1031", "element": {}},
    ]
    rec = events_to_recording(events, start_url=f"{b}/")
    assert rec["login_url"] == f"{b}/login"
    assert [s.get("text") or s.get("label") for s in rec["steps"]] == ["Reports", "Orders Summary", "Region", "Search"]
    assert rec["steps"][2] == {"action": "select", "selector": "#rg", "label": "Region", "value": "North",
                               "filter": "Region"}
    assert rec["filters"] == [{"label": "Region", "control": "select", "value": "North", "type": "select"}]
    assert rec["pagination_seen"] is True
    assert rec["detail"]["url_template"] == f"{b}/orders/{{key}}" and rec["detail"]["key_column"] == "Order No"
    assert any("read-only" in d["why"] for d in rec["dropped"])
    assert "analyst" not in str(rec["steps"]) and "[secret]" not in str(rec)
    text = steps_text(rec["steps"])
    assert text == "click Reports\nclick Orders Summary\nselect Region = North\nclick Search"
    again = parse_steps_text(text.replace("North", "South"), rec["steps"])
    assert again[2]["value"] == "South" and again[0] == rec["steps"][0]  # unchanged lines keep their selectors
    with pytest.raises(QAError):
        parse_steps_text("click Delete selected")


@pytest.fixture()
def site():
    server, url = report_site.start()
    report_site.DELETES.clear()
    yield url
    server.shutdown()


def wait(client, jobs, timeout=180):
    out = []
    for j in jobs:
        for _ in range(int(timeout / 0.25)):
            st = client.get(f"/api/comparison/jobs/{j['job']}").json()
            if st["status"] != "running":
                break
            time.sleep(0.25)
        assert st["status"] == "done", st
        out.append(st)
    return out


def test_record_replay_master_detail(client, rt, site):
    pytest.importorskip("playwright")
    pid = client.post(W, json={"name": "Orders", "applications": ["Sales reports", "ERP"]}).json()
    aid = pid["applications"][0]["id"]
    client.patch(f"{W}/applications/{aid}", json={"url": site + "/", "auth_method": "none"})
    ws = rt.comparison.workspace if hasattr(rt.comparison, "workspace") else None
    from qa_platform.api.workspace import _ws
    ws = _ws(rt)
    # in CI there is no screen: the same session, headless (the product opens it visible and maximized)
    real = ws._session
    ws._session = lambda a, visible=None, start=True: real(a, False, start)
    try:
        r = client.post(f"{W}/applications/{aid}/guided/start").json()
        assert r["ok"] and r["recording"], r
        s = ws._sessions()[aid]

        def person(ex):
            pg = ex.page
            pg.goto(site + "/login")
            pg.fill("input[name=user]", report_site.USER)
            pg.fill("input[name=pw]", report_site.PASSWORD)
            pg.click("button:has-text('Sign in')")
            pg.wait_for_url(site + "/")
            pg.click("ul >> text=Reports")
            pg.click("text=Orders Summary")
            pg.select_option("#rg", label="North")
            pg.click("text=Search")
            pg.wait_for_load_state()
            pg.click(".pagination >> text=2")
            pg.wait_for_load_state()
            pg.click("text=SO-1020")
            pg.wait_for_url("**/orders/SO-1020")
            pg.go_back()
            pg.wait_for_load_state()
            return pg.url

        s.call(person, timeout=120)
        st = client.get(f"{W}/applications/{aid}/guided").json()
        assert st["recording"] and st["live"]["events"] >= 6, st
        saved = client.post(f"{W}/applications/{aid}/guided/stop").json()
        assert saved["ok"], saved
    finally:
        ws._session = real
    rec = saved["saved"]
    assert [x.get("text") or x.get("label") for x in rec["steps"]] == ["Reports", "Orders Summary", "Region", "Search"]
    assert rec["filters"][0]["label"] == "Region" and rec["filters"][0]["value"] == "North"
    assert rec["pagination_seen"] and rec["detail"]["key_column"] == "Order No"
    assert rec["detail"]["url_template"] == site + "/orders/{key}"
    assert report_site.PASSWORD not in str(saved) and saved.get("auth_method") == "manual"
    assert rec["tables"] and rec["tables"][0]["headers"][:2] == ["Order No", "Customer"]
    # replay: sign-in was by hand while recording; for an unattended replay the login is saved (Credential Manager)
    client.patch(f"{W}/applications/{aid}", json={"auth_method": "form", "username": report_site.USER,
                                                   "password": report_site.PASSWORD,
                                                   "url": site + "/login"})
    jobs = client.post(f"{W}/applications/{aid}/guided/replay").json()["jobs"]
    st = wait(client, jobs, timeout=300)[0]
    ds = client.get(f"/api/comparison/datasets/{st['dataset_id']}").json()
    d = ds.get("dataset", ds)
    stats = d["stats"]
    assert stats["records"] == 40 and stats["pages_visited"] == 3 and stats["complete"], stats
    detail = client.get(f"/api/comparison/datasets/{stats['detail_dataset']}?records=1").json()
    dd = detail.get("dataset", detail)
    assert dd["stats"]["parents"] == 40 and dd["stats"]["parents_read"] == 40 and dd["stats"]["complete"]
    want = sum(len(o["items"]) for o in report_site.ORDERS if o["region"] == "North")
    assert dd["stats"]["records"] == want
    assert report_site.DELETES == []  # nothing changed in the application
    # change the filter value without recording again
    up = client.put(f"{W}/applications/{aid}/guided", json={"filter_values": {"Region": "South"},
                                                              "detail_enabled": False}).json()
    assert "select Region = South" in up["steps_text"]
    jobs = client.post(f"{W}/applications/{aid}/guided/replay").json()["jobs"]
    st = wait(client, jobs, timeout=300)[0]
    d = client.get(f"/api/comparison/datasets/{st['dataset_id']}").json()
    stats = d.get("dataset", d)["stats"]
    assert stats["records"] == 10 and "detail_dataset" not in stats
