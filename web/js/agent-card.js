// One runtime agent card used by the Dashboard squadron, Agent Workflow and the Voice Command Center.
// Cards show only what the runtime reported: state, activity, run ID, start time, elapsed time, last result.

import { state } from "./store.js";
import { robotSvg } from "./robots.js";
import { agentState } from "./agent-state.js";
import { esc, elapsed, timeOf } from "./util.js";

function short(name) { return name.replace(/ Agent$/, ""); }

function activityText(a, s) {
  if (s.state === "WAITING") return `Queued behind ${short(state.agents.get(s.waiting_for)?.name || s.waiting_for)}`;
  if (s.state === "READY") return a.activity && a.finished_at ? `Last: ${a.activity}` : "Ready for tasking";
  return a.activity || "";
}

function timeText(a, s) {
  if (s.state === "RUNNING" || s.state === "PAUSED") return elapsed(a.started_at);
  if (a.finished_at && a.started_at) return elapsed(a.started_at, a.finished_at);
  return "—";
}

export function cardHtml(a, { compact = false } = {}) {
  const s = agentState(a, state.agents);
  const pct = a.progress ?? 0;
  return `
  <article class="agent-card ${esc(s.state)}" data-agent="${esc(a.id)}" aria-live="polite">
    <div class="agent-head">
      <div class="agent-robot">${robotSvg(a.robot, a.name)}</div>
      <div style="min-width:0">
        <div class="agent-name">${esc(compact ? short(a.name) : a.name)}</div>
        <span class="pill ${esc(s.state)}" data-f="state">${esc(s.state)}</span>
      </div>
    </div>
    <div class="agent-activity" data-f="activity">${esc(activityText(a, s))}</div>
    <div class="progress" title="Progress"><span data-f="bar" style="width:${pct}%"></span></div>
    <dl class="agent-facts">
      <dt>Run</dt><dd data-f="run">${esc(a.run_id || "—")}</dd>
      <dt>Started</dt><dd data-f="start">${esc(a.started_at ? timeOf(a.started_at) : "—")}</dd>
      <dt>Elapsed</dt><dd data-f="time">${esc(timeText(a, s))}</dd>
      <dt>Last result</dt><dd data-f="last">${esc(s.last_result || "—")}</dd>
    </dl>
  </article>`;
}

/** Update one card in place (no re-render of the grid). */
export function updateCard(root, a) {
  const el = root.querySelector(`.agent-card[data-agent="${CSS.escape(a.id)}"]`);
  if (!el) return;
  const s = agentState(a, state.agents);
  el.className = `agent-card ${s.state}`;
  const f = (k) => el.querySelector(`[data-f="${k}"]`);
  f("state").className = `pill ${s.state}`;
  f("state").textContent = s.state;
  f("activity").textContent = activityText(a, s);
  f("bar").style.width = `${a.progress ?? 0}%`;
  f("run").textContent = a.run_id || "—";
  f("start").textContent = a.started_at ? timeOf(a.started_at) : "—";
  f("time").textContent = timeText(a, s);
  f("last").textContent = s.last_result || "—";
}

/** Refresh every card in a root (used by the shared 1 s ticker: only text, no layout work). */
export function refreshCards(root) {
  for (const a of state.agents.values()) updateCard(root, a);
}
