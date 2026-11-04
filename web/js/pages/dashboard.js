// Dashboard (23.3.14): Azure DevOps figures, QA progress, statistics with pie and bar charts, and Performance.
// The agent cards and the recent-runs table moved off the dashboard (agents are on Agent Workflow, runs on
// Execution History). Every figure comes from TFS or recorded results; a missing figure shows "—".

import { api } from "../api.js";
import { state, subscribe, onTick } from "../store.js";
import { agentState } from "../agent-state.js";
import { esc } from "../util.js";
import { donut, dayColumns, hbars, progressRow } from "../dash-charts.js";

let unsub = null;
let stopTick = null;
let refreshTimer = null;

const C = {
  ok: "var(--ok)", fail: "var(--fail)", warn: "var(--warn)", run: "var(--run)", accent: "var(--accent)",
  muted: "var(--muted)", pink: "var(--cat-2)", violet: "#a78bfa",
};

function kpi(label, value, note = "") {
  return `<div class="card"><div class="kpi-label">${esc(label)}</div><div class="kpi-value">${esc(value)}</div><div class="kpi-note">${esc(note)}</div></div>`;
}

const pct = (v) => (v === null || v === undefined ? "—" : `${v}%`);
const link = (href, html) => `<a href="${href}" style="text-decoration:none;color:inherit">${html}</a>`;
const ms = (value, label) => `<div class="ms"><b>${esc(value ?? "—")}</b><span>${esc(label)}</span></div>`;
const chartCard = (title, stats, body, extra = "") =>
  `<section class="card dash-card ${extra}"><h3>${esc(title)}</h3>${stats ? `<div class="ms-row">${stats}</div>` : ""}${body}</section>`;

const KIND_LABEL = {
  TCGEN: "Test case design", TDATA: "Test data", DESIGN: "AI test design", GEN: "Script generation",
  RUN: "Test execution", REC: "Recording", DISC: "App discovery", PERF: "Performance", DIAG: "Diagnostics",
  design: "Test design", automation: "Automation", functional: "Test execution", performance: "Performance", diagnostics: "Diagnostics",
};
const kindLabel = (k) => KIND_LABEL[k] || String(k || "other").replace(/[_-]+/g, " ").replace(/^./, (c) => c.toUpperCase());

function agentsDonut() {
  const n = { RUNNING: 0, COMPLETED: 0, READY: 0, WAITING: 0, FAILED: 0 };
  for (const a of state.agents.values()) {
    const st = agentState(a, state.agents).state;
    n[st === "PAUSED" ? "RUNNING" : st] = (n[st === "PAUSED" ? "RUNNING" : st] || 0) + 1;
  }
  return donut([
    { label: "Running", value: n.RUNNING, color: C.run, href: "#/workflow" },
    { label: "Completed", value: n.COMPLETED, color: C.ok, href: "#/workflow" },
    { label: "Ready", value: n.READY, color: C.violet, href: "#/workflow" },
    { label: "Waiting", value: n.WAITING, color: C.warn, href: "#/workflow" },
    { label: "Failed", value: n.FAILED, color: C.fail, href: "#/workflow" },
  ], { center: state.agents.size, sub: "AGENTS", empty: "No agents reported yet" });
}

async function render(view) {
  const area = view.querySelector("#kpiArea");
  let summary, a;
  try {
    [summary, a] = await Promise.all([api("/api/dashboard/summary"), api("/api/analytics/summary")]);
  } catch (err) {
    area.innerHTML = `<div class="card"><h2>Dashboard unavailable</h2><p class="muted">${esc(err.message)}</p></div>`;
    return;
  }
  const tfs = summary.tfs;
  const t = a.tfs.connected ? a.tfs : {};
  const cov = a.coverage, fn = a.functional, perf = a.performance, ac = a.acceptance_criteria;
  const ins = a.insights || {};
  const td = ins.test_data || {};
  const ex = ins.executions || null;
  const def = ins.defects || null;
  const drafts = ins.defect_drafts || {};
  const notConnected = "TFS not connected";
  const tfsNote = tfs.connected ? tfs.project : notConnected;
  const tcTotal = "tfs_test_cases" in cov ? cov.tfs_test_cases : (t.testcase ?? null);

  const quality = [
    link("#/user-stories", kpi("User Stories", t.requirement ?? "—", tfsNote)),
    link("#/acceptance-criteria", kpi("Acceptance Criteria", ac.available ? ac.criteria : "—",
      ac.available ? `on ${ac.stories_with_criteria} of ${ac.stories} user stories` : tfs.connected ? "Not available" : notConnected)),
    link("#/test-cases", kpi("Test Cases", t.testcase ?? "—", tfsNote)),
    link("#/test-data", kpi("Test Data", a.test_data, td.test_cases ? `Rows across ${td.test_cases} test case${td.test_cases === 1 ? "" : "s"}` : "Saved data rows")),
    link("#/ai-automation", kpi("Automated", cov.automated_test_cases, `${cov.scripts} automated test${cov.scripts === 1 ? "" : "s"}`)),
    kpi("Not Automated", cov.not_automated ?? "—", "tfs_test_cases" in cov ? "TFS test cases without a script" : notConnected),
    kpi("Automation Coverage", pct(cov.automation_coverage), "tfs_test_cases" in cov ? `${cov.automated_test_cases} of ${cov.tfs_test_cases} test cases` : notConnected),
    link("#/defects", kpi("Open Defects", t.open_defects ?? "—", tfsNote)),
  ];
  const performance = [
    link("#/perf-results", kpi("Performance Tests", perf.runs, "Finished JMeter runs")),
    kpi("Performance Failures", perf.failed, "Runs that failed or missed the SLA"),
    kpi("Performance SLA Pass Rate", pct(perf.sla_pass_rate), perf.sla_pass + perf.sla_fail ? `${perf.sla_pass} passed, ${perf.sla_fail} failed` : "No SLA results yet"),
  ];

  // ---- QA progress: one bar per step from user story to a passing test ----
  const progress = [
    progressRow("User stories with acceptance criteria", ac.available ? ac.stories_with_criteria : null, ac.available ? ac.stories : null,
      { color: C.accent, href: "#/acceptance-criteria" }),
    progressRow("Test cases with test data", tcTotal != null && td.tfs_test_cases_with_data != null ? td.tfs_test_cases_with_data : null, tcTotal,
      { color: C.violet, href: "#/test-data" }),
    progressRow("Test cases automated", "tfs_test_cases" in cov ? cov.automated_test_cases : null, "tfs_test_cases" in cov ? cov.tfs_test_cases : null,
      { color: C.run, href: "#/ai-automation" }),
    progressRow("Automated tests executed", cov.scripts ? fn.scripts_run : null, cov.scripts || null, { color: C.warn, href: "#/history" }),
    progressRow("Executed tests passing", fn.scripts_run ? fn.passed : null, fn.scripts_run || null, { color: C.ok, href: "#/history" }),
    progressRow("Defects closed", def ? def.closed : null, def ? def.total : null, { color: C.pink, href: "#/defects" }),
  ].join("");

  // ---- statistics cards ----
  const storiesCard = chartCard("User Stories & Acceptance Criteria",
    ms(t.requirement, "User stories") + ms(ac.available ? ac.criteria : null, "Criteria")
      + ms(ac.available && ac.stories_with_criteria ? (ac.criteria / ac.stories_with_criteria).toFixed(1) : null, "Avg per story"),
    ac.available ? donut([
      { label: "With acceptance criteria", value: ac.stories_with_criteria, color: C.ok, href: "#/acceptance-criteria" },
      { label: "Without acceptance criteria", value: Math.max(0, ac.stories - ac.stories_with_criteria), color: C.warn, href: "#/user-stories" },
    ], { center: ac.stories, sub: "STORIES" }) : `<p class="muted small">${esc(ac.reason || notConnected)}</p>`);

  const casesCard = chartCard("Test Cases",
    ms(tcTotal, "Total") + ms(cov.automated_test_cases, "Automated") + ms(cov.not_automated, "Not automated"),
    "tfs_test_cases" in cov ? donut([
      { label: "Automated", value: cov.automated_test_cases, color: C.run, href: "#/ai-automation" },
      { label: "Not automated", value: cov.not_automated, color: C.muted, href: "#/test-cases" },
    ], { center: pct(cov.automation_coverage), sub: "AUTOMATED" }) : `<p class="muted small">${esc(cov.tfs_reason || notConnected)}</p>`);

  const dataCard = chartCard("Test Data",
    ms(td.rows ?? a.test_data, "Data rows") + ms(td.test_cases, "Test cases") + ms(td.tfs_test_cases_without_data, "Cases without data"),
    donut([
      { label: "Valid rows", value: td.valid || 0, color: C.ok, href: "#/test-data" },
      { label: "Invalid (negative) rows", value: td.invalid || 0, color: C.fail, href: "#/test-data" },
      { label: "Other rows", value: Math.max(0, (td.rows || 0) - (td.valid || 0) - (td.invalid || 0)), color: C.muted, href: "#/test-data" },
    ], { center: td.rows || 0, sub: "ROWS", empty: "No test data yet" })
      + (td.top?.length ? `<div class="sub-h">Rows per test case</div>` + hbars(td.top.map((r) => ({
        label: /^\d+$/.test(r.test_case) ? `#${r.test_case}` : r.test_case, href: "#/test-data",
        parts: [{ value: r.rows, color: C.violet, label: "Rows" }],
      }))) : ""));

  const draftList = Object.entries(drafts).filter(([, v]) => v);
  const defectsCard = chartCard("Defects",
    ms(def ? def.total : null, "In TFS") + ms(t.open_defects, "Open") + ms(a.open_defect_drafts, "Drafts to review"),
    (def ? donut([
      { label: "Open", value: def.open, color: C.fail, href: "#/defects" },
      { label: "Closed", value: def.closed, color: C.ok, href: "#/defects" },
    ], { center: def.total, sub: "DEFECTS", empty: "No defects in TFS" }) : `<p class="muted small">${esc(tfs.connected ? "Defect totals are not available for this project." : notConnected)}</p>`)
      + (draftList.length ? `<div class="sub-h">Defect drafts</div>` + hbars(draftList.map(([k, v]) => ({
        label: k.replace(/_/g, " "), href: "#/defect-review", parts: [{ value: v, color: k === "draft" || k === "needs_review" ? C.warn : C.accent, label: k }],
      }))) : ""));

  const passCard = chartCard("Pass / Fail",
    ms(fn.scripts_run, "Tests with results") + ms(pct(fn.pass_rate), "Pass rate") + ms(ex ? ex.total : null, "Executions (all)"),
    donut([
      { label: "Passed", value: fn.passed, color: C.ok, href: "#/history" },
      { label: "Failed", value: fn.failed, color: C.fail, href: "#/history?status=FAILED" },
    ], { center: pct(fn.pass_rate), sub: "PASS RATE", empty: "No results yet" })
      + `<p class="muted small" style="margin:6px 0 0">Latest result of each automated test.${ex ? ` All executions: ${ex.passed} passed, ${ex.failed} failed.` : ""}</p>`);

  const trendCard = chartCard("Executions · last 7 days",
    ex ? ms(ex.trend.reduce((n, d) => n + d.passed + d.failed, 0), "This week") + ms(ex.trend.reduce((n, d) => n + d.passed, 0), "Passed")
      + ms(ex.trend.reduce((n, d) => n + d.failed, 0), "Failed") : "",
    ex ? dayColumns(ex.trend, [{ key: "passed", label: "Passed", color: C.ok }, { key: "failed", label: "Failed", color: C.fail }])
      : `<p class="muted small">Not available.</p>`, "wide");

  const kinds = ins.runs_by_kind || [];
  const runsCard = chartCard("Agent runs by type",
    ms(kinds.reduce((n, k) => n + k.passed + k.failed + k.other, 0), "Runs") + ms(kinds.reduce((n, k) => n + k.passed, 0), "Passed")
      + ms(kinds.reduce((n, k) => n + k.failed, 0), "Failed"),
    hbars(kinds.slice(0, 8).map((k) => ({
      label: kindLabel(k.kind), href: "#/history",
      parts: [{ value: k.passed, color: C.ok, label: "Passed" }, { value: k.failed, color: C.fail, label: "Failed" },
        { value: k.other, color: C.muted, label: "Running or other" }],
    })), { empty: "No runs yet" })
      + (kinds.length ? `<div class="legend-inline"><span><i style="background:${C.ok}"></i>Passed</span><span><i style="background:${C.fail}"></i>Failed</span><span><i style="background:${C.muted}"></i>Running or other</span></div>` : ""));

  const agentsCard = chartCard("Agents right now", "", `<div id="agentsDonut">${agentsDonut()}</div>`);

  const slaCard = perf.sla_pass + perf.sla_fail ? chartCard("Performance SLA", "", donut([
    { label: "SLA passed", value: perf.sla_pass, color: C.ok, href: "#/perf-results" },
    { label: "SLA failed", value: perf.sla_fail, color: C.fail, href: "#/perf-results" },
  ], { center: pct(perf.sla_pass_rate), sub: "SLA PASS" })) : "";

  area.innerHTML = `
    <h3 style="margin:0 0 10px">${esc(tfs.label)}${tfs.project ? " · " + esc(tfs.project) : ""}</h3>
    ${tfs.connected ? "" : `<p class="muted">${esc(tfs.reason)} <a href="#/tfs-settings">TFS connection settings</a>. Figures come only from your configured project; nothing is estimated.</p>`}
    <div class="grid kpis">${quality.join("")}</div>
    <h3 style="margin:22px 0 10px">QA progress</h3>
    <section class="card qa-progress">${progress}</section>
    <h3 style="margin:22px 0 10px">Statistics</h3>
    <div class="dash-charts">
      ${passCard}${trendCard}${storiesCard}${casesCard}${dataCard}${defectsCard}${runsCard}${agentsCard}
    </div>
    <h3 style="margin:22px 0 10px">Performance</h3>
    <div class="grid kpis">${performance.join("")}</div>
    ${slaCard ? `<div class="dash-charts" style="margin-top:14px">${slaCard}</div>` : ""}`;
}

export async function mount(view) {
  view.innerHTML = `<div id="kpiArea" class="dash"><p class="muted">Loading…</p></div>`;
  await render(view);
  const redrawAgents = () => { const el = view.querySelector("#agentsDonut"); if (el) el.innerHTML = agentsDonut(); };
  // The agent chart follows runtime events; the figures reload only when work finishes (debounced).
  unsub = subscribe((c) => {
    if (c.type === "snapshot") { redrawAgents(); return; }
    if (c.type !== "event") return;
    if (c.agent) redrawAgents();
    if (!["agent.completed", "agent.failed", "defect.created"].includes(c.event.name)) return;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => render(view), 1500);
  });
  let tick = 0;
  stopTick = onTick(() => { if (++tick % 5 === 0) redrawAgents(); }); // COMPLETED turns back to READY after 30 s
}

export function unmount() {
  if (unsub) unsub();
  if (stopTick) stopTick();
  clearTimeout(refreshTimer);
  unsub = stopTick = null;
}
