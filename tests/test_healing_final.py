"""Final patch, defect 7: context-aware locators, shadow DOM candidates and healing that asks when unsure."""

from __future__ import annotations

import pytest

from qa_platform.automation import healing
from qa_platform.automation.dsl import Script, Step, Target, new_script_id
from qa_platform.errors import QAError

try:
    import playwright  # noqa: F401

    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

needs_browser = pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright is not installed")


def test_context_signals_break_ties_and_review_band():
    target = Target(selector="#save", tag="button", text="Save", role="button", nearby_text="Billing address",
                    form="billing", ancestors=["section.billing"])
    billing = {"selector": "#a", "tag": "button", "text": "Save", "role": "button", "nearby_text": "Billing address",
               "form": "billing", "ancestors": ["section.billing", "main"]}
    shipping = {"selector": "#b", "tag": "button", "text": "Save", "role": "button", "nearby_text": "Shipping address",
                "form": "shipping", "ancestors": ["section.shipping", "main"]}
    ranked = healing.rank(target, [shipping, billing])
    assert ranked[0]["selector"] == "#a" and healing.choose(ranked)["selector"] == "#a"
    # an old fingerprint without context fields scores exactly as before
    old = Target(selector="#save", tag="button", text="Save", role="button")
    assert healing.score(old, billing)[0] == healing.score(old, shipping)[0]
    # close but below the threshold: offered for review, never chosen
    weak = [{"selector": "#x", "confidence": 0.5, "signals": {}}, {"selector": "#y", "confidence": 0.3, "signals": {}}]
    assert healing.choose(weak) is None and [c["selector"] for c in healing.review(weak)] == ["#x"]
    assert healing.review([{"selector": "#z", "confidence": 0.2, "signals": {}}]) is None


@needs_browser
def test_ambiguous_shadow_dom_match_waits_for_confirmation(rt, site):
    auto = rt.automation
    rt.settings.update({"playback": {"visual_delay_ms": 0}})
    button = Target(selector="#signin", tag="button", text="Proceed", role="button", name="Proceed",
                    nearby_text="Continue to your account", shadow_host="#actions", description="Proceed button")
    script = auto.scripts.save(Script(id=new_script_id(), name="Shadow login", mode="ai", base_url=site,
                                      application="Shop", steps=[
                                          Step(action="goto", value=f"{site}/heal_shadow.html"),
                                          Step(action="fill", target=Target(selector="#username"), value="alice"),
                                          Step(action="click", target=button, timeout_ms=500),
                                          Step(action="expect_text", target=Target(selector="#welcome"),
                                               value="Welcome alice")]))
    record = auto.run_script(script.id, headless=True, background=False)
    assert record["status"] == "FAILED" and "low-confidence" in record["error"]
    event = auto.runner.healing_events(script.id)[0]
    assert event["result"] == "needs_confirmation"
    offered = [c["selector"] for c in event["candidates"]]
    assert len(offered) >= 2 and all(s.startswith("#actions ") for s in offered[:2]), offered
    assert auto.scripts.get(script.id).steps[2].target.selector == "#signin"  # nothing changed silently
    with pytest.raises(QAError):
        auto.runner.confirm_healing(event["id"], "#not-offered")
    out = auto.runner.confirm_healing(event["id"], offered[0])
    assert out["result"] == "confirmed"
    saved = auto.scripts.get(script.id).steps[2].target
    assert saved.selector == offered[0] and "#signin" in saved.alternates
    assert auto.run_script(script.id, headless=True, background=False)["status"] == "PASSED"
    with pytest.raises(QAError):
        auto.runner.confirm_healing(event["id"], offered[0])


def test_confirm_api_rejects_unknown_event(client):
    r = client.post("/api/automation/healing/999999/confirm", json={"selector": None})
    assert r.status_code == 404
