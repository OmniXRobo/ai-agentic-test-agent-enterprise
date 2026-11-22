"""23.14.0: Stop, Pause and Resume from an agent card on Agent Workflow.

There is no new orchestration here. Each card button calls the pause / resume / stop that the module running the work
already has, found from the run the agent is working on now (the runtime's own agent state, never a run ID sent by the
browser):

* Security, Fuse and ActiveMQ runs: pause, resume and cancel (``itest.common.CONTROL``)
* AI Operations executions: pause, resume and stop (the operations engine)
* Test execution and manual playback: stop
* Application discovery: stop
* JMeter load tests: graceful stop (results and the report are still written); a queued test is taken off the queue
* API Testing runs: stop (cancels the tests that have not started)

Agents whose work cannot be paused or stopped safely say so: ``GET /api/agents/control`` lists, per running agent,
only the actions its module supports, and the card shows "Not available" for the rest. The middleware's role rules
apply as for every other change (view-only accounts cannot press these buttons).
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..errors import QAError
from ..events import names
from ..events.bus import QAEvent
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/agents", tags=["agents"])

Action = Literal["pause", "resume", "stop"]
ITEST_SERVICES = {"security": "security", "fuse": "fuse", "activemq": "activemq"}
AUTOMATION_RUNNERS = ("test_execution", "manual_playback", "self_healing")
PERF_AGENTS = ("jmeter_load", "performance_engineering")
API_AGENTS = ("api_testing", "api_failure_analysis", "api_contract")


def _by(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", None) or "local-program"


def _current(rt: Runtime, agent_id: str) -> dict[str, Any] | None:
    for s in rt.state.snapshot():
        if s.get("agent_id") == agent_id:
            return s
    return None


def _handlers(rt: Runtime, agent_id: str, run_id: str, by: str) -> dict[str, Callable[[], Any]]:
    """The actions the module that runs ``run_id`` supports right now, as callables."""
    # Security / Fuse / ActiveMQ: the shared run control (pause at the next safe point, cancel keeps partial results)
    try:
        from ..itest.common import CONTROL
    except Exception:  # noqa: BLE001 - the modules are optional
        CONTROL = None
    if CONTROL is not None and CONTROL.active(run_id) and getattr(rt, "itest", None) is not None:
        try:
            module = rt.itest.run(run_id)["module"]
        except Exception:  # noqa: BLE001
            module = None
        svc = getattr(rt, ITEST_SERVICES.get(module or "", ""), None)
        if svc is not None:
            return {"pause": lambda: svc.control(run_id, "pause"), "resume": lambda: svc.control(run_id, "resume"),
                    "stop": lambda: svc.control(run_id, "cancel")}

    # AI Operations: the engine's own pause / resume / stop (the browser session and checkpoint are kept on pause)
    ops = getattr(rt, "opsagent", None)
    if agent_id == "ai_operations" and ops is not None:
        job = ops.jobs.get(run_id) if hasattr(ops, "jobs") else None
        if (job is not None and job.kind == "run") or run_id in getattr(ops, "hosted", {}):
            return {"pause": lambda: ops.agent.engine.pause(run_id, by=by),
                    "resume": lambda: ops.resume(run_id, by), "stop": lambda: ops.stop(run_id, by)}

    auto = getattr(rt, "automation", None)
    if agent_id in AUTOMATION_RUNNERS and auto is not None and run_id in auto.runner.stop_events:
        return {"stop": lambda: auto.runner.stop(run_id)}
    if agent_id == "app_discovery" and auto is not None:
        job = auto.discovery_jobs.get(run_id)
        if job is not None and job.active:
            return {"stop": job.stop}

    perf = getattr(rt, "performance", None)
    if agent_id in PERF_AGENTS and perf is not None:
        if run_id in getattr(perf, "_procs", {}):
            return {"stop": lambda: perf.stop(run_id, "graceful", reason=f"Stopped from Agent Workflow by {by}")}
        if any(q.get("run_id") == run_id for q in perf.queue()):
            return {"stop": lambda: perf.cancel(run_id)}

    api = getattr(rt, "apitesting", None)
    if agent_id in API_AGENTS and api is not None:
        return {"stop": lambda: api.cancel(run_id)}
    return {}


def _emit(rt: Runtime, agent_id: str, run_id: str, event_name: str, activity: str) -> None:
    """Pause / resume of the shared run control are not reported by the modules themselves: say so on the card."""
    from ..agents.registry import get_agent

    d = get_agent(agent_id)
    rt.bus.publish(QAEvent(name=event_name, agent_id=agent_id, agent_name=d.name, activity=activity, run_id=run_id,
                           source=d.source, details={"from": "agent_workflow"}))


@router.get("/control")
def control_options(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Per running agent: its run and the actions its module supports now (pause, resume, stop)."""
    out: dict[str, Any] = {}
    for s in rt.state.snapshot():
        if s.get("status") != "RUNNING" or not s.get("run_id"):
            continue
        acts = sorted(_handlers(rt, s["agent_id"], s["run_id"], "local-program"))
        out[s["agent_id"]] = {"run_id": s["run_id"], "actions": acts, "paused": bool(s.get("paused"))}
    return {"ok": True, "agents": out}


class ControlIn(BaseModel):
    action: Action
    run_id: str | None = None  # optional check: refused when the agent moved on to another run


@router.post("/{agent_id}/control")
def control(agent_id: str, body: ControlIn, request: Request, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    st = _current(rt, agent_id)
    if st is None:
        raise QAError("AGENT_NOT_FOUND", f"Unknown agent: {agent_id}.", status=404)
    if st.get("status") != "RUNNING" or not st.get("run_id"):
        raise QAError("AGENT_NOT_RUNNING", "This agent is not running.", status=409)
    run_id = st["run_id"]
    if body.run_id and body.run_id != run_id:
        raise QAError("AGENT_RUN_CHANGED", "This agent has moved on to another run; refresh and try again.", status=409)
    by = _by(request)
    handlers = _handlers(rt, agent_id, run_id, by)
    fn = handlers.get(body.action)
    if fn is None:
        what = {"pause": "paused", "resume": "resumed", "stop": "stopped"}[body.action]
        raise QAError("AGENT_CONTROL_UNSUPPORTED", f"This agent's work cannot be {what} from here.", status=409,
                      details={"supported": sorted(handlers)})
    result = fn()
    if result is False:
        raise QAError("AGENT_NOT_RUNNING", "This run is not running any more.", status=409)
    # The shared run control does not report pause / resume on the card itself; the operations engine reports its
    # own pause (when the browser reaches a safe point) but not the resume.
    if body.action == "resume" or (body.action == "pause" and agent_id != "ai_operations"):
        _emit(rt, agent_id, run_id, names.AGENT_PAUSED if body.action == "pause" else names.AGENT_RESUMED,
              f"{'Paused' if body.action == 'pause' else 'Resumed'} by {by} from Agent Workflow")
    return {"ok": True, "agent_id": agent_id, "run_id": run_id, "action": body.action}
