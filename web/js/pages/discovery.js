// Application Discovery & Update: authenticated, interactive discovery with a versioned Application Memory.
// The Application Discovery Agent can pause for input (credentials, OTP, manual verification); the tester answers
// in a window here and the agent continues in the same browser session.

import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";
import { scopeSelector } from "./tfs-scope.js";

const SOURCES = [
  ["application", "Application Only", "Explore the live application with Playwright; TFS is not needed."],
  ["tfs", "TFS Requirements Only", "Read the selected TFS hierarchy and build Requirement Memory; the application is not opened."],
  ["tfs_app", "TFS + Application — Intelligent Discovery", "Requirements guide the exploration (without limiting it), then each Feature, User Story, Acceptance Criterion and Test Case is mapped to the application, with coverage and gaps."],
];
const LEVEL_LABEL = { epic: "Epic", feature: "Feature", story: "User Story", ac: "Acceptance Criterion", testcase: "Test Case" };

const STATE_PILL = { COMPLETED: "PASSED", FAILED: "FAILED", STOPPED: "FAILED", WAITING_FOR_INPUT: "WARN" };
const STATE_TEXT = {
  PREPARING: "Preparing", READING_REQUIREMENTS: "Reading requirements", RESOLVING_HIERARCHY: "Resolving hierarchy",
  BUILDING_REQUIREMENT_MEMORY: "Building Requirement Memory", OPENING_APPLICATION: "Opening application",
  AUTHENTICATING: "Authenticating", RUNNING: "Exploring", WAITING_FOR_INPUT: "Waiting for input", RESUMING: "Resuming",
  MAPPING_REQUIREMENTS: "Mapping requirements", ANALYZING_GAPS: "Analyzing gaps", GENERATING_SCRIPTS: "Generating scripts", UPDATING_MEMORY: "Updating memory",
  COMPLETED: "Completed", FAILED: "Failed", STOPPED: "Stopped",
};

function params() {
  return new URLSearchParams(location.hash.split("?")[1] || "");
}

function modal(title, body, onCleanup) {
  const el = document.createElement("div");
  el.className = "modal-backdrop";
  el.innerHTML = `<div class="modal card" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <div class="row"><h3 style="margin:0">${esc(title)}</h3><span class="spacer"></span>
      <button class="icon-btn" data-close aria-label="Close">✕</button></div>
    <div class="modal-body">${body}</div></div>`;
  let onClose = null;
  const close = () => {
    if (!el.isConnected) return;
    el.remove();
    if (onClose) onClose();
  };
  el.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) close(); });
  document.body.appendChild(el);
  onCleanup(close);
  return { el, close, onClose: (fn) => { onClose = fn; } };
}

// "How should discovery run?" - asked before every discovery.
function modeDialog(onCleanup, summary = "", needsBrowser = true, startLabel = "Start Discovery") {
  return new Promise((resolve) => {
    if (!needsBrowser) {
      const m = modal("Start", `${summary}<div class="row"><span class="spacer"></span><button class="btn" data-cancel>Cancel</button>
        <button class="btn primary" data-start>${esc(startLabel)}</button></div>`, onCleanup);
      let done = false;
      const answer = (v) => { if (!done) { done = true; resolve(v); } m.close(); };
      m.onClose(() => { if (!done) { done = true; resolve(null); } });
      m.el.querySelector("[data-cancel]").addEventListener("click", () => answer(null));
      m.el.querySelector("[data-start]").addEventListener("click", () => answer({ headless: true }));
      return;
    }
    const m = modal("Start discovery", `${summary}
      <p>How should discovery run?</p>
      <div class="choices">
        <label class="choice"><input type="radio" name="mode" value="visible" checked>
          <span><strong>Show Browser</strong><span class="muted">Watch the agent: the cursor moves to each control, the current target is highlighted, typing and clicks are visible. Needed when the application asks for a manual verification (CAPTCHA).</span></span></label>
        <label class="choice"><input type="radio" name="mode" value="headless">
          <span><strong>Background / Headless</strong><span class="muted">The same discovery without a browser window.</span></span></label>
      </div>
      <div class="row"><span class="spacer"></span><button class="btn" data-cancel>Cancel</button>
        <button class="btn primary" data-start>${esc(startLabel)}</button></div>`, onCleanup);
    let done = false;
    const answer = (v) => { if (!done) { done = true; resolve(v); } m.close(); };
    m.onClose(() => { if (!done) { done = true; resolve(null); } });
    m.el.querySelector("[data-cancel]").addEventListener("click", () => answer(null));
    m.el.querySelector("[data-start]").addEventListener("click", () => answer({
      headless: m.el.querySelector("input[name=mode]:checked").value === "headless" }));
  });
}

// "Application Discovery Requires Input" - the agent waits with the browser open until Proceed or Cancel.
function inputDialog(runId, req, onCleanup) {
  const fields = req.fields || [];
  const m = modal(req.title || "Application Discovery Requires Input", `
    <form id="inForm" class="grid" autocomplete="off">
      <div><span class="muted">Page</span><div><strong>${esc(req.page || "")}</strong></div><div class="muted" style="word-break:break-all">${esc(req.url || "")}</div></div>
      ${fields.length ? `<div><span class="muted">Required ${fields.length > 1 ? "fields" : "field"}</span><div><strong>${fields.map((f) => esc(f.label)).join(", ")}</strong></div></div>` : ""}
      <p class="${req.error ? "" : "muted"}" style="margin:0">${esc(req.message || "")}</p>
      ${fields.map((f) => `<label>${esc(f.label)}${f.runtime_only ? ' <span class="pill WAITING">Runtime Authentication Input</span>' : ""}
        <input name="${esc(f.key)}" type="${f.secret ? "password" : "text"}" required autocomplete="off"></label>`).join("")}
      ${req.static_allowed ? '<label class="row" style="display:flex"><input type="checkbox" name="__save"> Save if this is reusable/static (stored in Application Credentials, never in scripts)</label>' : ""}
      <div class="row"><span class="spacer"></span><button class="btn" type="button" data-stop>Cancel Discovery</button>
        <button class="btn primary" type="submit">Proceed</button></div>
    </form>`, onCleanup);
  m.el.querySelector("input:not([type=checkbox])")?.focus();
  m.el.querySelector("[data-stop]").addEventListener("click", async () => {
    try { await api(`/api/automation/discovery-jobs/${encodeURIComponent(runId)}/stop`, { method: "POST" }); } catch (err) { toast(err.message, "error"); }
    m.close();
  });
  m.el.querySelector("#inForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const values = {};
    fields.forEach((f) => { values[f.key] = fd.get(f.key) || ""; });
    try {
      await api(`/api/automation/discovery-jobs/${encodeURIComponent(runId)}/input`, { method: "POST", body: { values, save_static: fd.get("__save") === "on" } });
      e.target.reset();  // typed values do not stay in the page
      m.close();
    } catch (err) { toast(err.message, "error"); }
  });
  return m;
}

function summaryHtml(d) {
  const s = d.summary || {};
  const a = d.authentication || {};
  const diff = d.diff;
  const item = (label, value) => `<div><div class="kpi-label">${esc(label)}</div><div class="kpi-value" style="font-size:20px">${esc(value ?? "—")}</div></div>`;
  return `<div class="grid kpis">
      ${item("Application", d.application || d.base_url)}
      ${item("Memory version", d.version ? `v${d.version}` : "—")}
      ${item("Pages", s.pages ?? d.pages.length)}
      ${item("Interactive elements", s.elements ?? d.pages.reduce((n, p) => n + (p.elements?.length || 0), 0))}
      ${item("Forms", s.forms)}
      ${item("APIs", (d.endpoints || []).length)}
      ${item("Business flows", (d.flows || []).length)}
      ${item("Authentication", a.status ? `${a.status.replace("_", " ")}${a.type && a.type !== "none" ? ` (${a.type})` : ""}` : "—")}
      ${item("Changes detected", diff ? diff.total_changes ?? "—" : "First version")}
    </div>`;
}

function changesHtml(diff) {
  if (!diff) return '<p class="muted">First discovery of this application; the next one shows what changed.</p>';
  const groups = [
    ["New pages", diff.pages_added], ["Removed pages", diff.pages_removed], ["New fields", diff.fields_added || []],
    ["Removed fields", diff.fields_removed || []], ["Changed fields", diff.fields_changed || []],
    ["Changed locators", (diff.locators_changed || []).map((l) => `${l.element}: ${l.old} → ${l.new}`)],
    ["New APIs", diff.endpoints_added], ["Removed APIs", diff.endpoints_removed],
    ["New workflows", diff.workflows_added || []], ["Changed workflows", diff.workflows_changed || []],
  ];
  return `<p class="muted">Compared with ${esc(diff.compared_version ? `v${diff.compared_version}` : diff.compared_with)}: ${diff.total_changes ?? 0} change(s).</p>
    ${groups.filter(([, items]) => items.length).map(([label, items]) => `<details><summary>${esc(label)} (${items.length})</summary>
      ${items.map((x) => `<div><code>${esc(x)}</code></div>`).join("")}</details>`).join("")}
    ${diff.elements_removed?.length ? `<details><summary>Removed elements (${diff.elements_removed.length}; scripts using these may need self-healing)</summary>${diff.elements_removed.map((x) => `<div><code>${esc(x)}</code></div>`).join("")}</details>` : ""}`;
}

async function discoveryDetail(view, id) {
  const { discovery: d } = await api(`/api/automation/discoveries/${encodeURIComponent(id)}`);
  const a = d.authentication || {};
  view.innerHTML = `
    <p><a href="#/discovery">← Application Discovery & Update</a></p>
    <div class="card"><h2>${esc(d.application || d.base_url)} ${d.version ? `<span class="pill WAITING">v${d.version}</span>` : ""}</h2>
      <p class="card-sub">${esc(d.base_url)} · ${esc(d.id)} · ${esc(dateTimeOf(d.created_at))}${d.mode ? ` · ${d.mode === "visible" ? "Show Browser" : "Background / Headless"}` : ""}</p>
      ${summaryHtml(d)}
      ${a.required ? `<p class="muted" style="margin-top:10px">Signed in at ${esc(a.login_url || "")}${a.credential ? ` with Application Credentials ${esc(a.credential.application)} / ${esc(a.credential.role)}` : ""}.
        ${a.runtime_inputs?.length ? `Runtime inputs (never saved): ${esc(a.runtime_inputs.join(", "))}.` : ""} ${a.captcha ? "Manual verification was required." : ""}</p>` : ""}
      ${(d.warnings || []).map((w) => `<p class="muted">⚠ ${esc(w)}</p>`).join("")}
    </div>
    <div class="card" style="margin-top:12px"><h3>Changes since the previous version</h3>${changesHtml(d.diff)}</div>
    ${(d.flows || []).length ? `<div class="card" style="margin-top:12px"><h3>Business flows</h3>
      ${d.flows.map((f) => `<div>${esc(f.name)}</div>`).join("")}
      <p class="muted">Page Object candidates: ${esc([...new Set(d.pages.filter((p) => !p.error && p.name).map((p) => p.name))].join(", "))}</p></div>` : ""}
    ${(d.skipped_actions || []).length ? `<div class="card" style="margin-top:12px"><h3>Not clicked during discovery</h3>
      <p class="muted">Recorded in the inventory but not clicked, so discovery never changes data or ends the session.</p>
      <table class="data"><thead><tr><th>Control</th><th>Reason</th><th>Seen on</th></tr></thead><tbody>
      ${d.skipped_actions.map((s) => `<tr><td>${esc(s.element)}</td><td>${esc(s.reason)}</td>
        <td>${(s.pages || [s.page]).length > 1 ? `${(s.pages || []).length} pages` : esc(s.page)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <div class="card" style="margin-top:12px"><h3>API endpoints</h3>
      ${d.endpoints.length ? `<table class="data"><thead><tr><th>Method</th><th>Endpoint</th><th>Calls</th><th>Status</th><th>Content type</th></tr></thead><tbody>
        ${d.endpoints.map((e) => `<tr><td>${esc(e.method)}</td><td><code>${esc(e.endpoint)}</code></td><td>${e.count}</td><td>${esc(e.statuses.join(", "))}</td><td>${esc(e.content_type || "")}</td></tr>`).join("")}
      </tbody></table><p class="muted">This inventory is used to build JMeter performance tests.</p>` : '<p class="muted">No XHR/fetch traffic was observed.</p>'}
    </div>
    ${d.pages.map((p, pi) => `
      <details class="card tc" style="margin-top:12px;padding:12px 16px">
        <summary><strong>${esc(p.name ? `${p.name} · ` : "")}${esc(p.title || p.url)}</strong> <span class="muted">${esc(p.url)}</span>
          ${p.error ? `<span class="pill FAIL">${esc(p.error)}</span>` : `<span class="muted">${p.elements.length} elements · ${p.forms.length} forms${p.auth ? ` · ${esc(p.auth)} screen` : ""}</span>`}</summary>
        ${p.error ? "" : `${(p.menus || []).length ? `<p class="muted">Menus: ${p.menus.map((m) => esc(m.items.join(", "))).join(" | ")}</p>` : ""}
          ${(p.dialogs || []).length ? `<p class="muted">Dialogs: ${p.dialogs.map((x) => esc(x.title)).join(", ")}</p>` : ""}
          ${(p.tables || []).length ? `<p class="muted">Tables: ${p.tables.map((t) => esc(`${t.headers.join(" / ")} (${t.rows} rows)`)).join("; ")}</p>` : ""}
          <form data-page="${pi}">
          <table class="data"><thead><tr><th></th><th>Element</th><th>Role</th><th>Locator</th></tr></thead><tbody>
          ${p.elements.map((el) => `<tr><td><input type="checkbox" value="${esc(el.selector)}"></td><td>${esc(el.name || el.text || el.placeholder || el.tag)}
            ${el.destructive ? ' <span class="pill WARN">not clicked</span>' : ""}${el.revealed_by ? ` <span class="muted">(after ${esc(el.revealed_by)})</span>` : ""}</td>
            <td>${esc(el.role || el.tag)}</td><td><code>${esc(el.selector)}</code></td></tr>`).join("")}</tbody></table>
          <div class="row"><input name="name" placeholder="Script name"><button class="btn primary" type="submit">Create script from selected elements</button></div></form>`}
      </details>`).join("")}`;
  view.querySelectorAll("form[data-page]").forEach((form) => form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const selectors = [...form.querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value);
    if (!selectors.length) { toast("Select at least one element.", "warning"); return; }
    try {
      const { script } = await api(`/api/automation/discoveries/${encodeURIComponent(id)}/scripts`, { method: "POST", body: {
        page_url: d.pages[Number(form.dataset.page)].url, selectors, name: form.querySelector("[name=name]").value } });
      location.hash = `#/discovery?script=${encodeURIComponent(script.id)}`;
    } catch (err) { toast(err.message, "error"); }
  }));
}

function pct(v) {
  return v === null || v === undefined ? "—" : `${v}%`;
}

function coverageTree(nodes) {
  const row = (n) => `<details class="tc" ${n.level !== "ac" && n.level !== "testcase" ? "open" : ""} style="margin-left:${n.level === "epic" ? 0 : 14}px">
    <summary><strong>${esc(n.label)}</strong> ${esc(n.title || "")} <span class="muted">${esc(LEVEL_LABEL[n.level] || n.level)}${n.active ? "" : " · context"}</span>
      <span class="pill ${n.status === "MAPPED" ? "PASSED" : n.status === "PARTIAL" ? "WARN" : "FAILED"}">${n.status === "NOT_MAPPED" ? "⚠ NOT MAPPED" : pct(n.coverage)}</span></summary>
    ${n.application_areas?.length ? `<div class="muted">Mapped application areas: ${esc(n.application_areas.join(", "))}</div>` : ""}
    ${n.mapped_to?.length ? `<div class="muted">Mapped to: ${n.mapped_to.map((m) => esc(`${m.page}${m.element ? ` / ${m.element}` : ""}`)).join(", ")}</div>` : ""}
    ${(n.children || []).map(row).join("")}</details>`;
  return nodes.map(row).join("");
}

async function knowledgeDetail(view, kid) {
  const { knowledge: k } = await api(`/api/knowledge/${encodeURIComponent(kid)}`);
  const s = k.summary;
  const cnt = k.counts || {};
  const item = (label, value) => `<div><div class="kpi-label">${esc(label)}</div><div class="kpi-value" style="font-size:20px">${esc(value ?? "—")}</div></div>`;
  view.innerHTML = `
    <p><a href="#/discovery">← Application Discovery & Update</a></p>
    <div class="card"><h2>Unified QA Knowledge · ${esc(k.application)}</h2>
      <p class="card-sub">${esc(k.id)} · ${esc(dateTimeOf(k.created_at))} · Knowledge source: TFS + Application ·
        <a href="#/discovery?id=${encodeURIComponent(k.discovery_id)}">Application Memory</a> ·
        <a href="#/discovery?memory=${encodeURIComponent(k.requirement_memory_id)}">Requirement Memory</a></p>
      <div class="grid kpis">
        ${item("Coverage", pct(k.coverage.overall))}${item("TFS Project", k.project?.name)}${item("Sprint", k.iteration?.name || "—")}
        ${item("Features", cnt.feature)}${item("User Stories", cnt.story)}${item("Acceptance Criteria", cnt.ac)}${item("Test Cases", cnt.testcase)}
        ${item("Requirement mappings", s.requirement_mappings)}${item("Unmapped requirements", s.unmapped_requirements)}${item("Unmapped application features", s.unmapped_application_features)}
      </div></div>
    <div class="card" style="margin-top:12px"><h3>Requirement Coverage Map</h3>${coverageTree(k.tree)}</div>
    <div class="card" style="margin-top:12px"><div class="row"><h3 style="margin:0">Test Case mapping</h3><span class="spacer"></span>
      ${Object.keys(k.test_cases).length ? `<button class="btn primary" id="genScripts">Generate Playwright Scripts (${Object.keys(k.test_cases).length} Test Case${Object.keys(k.test_cases).length === 1 ? "" : "s"})</button>` : ""}</div>
      ${Object.values(k.test_cases).map((t) => `<details class="tc"><summary><strong>${esc(t.label)}</strong> ${esc(t.title || "")}
        <span class="pill ${t.coverage >= 80 ? "PASSED" : t.coverage ? "WARN" : "FAILED"}">${t.coverage}%</span></summary>
        ${t.steps.map((st) => `<div>Step ${st.index} — ${esc(st.action)}: ${st.match ? `✓ ${esc(st.match.page)}${st.match.element ? ` / ${esc(st.match.element)}` : ""}` : '<span class="pill FAILED">⚠ NOT FOUND</span>'}</div>
          ${st.expected ? `<div class="muted" style="margin-left:16px">Expected: ${esc(st.expected)} — ${st.expected_match ? `✓ ${esc(st.expected_match.page)}${st.expected_match.element ? ` / ${esc(st.expected_match.element)}` : ""}` : "⚠ not observed"}</div>` : ""}`).join("")}
      </details>`).join("") || '<p class="muted">No Test Cases in scope.</p>'}</div>
    <div class="card" style="margin-top:12px"><h3>Requirement gaps</h3><p class="muted">Requirement exists, but the application behaviour could not be found.</p>
      ${k.requirement_gaps.length ? `<table class="data"><thead><tr><th>Item</th><th>Detail</th><th>Reason</th></tr></thead><tbody>
        ${k.requirement_gaps.map((g) => `<tr><td>${esc(g.label)}${g.step ? ` step ${g.step}` : ""}</td><td>${esc(g.text || "")}</td><td>${esc(g.reason)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">None.</p>'}</div>
    <div class="card" style="margin-top:12px"><h3>Application gaps</h3><p class="muted">Application functionality exists, but no requirement maps to it. Nothing is created in TFS automatically.</p>
      ${k.application_gaps.length ? k.application_gaps.map((g) => `<div>${esc(g.page)} <span class="muted">${esc(g.reason)}</span></div>`).join("") : '<p class="muted">None (or reported as out-of-scope discovery below for a partial scope).</p>'}</div>
    <div class="card" style="margin-top:12px"><h3>Out-of-Scope Discovery</h3><p class="muted">Adjacent application structure found while exploring, outside the selected requirements.</p>
      ${k.out_of_scope.length ? k.out_of_scope.map((g) => `<div>${esc(g.page)} <span class="muted">${esc(g.reason)}</span></div>`).join("") : '<p class="muted">None.</p>'}</div>
    ${k.flows?.length ? `<div class="card" style="margin-top:12px"><h3>Business flows</h3>${k.flows.map((f) => `<div>${esc(f.name)}</div>`).join("")}
      <p class="muted">Page Objects: ${esc((k.page_objects || []).join(", "))}. AI-Driven Automation uses this knowledge when it generates these Test Cases.</p></div>` : ""}`;
  view.querySelector("#genScripts")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const r = await api(`/api/knowledge/${encodeURIComponent(kid)}/scripts`, { method: "POST", body: { headless: true, on_existing: "skip" } });
      location.hash = `#/ai-automation?job=${encodeURIComponent(r.job_id)}`;
    } catch (err) { toast(err.message, "error"); e.target.disabled = false; }
  });
}

async function memoryDetail(view, mid) {
  const { memory: m } = await api(`/api/knowledge/requirement-memory/${encodeURIComponent(mid)}`);
  const list = (title, rows, fmt) => rows.length ? `<details class="tc" open><summary><strong>${esc(title)}</strong> <span class="muted">${rows.length}</span></summary>${rows.map(fmt).join("")}</details>` : "";
  const ctx = (r) => (r.active ? "" : ' <span class="muted">(parent context)</span>');
  view.innerHTML = `
    <p><a href="#/discovery">← Application Discovery & Update</a></p>
    <div class="card"><h2>Requirement Memory · ${esc(m.project?.name || "")}</h2>
      <p class="card-sub">${esc(m.id)} · v${m.version || 1} · ${esc(dateTimeOf(m.created_at))} · scope: ${esc(m.option)}${m.iteration?.name ? ` · ${esc(m.iteration.name)}` : ""}</p>
      ${list("Epics", m.epics, (r) => `<div>${esc(r.label)} ${esc(r.title)}${ctx(r)}</div>`)}
      ${list("Features", m.features, (r) => `<div>${esc(r.label)} ${esc(r.title)}${ctx(r)}</div>`)}
      ${list("User Stories", m.user_stories, (r) => `<div>${esc(r.label)} ${esc(r.title)}${ctx(r)}${r.description ? `<div class="muted">${esc(r.description)}</div>` : ""}</div>`)}
      ${list("Acceptance Criteria", m.acceptance_criteria, (r) => `<div>${esc(r.label)} ${esc(r.text || r.title)}${ctx(r)}</div>`)}
      ${list("Test Cases", m.test_cases, (r) => `<details class="tc"><summary>${esc(r.label)} ${esc(r.title)}${ctx(r)} <span class="muted">${(r.steps || []).length} steps · ${(r.test_data || []).length} data sets</span></summary>
        ${(r.steps || []).map((s) => `<div>${s.index}. ${esc(s.action)}${s.expected ? ` <span class="muted">→ ${esc(s.expected)}</span>` : ""}</div>`).join("")}</details>`)}
      <h3 style="margin-top:12px">What the requirements expect</h3>
      <p class="muted">Pages: ${esc(m.expected.pages.join(", ") || "—")}<br>Fields: ${esc(m.expected.fields.join(", ") || "—")}<br>
        Buttons: ${esc(m.expected.buttons.join(", ") || "—")}<br>Validations: ${esc(m.expected.validations.join(" | ") || "—")}</p></div>`;
}

export async function discoveryPanel(view, { onCleanup, liveFeed, renderScriptList }) {
  const id = params().get("id");
  if (id) return discoveryDetail(view, id);
  if (params().get("knowledge")) return knowledgeDetail(view, params().get("knowledge"));
  if (params().get("memory")) return memoryDetail(view, params().get("memory"));
  let creds = [];
  let apps = [];
  try { ({ items: creds } = await api("/api/automation/credentials")); } catch { /* optional */ }
  try { ({ items: apps } = await api("/api/automation/discovery-applications")); } catch { /* optional */ }
  const appNames = [...new Set([...creds.map((c) => c.application), ...apps.map((a) => a.application)])];
  view.innerHTML = `
    <div class="card"><h3>Discovery Knowledge Source</h3><div class="choices" id="sources">
      ${SOURCES.map(([k, label, text]) => `<label class="choice"><input type="radio" name="source" value="${k}" ${k === "tfs_app" ? "checked" : ""}>
        <span><strong>${esc(label)}</strong>${k === "tfs_app" ? ' <span class="pill PASSED">Recommended</span>' : ""}<span class="muted">${esc(text)}</span></span></label>`).join("")}
    </div></div>
    <div class="card" id="scopeBox" style="margin-top:12px"></div>
    <form class="card grid" id="disc" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-top:12px">
      <label>Application URL <input name="base_url" required placeholder="https://your-app/"></label>
      <label>Max pages <input name="max_pages" type="number" min="1" max="200" value="15"></label>
      <label>Link depth <input name="max_depth" type="number" min="0" max="10" value="3"></label>
      <div class="row" style="align-self:end"><button class="btn primary" type="submit">Discover</button></div>
      <label>Application <input name="application" list="appNames" placeholder="Name used for its memory (default: host name)">
        <datalist id="appNames">${appNames.map((a) => `<option value="${esc(a)}">`).join("")}</datalist></label>
      <label>Credential role <select name="credential_role"><option value="">Any saved role for this application</option></select></label>
      <label>Environment <input name="environment" placeholder="Optional, e.g. UAT"></label>
      <label class="row" style="display:flex;align-self:end"><input type="checkbox" name="explore" checked> Click through menus, buttons and script links</label>
      <div class="row" id="genBox" style="grid-column:1/-1;flex-wrap:wrap;gap:12px">
        <label class="row"><input type="checkbox" name="generate_scripts" checked> Then generate the Playwright scripts for the Test Cases in scope (AI-Driven Automation)</label>
        <label class="row">If a script already exists <select name="on_existing"><option value="skip">Skip it</option>
          <option value="new_version">Create a new version</option><option value="regenerate">Regenerate it</option></select></label></div>
      <p class="muted" style="grid-column:1/-1">The Application Discovery Agent signs in when the application asks for it, using
        <a href="#/app-credentials">Application Credentials</a>. When a value is unknown (credentials, authentication code, tenant, CAPTCHA) it pauses
        and asks you, then continues in the same browser session. It explores pages, menus, dialogs and script-driven navigation, never clicks
        destructive actions (delete, remove, pay, finish, approve…) or logout, and keeps a versioned Application Memory for each application.</p>
    </form>
    <div class="card" id="progress" style="margin-top:12px" hidden>
      <div class="row"><h3 style="margin:0">Activity</h3><span class="pill WAITING" id="jobState"></span><span class="spacer"></span>
        <button class="btn" id="stopBtn" hidden>Stop Discovery</button></div>
      <div id="jobResult"></div>
      <div class="feed" id="feed" style="max-height:320px"></div></div>
    <div class="card" id="knowledge" style="margin-top:12px" hidden></div>
    <div class="card" id="discs" style="margin-top:12px"><p class="muted">Loading…</p></div>
    <div class="card" id="list" style="margin-top:12px"><p class="muted">Loading…</p></div>`;
  const form = view.querySelector("#disc");
  const source = () => view.querySelector("[name=source]:checked").value;
  let scope = null;
  const applySource = async () => {
    const src = source();
    const box = view.querySelector("#scopeBox");
    box.hidden = src === "application";
    form.base_url.required = src !== "tfs";
    form.querySelectorAll("label,p").forEach((el) => { if (!el.contains(form.base_url) && !el.querySelector("[name=max_pages]") && el.tagName !== "DIV") el.hidden = src === "tfs"; });
    view.querySelector("#genBox").hidden = src !== "tfs_app";
    form.querySelector("button[type=submit]").textContent = src === "tfs" ? "Build Requirement Memory" : src === "tfs_app" ? "Start Intelligent Discovery" : "Discover";
    if (src !== "application" && !scope) { box.innerHTML = '<p class="muted">Loading TFS…</p>'; scope = await scopeSelector(box); }
  };
  view.querySelectorAll("[name=source]").forEach((r) => r.addEventListener("change", applySource));
  const roleSel = form.querySelector("[name=credential_role]");
  const fillRoles = () => {
    const app = form.application.value.trim().toLowerCase();
    const roles = creds.filter((c) => c.application.toLowerCase() === app);
    roleSel.innerHTML = `<option value="">${roles.length ? "Any saved role for this application" : "No saved credentials (the agent asks when needed)"}</option>
      ${[...new Set(roles.map((c) => c.role))].map((r) => `<option>${esc(r)}</option>`).join("")}`;
  };
  form.application.addEventListener("input", fillRoles);
  fillRoles();

  let runId = null;
  let poll = null;
  let inputModal = null;
  let askedId = null;
  const stateEl = view.querySelector("#jobState");
  const stopBtn = view.querySelector("#stopBtn");
  const renderDiscs = async () => {
    const { items } = await api("/api/automation/discoveries");
    view.querySelector("#discs").innerHTML = `<h3>Application Memory</h3>${items.length ? `<table class="data"><thead><tr><th>Application</th><th>Version</th><th>Pages</th><th>Elements</th><th>APIs</th><th>Authentication</th><th>Changes</th><th>When</th></tr></thead><tbody>
      ${items.map((d) => `<tr class="clickable" data-id="${esc(d.id)}"><td>${esc(d.application)}<div class="muted">${esc(d.base_url)}</div></td><td>${d.version ? `v${d.version}` : ""}</td>
        <td>${d.pages}</td><td>${d.elements}</td><td>${d.endpoints}</td><td>${esc((d.authentication || "").replace("_", " "))}</td>
        <td>${d.changes ?? ""}</td><td>${esc(dateTimeOf(d.created_at))}</td></tr>`).join("")}</tbody></table>`
      : '<p class="muted">No discoveries yet.</p>'}`;
    view.querySelectorAll("#discs tr[data-id]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/discovery?id=${encodeURIComponent(tr.dataset.id)}`; }));
  };
  const showJob = (job) => {
    stateEl.textContent = STATE_TEXT[job.state] || job.state;
    stateEl.className = `pill ${STATE_PILL[job.state] || "RUNNING"}`;
    stopBtn.hidden = !job.active;
    if (job.state === "WAITING_FOR_INPUT" && job.request && job.request.id !== askedId) {
      askedId = job.request.id;
      inputModal?.close();
      inputModal = inputDialog(runId, job.request, onCleanup);
    }
    if (job.state !== "WAITING_FOR_INPUT" && inputModal) { inputModal.close(); inputModal = null; }
    const res = view.querySelector("#jobResult");
    if (job.state === "COMPLETED" && (job.discovery_id || job.requirement_memory_id)) {
      res.innerHTML = `<p>Completed.
        ${job.summary?.pages !== undefined ? `${job.summary.pages} pages, ${job.summary.elements} elements, ${job.summary.endpoints} APIs.` : ""}
        ${job.knowledge_id ? `<a href="#/discovery?knowledge=${encodeURIComponent(job.knowledge_id)}">Open coverage and gaps</a> ·` : ""}
        ${job.discovery_id ? `<a href="#/discovery?id=${encodeURIComponent(job.discovery_id)}">Open Application Memory</a>` : ""}
        ${job.requirement_memory_id ? ` · <a href="#/discovery?memory=${encodeURIComponent(job.requirement_memory_id)}">Open Requirement Memory</a>` : ""}</p>`;
      if (job.script_job_id) res.innerHTML += scriptsLink(job);
    } else if (job.state === "GENERATING_SCRIPTS" && job.script_job_id) {
      res.innerHTML = scriptsLink(job);
    } else if (job.state === "FAILED") {
      res.innerHTML = `<p class="pill FAILED">${esc(job.error || "Discovery failed")}</p>`;
    } else if (job.state === "STOPPED") {
      res.innerHTML = '<p class="muted">Discovery was stopped; Application Memory was not changed.</p>';
    } else res.innerHTML = "";
    if (!job.active) {
      clearInterval(poll);
      poll = null;
      renderDiscs();
    }
  };
  const scriptsLink = (job) => `<p>Playwright scripts: <a href="#/ai-automation?job=${encodeURIComponent(job.script_job_id)}">open the AI-Driven Automation job</a>
    ${job.summary?.scripts ? ` (${job.summary.scripts.completed ?? 0} of ${job.summary.scripts.test_cases} generated${job.summary.scripts.failed ? `, ${job.summary.scripts.failed} failed` : ""})` : ""}</p>`;
  const watch = () => {
    clearInterval(poll);
    poll = setInterval(async () => {
      try { showJob((await api(`/api/automation/discovery-jobs/${encodeURIComponent(runId)}`)).job); } catch { /* next tick */ }
    }, 1000);
  };
  onCleanup(() => clearInterval(poll));
  stopBtn.addEventListener("click", async () => {
    try { await api(`/api/automation/discovery-jobs/${encodeURIComponent(runId)}/stop`, { method: "POST" }); } catch (err) { toast(err.message, "error"); }
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const src = source();
    let resolved = null;
    if (src !== "application") {
      if (!scope?.ready) { toast("Connect TFS first, or choose Application Only.", "warning"); return; }
      try { resolved = await scope.preview(); } catch (err) { toast(err.message, "error"); return; }
      if (!resolved.total) { toast("The selected scope has no requirements.", "warning"); return; }
    }
    const c = resolved?.counts;
    const summary = resolved ? `<div class="card" style="margin:8px 0"><h3>${src === "tfs" ? "REQUIREMENT SCOPE" : "INTELLIGENT DISCOVERY SCOPE"}</h3>
      ${resolved.large ? '<p class="pill WARN">Large Scope: this loads a large part of the requirement hierarchy. Continue?</p>' : ""}
      <div>Project: <strong>${esc(resolved.project || "")}</strong></div>
      ${resolved.iteration ? `<div>Sprint: <strong>${esc(resolved.iteration)}</strong></div>` : ""}
      <div>${["epic", "feature", "story", "ac", "testcase"].filter((k) => c[k]).map((k) => `${esc({ epic: "Epics", feature: "Features", story: "User Stories", ac: "Acceptance Criteria", testcase: "Test Cases" }[k])}: <strong>${c[k]}</strong>`).join(" · ")}</div>
      ${src === "tfs_app" ? `<div>Application: <strong>${esc(f.base_url.trim())}</strong></div><div>Mode: <strong>TFS + Application</strong></div>` : ""}
      ${src === "tfs_app" && f.generate_scripts === "on" ? `<div>Then: <strong>generate Playwright scripts for ${c.testcase} Test Case(s)</strong></div>` : ""}</div>` : "";
    const choice = await modeDialog(onCleanup, summary, src !== "tfs",
      src === "tfs" ? "Build Requirement Memory" : src === "tfs_app" ? "Start Intelligent Discovery" : "Start Discovery");
    if (!choice) return;
    try {
      const r = await api("/api/knowledge/discoveries", { method: "POST", body: {
        source: src, base_url: f.base_url.trim() || null, max_pages: Number(f.max_pages), max_depth: Number(f.max_depth), headless: choice.headless,
        application: f.application.trim() || null, credential_role: f.credential_role || null,
        environment: f.environment.trim() || null, explore: f.explore === "on",
        generate_scripts: src === "tfs_app" && f.generate_scripts === "on", on_existing: f.on_existing || "skip", scope: src === "application" ? null : scope.value() } });
      runId = r.run_id;
      askedId = null;
      view.querySelector("#progress").hidden = false;
      view.querySelector("#feed").innerHTML = "";
      view.querySelector("#jobResult").innerHTML = "";
      watch();
    } catch (err) { toast(err.message, "error"); }
  });
  liveFeed(view.querySelector("#feed"), () => runId);
  const renderKnowledge = async () => {
    let items = [];
    try { ({ items } = await api("/api/knowledge")); } catch { return; }
    const box = view.querySelector("#knowledge");
    box.hidden = !items.length;
    box.innerHTML = `<h3>Unified QA Knowledge</h3><table class="data"><thead><tr><th>Application</th><th>TFS scope</th><th>Coverage</th><th>Mapped</th><th>Unmapped requirements</th><th>Unmapped application areas</th><th>When</th></tr></thead><tbody>
      ${items.map((k) => `<tr class="clickable" data-k="${esc(k.id)}"><td>${esc(k.application)}</td><td>${esc(k.project || "")} · ${esc(k.option || "")}</td><td>${k.coverage}%</td>
        <td>${k.summary.requirement_mappings}</td><td>${k.summary.unmapped_requirements}</td><td>${k.summary.unmapped_application_features}</td><td>${esc(dateTimeOf(k.created_at))}</td></tr>`).join("")}</tbody></table>`;
    box.querySelectorAll("tr[data-k]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/discovery?knowledge=${encodeURIComponent(tr.dataset.k)}`; }));
  };
  await Promise.all([renderDiscs(), renderKnowledge(), applySource(),
    renderScriptList(view.querySelector("#list"), ["discovery"], "No scripts created from discovery yet.")]);
}
