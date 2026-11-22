"""23.14.0: Agent Workflow looks (Preferences) and Stop / Pause / Resume on the agent cards."""
import threading
from pathlib import Path

from qa_platform.agents.registry import AGENTS
from qa_platform.events import names
from qa_platform.events.bus import QAEvent

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _start(rt, agent_id, run_id):
    rt.bus.publish(QAEvent(name=names.AGENT_STARTED, agent_id=agent_id, agent_name=agent_id, status="RUNNING",
                           activity="working", progress=10, run_id=run_id))


def test_workflow_look_setting(client):
    assert client.get("/api/settings").json()["settings"]["ui"]["workflow_look"] == "classic"
    for look in ("command_center", "holographic", "skyguard_core", "mission_control", "classic"):
        r = client.patch("/api/settings", json={"ui": {"workflow_look": look}})
        assert r.status_code == 200, r.text
        assert client.get("/api/settings").json()["settings"]["ui"]["workflow_look"] == look
    assert client.patch("/api/settings", json={"ui": {"workflow_look": "orange"}}).status_code == 400


def test_control_refuses_idle_and_unknown_agents(client):
    assert client.post("/api/agents/requirements/control", json={"action": "stop"}).status_code == 409
    assert client.post("/api/agents/nope/control", json={"action": "stop"}).status_code == 404
    assert client.post("/api/agents/requirements/control", json={"action": "explode"}).status_code == 422
    assert client.get("/api/agents/control").json() == {"ok": True, "agents": {}}


def test_unsupported_running_agent_says_not_available(client, rt):
    _start(rt, "requirements", "RUN-REQ-1")
    opts = client.get("/api/agents/control").json()["agents"]
    assert opts["requirements"] == {"run_id": "RUN-REQ-1", "actions": [], "paused": False}
    r = client.post("/api/agents/requirements/control", json={"action": "pause"})
    assert r.status_code == 409 and "AGENT_CONTROL_UNSUPPORTED" in r.text


def test_stop_test_execution_uses_the_runner(client, rt):
    ev = threading.Event()
    rt.automation.runner.stop_events["RUN-EXEC-1"] = ev
    try:
        _start(rt, "test_execution", "RUN-EXEC-1")
        assert client.get("/api/agents/control").json()["agents"]["test_execution"]["actions"] == ["stop"]
        assert client.post("/api/agents/test_execution/control", json={"action": "stop", "run_id": "OTHER"}).status_code == 409
        r = client.post("/api/agents/test_execution/control", json={"action": "stop", "run_id": "RUN-EXEC-1"})
        assert r.status_code == 200, r.text
        assert ev.is_set()
    finally:
        rt.automation.runner.stop_events.pop("RUN-EXEC-1", None)


def test_pause_resume_stop_itest_run(client, rt, monkeypatch):
    from qa_platform.itest.common import CONTROL

    calls = []

    class Store:
        def run(self, run_id):
            return {"module": "security", "id": run_id}

    class Security:
        def control(self, run_id, action):
            calls.append(action)
            return {"id": run_id}

    monkeypatch.setattr(rt, "itest", Store(), raising=False)
    monkeypatch.setattr(rt, "security", Security(), raising=False)
    CONTROL.register("SEC-1")
    try:
        _start(rt, "vulnerability_assessment", "SEC-1")
        assert client.get("/api/agents/control").json()["agents"]["vulnerability_assessment"]["actions"] == ["pause", "resume", "stop"]
        assert client.post("/api/agents/vulnerability_assessment/control", json={"action": "pause"}).status_code == 200
        agents = {a["id"]: a for a in client.get("/api/agents").json()["agents"]}
        assert agents["vulnerability_assessment"]["paused"] is True  # the card shows PAUSED
        assert client.post("/api/agents/vulnerability_assessment/control", json={"action": "resume"}).status_code == 200
        agents = {a["id"]: a for a in client.get("/api/agents").json()["agents"]}
        assert agents["vulnerability_assessment"]["paused"] is False
        assert client.post("/api/agents/vulnerability_assessment/control", json={"action": "stop"}).status_code == 200
        assert calls == ["pause", "resume", "cancel"]
    finally:
        CONTROL.done("SEC-1")


def test_web_files_for_the_looks():
    js = WEB / "js"
    wf = (js / "pages" / "workflow.js").read_text(encoding="utf-8")
    for key, name in (("classic", "Classic Squadron"), ("command_center", "Command Center"), ("holographic", "Holographic Command"),
                      ("skyguard_core", "SkyGuard Core"), ("mission_control", "Mission Control")):
        assert f'"{key}"' in wf and name in wf
    for f in ("classic", "command-center", "holographic", "skyguard-core", "mission-control"):
        assert (js / "pages" / "wf-looks" / f"{f}.js").exists()
    for f in ("command-center", "holographic", "skyguard-core", "mission-control"):
        css = (WEB / "css" / "wf-looks" / f"{f}.css").read_text(encoding="utf-8")
        assert "http://" not in css and "https://" not in css and "@import" not in css
        assert "255, 138, 31" not in css and "#ff8a1f" not in css  # no orange: SkyGuard navy and cyan
    mc = (js / "pages" / "wf-looks" / "mission-control.js").read_text(encoding="utf-8")
    assert "255,138,31" not in mc and "#ff8a1f" not in mc and "SAMPLE" not in mc.upper().replace("SAMPLED", "")
    assert "Not available" in mc and "View only" in mc
    card = (js / "agent-card.js").read_text(encoding="utf-8")
    assert "controlsHtml" in card and "updateControls" in card
    assert "/control" in (js / "agent-controls.js").read_text(encoding="utf-8")
    assert "workflow_look" in (js / "pages" / "settings.js").read_text(encoding="utf-8")


def test_every_agent_has_its_own_robot():
    src = (WEB / "js" / "robots.js").read_text(encoding="utf-8")
    for a in AGENTS:
        assert src.count(f"\n  {a.robot}:") >= 2, a.robot  # emblem and squadron style
