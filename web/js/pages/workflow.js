// Agent Workflow: robot cards driven only by runtime events, plus Live Agent Activity.

import { api } from "../api.js";
import { state, subscribe, onTick } from "../store.js";
import { cardHtml, refreshCards } from "../agent-card.js";
import { esc, timeOf, toast, isRepeat } from "../util.js";

let unsub = null;
let stopTick = null;

function feedItemHtml(ev) {
  const cls = ev.name === "agent.failed" ? "failed" : ev.name === "agent.completed" ? "passed" : "";
  const who = ev.agent_name && !String(ev.activity || "").startsWith(ev.agent_name) ? `${ev.agent_name} · ` : "";
  return `<div class="feed-item ${cls}"><span class="feed-time">${esc(timeOf(ev.timestamp))}</span><span class="feed-text">${esc(who + (ev.activity || ev.name))}</span></div>`;
}

export async function mount(view) {
  const agents = [...state.agents.values()];
  view.innerHTML = `
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
    </div>`;
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
    } else if (c.type === "event") {
      if (c.agent) refreshCards(view); // a start or finish can move pipeline peers to WAITING or READY
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
  stopTick = onTick(() => refreshCards(view));
}

export function unmount() {
  if (unsub) unsub();
  if (stopTick) stopTick();
  unsub = stopTick = null;
}
