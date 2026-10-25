import { api } from "./api.js";
import { state, subscribe, start, onTick, loadHealth } from "./store.js";
import { esc, toast, dateTimeOf } from "./util.js";
import * as dashboard from "./pages/dashboard.js";
import * as workflow from "./pages/workflow.js";
import * as notifications from "./pages/notifications.js";
import * as history from "./pages/history.js";
import * as settings from "./pages/settings.js";
import * as tfsSettings from "./pages/tfs-settings.js";
import * as testPlans from "./pages/testplans.js";
import { workItemPage } from "./pages/workitems.js";
import * as aiSettings from "./pages/ai-settings.js";
import * as testDesign from "./pages/test-design.js";
import * as testData from "./pages/test-data.js";
import * as scriptStudio from "./pages/script-studio.js";
import { aiAutomation, guidedAutomation, hybridAutomation, discovery } from "./pages/automation.js";
import * as appCredentials from "./pages/app-credentials.js";
import { perfPlans, perfLoad, perfStress, perfSpike, perfSoak, perfResults } from "./pages/performance.js";
import { defectReview, perfDefects } from "./pages/defect-review.js";
import * as voicePage from "./pages/voice.js";
import * as reports from "./pages/reports.js";
import * as analytics from "./pages/analytics.js";
import * as commandCenter from "./pages/command-center.js";
import { voice, onVoice, listen, loadSettings, agentName } from "./voice.js";

// route -> { title, page }
const NAV = [
  ["Mission Control", [
    ["command-center", "AI Voice Command Center", commandCenter],
    ["dashboard", "Dashboard", dashboard],
    ["workflow", "Agent Workflow", workflow],
    ["notifications", "Notifications", notifications],
    ["history", "Execution History", history],
  ]],
  ["Test Design", [
    ["tfs-explorer", "TFS Explorer", workItemPage({ category: null, explorer: true, createLabel: "New work item" })],
    ["user-stories", "User Stories", workItemPage({ category: "requirement", createLabel: "New user story" })],
    ["test-design", "AI Test Design", testDesign],
    ["acceptance-criteria", "Acceptance Criteria", workItemPage({ category: "requirement", focus: "acceptance_criteria" })],
    ["test-plans", "Test Plans", testPlans],
    ["test-suites", "Test Suites", testPlans],
    ["test-cases", "Test Cases", workItemPage({ category: "testcase", createLabel: "New test case" })],
    ["test-data", "Test Data", testData],
  ]],
  ["Automation Studio", [
    ["ai-automation", "AI-Driven Automation", scriptStudio],
    ["guided-automation", "Guided Browser Automation", guidedAutomation],
    ["discovery", "Application Discovery & Update", discovery],
    ["hybrid-automation", "Hybrid Test Automation", hybridAutomation],
  ]],
  ["Performance Testing", [
    ["perf-plans", "Performance Test Plans", perfPlans],
    ["perf-load", "Load Tests", perfLoad],
    ["perf-stress", "Stress Tests", perfStress],
    ["perf-spike", "Spike Tests", perfSpike],
    ["perf-soak", "Soak Tests", perfSoak],
    ["perf-results", "Performance Results", perfResults],
    ["perf-defects", "Performance Defects", perfDefects],
  ]],
  ["Quality", [
    ["defects", "Defects", workItemPage({ category: "bug", createLabel: "New defect" })],
    ["defect-review", "Defect Review", defectReview],
    ["reports", "Reports", reports],
    ["analytics", "Analytics", analytics],
  ]],
  ["Settings", [
    ["settings", "Preferences", settings],
    ["tfs-settings", "TFS Connection", tfsSettings],
    ["ai-settings", "AI Runtime", aiSettings],
    ["app-credentials", "Application Credentials", appCredentials],
    ["voice", "Voice & Command Agent", voicePage],
  ]],
];

const ROUTES = new Map();
for (const [, items] of NAV) {
  for (const [route, title, page] of items) ROUTES.set(route, { route, title, page });
}
// The earlier single-script AI generation page stays available (and opens its scripts) without a nav entry.
ROUTES.set("ai-automation-classic", { route: "ai-automation", title: "AI-Driven Automation (single script)", page: aiAutomation });

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = NAV.map(([group, items]) => `
    <div class="nav-group">${esc(group)}</div>
    ${items.map(([route, title]) => `
      <a class="nav-link" href="#/${route}" data-route="${route}"><span>${esc(title)}</span></a>`).join("")}
  `).join("");
}

let current = null;

async function navigate() {
  const route = (location.hash.replace(/^#\/?/, "").split("?")[0]) || "dashboard";
  const entry = ROUTES.get(route) || ROUTES.get("dashboard");
  if (current?.page.unmount) current.page.unmount();
  current = entry;
  document.querySelectorAll(".nav-link").forEach((a) => a.classList.toggle("active", a.dataset.route === entry.route));
  document.getElementById("pageTitle").textContent = entry.title;
  document.title = `${entry.title} · QA Enterprise AI`;
  document.getElementById("sidebar").classList.remove("open");
  const view = document.getElementById("view");
  view.innerHTML = "";
  try {
    await entry.page.mount(view, entry);
  } catch (err) {
    view.innerHTML = `<div class="card"><h2>Could not load this page</h2><p class="muted">${esc(err.message)}</p></div>`;
  }
}

// ---- theme ----
function setTheme(theme, persist = true) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("qa.theme", theme); } catch { /* storage may be blocked */ }
  if (persist) api("/api/settings", { method: "PATCH", body: { ui: { theme } } }).catch(() => {});
}

async function initTheme() {
  document.getElementById("themeBtn").addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
  let local = null;
  try { local = localStorage.getItem("qa.theme"); } catch { /* ignore */ }
  if (!local) {
    try {
      const { settings } = await api("/api/settings");
      setTheme(settings.ui.theme || "dark", false);
    } catch { /* keep default */ }
  }
}

// ---- notification bell ----
function renderBadge() {
  const badge = document.getElementById("unreadBadge");
  badge.hidden = state.unread === 0;
  badge.textContent = state.unread > 99 ? "99+" : String(state.unread);
}

async function openBell() {
  const pop = document.getElementById("bellPopover");
  const list = document.getElementById("bellList");
  pop.hidden = false;
  document.getElementById("bellBtn").setAttribute("aria-expanded", "true");
  list.innerHTML = `<li class="muted">Loading…</li>`;
  try {
    const { notifications: items } = await api("/api/notifications?limit=15");
    list.innerHTML = items.length ? items.map((n) => `
      <li class="${n.read ? "" : "unread"}">
        <span class="note-dot ${esc(n.severity)}"></span>
        <div><div class="note-title">${esc(n.title)}</div><div>${esc(n.message || "")}</div>
        <div class="note-time">${esc(dateTimeOf(n.timestamp))}</div></div>
      </li>`).join("") : `<li class="muted">No notifications yet.</li>`;
  } catch (err) {
    list.innerHTML = `<li class="muted">${esc(err.message)}</li>`;
  }
}

function initBell() {
  const btn = document.getElementById("bellBtn");
  const pop = document.getElementById("bellPopover");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (pop.hidden) openBell(); else { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); }
  });
  document.addEventListener("click", (e) => {
    if (!pop.hidden && !pop.contains(e.target)) { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); }
  });
  document.getElementById("markAllRead").addEventListener("click", async () => {
    try { await api("/api/notifications/read", { method: "POST", body: {} }); openBell(); }
    catch (err) { toast(err.message, "error"); }
  });
}

function initConnection() {
  const el = document.getElementById("connState");
  const render = () => {
    el.textContent = state.connected ? "Live" : "Reconnecting";
    el.className = `conn ${state.connected ? "live" : "down"}`;
  };
  subscribe((c) => {
    if (c.type === "connection") render();
    if (c.type === "unread") renderBadge();
    if (c.type === "notification") toast(`${c.notification.title}${c.notification.message ? ": " + c.notification.message : ""}`, c.notification.severity === "error" ? "error" : c.notification.severity);
  });
}

// ---- header microphone: speak a command from any page ----
function initVoice() {
  const btn = document.getElementById("voiceBtn");
  const pop = document.getElementById("voicePop");
  let hideTimer = null;
  const show = () => {
    const last = voice.log.slice(-3);
    const status = { listening: "Listening… speak now", transcribing: "Transcribing…", thinking: "Understanding…", executing: "Executing…" }[voice.status] || voice.error || "";
    pop.innerHTML = `${status ? `<div class="vstatus">${esc(status)}</div>` : ""}${last.map((m) => `<div class="vmsg ${m.who}"><span class="vwho">${m.who === "you" ? "You" : esc(agentName())}</span><span class="vtext">${esc(m.text)}</span></div>`).join("")}
      <a class="popover-foot" href="#/command-center">Open Command Center</a>`;
    btn.classList.toggle("listening", voice.status === "listening");
    if (location.hash.startsWith("#/command-center")) { pop.hidden = true; return; } // the page shows the conversation
    pop.hidden = !status && !last.length;
    clearTimeout(hideTimer);
    if (!["listening", "transcribing", "thinking"].includes(voice.status)) hideTimer = setTimeout(() => { pop.hidden = true; }, 9000);
  };
  onVoice(show);
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      const s = await loadSettings();
      if (s.stt_engine === "off" || (s.stt_engine === "local" && !s.stt_url)) { location.hash = "#/voice"; toast("Set up speech recognition first, or type a command here.", "info"); return; }
      await listen();
    } catch (err) { show(); toast(err.message, "error"); }
  });
  document.addEventListener("click", (e) => { if (!pop.hidden && !pop.contains(e.target)) pop.hidden = true; });
  window.addEventListener("hashchange", () => { if (location.hash.startsWith("#/command-center")) pop.hidden = true; });
}

// ---- command-center status strip, Windows clock and agent name ----
const STRIP_ORDER = ["tfs", "ai", "playwright", "speech"];
function renderStrip() {
  const el = document.getElementById("sysStrip");
  const items = state.health?.items || [];
  const byKey = Object.fromEntries(items.map((i) => [i.key, i]));
  const mic = voice.status === "listening" ? { status: "RUNNING", value: "Live" }
    : voice.micError ? { status: "FAIL", value: "Blocked" } : { status: voice.settings?.voice_enabled === false ? "NOT_CONFIGURED" : "PASS", value: voice.settings?.voice_enabled === false ? "Off" : "Standby" };
  el.innerHTML = STRIP_ORDER.map((k) => byKey[k]).filter(Boolean).map((i) =>
    `<span class="sys-chip" title="${esc(i.label + ": " + i.detail)}"><span class="lamp ${esc(i.status)}"></span>${esc(i.label)} <b>${esc(i.value)}</b></span>`).join("")
    + `<span class="sys-chip" title="${esc(voice.micError || "Microphone")}"><span class="lamp ${mic.status}"></span>Mic <b>${mic.value}</b></span>`
    + `<button class="sys-chip" id="stripRefresh" title="Check again now" style="cursor:pointer">&#8635;</button>`;
  el.querySelector("#stripRefresh").addEventListener("click", () => loadHealth(true));
}

function initStrip() {
  let lastForced = 0, ticks = 0;
  const clock = document.getElementById("sysClock");
  const showClock = () => {
    const d = new Date();
    clock.textContent = `${d.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" })}  ${d.toLocaleTimeString([], { hour12: false })}`;
  };
  showClock();
  onTick(() => {
    showClock();
    if (++ticks % 300 === 0) loadHealth(); // the runtime answers from its 5-minute cache
  });
  subscribe((c) => {
    if (c.type === "health") renderStrip();
    // A real failure reported by TFS or the AI re-checks at once (at most once a minute).
    if (c.type === "event" && (c.event.name === "tfs.unavailable" || c.event.name === "ai.unavailable") && Date.now() - lastForced > 60000) {
      lastForced = Date.now();
      loadHealth(true);
    }
  });
  onVoice(() => renderStrip());
  renderStrip();
  loadHealth();
  loadSettings().then(() => {
    document.getElementById("agentNameFoot").textContent = agentName();
    renderStrip();
  }).catch(() => {});
  api("/api/health").then((h) => { document.getElementById("appVersion").textContent = `Version ${h.version}`; }).catch(() => {});
}

document.getElementById("menuBtn").addEventListener("click", () => document.getElementById("sidebar").classList.toggle("open"));
renderNav();
initTheme();
initBell();
initConnection();
initVoice();
initStrip();
window.addEventListener("hashchange", navigate);
start().then(navigate);
