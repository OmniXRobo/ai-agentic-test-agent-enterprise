// 23.14.0: what every Agent Workflow look shares, so a look only changes the presentation: the agent cards (with
// Stop / Pause / Resume), the status counts, Live Agent Activity and Run system diagnostics, all driven only by
// runtime events (the same rules as Classic).

import { api } from "../../api.js";
import { state, subscribe, onTick } from "../../store.js";
import { cardHtml, refreshCards } from "../../agent-card.js";
import { agentState } from "../../agent-state.js";
import { bindControls, loadControls } from "../../agent-controls.js";
import { esc, timeOf, toast, isRepeat } from "../../util.js";

export const EXPLAIN = "Cards change only when an agent reports real work: READY, WAITING (queued in the same run), RUNNING, "
  + "PAUSED (waiting for you), COMPLETED or FAILED. UNAVAILABLE: its engine or dependency is not available on this PC. "
  + "Pause, Resume and Stop work while an agent runs, where its module supports them.";

export function counts() {
  const c = { RUNNING: 0, READY: 0, FAILED: 0, WAITING: 0, COMPLETED: 0, PAUSED: 0, UNAVAILABLE: 0, total: 0 };
  for (const a of state.agents.values()) {
    const st = agentState(a, state.agents).state;
    c[st] = (c[st] || 0) + 1;
    c.total++;
  }
  c.airborne = c.RUNNING + c.PAUSED;
  c.down = c.FAILED + c.UNAVAILABLE;
  c.standby = c.total - c.airborne - c.down;
  return c;
}

export function feedItemHtml(ev) {
  const cls = ev.name === "agent.failed" ? "failed" : ev.name === "agent.completed" ? "passed" : "";
  const who = ev.agent_name && !String(ev.activity || "").startsWith(ev.agent_name) ? `${ev.agent_name} · ` : "";
  return `<div class="feed-item ${cls}"><span class="feed-time">${esc(timeOf(ev.timestamp))}</span><span class="feed-text">${esc(who + (ev.activity || ev.name))}</span></div>`;
}

export function feedHtml() {
  return state.events.slice(-200).filter((e, i, a) => !isRepeat(a[i - 1], e)).map(feedItemHtml).join("")
    || '<p class="muted">No agent activity yet.</p>';
}

export function gridHtml() {
  return [...state.agents.values()].map((a) => cardHtml(a)).join("");
}

/**
 * Wire a rendered look: #agentsGrid, #feed, #wfStrip (optional) and #runDiag. ``strip`` renders the counts,
 * ``onGrid`` runs after the grid is (re)drawn, ``onTickExtra`` runs on the shared 1 s ticker. Returns cleanup.
 */
export function wire(view, { strip = null, onGrid = null, onTickExtra = null, onAgentEvent = null } = {}) {
  const grid = view.querySelector("#agentsGrid");
  const feed = view.querySelector("#feed");
  const stripEl = view.querySelector("#wfStrip");
  const drawStrip = () => { if (strip && stripEl) stripEl.innerHTML = strip(); };
  const refresh = () => refreshCards(view);
  if (feed) feed.scrollTop = feed.scrollHeight;
  if (onGrid) onGrid(grid);
  loadControls(true).then(refresh);

  view.querySelector("#runDiag")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const r = await api("/api/diagnostics/run", { method: "POST" });
      toast(`Diagnostics started (${r.run_id})`);
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });
  const offControls = bindControls(view, refresh);

  const unsub = subscribe((c) => {
    if (c.type === "snapshot") {
      grid.innerHTML = gridHtml();
      if (onGrid) onGrid(grid);
      drawStrip();
      loadControls(true).then(refresh);
    } else if (c.type === "event") {
      if (c.agent) { // a start or finish can move pipeline peers to WAITING or READY
        refresh();
        drawStrip();
        if (/^agent\.(started|paused|resumed|completed|failed)$/.test(c.event.name)) loadControls(true).then(refresh);
        if (onAgentEvent) onAgentEvent(c);
      }
      if (!feed) return;
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
  const stopTick = onTick(() => {
    refresh();
    if (++tick % 5 === 0) { drawStrip(); loadControls().then(refresh); }
    if (onTickExtra) onTickExtra(tick);
  });
  return () => { unsub(); stopTick(); offControls(); };
}
