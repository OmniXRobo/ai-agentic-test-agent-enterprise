// One runtime agent card, used by Agent Workflow (23.3.14: the Dashboard and Voice Command Center no longer show cards).
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

const secs = (fromIso, toIso) => Math.max(0, ((toIso ? new Date(toIso) : new Date()) - new Date(fromIso)) / 1000);

function dur(sec) {
  let s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  return h ? `${h}h ${m}m ${s}s` : m ? `${m}m ${s}s` : `${s}s`;
}

// Remaining and total time. While an agent runs both are estimates from its own progress so far
// (elapsed / progress x what is left); after it finishes, Total is the real duration. Nothing is guessed
// before the agent has reported any progress.
function etaText(a, s) {
  if (s.state === "RUNNING" || s.state === "PAUSED") {
    const p = Number(a.progress) || 0;
    const el = a.started_at ? secs(a.started_at) : 0;
    if (s.state === "PAUSED") return { rem: "Paused", total: "—" };
    if (p >= 100) return { rem: "Finishing…", total: `≈ ${dur(el)}` };
    if (p <= 0 || el < 3) return { rem: "Estimating…", total: "Estimating…" };
    const rem = (el * (100 - p)) / p;
    return { rem: `≈ ${dur(rem)} left`, total: `≈ ${dur(el + rem)}` };
  }
  if (a.finished_at && a.started_at) return { rem: "—", total: dur(secs(a.started_at, a.finished_at)) };
  return { rem: "—", total: "—" };
}

function timeText(a, s) {
  if (s.state === "RUNNING" || s.state === "PAUSED") return elapsed(a.started_at);
  if (a.finished_at && a.started_at) return elapsed(a.started_at, a.finished_at);
  return "—";
}

export function cardHtml(a, { compact = false } = {}) {
  const s = agentState(a, state.agents);
  const pct = a.progress ?? 0;
  const eta = etaText(a, s);
  return `
  <article class="agent-card ${esc(s.state)}" data-agent="${esc(a.id)}" aria-live="polite">
    <div class="agent-head">
      <div class="agent-robot"><span class="unit-radar" aria-hidden="true"></span>${robotSvg(a.robot, a.name)}</div>
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
      <dt>Remaining</dt><dd data-f="rem" title="Estimated from the agent's progress so far">${esc(eta.rem)}</dd>
      <dt>Total time</dt><dd data-f="total" title="Elapsed + remaining while it runs; the real duration when it has finished">${esc(eta.total)}</dd>
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
  const eta = etaText(a, s);
  if (f("rem")) { f("rem").textContent = eta.rem; f("total").textContent = eta.total; }
  f("last").textContent = s.last_result || "—";
}

/** Refresh every card in a root (used by the shared 1 s ticker: only text, no layout work). */
export function refreshCards(root) {
  for (const a of state.agents.values()) updateCard(root, a);
}
