// 23.14.0: Pause / Resume and Stop on every agent card (Agent Workflow, every look).
// The buttons call POST /api/agents/{id}/control, which uses the pause / resume / stop of the module that runs the
// work. Which actions a running agent supports comes from GET /api/agents/control; anything else shows
// "Not available". Nothing is guessed: a card that is not running has its buttons switched off.

import { api } from "./api.js";
import { state } from "./store.js";
import { canEdit } from "./auth.js";
import { agentState } from "./agent-state.js";
import { toast } from "./util.js";

let options = {}; // agent_id -> {run_id, actions[], paused}
let loading = null;
let loadedAt = 0;
const busy = new Set();

const PAUSE_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="2.5" width="3" height="11" rx=".8"/><rect x="9.5" y="2.5" width="3" height="11" rx=".8"/></svg>';
const PLAY_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z"/></svg>';
const STOP_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="3" width="10" height="10" rx="1.2"/></svg>';

export function controlsHtml(a) {
  return `<div class="agent-ctl" data-f="ctl">
    <button type="button" class="agent-ctl-btn pause" data-act="pause" disabled>${PAUSE_ICON}<span>Pause</span></button>
    <button type="button" class="agent-ctl-btn stop" data-act="stop" disabled>${STOP_ICON}<span>Stop</span></button>
    <span class="agent-ctl-note" data-f="ctlnote"></span>
  </div>`;
}

function anyRunning() {
  for (const a of state.agents.values()) if (a.status === "RUNNING") return true;
  return false;
}

/** Fetch what the running agents support (at most every 2 s; nothing is fetched while no agent runs). */
export function loadControls(force = false) {
  if (!anyRunning()) { options = {}; return Promise.resolve(options); }
  if (loading) return loading;
  if (!force && Date.now() - loadedAt < 2000) return Promise.resolve(options);
  loading = api("/api/agents/control")
    .then((r) => { options = r.agents || {}; loadedAt = Date.now(); return options; })
    .catch(() => options)
    .finally(() => { loading = null; });
  return loading;
}

/** Bring the buttons of one card element in line with the agent's state. */
export function updateControls(el, a) {
  const box = el.querySelector('[data-f="ctl"]');
  if (!box) return;
  const s = agentState(a, state.agents).state;
  const pause = box.querySelector(".agent-ctl-btn.pause");
  const stop = box.querySelector(".agent-ctl-btn.stop");
  const note = box.querySelector('[data-f="ctlnote"]');
  const live = s === "RUNNING" || s === "PAUSED";
  const opt = live ? options[a.id] : null;
  const acts = opt && opt.run_id === a.run_id ? opt.actions : null;
  const paused = s === "PAUSED";
  const wantPause = paused ? "resume" : "pause";
  pause.dataset.act = wantPause;
  pause.innerHTML = `${paused ? PLAY_ICON : PAUSE_ICON}<span>${paused ? "Resume" : "Pause"}</span>`;
  const mayEdit = canEdit();
  const working = busy.has(a.id);
  pause.disabled = !live || !mayEdit || working || !acts || !acts.includes(wantPause);
  stop.disabled = !live || !mayEdit || working || !acts || !acts.includes("stop");
  box.classList.toggle("live", live);
  let text = "";
  let title = "";
  if (!live) title = "Available while this agent runs";
  else if (!mayEdit) { text = "View only"; title = "Your account is view only"; }
  else if (!acts) text = "…";
  else if (!acts.length) { text = "Not available"; title = "This agent's current work cannot be paused or stopped from here"; }
  else if (!acts.includes(wantPause)) { text = `${paused ? "Resume" : "Pause"}: not available`; }
  note.textContent = text;
  box.title = title;
  if (stop.dataset.confirm && (stop.disabled || Date.now() > Number(stop.dataset.confirm))) {
    delete stop.dataset.confirm;
  }
  stop.classList.toggle("confirm", !!stop.dataset.confirm);
  if (stop.dataset.confirm) stop.querySelector("span").textContent = "Confirm stop";
}

/** Delegated click handling for every card inside root. Returns an unbind function. */
export function bindControls(root, refresh) {
  const onClick = async (e) => {
    const btn = e.target.closest(".agent-ctl-btn");
    if (!btn || btn.disabled || !root.contains(btn)) return;
    const card = btn.closest(".agent-card");
    const id = card?.dataset.agent;
    const a = id && state.agents.get(id);
    if (!a) return;
    const action = btn.dataset.act;
    if (action === "stop" && !btn.dataset.confirm) { // a second press within 4 s confirms (no browser dialogs)
      btn.dataset.confirm = String(Date.now() + 4000);
      updateControls(card, a);
      setTimeout(() => { updateControls(card, a); }, 4100);
      return;
    }
    delete btn.dataset.confirm;
    busy.add(id);
    updateControls(card, a);
    try {
      await api(`/api/agents/${encodeURIComponent(id)}/control`, { method: "POST", body: { action, run_id: a.run_id } });
      toast(`${a.name}: ${action === "stop" ? "stop requested" : action === "pause" ? "pause requested" : "resumed"}`, "success");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      busy.delete(id);
      await loadControls(true);
      if (refresh) refresh(); else updateControls(card, a);
    }
  };
  root.addEventListener("click", onClick);
  return () => root.removeEventListener("click", onClick);
}
