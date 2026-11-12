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
import * as testSuites from "./pages/test-suites.js";
import * as testCases from "./pages/test-cases.js";
import * as scriptStudio from "./pages/script-studio.js";
import { aiAutomation, guidedAutomation, hybridAutomation, discovery } from "./pages/automation.js";
import { scheduledRuns, emailSettings } from "./pages/schedules.js";
import * as appCredentials from "./pages/app-credentials.js";
import { perfScenarios, perfPlans, perfLoad, perfStress, perfSpike, perfSoak, perfResults } from "./pages/performance.js";
import { defectReview, perfDefects } from "./pages/defect-review.js";
import { perfData } from "./pages/perf-data.js";
import { perfLive } from "./pages/perf-live.js";
import { perfSchedules } from "./pages/perf-schedules.js";
import * as voicePage from "./pages/voice.js";
import * as reports from "./pages/reports.js";
import * as analytics from "./pages/analytics.js";
import * as commandCenter from "./pages/command-center.js";
import * as setupPage from "./pages/setup.js";
import { voice, onVoice, loadSettings, agentName } from "./voice.js";
import { initVoiceAgent } from "./voice-agent.js";
import { productName, loadBrand, onBrand } from "./brand.js";
import * as usersPage from "./pages/users.js";
import { ensureSignedIn, initUserMenu, isAdmin, canEdit } from "./auth.js";
import { cmpPages } from "./pages/comparison.js";
import { tfsIntegration, browserManager, auraMemory } from "./pages/preferences-final.js";

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
    ["test-suites", "Test Suites", testSuites],
    ["test-cases", "Test Cases", testCases],
    ["test-data", "Test Data", testData],
  ]],
  ["Automation Studio", [
    ["ai-automation", "AI-Driven Automation", scriptStudio],
    ["guided-automation", "Guided Browser Automation", guidedAutomation],
    ["discovery", "Application Discovery & Update", discovery],
    ["hybrid-automation", "Hybrid Test Automation", hybridAutomation],
    ["scheduled-runs", "Scheduled Runs", scheduledRuns],
  ]],
  ["Performance Testing", [
    ["perf-scenarios", "Performance Scenarios", perfScenarios],
    ["perf-plans", "Performance Test Plans", perfPlans],
    ["perf-data", "Performance Test Data", perfData],
    ["perf-load", "Load Tests", perfLoad],
    ["perf-stress", "Stress Tests", perfStress],
    ["perf-spike", "Spike Tests", perfSpike],
    ["perf-soak", "Soak Tests", perfSoak],
    ["perf-live", "Live Dashboard", perfLive],
    ["perf-schedules", "Performance Schedules", perfSchedules],
    ["perf-results", "Performance Results", perfResults],
    ["perf-defects", "Performance Defects", perfDefects],
  ]],
  ["Application Data Comparison Agent", cmpPages],
  ["Quality", [
    ["defects", "Defects", workItemPage({ category: "bug", createLabel: "New defect" })],
    ["defect-review", "Defect Review", defectReview],
    ["reports", "Reports", reports],
    ["analytics", "Analytics", analytics],
  ]],
  ["Settings", [
    ["setup", "First-Run Setup", setupPage],
    ["settings", "Preferences", settings],
    ["tfs-settings", "TFS Connection", tfsSettings],
    ["tfs-integration", "TFS Integration (fields)", tfsIntegration],
    ["browser-manager", "Browser Manager", browserManager],
    ["aura", "AURA Learning Memory", auraMemory],
    ["ai-settings", "AI Runtime", aiSettings],
    ["app-credentials", "Application Credentials", appCredentials],
    ["email-settings", "Outlook Email", emailSettings],
    ["voice", "Voice & Command Agent", voicePage],
    ["users", "Users & Network Access", usersPage],
  ]],
];

const ROUTES = new Map();
for (const [, items] of NAV) {
  for (const [route, title, page] of items) ROUTES.set(route, { route, title, page });
}
// The earlier single-script AI generation page stays available (and opens its scripts) without a nav entry.
ROUTES.set("ai-automation-classic", { route: "ai-automation", title: "AI-Driven Automation (single script)", page: aiAutomation });

// 23.3.34: view-only accounts do not see the Settings pages; only an admin sees Users & Network Access.
function visibleNav() {
  return NAV.map(([group, items]) => [group, items.filter(([route]) =>
    route === "users" ? isAdmin() : group === "Settings" ? canEdit() : true)]).filter(([, items]) => items.length);
}

function allowed(route) {
  return visibleNav().some(([, items]) => items.some(([r]) => r === route)) || route === "ai-automation-classic";
}

// Final patch: collapsible sidebar. Every entry has an icon; when the sidebar is collapsed only icons show and the
// name appears as a tooltip. The choice is kept in this browser and in Preferences (ui.sidebar_collapsed).
const I = {
  mic: "M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-3.1A7 7 0 0 0 19 11Z",
  grid: "M3 3h8v8H3Zm10 0h8v8h-8ZM3 13h8v8H3Zm10 0h8v8h-8Z",
  flow: "M5 3a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm0 6v6h6v-2H7V9Zm12-6a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm-1 6v6h-3v2h5V9ZM12 17a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z",
  bell: "M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22Zm7-6V11a7 7 0 0 0-14 0v5l-2 2v1h18v-1Z",
  clock: "M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2Zm1 11h-5v-2h3V6h2Z",
  doc: "M6 2h9l5 5v15H6Zm8 1.5V8h4.5ZM8 12h8v2H8Zm0 4h8v2H8Z",
  check: "M4 4h16v16H4Zm2 2v12h12V6Zm3.5 7.5 2 2 4-5 1.5 1.2-5.4 6.6-3.5-3.4Z",
  bot: "M11 2h2v3h5a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5Zm-2 8a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Zm6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z",
  play: "M8 5v14l11-7Z",
  gauge: "M12 4a10 10 0 0 0-10 10h2a8 8 0 0 1 16 0h2A10 10 0 0 0 12 4Zm4.2 4.4-5.6 4.4a2 2 0 1 0 2.8 2.8Z",
  bug: "M8 6a4 4 0 0 1 8 0h-8Zm-3 3h14v2h-3v2h3v2h-3a4 4 0 0 1-8 0H5v-2h3v-2H5Z",
  chart: "M4 20h16v2H2V2h2Zm3-3V10h3v7Zm5 0V6h3v11Zm5 0v-5h3v5Z",
  compare: "M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h5v2h2V1h-2Zm0 15H5l5-6Zm9-15h-5v2h5v13l-5-6v9h5a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2Z",
  gear: "M19.4 13a7.5 7.5 0 0 0 0-2l2.1-1.6-2-3.5-2.5 1a7.4 7.4 0 0 0-1.7-1L15 3h-4l-.4 2.6a7.4 7.4 0 0 0-1.7 1l-2.5-1-2 3.5L6.6 11a7.5 7.5 0 0 0 0 2l-2.1 1.6 2 3.5 2.5-1a7.4 7.4 0 0 0 1.7 1L11 21h4l.4-2.6a7.4 7.4 0 0 0 1.7-1l2.5 1 2-3.5ZM13 15.5a3.5 3.5 0 1 1 3.5-3.5 3.5 3.5 0 0 1-3.5 3.5Z",
  key: "M7 14a3 3 0 1 1 3-3 3 3 0 0 1-3 3Zm5.6-5A6 6 0 1 0 12.6 13H15v3h3v-3h3V9Z",
  user: "M12 12a5 5 0 1 0-5-5 5 5 0 0 0 5 5Zm0 2c-4 0-8 2-8 5v2h16v-2c0-3-4-5-8-5Z",
  data: "M12 2C7 2 4 3.8 4 6v12c0 2.2 3 4 8 4s8-1.8 8-4V6c0-2.2-3-4-8-4Zm0 2c3.9 0 6 1.2 6 2s-2.1 2-6 2-6-1.2-6-2 2.1-2 6-2Z",
  search: "M10 2a8 8 0 1 0 4.9 14.3l5.4 5.4 1.4-1.4-5.4-5.4A8 8 0 0 0 10 2Zm0 2a6 6 0 1 1-6 6 6 6 0 0 1 6-6Z",
};
const ICON_BY_ROUTE = [
  [/command-center|voice/, "mic"], [/^dashboard|cmp-dashboard|analytics/, "grid"], [/workflow/, "flow"], [/notif/, "bell"],
  [/history|schedul|perf-schedules/, "clock"], [/test-data|perf-data|cmp-import|cmp-extract/, "data"],
  [/discovery|cmp-discovery|explorer|investigation/, "search"], [/test-|stories|acceptance|plans|suites/, "check"],
  [/automation/, "bot"], [/perf-live|perf-load|perf-stress|perf-spike|perf-soak/, "gauge"], [/perf-/, "play"],
  [/defect/, "bug"], [/report|results|aggregate/, "chart"], [/^cmp-/, "compare"], [/credential/, "key"], [/users/, "user"],
  [/aura/, "data"], [/browser-manager/, "grid"], [/tfs-integration/, "check"], [/setup|settings|email/, "gear"],
];
function iconFor(route) {
  const hit = ICON_BY_ROUTE.find(([re]) => re.test(route));
  return `<svg class="nav-ico" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="${I[hit ? hit[1] : "doc"]}"/></svg>`;
}

function collapsed() { return document.querySelector(".shell")?.classList.contains("nav-collapsed"); }

function applyCollapsed(on, persist = false) {
  document.querySelector(".shell").classList.toggle("nav-collapsed", on);
  const btn = document.getElementById("menuBtn");
  btn.setAttribute("aria-expanded", String(!on));
  btn.title = on ? "Expand the menu" : "Collapse the menu";
  document.querySelectorAll(".nav-link").forEach((a) => { if (on) a.title = a.dataset.title; else a.removeAttribute("title"); });
  if (!persist) return;
  try { localStorage.setItem("qa.nav.collapsed", on ? "1" : "0"); } catch { /* storage blocked */ }
  if (canEdit()) api("/api/settings", { method: "PATCH", body: { ui: { sidebar_collapsed: on } } }).catch(() => {});
}

async function initSidebar() {
  document.getElementById("menuBtn").addEventListener("click", () => {
    if (window.matchMedia("(max-width: 760px)").matches) document.getElementById("sidebar").classList.toggle("open");
    else applyCollapsed(!collapsed(), true);
  });
  let saved = null;
  try { saved = localStorage.getItem("qa.nav.collapsed"); } catch { /* ignore */ }
  if (saved === null) {
    try { const { settings } = await api("/api/settings"); saved = settings.ui?.sidebar_collapsed ? "1" : "0"; } catch { saved = "0"; }
  }
  applyCollapsed(saved === "1");
}

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = visibleNav().map(([group, items]) => `
    <div class="nav-group" title="${esc(group)}"><span class="nav-text">${esc(group)}</span></div>
    ${items.map(([route, title]) => `
      <a class="nav-link" href="#/${route}" data-route="${route}" data-title="${esc(title)}" aria-label="${esc(title)}">${iconFor(route)}<span class="nav-text">${esc(title)}</span></a>`).join("")}
  `).join("");
  if (collapsed()) applyCollapsed(true);
}

let current = null;

async function navigate() {
  const route = (location.hash.replace(/^#\/?/, "").split("?")[0]) || "dashboard";
  const requested = route;
  const entry = (allowed(route) && ROUTES.get(route)) || ROUTES.get("dashboard");
  if (current?.page.unmount) current.page.unmount();
  current = entry;
  document.querySelectorAll(".nav-link").forEach((a) => a.classList.toggle("active", a.dataset.route === entry.route));
  const titleEl = document.getElementById("pageTitle");
  titleEl.textContent = entry.title;
  titleEl.title = entry.title;  // 23.3.35: a long page name is cut with "…" in the top bar; the full name shows on hover
  document.title = `${entry.title} · ${productName()}`;
  document.getElementById("sidebar").classList.remove("open");
  const view = document.getElementById("view");
  view.innerHTML = "";
  let ok = true, error = null;
  try {
    await entry.page.mount(view, entry);
  } catch (err) {
    ok = false; error = err.message;
    view.innerHTML = `<div class="card"><h2>Could not load this page</h2><p class="muted">${esc(err.message)}</p></div>`;
  }
  // The voice agent only reports a page as open once it has really loaded (or says why it did not).
  window.dispatchEvent(new CustomEvent("qa:route", { detail: { route: requested, shown: entry.route, title: entry.title, ok: ok && ROUTES.has(requested), error: ROUTES.has(requested) ? error : "That page does not exist." } }));
}

/** The modules the voice agent can open, from this router's own table (never a hand-written list). */
export function routeTable() { return [...ROUTES.values()].map(({ route, title }) => ({ route, title })); }

// ---- theme ----
function setTheme(theme, persist = true) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("qa.theme", theme); } catch { /* storage may be blocked */ }
  if (persist && canEdit()) api("/api/settings", { method: "PATCH", body: { ui: { theme } } }).catch(() => {});
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

// ---- 23.3.32: Exit (only when started from the desktop icon) ----
async function initExit() {
  const btn = document.getElementById("exitBtn");
  let info;
  try { info = await api("/api/app/status"); } catch { return; }
  if (!info.desktop || !isAdmin()) return; // 23.3.34: only an admin can stop SkyGuard QA for everyone
  btn.hidden = false;
  btn.addEventListener("click", async () => {
    const extra = info.active_schedules ? " Scheduled runs will not start until you open it again." : "";
    if (!window.confirm(`Exit SkyGuard QA? Running tests, load tests, the local AI and the speech server stop.${extra}`)) return;
    try { await api("/api/app/exit", { method: "POST" }); } catch (err) { toast(err.message, "error"); return; }
    document.body.innerHTML = '<p style="font:16px system-ui;padding:40px">SkyGuard QA has stopped. You can close this window.</p>';
  });
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

// ---- command-center status strip, Windows clock and agent name ----
const STRIP_ORDER = ["tfs", "ai", "playwright", "speech"];
function renderStrip() {
  const el = document.getElementById("sysStrip");
  const items = state.health?.items || [];
  const byKey = Object.fromEntries(items.map((i) => [i.key, i]));
  const mic = voice.status === "listening" || voice.wakeState === "on" ? { status: "RUNNING", value: voice.wakeState === "on" && voice.status !== "listening" ? "Wake" : "Live" }
    : voice.micError ? { status: "FAIL", value: "Blocked" } : { status: voice.settings?.voice_enabled === false ? "NOT_CONFIGURED" : "PASS", value: voice.settings?.voice_enabled === false ? "Off" : "Standby" };
  el.innerHTML = STRIP_ORDER.map((k) => byKey[k]).filter(Boolean).map((i) =>
    `<span class="sys-chip" title="${esc(i.label + ": " + i.detail)}"><span class="lamp ${esc(i.status)}"></span>${esc(i.label)} <b>${esc(i.value)}</b></span>`).join("")
    + `<span class="sys-chip" title="${esc(voice.micError || "Microphone")}"><span class="lamp ${mic.status}"></span>Mic <b>${mic.value}</b></span>`
    + `<button class="sys-chip" id="stripRefresh" title="Check again now" style="cursor:pointer">&#8635;</button>`;
  el.querySelector("#stripRefresh").addEventListener("click", () => loadHealth(true));
}

function initStrip() {
  let lastForced = 0, ticks = 0, startingTimer = null;
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
    if (c.type === "health") {
      renderStrip();
      // 23.3.33: while the local AI model or the speech server is still starting, check again every 5 seconds.
      clearTimeout(startingTimer);
      if ((state.health?.items || []).some((i) => i.value === "Starting" || i.value === "Loading")) startingTimer = setTimeout(() => loadHealth(true), 5000);
    }
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

// A new install opens First-Run Setup until TFS and the local LLM model are set (or the screen is skipped).
async function firstRun() {
  if (!canEdit()) return;
  try {
    const s = await api("/api/setup");
    if (s.needed && !location.hash.replace(/^#\/?/, "")) history.replaceState(null, "", "#/setup"); // no hashchange: navigate() runs next
  } catch { /* the dashboard still opens */ }
}
// 23.3.34: nothing loads before the account is signed in (the sign-in screen shows first when needed).
async function boot() {
  loadBrand();
  await ensureSignedIn();
  renderNav();
  await initSidebar();
  onBrand(() => { if (current) document.title = `${current.title} · ${productName()}`; });
  initUserMenu(document.getElementById("userMenu"));
  initTheme();
  initExit();
  initBell();
  initConnection();
  initVoiceAgent(document.getElementById("voiceAgent"));
  initStrip();
  window.addEventListener("hashchange", navigate);
  await start();
  await firstRun();
  await navigate();
}
boot();
