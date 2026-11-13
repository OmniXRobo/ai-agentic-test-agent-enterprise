"""23.6.0: the AI Operations Agent and look-alike controls ("Multiple matching controls found for 'Add to cart'").

A learned control with an exact unique id (data-test / id / name) is used even when several controls share its
name; real duplicates are listed in chat by their row or card so the person can pick one; the pick is used once."""

from __future__ import annotations

import pytest

from skyguard_agent import locator
from skyguard_agent.agent import OperationsAgent
from skyguard_agent.util import AgentError
from skyguard_agent.vault import MemoryBackend, Vault
from tests.ops_agent_fixtures import setup_fleetops

PRODUCTS = ["Sauce Labs Backpack", "Sauce Labs Bike Light", "Sauce Labs Bolt T-Shirt", "Sauce Labs Fleece Jacket",
            "Sauce Labs Onesie", "Test.allTheThings() T-Shirt (Red)"]


def _slug(name):
    return "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-").replace("--", "-")


def _inventory(with_ids=True):
    els = []
    for i, p in enumerate(PRODUCTS):
        e = {"tag": "button", "role": "button", "name": "Add to cart", "text": "Add to cart", "nearby": p,
             "section": "Products", "ancestors": ["inventory_item", "pricebar"], "h": f"0:{i}", "visible": True}
        if with_ids:
            e.update(test_id=f"add-to-cart-{_slug(p)}", id=f"add-to-cart-{_slug(p)}", name_attr=f"add-to-cart-{_slug(p)}")
        els.append(e)
    els.append({"tag": "a", "role": "link", "name": "Cart", "h": "0:99", "visible": True})
    return {"elements": els}


def test_exact_unique_id_wins_among_look_alike_buttons():
    snap = _inventory()
    learned = locator.fingerprint_of(snap["elements"][1])
    res = locator.resolve(learned, snap)
    assert res.status == "found", res.to_dict()
    assert res.element["nearby"] == "Sauce Labs Bike Light" and not res.healed


def test_similar_ids_alone_do_not_decide():
    snap = _inventory()
    learned = dict(locator.fingerprint_of(snap["elements"][0]), test_id="add-to-cart-sauce-labs-backpack-v2",
                   id="x", name_attr="y")
    assert locator.resolve(learned, snap).status in ("ambiguous", "found")
    res = locator.resolve(learned, snap)
    if res.status == "found":  # only acceptable when the closest match is the backpack itself
        assert res.element["nearby"] == "Sauce Labs Backpack"


def test_true_duplicates_are_ambiguous_and_named_by_context():
    snap = _inventory(with_ids=False)
    res = locator.resolve({"tag": "button", "role": "button", "name": "Add to cart"}, snap)
    assert res.status == "ambiguous"
    names = [locator.context_of(c["element"]) for c in res.candidates]
    assert all(n in PRODUCTS for n in names) and len(set(names)) == len(names)
    chosen = locator.fingerprint_of(snap["elements"][3])
    picked = locator.pick(chosen, snap)
    assert picked.status == "found" and picked.element["nearby"] == "Sauce Labs Fleece Jacket"
    gone = {"elements": [e for e in snap["elements"] if e.get("nearby") != "Sauce Labs Fleece Jacket"]}
    assert locator.pick(chosen, gone).status != "found" or locator.pick(chosen, gone).element["nearby"] != "Sauce Labs Fleece Jacket"


@pytest.fixture()
def agent(tmp_path):
    a = OperationsAgent(tmp_path / "ops", vault=Vault(MemoryBackend()))
    yield a
    a.close()


def _paused_run(agent):
    setup = setup_fleetops(agent, "http://127.0.0.1:9")
    view = agent.engine.create(app_id=setup["app"]["id"],
                               stages=[{"workflow": "Create trip", "account_id": setup["accounts"]["operator"]["id"]}],
                               records=[{"trip_no": "Q1", "route": "Dubai to Sharjah", "date": "2099-01-01",
                                         "time": "06:00", "bus": "BUS-101", "driver": "Carlos Diaz"}],
                               interface="test", by="t")
    run = agent.engine.run(view["run"]["id"])
    snap = _inventory(with_ids=False)
    res = locator.resolve({"tag": "button", "role": "button", "name": "Add to cart"}, snap)
    out = agent.engine._locate_problem(run, run["checkpoint"], res, "Add to cart")
    return run["id"], out


def test_ambiguity_is_asked_in_chat_with_choices_and_resolved_once(agent):
    run_id, out = _paused_run(agent)
    pause = out["pause"]
    assert pause["code"] == "ambiguous" and "Select the correct one manually" not in pause["message"]
    assert "Tell me which one" in pause["message"] and "Sauce Labs Onesie" in pause["message"]
    assert [c["index"] for c in pause["choices"]] == list(range(len(pause["choices"])))
    assert all("value" not in c["fingerprint"] and "h" not in c["fingerprint"] for c in pause["choices"])
    view = agent.engine.view(run_id)
    assert view["status"]["pause"]["choices"]

    with pytest.raises(AgentError) as exc:
        agent.engine.resume(run_id, by="t", choice=9)
    assert exc.value.code == "CHOICE_INVALID"
    agent.engine.resume(run_id, by="t", choice=2)
    cp = agent.engine.run(run_id)["checkpoint"]
    assert cp["choice"]["target"] == "Add to cart" and cp["choice"]["fingerprint"]["nearby"] == pause["choices"][2]["fingerprint"]["nearby"]
    assert cp["pause"] is None
    # not remembered as the control's general location (other records may need another product)
    assert not agent.kb.locator(agent.engine.run(run_id)["plan"]["app_id"], "*", "Add to cart")


def test_chat_page_is_simplified():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "web/js/pages/ops-agent.js").read_text(encoding="utf-8")
    assert "e.g. create 5 trips" not in js and "Local Qwen model + learned knowledge" not in js
    assert "The microphone records only between Speak and Stop" not in js
    assert 'data-run-act="pause"' in js and 'data-run-act="resume"' in js and 'data-run-act="stop"' in js
    assert "<progress" in js and 'id="micBtn"' in js and "data-choice" in js
    assert "msg.textContent = s.pause.message" in js  # pause text is never rendered as HTML
