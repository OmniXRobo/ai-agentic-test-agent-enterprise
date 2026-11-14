// SkyGuard Adaptive AI Agent: service worker.
//
// It is the browser "driver" of the shared agent core that runs in the local companion: for an execution it sends
// what the page shows (a DOM snapshot made by lib/agent_dom.js) and receives the next step (navigate, click, type,
// choose, check, wait). It acts only in a tab of a registered application, only on origins the person granted, and
// only for executions the person confirmed in the side panel. Page content can never start or authorise anything:
// the content script is injected on demand and only reports the person's own actions while observing is on.
import * as api from "./lib/api.js";

const DRIVES = new Map();      // runId -> { tabId, stop }
const OBSERVE = new Map();     // tabId -> { purpose: "learning" | "manual" | "watch", id, events: [] }
const WATCH_TIMERS = new Map(); // tabId -> pending flush timer (23.7.0 automatic learning)
let blinkTimer = null;

// ---- side panel ------------------------------------------------------------------------------------------------
chrome.runtime.onInstalled.addListener(async () => {
  if (chrome.sidePanel?.setPanelBehavior) await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});
// Browsers without the side panel API: open the same page in a small window instead.
chrome.action.onClicked.addListener(async () => {
  if (!chrome.sidePanel) await chrome.windows.create({ url: "sidepanel.html", type: "popup", width: 460, height: 760 });
});

// ---- helpers -----------------------------------------------------------------------------------------------------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const originOf = (url) => { try { const u = new URL(url); return `${u.protocol}//${u.host}`; } catch (e) { return ""; } };

function broadcast(msg) { chrome.runtime.sendMessage(msg).catch(() => {}); }

async function appOrigins() {
  const { applications } = await api.get("/v1/applications");
  const set = new Set();
  for (const a of applications) for (const o of a.origins || [originOf(a.base_url)]) set.add(o);
  return set;
}

async function allowedTab(tabId) {
  const tab = await chrome.tabs.get(tabId);
  const origin = originOf(tab.url || "");
  if (!origin.startsWith("http")) throw new Error("This tab is not a web application page.");
  if (!(await appOrigins()).has(origin)) throw new Error(`${origin} is not a registered application.`);
  if (!(await chrome.permissions.contains({ origins: [origin + "/*"] }))) throw new Error(`Access to ${origin} was not granted. Open Applications in the side panel and press Grant access.`);
  return tab;
}

async function waitForLoad(tabId, timeoutMs = 30000) {
  const end = Date.now() + timeoutMs;
  await sleep(250);
  while (Date.now() < end) {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    if (!tab) throw new Error("The tab was closed.");
    if (tab.status === "complete") return tab;
    await sleep(200);
  }
  return chrome.tabs.get(tabId);
}

async function inject(tabId) {
  const res = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ["lib/agent_dom.js"] });
  return res.map((r) => r.frameId);
}

async function snapshot(tabId) {
  await waitForLoad(tabId);
  const tab = await allowedTab(tabId);
  let frames;
  try { frames = await inject(tabId); } catch (e) { return { url: tab.url, title: tab.title, elements: [], tables: [], messages: [], auth: {}, error: String(e.message || e) }; }
  let merged = null;
  for (const frameId of frames.sort((a, b) => a - b)) {
    let snap;
    try {
      [{ result: snap }] = await chrome.scripting.executeScript({ target: { tabId, frameIds: [frameId] }, func: (p) => window.__sgAgent && window.__sgAgent.snapshot({ prefix: p }), args: [String(frameId)] });
    } catch (e) { continue; }
    if (!snap) continue;
    if (!merged) { merged = snap; continue; }
    merged.elements.push(...(snap.elements || []));
    merged.tables.push(...(snap.tables || []));
    merged.messages.push(...(snap.messages || []));
    merged.text = (merged.text || "") + "\n" + (snap.text || "").slice(0, 1000);
    for (const k of ["password_fields", "otp_fields"]) merged.auth[k] = (merged.auth[k] || 0) + ((snap.auth || {})[k] || 0);
    merged.auth.captcha = merged.auth.captcha || (snap.auth || {}).captcha;
  }
  const obs = OBSERVE.get(tabId);
  if (obs) await observe(tabId, true);
  return merged || { url: tab.url, title: tab.title, elements: [], tables: [], messages: [], auth: {} };
}

function frameOf(handle) { const p = String(handle || "").split(":")[0]; return /^\d+$/.test(p) ? Number(p) : 0; }

async function inFrame(tabId, handle, func, args) {
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId, frameIds: [frameOf(handle)] }, func, args });
  return result;
}

// Typing through Chrome's own input pipeline (chrome.debugger Input.insertText): optional, only when the person
// enabled trusted input in Options and granted the "debugger" permission. Never direct value assignment.
async function trustedType(tabId, action) {
  const { trusted_input } = await api.settings();
  if (!trusted_input || !(await chrome.permissions.contains({ permissions: ["debugger"] }))) return null;
  const focused = await inFrame(tabId, action.handle, (h) => { const el = window.__sgAgent.element(h); if (!el) return false; el.focus(); if (el.select) el.select(); return true; }, [action.handle]);
  if (!focused) return { ok: false, error: "STALE" };
  const target = { tabId };
  try {
    await chrome.debugger.attach(target, "1.3");
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", { type: "keyDown", key: "Delete", code: "Delete", windowsVirtualKeyCode: 46 });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", { type: "keyUp", key: "Delete", code: "Delete", windowsVirtualKeyCode: 46 });
    await chrome.debugger.sendCommand(target, "Input.insertText", { text: String(action.value ?? "") });
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
  return inFrame(tabId, action.handle, (h, v) => { const el = window.__sgAgent.element(h); if (!el) return { ok: false, error: "STALE" }; el.dispatchEvent(new Event("change", { bubbles: true })); el.blur(); return { ok: el.value === v || el.type === "password" && el.value.length === v.length, invalid: el.validity ? !el.validity.valid : false, validation: (el.validationMessage || "").slice(0, 120) }; }, [action.handle, String(action.value ?? "")]);
}

async function perform(tabId, action) {
  const t = action.type;
  try {
    if (t === "snapshot") { await sleep(Math.min(5000, action.wait_ms || 300)); return { ok: true }; }
    if (t === "navigate") {
      const origin = originOf(action.url);
      if (!(await appOrigins()).has(origin)) return { ok: false, error: "URL_REFUSED", message: "Not a registered application address." };
      if (!(await chrome.permissions.contains({ origins: [origin + "/*"] }))) return { ok: false, error: "NO_PERMISSION", message: `Access to ${origin} was not granted.` };
      await chrome.tabs.update(tabId, { url: action.url });
      await waitForLoad(tabId);
      return { ok: true };
    }
    if (t === "switch_context") return { ok: false, error: "UNSUPPORTED", message: "Separate browser contexts are not available in the extension; the agent signs out and in instead." };
    if (!["click", "fill", "select", "check"].includes(t)) return { ok: false, error: "UNKNOWN_ACTION" };
    if (action.secret) {
      // a saved password is typed only into a password field of a registered application's sign-in page
      const ok = await inFrame(tabId, action.handle, (h) => { const el = window.__sgAgent.element(h); return !!el && el.type === "password"; }, [action.handle]);
      if (!ok) return { ok: false, error: "REFUSED", message: "The password field could not be confirmed; nothing was typed." };
    }
    await inFrame(tabId, action.handle, (h) => window.__sgAgent.highlight(h, true), [action.handle]).catch(() => {});
    let result = await inFrame(tabId, action.handle, (a) => window.__sgAgent.act(a), [{ type: t, handle: action.handle, value: action.value }]);
    if (result && result.error === "TYPING_NOT_ACCEPTED") result = (await trustedType(tabId, action)) || result;
    if (t === "click") await waitForLoad(tabId).catch(() => {});
    return result || { ok: false, error: "NO_RESULT" };
  } catch (e) {
    return { ok: false, error: /closed|No tab/i.test(String(e.message)) ? "TAB_CLOSED" : "BROWSER_ERROR", message: String(e.message || e).slice(0, 200) };
  }
}

// ---- the person's own actions (guided learning, manual intervention) ----------------------------------------------
async function observe(tabId, on) {
  try {
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ["lib/agent_dom.js"] });
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: (enable) => enable
      ? window.__sgAgent.observe((e) => chrome.runtime.sendMessage({ type: "sg-observed", event: e }))
      : window.__sgAgent.stopObserving(), args: [on] });
  } catch (e) { /* tab navigating; re-armed on the next load */ }
}

async function banner(tabId, text) {
  try { await chrome.scripting.executeScript({ target: { tabId }, func: (t) => window.__sgAgent && window.__sgAgent.banner(t), args: [text || ""] }); } catch (e) { /* ignore */ }
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return false; // only this extension's own pages and injected scripts
  if (msg.type === "sg-observed" && sender.tab) {
    const obs = OBSERVE.get(sender.tab.id);
    if (obs && msg.event && typeof msg.event === "object") {
      obs.events.push(msg.event);
      if (obs.purpose === "learning" && obs.events.length >= 5) flushLearning(sender.tab.id).catch(() => {});
      if (obs.purpose === "watch") scheduleWatchFlush(sender.tab.id, ["submit", "click"].includes(msg.event.kind) ? 900 : obs.events.length >= 8 ? 50 : 4000);
    }
    return false;
  }
  handle(msg).then(reply, (e) => reply({ ok: false, error: String(e.message || e), code: e.code }));
  return true;
});

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "complete" && OBSERVE.has(tabId)) {
    observe(tabId, true);
    const o = OBSERVE.get(tabId);
    if (o.banner) banner(tabId, o.banner);
    if (o.purpose === "watch") scheduleWatchFlush(tabId, 700);
  } else if (info.status === "complete") {
    maybeWatch(tabId).catch(() => {});
  }
});

// ---- 23.7.0: learn automatically while the person browses ------------------------------------------------------------
// With "Learn automatically while I browse" on (Options), every tab of a registered application whose access was
// granted is observed: the person's own clicks, choices and entries (passwords and codes only as "[secret]") and what
// the page shows afterwards go to the companion, which works out screens, flows, statuses, rules and roles itself.
// Nothing is clicked or typed by the extension for this, and tabs the agent is driving are never observed.
let appCache = { at: 0, apps: [] };
async function registeredApp(url) {
  if (Date.now() - appCache.at > 30000) {
    try { appCache = { at: Date.now(), apps: (await api.get("/v1/applications")).applications || [] }; } catch (e) { return null; }
  }
  const origin = originOf(url || "");
  return appCache.apps.find((a) => (a.origins || [originOf(a.base_url)]).includes(origin)) || null;
}

async function maybeWatch(tabId) {
  const { auto_learn } = await api.settings();
  if (!auto_learn || OBSERVE.has(tabId) || [...DRIVES.values()].some((d) => d.tabId === tabId)) return;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || !/^https?:/.test(tab.url || "")) return;
  const app = await registeredApp(tab.url);
  if (!app || !(await chrome.permissions.contains({ origins: [originOf(tab.url) + "/*"] }))) return;
  const key = "watch:" + tabId;
  const saved = (await chrome.storage.session.get(key))[key];
  const id = saved && saved.appId === app.id ? saved.id : `ext-${app.id.slice(-6)}-${tabId}-${Date.now().toString(36)}`;
  await chrome.storage.session.set({ [key]: { id, appId: app.id } });
  OBSERVE.set(tabId, { purpose: "watch", id, appId: app.id, events: [] });
  await observe(tabId, true);
  scheduleWatchFlush(tabId, 700);
}

function scheduleWatchFlush(tabId, ms) {
  clearTimeout(WATCH_TIMERS.get(tabId));
  WATCH_TIMERS.set(tabId, setTimeout(() => { WATCH_TIMERS.delete(tabId); flushWatch(tabId).catch(() => {}); }, ms));
}

async function flushWatch(tabId, final = false) {
  const obs = OBSERVE.get(tabId);
  if (!obs || obs.purpose !== "watch") return;
  const events = obs.events.splice(0, obs.events.length);
  let snap;
  if (!final) {
    try { snap = await snapshot(tabId); } catch (e) { snap = undefined; }
    if (snap && !(await registeredApp(snap.url))) { snap = undefined; await stopWatch(tabId); }
  }
  if (!events.length && !snap && !final) return;
  const r = await api.post("/v1/observe", { application_id: obs.appId, session_id: obs.id, events, snapshot: snap, final: final || undefined });
  if (r && r.session_id) obs.id = r.session_id;
  broadcast({ type: "learned", appId: obs.appId, digested: !!(r && r.digested), changes: (r && r.changes) || [] });
}

async function stopWatch(tabId, final = false) {
  const obs = OBSERVE.get(tabId);
  if (!obs || obs.purpose !== "watch") return;
  if (final) await flushWatch(tabId, true).catch(() => {});
  OBSERVE.delete(tabId);
  clearTimeout(WATCH_TIMERS.get(tabId));
  await observe(tabId, false);
}

chrome.tabs.onRemoved.addListener((tabId) => {
  const obs = OBSERVE.get(tabId);
  if (obs && obs.purpose === "watch") stopWatch(tabId, true).catch(() => {});
  chrome.storage.session.remove("watch:" + tabId).catch(() => {});
});

async function flushLearning(tabId) {
  const obs = OBSERVE.get(tabId);
  if (!obs || obs.purpose !== "learning" || !obs.events.length) return;
  const events = obs.events.splice(0, obs.events.length);
  let snap = null;
  try { snap = await snapshot(tabId); } catch (e) { snap = null; }
  await api.post(`/v1/learning/${obs.id}/events`, { events, snapshot: snap || undefined });
}

// ---- attention: notification, blinking badge, page banner ---------------------------------------------------------
function attention(on, title, text) {
  if (on) {
    chrome.notifications.create("sg-attention", { type: "basic", iconUrl: "icons/icon128.png", title: title || "SkyGuard Agent needs you", message: (text || "").slice(0, 250), priority: 2, requireInteraction: true });
    let lit = false;
    clearInterval(blinkTimer);
    blinkTimer = setInterval(() => { lit = !lit; chrome.action.setBadgeText({ text: lit ? "!" : "" }); chrome.action.setBadgeBackgroundColor({ color: "#f59e0b" }); }, 700);
  } else {
    clearInterval(blinkTimer);
    blinkTimer = null;
    chrome.action.setBadgeText({ text: "" });
    chrome.notifications.clear("sg-attention");
  }
}
chrome.notifications.onClicked.addListener(async () => {
  const [win] = await chrome.windows.getAll({ windowTypes: ["normal"] });
  if (win && chrome.sidePanel?.open) chrome.sidePanel.open({ windowId: win.id }).catch(() => {});
});

// ---- execution loop ----------------------------------------------------------------------------------------------
async function drive(runId, tabId) {
  if (DRIVES.has(runId)) return { already: true };
  await stopWatch(tabId, true); // the agent's own actions are never learned
  const state = { tabId, stop: false };
  DRIVES.set(runId, state);
  await chrome.storage.session.set({ ["drive:" + runId]: { tabId } });
  const caps = async () => ({ trusted_input: (await api.settings()).trusted_input });
  const keepAlive = setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);
  try {
    let obs = { snapshot: await snapshot(tabId), capabilities: await caps() };
    for (let steps = 0; steps < 20000 && !state.stop; steps++) {
      const d = await api.post(`/v1/runs/${runId}/next`, { observation: obs });
      broadcast({ type: "run-status", runId, status: d.status });
      if (d.done) {
        attention(false);
        await banner(tabId, "");
        const st = d.done.status;
        chrome.notifications.create("sg-done-" + runId, { type: "basic", iconUrl: "icons/icon128.png", title: `Execution ${st}`, message: (d.done.message || "").slice(0, 250) });
        broadcast({ type: "run-done", runId, done: d.done });
        return d.done;
      }
      if (d.wait) {
        if (d.wait === "paused") {
          const pause = d.pause || {};
          OBSERVE.set(tabId, { purpose: "manual", id: runId, events: [], banner: "SkyGuard Agent paused: " + (pause.message || "your action is needed") });
          await observe(tabId, true);
          await banner(tabId, OBSERVE.get(tabId).banner);
          attention(true, "Your action is needed", pause.message || "The execution is paused.");
          broadcast({ type: "run-paused", runId, pause });
        } else if (d.wait === "awaiting_confirmation") {
          attention(true, "Preview ready", "Review the preview in the side panel and confirm to continue.");
        }
        while (!state.stop) {
          await sleep(1200);
          const v = await api.get(`/v1/runs/${runId}`);
          if (!["paused", "awaiting_confirmation", "planning"].includes(v.run.status)) break;
        }
        if (OBSERVE.get(tabId)?.purpose === "manual") { OBSERVE.delete(tabId); await observe(tabId, false); await banner(tabId, ""); }
        attention(false);
        obs = { snapshot: await snapshot(tabId), capabilities: await caps() };
        continue;
      }
      const result = await perform(tabId, d.action);
      obs = { snapshot: await snapshot(tabId).catch((e) => ({ url: "", elements: [], tables: [], messages: [], auth: {}, error: String(e.message) })), result, capabilities: await caps() };
    }
    return { status: "detached" };
  } catch (e) {
    broadcast({ type: "run-error", runId, error: String(e.message || e) });
    attention(true, "Execution interrupted", String(e.message || e));
    return { status: "error", error: String(e.message || e) };
  } finally {
    clearInterval(keepAlive);
    DRIVES.delete(runId);
    await chrome.storage.session.remove("drive:" + runId);
  }
}

// After the service worker restarts, executions that were being driven continue from the companion's checkpoint
// (the agent looks at the application before repeating any submit).
async function resumeDrives() {
  const all = await chrome.storage.session.get(null);
  for (const [k, v] of Object.entries(all)) if (k.startsWith("drive:") && v?.tabId) drive(k.slice(6), v.tabId);
}
resumeDrives().catch(() => {});

async function tabFor(appId, preferTabId) {
  const { applications } = await api.get("/v1/applications");
  const app = applications.find((a) => a.id === appId);
  if (!app) throw new Error("Unknown application.");
  const origins = new Set(app.origins || [originOf(app.base_url)]);
  if (preferTabId) {
    const tab = await chrome.tabs.get(preferTabId).catch(() => null);
    if (tab && origins.has(originOf(tab.url || ""))) return tab.id;
  }
  const [active] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (active && origins.has(originOf(active.url || ""))) return active.id;
  const tab = await chrome.tabs.create({ url: app.base_url, active: true });
  await waitForLoad(tab.id);
  return tab.id;
}

async function handle(msg) {
  switch (msg.type) {
    case "drive": {
      const run = (await api.get(`/v1/runs/${msg.runId}`)).run;
      const tabId = await tabFor(run.application_id, msg.tabId);
      await allowedTab(tabId);
      drive(msg.runId, tabId);
      return { ok: true, tabId };
    }
    case "stop-drive": {
      const d = DRIVES.get(msg.runId);
      if (d) d.stop = true;
      return { ok: true };
    }
    case "manual-events": {
      const d = DRIVES.get(msg.runId);
      const obs = d ? OBSERVE.get(d.tabId) : null;
      return { ok: true, events: obs && obs.purpose === "manual" ? obs.events.slice(-50) : [] };
    }
    case "learn-start": {
      const tabId = await tabFor(msg.appId, msg.tabId);
      await allowedTab(tabId);
      await stopWatch(tabId, true);
      OBSERVE.set(tabId, { purpose: "learning", id: msg.sessionId, events: [], banner: "SkyGuard Agent is learning: perform the task normally, then press Finish in the side panel." });
      await observe(tabId, true);
      await banner(tabId, OBSERVE.get(tabId).banner);
      return { ok: true, tabId };
    }
    case "learn-stop": {
      for (const [tabId, o] of OBSERVE) if (o.purpose === "learning" && o.id === msg.sessionId) {
        await flushLearning(tabId).catch(() => {});
        OBSERVE.delete(tabId);
        await observe(tabId, false);
        await banner(tabId, "");
      }
      return { ok: true };
    }
    case "auto-learn": {
      // Options turned automatic learning on or off: apply it to the open tabs at once
      if (msg.on) { for (const t of await chrome.tabs.query({})) maybeWatch(t.id).catch(() => {}); }
      else { for (const [tabId, o] of [...OBSERVE]) if (o.purpose === "watch") await stopWatch(tabId, true); }
      return { ok: true };
    }
    case "watching": return { ok: true, tabs: [...OBSERVE].filter(([, o]) => o.purpose === "watch").map(([tabId, o]) => ({ tabId, appId: o.appId })) };
    case "acknowledge": attention(false); return { ok: true };
    case "drives": return { ok: true, runs: [...DRIVES.keys()] };
    default: return { ok: false, error: "unknown request" };
  }
}
