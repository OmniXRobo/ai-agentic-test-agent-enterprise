// Normalized runtime state of an agent card, derived from the runtime's own events (same rules as
// qa_platform/events/state.py): RUNNING, PAUSED, WAITING (queued behind an agent of the same pipeline run),
// COMPLETED (finished less than 30 s ago), FAILED, READY. Nothing here invents a status.

export const COMPLETED_HOLD_MS = 30000;
const PIPELINES = [
  ["requirements", "test_planning", "test_case_design", "test_data"],
  ["jmeter_load", "performance_engineering"],
];

export function agentState(a, agents, at = Date.now()) {
  const last = a.status === "PASSED" ? "COMPLETED" : a.status === "FAILED" ? "FAILED" : null;
  if (a.status === "RUNNING") return { state: a.paused ? "PAUSED" : "RUNNING", last_result: null, waiting_for: null };
  for (const p of PIPELINES) {
    const mine = p.indexOf(a.id);
    if (mine < 0) continue;
    for (const other of p.slice(0, mine)) {
      const o = agents.get(other);
      if (o?.status === "RUNNING" && o.run_id && o.run_id !== a.run_id) return { state: "WAITING", last_result: last, waiting_for: other };
    }
  }
  if (a.status === "FAILED") return { state: "FAILED", last_result: "FAILED", waiting_for: null };
  if (a.status === "PASSED" && a.finished_at && at - new Date(a.finished_at) < COMPLETED_HOLD_MS) {
    return { state: "COMPLETED", last_result: "COMPLETED", waiting_for: null };
  }
  return { state: "READY", last_result: last, waiting_for: null };
}

/** The agent that owns the work right now: the most recently started RUNNING/PAUSED agent, else null. */
export function activeAgent(agents, except = "voice_command") {
  let best = null;
  for (const a of agents.values()) {
    if (a.status !== "RUNNING" || a.id === except) continue;
    if (!best || new Date(a.started_at) > new Date(best.started_at)) best = a;
  }
  return best;
}
