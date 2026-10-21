import { api } from "./api.js";
import { state, subscribe, start } from "./store.js";
import { esc, toast, dateTimeOf } from "./util.js";
import * as dashboard from "./pages/dashboard.js";
import * as workflow from "./pages/workflow.js";
import * as notifications from "./pages/notifications.js";
import * as history from "./pages/history.js";
import * as settings from "./pages/settings.js";
import * as upcoming from "./pages/upcoming.js";
import * as tfsSettings from "./pages/tfs-settings.js";
import * as testPlans from "./pages/testplans.js";
import { workItemPage } from "./pages/workitems.js";
import * as aiSettings from "./pages/ai-settings.js";
import * as testDesign from "./pages/test-design.js";
import * as testData from "./pages/test-data.js";

// route -> { title, page, stage? }. A `stage` marks a section delivered by a later build stage.
const NAV = [
  ["Overview", [
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
    ["ai-automation", "AI-Driven Automation", null, 4],
    ["guided-automation", "Guided Browser Automation", null, 4],
    ["discovery", "Application Discovery & Update", null, 4],
    ["hybrid-automation", "Hybrid Test Automation", null, 4],
  ]],
  ["Performance Testing", [
    ["perf-plans", "Performance Test Plans", null, 5],
    ["perf-load", "Load Tests", null, 5],
    ["perf-stress", "Stress Tests", null, 5],
    ["perf-spike", "Spike Tests", null, 5],
    ["perf-soak", "Soak Tests", null, 5],
    ["perf-results", "Performance Results", null, 5],
    ["perf-defects", "Performance Defects", null, 6],
  ]],
  ["Quality", [
    ["defects", "Defects", workItemPage({ category: "bug", createLabel: "New defect" })],
    ["reports", "Reports", null, 8],
    ["analytics", "Analytics", null, 8],
  ]],
  ["Settings", [
    ["settings", "Preferences", settings],
    ["tfs-settings", "TFS Connection", tfsSettings],
    ["ai-settings", "On-Premise AI", aiSettings],
    ["voice", "Voice Commands", null, 7],
  ]],
];

const ROUTES = new Map();
for (const [, items] of NAV) {
  for (const [route, title, page, stage] of items) ROUTES.set(route, { route, title, page: page || upcoming, stage });
}

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = NAV.map(([group, items]) => `
    <div class="nav-group">${esc(group)}</div>
    ${items.map(([route, title, , stage]) => `
      <a class="nav-link" href="#/${route}" data-route="${route}">
        <span>${esc(title)}</span>${stage ? `<span class="soon" title="Delivered in build stage ${stage}">S${stage}</span>` : ""}
      </a>`).join("")}
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
  document.title = `${entry.title} · AI Agentic Test Agent Enterprise`;
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

document.getElementById("menuBtn").addEventListener("click", () => document.getElementById("sidebar").classList.toggle("open"));
renderNav();
initTheme();
initBell();
initConnection();
window.addEventListener("hashchange", navigate);
start().then(navigate);
