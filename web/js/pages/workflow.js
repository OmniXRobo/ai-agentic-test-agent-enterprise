// Agent Workflow: Air Force squadron cards driven only by runtime events, plus Live Agent Activity,
// on a tactical defense backdrop (23.3.14).

import { api } from "../api.js";
import { state, subscribe, onTick } from "../store.js";
import { cardHtml, refreshCards } from "../agent-card.js";
import { agentState } from "../agent-state.js";
import { esc, timeOf, toast, isRepeat } from "../util.js";

let unsub = null;
let stopTick = null;

// Fighter silhouette (top view), drawn in a 100 x 100 box pointing up.
const JET = "M50 2 54 30 96 56v8L54 52l-2 26 13 9v5l-15-4-15 4v-5l13-9-2-26L4 64v-8l42-26z";

// Tactical backdrop: radar rings and grid, HUD ladder, three fighter silhouettes and a sweeping radar.
// Decorative only (aria-hidden) and drawn behind the cards.
function backdropHtml() {
  const jets = [[78, 12, 0.9, 35], [86, 30, 0.6, 35], [70, 30, 0.6, 35]]
    .map(([x, y, s, r]) => `<path d="${JET}" transform="translate(${x * 12} ${y * 8}) rotate(${r}) scale(${s})" class="wf-jet"/>`).join("");
  const rings = [90, 180, 270, 360].map((r) => `<circle cx="1000" cy="160" r="${r}" class="wf-ring"/>`).join("");
  return `<div class="wf-backdrop" aria-hidden="true"><div class="wf-stick">
    <svg viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice">
      <defs><pattern id="wfGrid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" class="wf-grid"/></pattern></defs>
      <rect width="1200" height="800" fill="url(#wfGrid)"/>
      ${rings}<path d="M1000 -200V520M640 160h720" class="wf-ring"/>
      <path d="M0 760h1200" class="wf-hud"/><text x="1180" y="785" text-anchor="end" class="wf-hudtxt">ALT 32000 · HDG 045 · SQN QA-1 · DEFENSE GRID ONLINE</text>
      ${jets}
    </svg>
    <div class="wf-sweep"></div>
  </div></div>`;
}

function statusStripHtml() {
  const counts = { RUNNING: 0, READY: 0, FAILED: 0 };
  for (const a of state.agents.values()) {
    const st = agentState(a, state.agents).state;
    if (st === "RUNNING" || st === "PAUSED") counts.RUNNING++;
    else if (st === "FAILED") counts.FAILED++;
    else counts.READY++;
  }
  return `<div class="wf-strip">
    <span class="wf-callsign">&#9650; AIR DEFENSE OPERATIONS · AGENT SQUADRON</span>
    <span class="spacer"></span>
    <span class="wf-count run"><span class="lamp ${counts.RUNNING ? "RUNNING" : ""}"></span>AIRBORNE <b>${counts.RUNNING}</b></span>
    <span class="wf-count"><span class="lamp READY"></span>ON STANDBY <b>${counts.READY}</b></span>
    <span class="wf-count bad"><span class="lamp ${counts.FAILED ? "FAILED" : ""}"></span>DOWN <b>${counts.FAILED}</b></span>
  </div>`;
}

function feedItemHtml(ev) {
  const cls = ev.name === "agent.failed" ? "failed" : ev.name === "agent.completed" ? "passed" : "";
  const who = ev.agent_name && !String(ev.activity || "").startsWith(ev.agent_name) ? `${ev.agent_name} · ` : "";
  return `<div class="feed-item ${cls}"><span class="feed-time">${esc(timeOf(ev.timestamp))}</span><span class="feed-text">${esc(who + (ev.activity || ev.name))}</span></div>`;
}

export async function mount(view) {
  const agents = [...state.agents.values()];
  view.innerHTML = `<div class="wf-defense">
    ${backdropHtml()}
    <div id="wfStrip">${statusStripHtml()}</div>
    <div class="row" style="margin-bottom:14px">
      <p class="muted" style="margin:0">Cards change only when an agent reports real work: READY, WAITING (queued in the same run), RUNNING, PAUSED (waiting for you), COMPLETED or FAILED.</p>
      <span class="spacer"></span>
      <button class="btn primary" id="runDiag">Run system diagnostics</button>
    </div>
    <div class="workflow">
      <section class="agents-grid" id="agentsGrid">${agents.map(cardHtml).join("")}</section>
      <section class="card">
        <h2>Live Agent Activity</h2>
        <div class="feed" id="feed">${state.events.slice(-200).filter((e, i, a) => !isRepeat(a[i - 1], e)).map(feedItemHtml).join("") || '<p class="muted">No agent activity yet.</p>'}</div>
      </section>
    </div></div>`;
  const feed = view.querySelector("#feed");
  feed.scrollTop = feed.scrollHeight;

  view.querySelector("#runDiag").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const r = await api("/api/diagnostics/run", { method: "POST" });
      toast(`Diagnostics started (${r.run_id})`);
    } catch (err) {
      toast(err.message, "error");
    } finally {
      e.target.disabled = false;
    }
  });

  unsub = subscribe((c) => {
    if (c.type === "snapshot") {
      view.querySelector("#agentsGrid").innerHTML = [...state.agents.values()].map(cardHtml).join("");
      view.querySelector("#wfStrip").innerHTML = statusStripHtml();
    } else if (c.type === "event") {
      if (c.agent) { refreshCards(view); view.querySelector("#wfStrip").innerHTML = statusStripHtml(); } // a start or finish can move pipeline peers to WAITING or READY
      const prev = state.events[state.events.length - 2];
      if (isRepeat(prev, c.event)) return;
      const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40;
      if (feed.querySelector("p.muted")) feed.innerHTML = "";
      feed.insertAdjacentHTML("beforeend", feedItemHtml(c.event));
      while (feed.children.length > 300) feed.firstElementChild.remove();
      if (atBottom) feed.scrollTop = feed.scrollHeight;
    }
  });
  // Elapsed time and the COMPLETED -> READY hold come from the shared ticker.
  let tick = 0;
  stopTick = onTick(() => { refreshCards(view); if (++tick % 5 === 0) view.querySelector("#wfStrip").innerHTML = statusStripHtml(); });
}

export function unmount() {
  if (unsub) unsub();
  if (stopTick) stopTick();
  unsub = stopTick = null;
}
