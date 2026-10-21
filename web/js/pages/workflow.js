// Agent Workflow: robot cards driven only by runtime events, plus Live Agent Activity.

import { api } from "../api.js";
import { state, subscribe } from "../store.js";
import { robotSvg } from "../robots.js";
import { esc, timeOf, elapsed, toast } from "../util.js";

let unsub = null;
let ticker = null;

function cardHtml(a) {
  const pct = a.progress ?? 0;
  return `
  <article class="agent-card ${esc(a.status)}" data-agent="${esc(a.id)}" aria-live="polite">
    <div class="agent-head">
      <div class="agent-robot">${robotSvg(a.robot, a.name)}</div>
      <div>
        <div class="agent-name">${esc(a.name)}</div>
        <span class="pill ${esc(a.status)}" data-f="status">${esc(a.status)}</span>
      </div>
    </div>
    <div class="agent-activity" data-f="activity">${esc(a.activity || (a.status === "WAITING" ? waitingText(a) : ""))}</div>
    <div class="progress" title="Progress"><span data-f="bar" style="width:${pct}%"></span></div>
    <div class="agent-meta">
      <span data-f="source" title="Runtime component">${esc(a.source || "")}</span>
      <span data-f="time">${timeText(a)}</span>
    </div>
  </article>`;
}

function waitingText(a) {
  return a.stage > 1 ? `Idle. Capability delivered in build stage ${a.stage}.` : "Idle. Waiting for work.";
}

function timeText(a) {
  if (a.status === "RUNNING") return `${a.progress != null ? a.progress + "% · " : ""}${elapsed(a.started_at)}`;
  if (a.finished_at) return `took ${elapsed(a.started_at, a.finished_at)}`;
  return "";
}

function updateCard(a) {
  const el = document.querySelector(`.agent-card[data-agent="${CSS.escape(a.id)}"]`);
  if (!el) return;
  el.className = `agent-card ${a.status}`;
  const f = (k) => el.querySelector(`[data-f="${k}"]`);
  f("status").className = `pill ${a.status}`;
  f("status").textContent = a.status;
  f("activity").textContent = a.activity || (a.status === "WAITING" ? waitingText(a) : "");
  f("bar").style.width = `${a.progress ?? 0}%`;
  f("source").textContent = a.source || "";
  f("time").textContent = timeText(a);
}

function feedItemHtml(ev) {
  const cls = ev.name === "agent.failed" ? "failed" : ev.name === "agent.completed" ? "passed" : "";
  const who = ev.agent_name && !String(ev.activity || "").startsWith(ev.agent_name) ? `${ev.agent_name} · ` : "";
  return `<div class="feed-item ${cls}"><span class="feed-time">${esc(timeOf(ev.timestamp))}</span><span class="feed-text">${esc(who + (ev.activity || ev.name))}</span></div>`;
}

export async function mount(view) {
  const agents = [...state.agents.values()];
  view.innerHTML = `
    <div class="row" style="margin-bottom:14px">
      <p class="muted" style="margin:0">Cards change only when an agent reports real work. Nothing here runs on a timer.</p>
      <span class="spacer"></span>
      <button class="btn primary" id="runDiag">Run system diagnostics</button>
    </div>
    <div class="workflow">
      <section class="agents-grid" id="agentsGrid">${agents.map(cardHtml).join("")}</section>
      <section class="card">
        <h2>Live Agent Activity</h2>
        <div class="feed" id="feed">${state.events.slice(-200).map(feedItemHtml).join("") || '<p class="muted">No agent activity yet.</p>'}</div>
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
      if (c.agent) updateCard(c.agent);
      const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40;
      if (feed.querySelector("p.muted")) feed.innerHTML = "";
      feed.insertAdjacentHTML("beforeend", feedItemHtml(c.event));
      while (feed.children.length > 300) feed.firstElementChild.remove();
      if (atBottom) feed.scrollTop = feed.scrollHeight;
    }
  });
  // Refresh only the elapsed-time text of cards that are genuinely RUNNING.
  ticker = setInterval(() => {
    for (const a of state.agents.values()) {
      if (a.status !== "RUNNING") continue;
      const t = document.querySelector(`.agent-card[data-agent="${CSS.escape(a.id)}"] [data-f="time"]`);
      if (t) t.textContent = timeText(a);
    }
  }, 1000);
}

export function unmount() {
  if (unsub) unsub();
  if (ticker) clearInterval(ticker);
  unsub = ticker = null;
}
