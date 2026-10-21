// Live runtime state fed by one Server-Sent Events connection.
// Pages subscribe to changes instead of polling.

import { api } from "./api.js";

const listeners = new Set();
export const state = {
  agents: new Map(), // agent_id -> agent (definition + runtime state)
  events: [], // recent events, oldest first (capped)
  unread: 0,
  connected: false,
  lastEventId: null,
};
const MAX_EVENTS = 400;

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit(change) {
  for (const fn of listeners) {
    try { fn(change); } catch (err) { console.error(err); }
  }
}

function applyEvent(ev) {
  state.events.push(ev);
  if (state.events.length > MAX_EVENTS) state.events.splice(0, state.events.length - MAX_EVENTS);
  state.lastEventId = ev.id;
  const agent = ev.agent_id && state.agents.get(ev.agent_id);
  if (agent) {
    if (ev.name === "agent.started") {
      Object.assign(agent, { status: "RUNNING", activity: ev.activity, progress: ev.progress, run_id: ev.run_id, started_at: ev.timestamp, finished_at: null });
    } else if (ev.name === "agent.completed" || ev.name === "agent.failed") {
      Object.assign(agent, {
        status: ev.name === "agent.completed" ? "PASSED" : "FAILED",
        activity: ev.activity || agent.activity,
        progress: ev.name === "agent.completed" ? 100 : agent.progress,
        finished_at: ev.timestamp,
      });
    } else if (agent.status === "RUNNING" && ev.run_id === agent.run_id) {
      if (ev.activity) agent.activity = ev.activity;
      if (ev.progress !== null && ev.progress !== undefined) agent.progress = ev.progress;
    }
    if (ev.source) agent.source = ev.source;
  }
  emit({ type: "event", event: ev, agent });
}

let source = null;

export async function start() {
  // Seed recent history once so the activity feed is not empty after a reload.
  try {
    const [{ agents }, { events }] = await Promise.all([api("/api/agents"), api("/api/events?limit=150")]);
    agents.forEach((a) => state.agents.set(a.id, a));
    state.events = events;
    state.lastEventId = events.length ? events[events.length - 1].id : null;
    emit({ type: "snapshot" });
  } catch (err) {
    console.error("Initial load failed", err);
  }
  connect();
}

function connect() {
  if (source) source.close();
  source = new EventSource("/api/events/stream");
  source.addEventListener("open", () => { state.connected = true; emit({ type: "connection" }); });
  source.addEventListener("error", () => { state.connected = false; emit({ type: "connection" }); });
  source.addEventListener("snapshot", (e) => {
    const msg = JSON.parse(e.data);
    msg.agents.forEach((a) => state.agents.set(a.id, a));
    state.unread = msg.unread;
    emit({ type: "snapshot" });
    emit({ type: "unread" });
  });
  source.addEventListener("event", (e) => {
    const { event } = JSON.parse(e.data);
    if (state.lastEventId !== null && event.id <= state.lastEventId) return; // replay duplicate
    applyEvent(event);
  });
  source.addEventListener("notification", (e) => {
    const msg = JSON.parse(e.data);
    state.unread = msg.unread;
    emit({ type: "notification", notification: msg.notification });
    emit({ type: "unread" });
  });
  source.addEventListener("unread", (e) => {
    state.unread = JSON.parse(e.data).unread;
    emit({ type: "unread" });
  });
}

export function stop() {
  if (source) source.close();
}
