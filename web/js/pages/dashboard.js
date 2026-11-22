// Dashboard (23.3.14): Azure DevOps figures, QA progress, statistics with pie and bar charts, and Performance.
// The agent cards and the recent-runs table moved off the dashboard (agents are on Agent Workflow, runs on
// Execution History). Every figure comes from TFS or recorded results; a missing figure shows "—".

import { api } from "../api.js";
import { state, subscribe, onTick } from "../store.js";
import { agentState } from "../agent-state.js";
import { esc } from "../util.js";
import { donut, dayColumns, hbars, progressRow } from "../dash-charts.js";
import { mountAiReport } from "../ai-report.js";

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
  if (!area) return;  // the page was left before a refresh started
  let summary, a, itest = null;
  try {
    [summary, a] = await Promise.all([api("/api/dashboard/summary"), api("/api/analytics/summary")]);
    itest = await api("/api/itest/summary").catch(() => null);  // 23.14.0: optional; never breaks the dashboard
    if (!area.isConnected) return;
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
      ac.available ? `on ${ac.stories_with_criteria} of ${ac.stories} user stories` : tfs.connected ? (ac.reason || "Not available") : notConnected)),
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
    ex ? dayColumns(ex.trend, [{ key: "passed", label: "Passed", color: C.ok }, { key: "failed", label: "Failed", color: C.fail }],
      { hrefFor: (date, key) => `#/history?date=${date}${key === "failed" ? "&status=FAILED" : ""}` })
      : `<p class="muted small">Not available.</p>`, "wide");

  const kinds = ins.runs_by_kind || [];
  const runsCard = chartCard("Agent runs by type",
    ms(kinds.reduce((n, k) => n + k.passed + k.failed + k.other, 0), "Runs") + ms(kinds.reduce((n, k) => n + k.passed, 0), "Passed")
      + ms(kinds.reduce((n, k) => n + k.failed, 0), "Failed"),
    hbars(kinds.slice(0, 8).map((k) => ({
      label: kindLabel(k.kind), href: `#/history?kind=${encodeURIComponent(k.kind)}`,
      parts: [{ value: k.passed, color: C.ok, label: "Passed" }, { value: k.failed, color: C.fail, label: "Failed" },
        { value: k.other, color: C.muted, label: "Running or other" }],
    })), { empty: "No runs yet" })
      + (kinds.length ? `<div class="legend-inline"><span><i style="background:${C.ok}"></i>Passed</span><span><i style="background:${C.fail}"></i>Failed</span><span><i style="background:${C.muted}"></i>Running or other</span></div>` : ""));

  const agentsCard = chartCard("Agents right now", "", `<div id="agentsDonut">${agentsDonut()}</div>`);

  const slaCard = perf.sla_pass + perf.sla_fail ? chartCard("Performance SLA", "", donut([
    { label: "SLA passed", value: perf.sla_pass, color: C.ok, href: "#/perf-results" },
    { label: "SLA failed", value: perf.sla_fail, color: C.fail, href: "#/perf-results" },
  ], { center: pct(perf.sla_pass_rate), sub: "SLA PASS" })) : "";

  // 23.13.0: more charts, each slice / bar opens the matching items
  const MODULE = { framework: "AI-Driven Automation", functional: "Guided Browser", python: "Guided Browser (Python)",
    comparison: "Integration Comparison", api: "API Testing", performance: "Performance" };
  const MODULE_COLOR = [C.run, C.accent, C.violet, C.warn, C.pink, C.fail, C.ok, C.muted];
  const byModule = Object.entries(ins.defects_by_module || {});
  const modulesCard = chartCard("Defect drafts by module", ms(byModule.reduce((n, [, v]) => n + v, 0), "Drafts"),
    donut(byModule.map(([k, v], i) => ({ label: MODULE[k] || k, value: v, color: MODULE_COLOR[i % MODULE_COLOR.length],
      href: `#/defect-review?source=${encodeURIComponent(k)}&status=all` })), { sub: "DRAFTS", empty: "No defect drafts yet" }));
  const apiR = ins.api_results || null;
  const apiCard = chartCard("API Testing results",
    apiR ? ms(apiR.passed + apiR.failed + apiR.other, "Checks") + ms(apiR.passed, "Passed") + ms(apiR.failed, "Failed") : "",
    apiR ? donut([
      { label: "Passed", value: apiR.passed, color: C.ok, href: "#/api-testing" },
      { label: "Failed", value: apiR.failed, color: C.fail, href: "#/api-testing" },
      { label: "Other (skipped, error)", value: apiR.other, color: C.muted, href: "#/api-testing" },
    ], { sub: "API CHECKS", empty: "No API tests run yet" }) : '<p class="muted small">Not available.</p>');
  const heal = ins.healing || null;
  const healCard = chartCard("Self-healing",
    heal ? ms(heal.events, "Locator fixes") + ms(heal.applied, "Kept in the scripts") : "",
    heal ? donut(Object.entries(heal.by_result || {}).map(([k, v], i) => ({ label: k.replace(/_/g, " "), value: v,
      color: { validated: C.ok, taught: C.accent, healed: C.run, failed: C.fail, rejected: C.muted }[k] || MODULE_COLOR[i % MODULE_COLOR.length],
      href: "#/history" })), { sub: "FIXES", empty: "No fixes yet" }) : '<p class="muted small">Not available.</p>');
  area.innerHTML = `
    <h3 style="margin:0 0 10px">${esc(tfs.label)}${tfs.project ? " · " + esc(tfs.project) : ""}</h3>
    ${tfs.connected ? "" : `<p class="muted">${esc(tfs.reason)} <a href="#/tfs-settings">TFS connection settings</a>. Figures come only from your configured project; nothing is estimated.</p>`}
    <p class="muted small" style="margin:0 0 8px">Click a slice, a bar or a day to see the items behind it.</p>
    <div class="dash-charts">
      ${passCard}${trendCard}${defectsCard}${modulesCard}${casesCard}${storiesCard}${apiCard}${healCard}${dataCard}${runsCard}${agentsCard}${slaCard}
    </div>
    ${itestSection(itest)}
    <h3 style="margin:22px 0 10px">Key figures</h3>
    <div class="grid kpis">${quality.join("")}</div>
    <h3 style="margin:22px 0 10px">QA progress</h3>
    <section class="card qa-progress">${progress}</section>
    <h3 style="margin:22px 0 10px">Performance</h3>
    <div class="grid kpis">${performance.join("")}</div>`;
}

// 23.14.0: Security Testing, Red Hat Fuse, ActiveMQ and the shared result actions, from recorded results only. Every
// slice and figure opens the screen with the items behind it.
function itestSection(d) {
  if (!d) return "";
  const sec = d.security || {}, fuse = d.fuse || {}, amq = d.activemq || {}, sh = d.shared || {};
  const na = (m) => `<p class="muted small">${esc(m.error || "This module is not available.")}</p>`;
  const sevColor = { critical: C.fail, high: C.pink, medium: C.warn, low: C.accent, info: C.muted };
  const secCard = chartCard("Security findings", sec.unavailable ? "" : link("#/sec-findings", ms(sec.open, "Open")) + link("#/sec-scanning", ms(sec.assessments, "Assessments")) + link("#/sec-findings?status=open", ms(sec.pending_review, "To review")) + link("#/sec-regression", ms(sec.retests, "Retests")),
    sec.unavailable ? na(sec) : donut(["critical", "high", "medium", "low", "info"].map((k) => ({ label: k[0].toUpperCase() + k.slice(1), value: (sec.by_severity || {})[k] || 0,
      color: sevColor[k], href: `#/sec-findings?severity=${k}` })), { sub: "OPEN", empty: "No open finding" }));
  const fuseCard = chartCard("Red Hat Fuse integration", fuse.unavailable ? "" : link("#/fuse-testing?tab=connections", ms(fuse.connections, "Connections")) + link("#/fuse-testing?tab=monitoring", ms(fuse.routes_down, "Routes down")) + link("#/fuse-testing?tab=results", ms(fuse.unverified, "Unverified")),
    fuse.unavailable ? na(fuse) : donut([
      { label: "Passed", value: fuse.passed || 0, color: C.ok, href: "#/fuse-testing?tab=results" },
      { label: "Transformation failed", value: fuse.transformation_failed || 0, color: C.fail, href: "#/fuse-testing?tab=results" },
      { label: "Routing failed", value: fuse.routing_failed || 0, color: C.pink, href: "#/fuse-testing?tab=results" },
      { label: "Reliability failed", value: fuse.reliability_failed || 0, color: C.warn, href: "#/fuse-testing?tab=results" },
      { label: "End-to-end failed", value: fuse.e2e_failed || 0, color: C.violet, href: "#/fuse-testing?tab=results" },
    ], { sub: "RESULTS", empty: "No Fuse test run yet" }));
  const amqCard = chartCard("ActiveMQ messaging", amq.unavailable ? "" : link("#/activemq-testing?tab=connections", ms(amq.brokers, "Brokers")) + link("#/activemq-testing?tab=monitoring", ms(amq.health_alerts, "Health alerts")) + link("#/activemq-testing?tab=dlq", ms(amq.dlq_tests, "DLQ tests")),
    amq.unavailable ? na(amq) : donut([
      { label: "Published and verified", value: amq.published_verified || 0, color: C.ok, href: "#/activemq-testing?tab=results" },
      { label: "Other passed", value: Math.max(0, (amq.passed || 0) - (amq.published_verified || 0)), color: C.accent, href: "#/activemq-testing?tab=results" },
      { label: "Publish failed", value: amq.publish_failed || 0, color: C.fail, href: "#/activemq-testing?tab=results" },
      { label: "Consumer failed", value: amq.consumer_failed || 0, color: C.pink, href: "#/activemq-testing?tab=consumer" },
      { label: "Dead-letter failed", value: amq.dlq_failed || 0, color: C.warn, href: "#/activemq-testing?tab=dlq" },
      { label: "Other failed", value: Math.max(0, (amq.failed || 0) - (amq.publish_failed || 0) - (amq.consumer_failed || 0) - (amq.dlq_failed || 0)), color: C.violet, href: "#/activemq-testing?tab=results" },
    ], { sub: "RESULTS", empty: "No broker test run yet" }));
  const sharedCard = chartCard("Analysis, defects, reports and email",
    ms(sh.ai_analyses, "AI analyses") + ms(sh.pending_analyses, "Failures not analyzed") + ms(sh.defect_drafts, "Defect drafts"),
    `<div class="ms-row">${link("#/sec-reports", ms(sh.reports, "PDF reports"))}${link("#/email-settings", ms(sh.emails_accepted, "Emails accepted"))}${link("#/email-settings", ms(sh.emails_failed, "Emails failed"))}${link("#/email-settings", ms(sh.emails_draft, "Email drafts"))}${ms(sh.retests, "Retests")}</div>
     <p class="muted small" style="margin:8px 0 0">"Accepted" means the SMTP server took the email; delivery is not confirmed by SMTP.</p>`);
  return `<h3 style="margin:22px 0 10px">Security and integration testing</h3><div class="dash-charts-itest">${secCard}${fuseCard}${amqCard}${sharedCard}</div>`;
}

export async function mount(view) {
  // 23.3.31: AI dashboard analysis (kept outside the figures, which reload when work finishes, so edits are not lost)
  // 23.13.0: the charts come first; the AI analysis report is at the bottom
  view.innerHTML = `<div id="kpiArea" class="dash"><p class="muted">Loading…</p></div>
    <section class="card ai-dash" style="margin-top:22px"><h3 style="margin:0 0 8px">AI dashboard analysis</h3><div id="aiDash"></div></section>`;
  mountAiReport(view.querySelector("#aiDash"), { kind: "dashboard", id: "current", analyzeLabel: "Generate AI analysis",
    intro: "The On-Premise AI reads the figures on this dashboard (TFS counts, acceptance criteria, coverage, test data, pass / fail, defects and performance) and writes what they show, the gaps and risks, and the next steps. Read it, edit it or ask again; when you are satisfied, generate the PDF, download it or email it." });
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
