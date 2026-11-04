// Performance Testing: plans (load, stress, spike, soak), JMeter tool settings, live runs and results.

import { api } from "../api.js";
import { subscribe } from "../store.js";
import { esc, toast, timeOf, dateTimeOf, isRepeat } from "../util.js";
import { lineChart, fmtDuration } from "../charts.js";
import { analyzeRun } from "./defect-review.js";

const KINDS = { load: "Load", stress: "Stress", spike: "Spike", soak: "Soak" };
const ROUTE_FOR_KIND = { load: "perf-load", stress: "perf-stress", spike: "perf-spike", soak: "perf-soak" };
const KIND_HELP = {
  load: "Expected traffic: users ramp up and stay for the test duration.",
  stress: "Users are added in equal steps across the duration to find where response times or errors break the SLA.",
  spike: "A steady baseline plus a sudden burst of extra users for a short window.",
  soak: "Steady load for a long duration (hours) to reveal slow degradation and leaks.",
};
const DEFAULT_PROFILES = {
  load: { users: 20, ramp_up_s: 60, duration_s: 600, think_time_ms: 1000 },
  stress: { users: 200, ramp_up_s: 60, duration_s: 1200, steps: 5, think_time_ms: 500 },
  spike: { users: 20, ramp_up_s: 30, duration_s: 600, spike_users: 200, spike_at_s: 240, spike_duration_s: 60, think_time_ms: 500 },
  soak: { users: 30, ramp_up_s: 120, duration_s: 4 * 3600, think_time_ms: 2000 },
};

// 23.3.22: execution state (what happened to the run) and verdict (what the numbers say) are shown separately.
const STATE_PILL = { QUEUED: "WAITING", VALIDATING: "RUNNING", RUNNING: "RUNNING", STOPPING: "RUNNING", COMPLETED: "PASS",
  FAILED: "FAIL", CANCELLED: "WARN", INTERRUPTED: "WARN", SKIPPED: "WARN" };
const STATE_LABEL = { QUEUED: "Queued", VALIDATING: "Validating", RUNNING: "Running", STOPPING: "Stopping", COMPLETED: "Completed",
  FAILED: "Failed", CANCELLED: "Cancelled", INTERRUPTED: "Interrupted", SKIPPED: "Skipped" };
const VERDICT_PILL = { PASS: "PASS", FAIL: "FAIL", NOT_SPECIFIED: "WAITING", INCONCLUSIVE: "WARN" };
const VERDICT_LABEL = { PASS: "Targets met", FAIL: "Targets missed", NOT_SPECIFIED: "Target not specified", INCONCLUSIVE: "Inconclusive (partial run)" };
const ACTIVE = ["VALIDATING", "RUNNING", "STOPPING"];
const statePill = (st) => `<span class="pill ${STATE_PILL[st] || "WAITING"}" title="Execution state">${esc(STATE_LABEL[st] || st || "")}</span>`;
const verdictPill = (v) => (v ? `<span class="pill ${VERDICT_PILL[v] || "WAITING"}" title="Performance verdict">${esc(VERDICT_LABEL[v] || v)}</span>` : '<span class="muted">No verdict</span>');
const CHECK_PILL = { PASS: "PASS", WARN: "WARN", FAIL: "FAIL" };
const TRIGGERS = { manual: "Manual", voice: "Voice", schedule: "Scheduled", missed: "Missed schedule" };

function checksTable(check) {
  if (!check) return '<p class="muted">Not checked yet.</p>';
  return `<table class="data"><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>
    ${check.checks.map((c) => `<tr><td>${esc(c.name)}</td><td><span class="pill ${CHECK_PILL[c.status]}">${esc(c.status)}</span></td>
      <td>${esc(c.detail)}${c.fix && c.status !== "PASS" ? `<div class="muted">${esc(c.fix)}</div>` : ""}</td></tr>`).join("")}</tbody></table>`;
}

let cleanups = [];
const params = () => new URLSearchParams(location.hash.split("?")[1] || "");
const routeName = () => location.hash.replace(/^#\/?/, "").split("?")[0];
const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

function feed(container, getRunId, onEvent) {
  let last = null;
  cleanups.push(subscribe((c) => {
    if (c.type !== "event" || !getRunId() || c.event.run_id !== getRunId()) return;
    const repeat = isRepeat(last, c.event);
    last = c.event;
    if (!repeat) {
      container.insertAdjacentHTML("beforeend", `<div class="feed-item ${c.event.name.endsWith("failed") ? "failed" : ""}"><span class="feed-time">${esc(timeOf(c.event.timestamp))}</span><span class="feed-text">${esc((c.event.agent_name ? c.event.agent_name + " · " : "") + (c.event.activity || c.event.name))}</span></div>`);
      container.scrollTop = container.scrollHeight;
    }
    onEvent?.(c.event);
  }));
}

// ---- tool settings -------------------------------------------------------------------------

async function environmentCard(box) {
  const env = await api("/api/performance/environment");
  box.innerHTML = `
    <div class="row"><h3 style="margin:0">JMeter and Java</h3>
      <span class="pill ${env.ready ? "PASS" : "WARN"}">${env.ready ? "READY" : "NOT READY"}</span></div>
    <p class="muted">${env.java ? `Java: ${esc(env.java_version || "")} <code>${esc(env.java)}</code>` : "Java was not found."}<br>
      ${env.jmeter ? `JMeter: <code>${esc(env.jmeter)}</code>` : "JMeter was not found."}</p>
    <form class="row" id="envForm">
      <input name="jmeter_home" value="${esc(env.jmeter_home)}" placeholder="JMeter home, e.g. C:\\Tools\\apache-jmeter-5.6.3" style="flex:1;min-width:220px">
      <input name="java_home" value="${esc(env.java_home)}" placeholder="Java home (optional)" style="flex:1;min-width:180px">
      <button class="btn" type="submit">Save</button></form>
    <p class="muted">Leave empty to use the portable package's runtime folder, JMETER_HOME / JAVA_HOME, or PATH.</p>`;
  box.querySelector("#envForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/performance/environment", { method: "PUT", body: Object.fromEntries(new FormData(e.target)) });
      toast("Tool settings saved", "success");
      environmentCard(box);
    } catch (err) { toast(err.message, "error"); }
  });
}

// ---- plan list ---------------------------------------------------------------------------------

function planTable(items) {
  if (!items.length) return '<p class="muted">No plans yet.</p>';
  return `<table class="data"><thead><tr><th>Plan</th><th>Type</th><th>Requests</th><th>Peak users</th><th>Duration</th><th>Last run</th></tr></thead><tbody>
    ${items.map((p) => `<tr class="clickable" data-plan="${esc(p.id)}"><td>${esc(p.name)}<div class="muted">${esc(p.id)}</div></td>
      <td>${esc(KINDS[p.kind])}</td><td>${p.requests}</td><td>${p.peak_users}</td><td>${esc(fmtDuration(p.duration_s))}</td>
      <td>${p.last_run ? `<a href="#/perf-results?run=${esc(p.last_run.run_id)}"><span class="pill ${esc(p.last_run.status)}">${esc(p.last_run.status)}</span></a> <span class="muted">${esc(dateTimeOf(p.last_run.started_at))}</span>` : '<span class="muted">Never</span>'}</td></tr>`).join("")}
    </tbody></table>`;
}

async function planList(view, kind) {
  const [{ items }, { items: discoveries }] = await Promise.all([
    api("/api/performance/plans"), api("/api/automation/discoveries").catch(() => ({ items: [] }))]);
  const mine = kind ? items.filter((p) => p.kind === kind) : items;
  view.innerHTML = `
    ${kind ? `<p class="muted">${esc(KIND_HELP[kind])}</p>` : ""}
    <div class="card" id="env"><p class="muted">Checking JMeter…</p></div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr));margin-top:12px">
      <form class="card" id="fromDisc"><h3>Create from Application Discovery</h3>
        ${discoveries.length ? `<p class="muted">Uses the pages and GET API endpoints the discovery actually observed.</p>
          <div class="row"><select name="discovery_id">${discoveries.map((d) => `<option value="${esc(d.id)}">${esc(d.base_url)} · ${d.endpoints} endpoints · ${esc(dateTimeOf(d.created_at))}</option>`).join("")}</select>
          <select name="kind">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === (kind || "load") ? "selected" : ""}>${v}</option>`).join("")}</select>
          <button class="btn primary" type="submit">Create plan</button></div>`
          : '<p class="muted">Run an <a href="#/discovery">Application Discovery</a> first to capture your pages and API endpoints.</p>'}
      </form>
      <div class="card"><h3>Create manually</h3><p class="muted">Enter the requests yourself.</p>
        <a class="btn primary" href="#/${routeName()}?plan=new&kind=${kind || "load"}">New ${esc(KINDS[kind || "load"])} test plan</a></div>
    </div>
    <div class="card" style="margin-top:12px"><h3>${kind ? `${esc(KINDS[kind])} test plans` : "All performance test plans"}</h3><div id="plans">${planTable(mine)}</div></div>`;
  environmentCard(view.querySelector("#env"));
  view.querySelectorAll("tr[data-plan]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    location.hash = `#/${routeName()}?plan=${encodeURIComponent(tr.dataset.plan)}`;
  }));
  view.querySelector("#fromDisc").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      const { plan } = await api("/api/performance/plans/from-discovery", { method: "POST", body: { discovery_id: f.discovery_id, kind: f.kind } });
      location.hash = `#/${ROUTE_FOR_KIND[plan.kind]}?plan=${encodeURIComponent(plan.id)}`;
    } catch (err) { toast(err.message, "error"); }
  });
}

// ---- plan editor ---------------------------------------------------------------------------

function requestRow(r, i) {
  return `<tr data-i="${i}">
    <td><input data-f="name" value="${esc(r.name || "")}" required style="width:100%"></td>
    <td><select data-f="method">${["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"].map((m) => `<option ${m === r.method ? "selected" : ""}>${m}</option>`).join("")}</select></td>
    <td><input data-f="url" value="${esc(r.url || "")}" placeholder="https://…" required style="width:100%"></td>
    <td><textarea data-f="body" rows="1" placeholder="Request body">${esc(r.body || "")}</textarea></td>
    <td><textarea data-f="headers" rows="1" placeholder='{"Accept": "…"}'>${esc(Object.keys(r.headers || {}).length ? JSON.stringify(r.headers) : "")}</textarea></td>
    <td><input data-f="expect_status" value="${esc(r.expect_status || "")}" placeholder="2\\d\\d" style="width:70px"></td>
    <td><input data-f="expect_text" value="${esc(r.expect_text || "")}" style="width:100%"></td>
    <td><button type="button" class="link-btn" data-del title="Remove">✕</button></td></tr>`;
}

function readRequests(table) {
  return [...table.querySelectorAll("tbody tr")].map((tr) => {
    const g = (f) => tr.querySelector(`[data-f="${f}"]`).value.trim();
    let headers = {};
    if (g("headers")) {
      try { headers = JSON.parse(g("headers")); } catch { throw new Error(`Headers for "${g("name")}" are not valid JSON.`); }
    }
    return { name: g("name"), method: g("method"), url: g("url"), body: g("body") || null, headers,
      expect_status: g("expect_status") || null, expect_text: g("expect_text") || null };
  });
}

async function planEditor(view, planId) {
  const isNew = planId === "new";
  let plan, groups = [];
  if (isNew) {
    const kind = params().get("kind") || "load";
    plan = { name: "", kind, requests: [{ name: "Home page", method: "GET", url: "", headers: {} }], profile: { ...DEFAULT_PROFILES[kind] }, sla: { p95_ms: 2000, max_error_pct: 1 } };
  } else {
    ({ plan, thread_groups: groups } = await api(`/api/performance/plans/${encodeURIComponent(planId)}`));
  }
  const p = plan.profile, s = plan.sla;
  const field = (name, label, value, attrs = "") => `<label>${label}<input name="${name}" type="number" value="${value ?? ""}" ${attrs}></label>`;
  view.innerHTML = `
    <p><a href="#/${routeName()}">← ${esc(document.getElementById("pageTitle").textContent)}</a></p>
    <form id="planForm">
      <div class="card">
        <div class="row"><h2 style="margin:0">${isNew ? "New performance test plan" : esc(plan.name)}</h2>
          ${isNew ? "" : `<span class="muted">${esc(plan.id)}${plan.source ? ` · from ${esc(plan.source)}` : ""}</span>`}
          <span class="spacer"></span>
          ${isNew ? "" : `<a class="btn" href="/api/performance/plans/${esc(plan.id)}/jmx">Download JMX</a>
            <button class="btn" type="button" id="runBtn">Run with JMeter</button><button class="btn" type="button" id="stopBtn" hidden>Stop</button>`}
          <button class="btn primary" type="submit">Save</button></div>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-top:10px">
          <label>Name <input name="name" value="${esc(plan.name)}" required></label>
          <label>Test type <select name="kind">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === plan.kind ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          ${field("users", "Users", p.users, 'min="1"')}${field("ramp_up_s", "Ramp-up (s)", p.ramp_up_s, 'min="0"')}
          ${field("duration_s", "Duration (s)", p.duration_s, 'min="10"')}${field("think_time_ms", "Think time (ms)", p.think_time_ms, 'min="0"')}
          <span data-kind="stress">${field("steps", "Steps", p.steps ?? 4, 'min="1" max="50"')}</span>
          <span data-kind="spike">${field("spike_users", "Spike users", p.spike_users ?? 50, 'min="1"')}</span>
          <span data-kind="spike">${field("spike_at_s", "Spike starts at (s)", p.spike_at_s ?? 120, 'min="0"')}</span>
          <span data-kind="spike">${field("spike_duration_s", "Spike length (s)", p.spike_duration_s ?? 60, 'min="5"')}</span>
        </div>
        <p class="muted" id="kindHelp"></p>
        ${groups.length ? `<p class="muted">JMeter thread groups: ${groups.map((g) => `${esc(g.name)} ${g.users} users from ${fmtDuration(g.delay_s)} for ${fmtDuration(g.duration_s)}`).join(" · ")}</p>` : ""}
      </div>
      <div class="card" style="margin-top:12px"><h3>Service levels (SLA)</h3>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
          ${field("avg_ms", "Average ≤ (ms)", s.avg_ms)}${field("p90_ms", "90th pct ≤ (ms)", s.p90_ms)}
          ${field("p95_ms", "95th pct ≤ (ms)", s.p95_ms)}${field("p99_ms", "99th pct ≤ (ms)", s.p99_ms)}
          ${field("max_error_pct", "Error rate ≤ (%)", s.max_error_pct, 'step="0.1" min="0" max="100"')}
          ${field("min_throughput", "Throughput ≥ (req/s)", s.min_throughput, 'step="0.1"')}
        </div><p class="muted">Response-time and error limits are checked for every request and for the whole test. Leave a field empty for no limit.</p></div>
      <div class="card" style="margin-top:12px"><div class="row"><h3 style="margin:0">Requests</h3><span class="spacer"></span><button class="btn" type="button" id="addReq">Add request</button></div>
        <div style="overflow-x:auto"><table class="data steps" id="reqs"><thead><tr><th>Name</th><th>Method</th><th>URL</th><th>Body</th><th>Headers (JSON)</th><th>Expect code</th><th>Expect text</th><th></th></tr></thead>
          <tbody>${plan.requests.map(requestRow).join("")}</tbody></table></div></div>
    </form>
    <div class="card" id="live" style="margin-top:12px" hidden><h3>Live run</h3><div class="feed" id="feed"></div><p id="done"></p></div>
    ${isNew ? "" : `<div class="row" style="margin-top:12px"><button class="link-btn" id="delPlan">Delete this plan</button><span class="muted">Results of earlier runs are kept.</span></div>`}`;
  const form = view.querySelector("#planForm");
  const table = view.querySelector("#reqs");
  const syncKind = () => {
    const k = form.kind.value;
    view.querySelectorAll("[data-kind]").forEach((n) => (n.hidden = n.dataset.kind !== k));
    view.querySelector("#kindHelp").textContent = KIND_HELP[k];
  };
  form.kind.addEventListener("change", syncKind);
  syncKind();
  const bindDel = () => table.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => b.closest("tr").remove()));
  bindDel();
  view.querySelector("#addReq").addEventListener("click", () => {
    table.querySelector("tbody").insertAdjacentHTML("beforeend", requestRow({ method: "GET", headers: {} }, table.querySelectorAll("tbody tr").length));
    bindDel();
  });
  const collect = () => {
    const f = new FormData(form);
    const profile = {};
    for (const k of ["users", "ramp_up_s", "duration_s", "think_time_ms", "steps", "spike_users", "spike_at_s", "spike_duration_s"]) {
      const v = num(f.get(k));
      if (v !== null) profile[k] = v;
    }
    const sla = {};
    for (const k of ["avg_ms", "p90_ms", "p95_ms", "p99_ms", "max_error_pct", "min_throughput"]) sla[k] = num(f.get(k));
    return { name: f.get("name"), kind: f.get("kind"), profile, sla, requests: readRequests(table) };
  };
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const body = collect();
      const scenario = params().get("scenario");
      const r = isNew ? await api(`/api/performance/plans${scenario ? `?scenario_id=${encodeURIComponent(scenario)}` : ""}`, { method: "POST", body })
        : await api(`/api/performance/plans/${encodeURIComponent(plan.id)}`, { method: "PUT", body });
      toast("Plan saved", "success");
      if (isNew && params().get("scenario")) { location.hash = `#/perf-scenarios?scenario=${encodeURIComponent(params().get("scenario"))}`; return; }
      location.hash = `#/${ROUTE_FOR_KIND[r.plan.kind]}?plan=${encodeURIComponent(r.plan.id)}`;
      if (!isNew) planEditor(view, r.plan.id);
    } catch (err) {
      const detail = Array.isArray(err.details) ? `: ${err.details.map((d) => d.msg || JSON.stringify(d)).join("; ")}` : "";
      toast(err.message + detail, "error");
    }
  });
  if (isNew) return;
  let runId = null;
  const runBtn = view.querySelector("#runBtn"), stopBtn = view.querySelector("#stopBtn");
  runBtn.addEventListener("click", async () => {
    try {
      const r = await api(`/api/performance/plans/${encodeURIComponent(plan.id)}/run`, { method: "POST", body: {} });
      runId = r.run_id;
      view.querySelector("#live").hidden = false;
      view.querySelector("#feed").innerHTML = "";
      view.querySelector("#done").innerHTML = `<span class="muted">Queued as ${esc(runId)}. It starts when no other performance test is running.</span> <a href="#/perf-results?run=${esc(runId)}">Open run</a>`;
      runBtn.disabled = true; stopBtn.hidden = false;
    } catch (err) { toast(err.message, "error"); }
  });
  stopBtn.addEventListener("click", () => runId && api(`/api/performance/runs/${encodeURIComponent(runId)}/stop`, { method: "POST", body: {} })
    .then(() => toast("Stopping JMeter; results so far will be kept", "info")).catch((err) => toast(err.message, "error")));
  feed(view.querySelector("#feed"), () => runId, (ev) => {
    const finished = (ev.agent_id === "performance_engineering" && ev.name === "agent.completed") || (ev.agent_id === "jmeter_load" && ev.name === "agent.failed")
      || (ev.name === "performance.failed" && ev.details?.state === "FAILED") || (ev.name === "performance.completed" && ev.details?.state);
    if (!finished) return;
    runBtn.disabled = false; stopBtn.hidden = true;
    view.querySelector("#done").innerHTML = `<a class="btn primary" href="#/perf-results?run=${esc(runId)}">Open results</a>`;
  });
  view.querySelector("#delPlan").addEventListener("click", async () => {
    if (!confirm("Delete this plan? Results of earlier runs are kept.")) return;
    try { await api(`/api/performance/plans/${encodeURIComponent(plan.id)}`, { method: "DELETE" }); location.hash = `#/${routeName()}`; }
    catch (err) { toast(err.message, "error"); }
  });
}

// ---- results ------------------------------------------------------------------------------------

const ms = (v) => `${Math.round(v).toLocaleString()} ms`;

function kpi(label, value, note = "") {
  return `<div class="card"><div class="kpi-label">${esc(label)}</div><div class="kpi-value">${esc(value)}</div>${note ? `<div class="kpi-note">${esc(note)}</div>` : ""}</div>`;
}

function analysisHtml(a) {
  if (!a) return '<p class="muted">Ask the On-Premise AI to review these metrics. It only sees the numbers on this page and says when a cause cannot be determined from them.</p>';
  const list = (items) => items?.length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="muted">None.</p>';
  return `<p>${esc(a.summary)}</p>
    <strong>Observations</strong>${list(a.observations)}
    ${a.suspected_bottlenecks?.length ? `<strong>Suspected bottlenecks</strong><ul>${a.suspected_bottlenecks.map((b) => `<li><strong>${esc(b.area)}</strong> (${esc(b.confidence)} confidence): ${esc(b.evidence)}</li>`).join("")}</ul>` : ""}
    <strong>Recommendations</strong>${list(a.recommendations)}
    ${a.undetermined?.length ? `<strong>Not determinable from these results</strong>${list(a.undetermined)}` : ""}
    <p class="muted">AI analysis by ${esc(a.model || "the On-Premise AI")} on ${esc(dateTimeOf(a.created_at))}. Treat it as hypotheses to verify.</p>`;
}

function generatorCard(g) {
  if (!g) return "";
  if (!g.available) return `<div class="card" style="margin-top:12px"><h3>Load generator (this PC)</h3><p class="muted">${esc(g.reason || "Not monitored.")}</p></div>`;
  const st = g.stats || {};
  return `<div class="card" style="margin-top:12px"><div class="row"><h3 style="margin:0">Load generator (this PC)</h3>
      <span class="pill ${g.saturated ? "WARN" : "PASS"}">${g.saturated ? "SATURATED" : "OK"}</span></div>
    ${g.saturated ? `<p>The PC running JMeter was overloaded, which can inflate response times: ${g.flags.map((f) => `${esc(f.message)} (first at ${fmtDuration(Math.round(f.first_at_s))})`).join("; ")}.</p>` : ""}
    <p class="muted">CPU average ${st.cpu_avg_pct ?? "–"}%, peak ${st.cpu_max_pct ?? "–"}% · memory peak ${st.mem_max_pct ?? "–"}% · lowest free disk ${st.disk_min_free_mb != null ? st.disk_min_free_mb.toLocaleString() + " MB" : "–"} · JMeter memory peak ${st.jmeter_mem_max_mb ?? "–"} MB. Sampled every ${g.interval_s} s. Server and database metrics: not configured.</p>
    ${g.samples?.length > 1 ? '<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))"><div id="cGen"></div><div id="cGenMem"></div><div></div></div>' : ""}</div>`;
}

async function runDetail(view, runId) {
  const { run } = await api(`/api/performance/runs/${encodeURIComponent(runId)}`);
  const m = run.metrics, sla = run.sla;
  const o = m?.overall;
  const file = (path) => `/api/performance/runs/${encodeURIComponent(run.run_id)}/files/${path}`;
  view.innerHTML = `
    <p><a href="#/perf-results">← Performance Results</a></p>
    <div class="card"><div class="row"><h2 style="margin:0">${esc(KINDS[run.kind])} test: ${esc(run.name)}</h2>
      ${statePill(run.state)} ${verdictPill(run.verdict)}
      <span class="spacer"></span>
      ${run.state === "QUEUED" ? '<button class="btn" id="cancelRun">Cancel</button>' : ""}
      ${["VALIDATING", "RUNNING"].includes(run.state) ? '<button class="btn" id="stopRun" title="JMeter stops cleanly and writes its results and report">Stop</button>' : ""}
      ${ACTIVE.includes(run.state) ? '<button class="btn" id="killRun" title="Ends JMeter at once; the HTML report may be missing">Emergency stop</button>' : ""}
      ${run.verdict === "FAIL" ? '<button class="btn" id="analyzeFailure">Analyze failure</button>' : ""}
      ${run.has_report ? `<a class="btn" href="${file("report/index.html")}" target="_blank" rel="noopener">JMeter HTML report</a>` : ""}
      ${run.scenario_id ? `<a class="btn" href="#/perf-scenarios?scenario=${esc(run.scenario_id)}">Open scenario</a>` : ""}
      <a class="btn" href="#/${ROUTE_FOR_KIND[run.kind]}?plan=${esc(run.plan_id)}">Open plan</a></div>
      <p class="muted">${esc(run.run_id)} · ${esc(TRIGGERS[run.trigger] || run.trigger || "Manual")} · ${run.state === "QUEUED" ? `queued ${esc(dateTimeOf(run.queued_at))}` : `started ${esc(dateTimeOf(run.started_at))}`}${run.finished_at ? ` · ended ${esc(dateTimeOf(run.finished_at))}` : ""}${m ? ` · ${fmtDuration(Math.round(m.duration_s))} · peak ${m.peak_threads} users` : ""}${run.config?.environment ? ` · environment ${esc(run.config.environment.name)}` : ""}</p>
      ${run.partial ? '<p class="pill WARN">Partial results: the run did not complete, so these numbers cover only part of the planned load and cannot pass or fail it.</p>' : ""}
      ${run.state_reason ? `<p class="muted">${esc(run.state_reason)}</p>` : ""}
      ${run.error ? `<p class="pill FAIL">${esc(run.error)}</p>` : ""}
      ${run.live ? `<p>Live: ${run.live.samples.toLocaleString()} samples · avg ${esc(run.live.avg_ms)} ms (cumulative) · errors ${esc(run.live.error_pct)}% · ${run.live.threads} active users · ${fmtDuration(run.live.elapsed_s)} elapsed${run.live.generator ? ` · generator CPU ${run.live.generator.cpu_pct}%, memory ${run.live.generator.mem_pct}%` : ""}</p>` : ""}
      <p class="muted">Files: ${run.files.map((f) => `<a href="${file(f)}" target="_blank" rel="noopener">${esc(f)}</a>`).join(" · ") || "none yet"}</p></div>
    ${run.checks ? `<details class="card" style="margin-top:12px" ${run.checks.status === "FAIL" ? "open" : ""}><summary><strong>Pre-run checks</strong> <span class="pill ${CHECK_PILL[run.checks.status]}">${esc(run.checks.status)}</span></summary>${checksTable(run.checks)}</details>` : ""}
    ${generatorCard(run.generator)}
    ${o ? `
    <div class="grid kpis" style="margin-top:12px">
      ${kpi("Samples", o.samples.toLocaleString(), `${o.errors.toLocaleString()} errors`)}
      ${kpi("Error rate", `${o.error_pct}%`)}
      ${kpi("Average", ms(o.avg_ms), `min ${ms(o.min_ms)} · max ${ms(o.max_ms)}`)}
      ${kpi("95th percentile", ms(o.p95_ms), `90th ${ms(o.p90_ms)} · 99th ${ms(o.p99_ms)}`)}
      ${kpi("Throughput", `${o.throughput.toFixed(1)} req/s`, `${o.received_kb_s} KB/s received`)}
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr));margin-top:12px">
      <div class="card" id="cResp"></div><div class="card" id="cTput"></div>
      <div class="card" id="cErr"></div><div class="card" id="cUsers"></div>
    </div>
    <div class="card" style="margin-top:12px"><h3>SLA checks</h3>
      ${sla?.checks.length ? `<table class="data"><thead><tr><th>Check</th><th>Scope</th><th>Limit</th><th>Actual</th><th>Result</th></tr></thead><tbody>
        ${sla.checks.map((c) => `<tr><td>${esc(c.name)}</td><td>${esc(c.scope)}</td><td>${c.name === "Throughput" ? "≥" : "≤"} ${esc(c.limit)} ${esc(c.unit)}</td><td>${esc(c.actual)} ${esc(c.unit)}</td><td><span class="pill ${esc(c.status)}">${esc(c.status)}</span></td></tr>`).join("")}
      </tbody></table>` : '<p class="muted">Target not specified: no limits were set on this plan, so the measurements are reported without PASS or FAIL.</p>'}</div>
    <div class="card" style="margin-top:12px"><h3>Requests</h3><div style="overflow-x:auto">
      <table class="data"><thead><tr><th>Request</th><th>Samples</th><th>Errors</th><th>Avg</th><th>90th</th><th>95th</th><th>99th</th><th>Max</th><th>Req/s</th><th>Codes</th></tr></thead><tbody>
      ${[...m.requests, o].map((r) => `<tr${r === o ? ' style="font-weight:600"' : ""}><td>${esc(r.label)}</td><td>${r.samples}</td><td>${r.error_pct}%</td><td>${ms(r.avg_ms)}</td><td>${ms(r.p90_ms)}</td><td>${ms(r.p95_ms)}</td><td>${ms(r.p99_ms)}</td><td>${ms(r.max_ms)}</td><td>${r.throughput}</td>
        <td>${Object.entries(r.response_codes).map(([k, v]) => `${esc(k)}: ${v}`).join(", ")}</td></tr>`).join("")}</tbody></table></div>
      ${o.top_errors.length ? `<h3>Most frequent errors</h3><table class="data"><tbody>${o.top_errors.map((e) => `<tr><td>${esc(e.error)}</td><td>${e.count}</td></tr>`).join("")}</tbody></table>` : ""}
      <details><summary class="muted">Timeline table</summary><table class="data"><thead><tr><th>Time</th><th>Req/s</th><th>Avg</th><th>Errors</th><th>Users</th></tr></thead><tbody>
        ${m.timeline.map((t) => `<tr><td>${fmtDuration(t.t)}</td><td>${t.throughput}</td><td>${ms(t.avg_ms)}</td><td>${t.errors}</td><td>${t.threads}</td></tr>`).join("")}</tbody></table></details>
    </div>
    <div class="card" style="margin-top:12px"><div class="row"><h3 style="margin:0">AI performance analysis</h3><span class="spacer"></span>
      <button class="btn" id="analyze">${run.analysis ? "Analyze again" : "Analyze with On-Premise AI"}</button></div><div id="analysis">${analysisHtml(run.analysis)}</div></div>` : ""}`;
  view.querySelector("#analyzeFailure")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { await analyzeRun("performance", run.run_id); } catch (err) { toast(err.message, "error"); e.target.disabled = false; }
  });
  const act = (path, body, msg) => api(`/api/performance/runs/${encodeURIComponent(run.run_id)}/${path}`, { method: "POST", body })
    .then(() => { toast(msg, "info"); runDetail(view, run.run_id); }).catch((err) => toast(err.message, "error"));
  view.querySelector("#cancelRun")?.addEventListener("click", () => act("cancel", {}, "Run cancelled; it will not start"));
  view.querySelector("#stopRun")?.addEventListener("click", () => act("stop", { mode: "graceful" }, "Stopping JMeter; results so far will be kept"));
  view.querySelector("#killRun")?.addEventListener("click", () => {
    if (confirm("End JMeter at once? Results so far are kept, but JMeter's HTML report may be missing.")) act("stop", { mode: "emergency" }, "JMeter is being ended");
  });
  if (run.state === "QUEUED" || ACTIVE.includes(run.state)) {
    const timer = setTimeout(() => { if (params().get("run") === run.run_id) runDetail(view, run.run_id).catch(() => {}); }, 3000);
    cleanups.push(() => clearTimeout(timer));
  }
  if (run.generator?.samples?.length > 1 && view.querySelector("#cGen")) {
    lineChart(view.querySelector("#cGen"), run.generator.samples, { key: "cpu_pct", title: "Generator CPU (% of all cores)", unit: "%" });
    lineChart(view.querySelector("#cGenMem"), run.generator.samples, { key: "mem_pct", title: "Generator memory (% used)", unit: "%" });
  }
  if (!o) return;
  lineChart(view.querySelector("#cResp"), m.timeline, { key: "avg_ms", title: "Average response time (ms)", unit: "ms" });
  lineChart(view.querySelector("#cTput"), m.timeline, { key: "throughput", title: "Throughput (requests per second)", unit: "req/s" });
  lineChart(view.querySelector("#cErr"), m.timeline, { key: "errors", title: "Errors per interval", unit: "errors", color: "var(--fail)", integer: true });
  lineChart(view.querySelector("#cUsers"), m.timeline, { key: "threads", title: "Active users", unit: "users", integer: true });
  view.querySelector("#analyze").addEventListener("click", async (e) => {
    e.target.disabled = true;
    view.querySelector("#analysis").innerHTML = '<p class="muted">The On-Premise AI is reviewing the metrics…</p>';
    try { const { analysis } = await api(`/api/performance/runs/${encodeURIComponent(run.run_id)}/analysis`, { method: "POST", body: {} }); view.querySelector("#analysis").innerHTML = analysisHtml(analysis); }
    catch (err) { view.querySelector("#analysis").innerHTML = analysisHtml(run.analysis); toast(err.message, "error"); }
    finally { e.target.disabled = false; }
  });
}

async function runList(view) {
  const p = params();
  const filters = { q: p.get("q") || "", state: p.get("state") || "", verdict: p.get("verdict") || "", kind: p.get("kind") || "", trigger: p.get("trigger") || "" };
  const qs = new URLSearchParams({ limit: "300", ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) });
  const [{ items }, { items: queue }] = await Promise.all([api(`/api/performance/runs?${qs}`), api("/api/performance/queue")]);
  const opt = (name, values, labels) => `<select name="${name}"><option value="">All</option>${values.map((v) => `<option value="${v}" ${filters[name] === v ? "selected" : ""}>${esc(labels[v] || v)}</option>`).join("")}</select>`;
  view.innerHTML = `
    <div class="card"><div class="row"><h3 style="margin:0">Run queue</h3><span class="muted">Runs start one at a time on this PC, oldest first. Closing the browser does not stop them; closing the application does.</span></div>
      ${queue.length ? `<table class="data"><thead><tr><th>Run</th><th>Plan</th><th>State</th><th>Position</th><th>Queued</th><th></th></tr></thead><tbody>
        ${queue.map((r) => `<tr><td><a href="#/perf-results?run=${esc(r.run_id)}">${esc(r.run_id)}</a></td><td>${esc(r.name)}</td><td>${statePill(r.state)}</td>
          <td>${r.position ?? ""}</td><td>${esc(dateTimeOf(r.queued_at))}</td>
          <td>${r.state === "QUEUED" ? `<button class="link-btn" data-cancel="${esc(r.run_id)}">Cancel</button>` : `<button class="link-btn" data-stop="${esc(r.run_id)}">Stop</button>`}</td></tr>`).join("")}</tbody></table>`
        : '<p class="muted">Nothing is running or waiting.</p>'}</div>
    <form class="card row" id="runFilters" style="margin-top:12px;flex-wrap:wrap">
      <label>Search <input name="q" value="${esc(filters.q)}" placeholder="Run ID or plan"></label>
      <label>State ${opt("state", Object.keys(STATE_LABEL), STATE_LABEL)}</label>
      <label>Verdict ${opt("verdict", Object.keys(VERDICT_LABEL), VERDICT_LABEL)}</label>
      <label>Type ${opt("kind", Object.keys(KINDS), KINDS)}</label>
      <label>Started by ${opt("trigger", ["manual", "schedule"], TRIGGERS)}</label>
      <button class="btn" type="submit">Filter</button></form>
    <div class="card" style="margin-top:12px">${items.length ? `<table class="data"><thead><tr><th>Run</th><th>Type</th><th>Plan</th><th>State</th><th>Verdict</th><th>Samples</th><th>Errors</th><th>95th pct</th><th>Req/s</th><th>Started</th></tr></thead><tbody>
    ${items.map((r) => `<tr class="clickable" data-run="${esc(r.run_id)}"><td>${esc(r.run_id)}${r.trigger && r.trigger !== "manual" ? `<div class="muted">${esc(TRIGGERS[r.trigger] || r.trigger)}</div>` : ""}</td><td>${esc(KINDS[r.kind])}</td><td>${esc(r.name)}</td>
      <td>${statePill(r.state)}${r.partial ? ' <span class="muted">partial</span>' : ""}</td><td>${verdictPill(r.verdict)}</td>
      <td>${r.overall ? r.overall.samples.toLocaleString() : ""}</td><td>${r.overall ? `${r.overall.error_pct}%` : ""}</td>
      <td>${r.overall ? ms(r.overall.p95_ms) : ""}</td><td>${r.overall ? r.overall.throughput : ""}</td><td>${esc(dateTimeOf(r.started_at))}</td></tr>`).join("")}
    </tbody></table>` : '<p class="muted">No performance runs match. Create a scenario under Performance Scenarios and run it with JMeter.</p>'}</div>`;
  view.querySelectorAll("tr[data-run]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/perf-results?run=${encodeURIComponent(tr.dataset.run)}`; }));
  view.querySelector("#runFilters").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = Object.fromEntries([...new FormData(e.target)].filter(([, v]) => v));
    location.hash = `#/perf-results${Object.keys(f).length ? "?" + new URLSearchParams(f) : ""}`;
  });
  const post = (id, path, body) => api(`/api/performance/runs/${encodeURIComponent(id)}/${path}`, { method: "POST", body }).then(() => runList(view)).catch((err) => toast(err.message, "error"));
  view.querySelectorAll("[data-cancel]").forEach((b) => b.addEventListener("click", () => post(b.dataset.cancel, "cancel", {})));
  view.querySelectorAll("[data-stop]").forEach((b) => b.addEventListener("click", () => post(b.dataset.stop, "stop", { mode: "graceful" })));
  if (queue.length) {
    const timer = setTimeout(() => { if (routeName() === "perf-results" && !params().get("run")) runList(view).catch(() => {}); }, 4000);
    cleanups.push(() => clearTimeout(timer));
  }
}

// ---- 23.3.22: scenarios and target environments ----------------------------------------------------------

function envForm(e = {}) {
  return `<form class="grid env-form" data-id="${esc(e.id || "")}" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-top:8px">
    <label>Name <input name="name" value="${esc(e.name || "")}" required></label>
    <label>Base URL <input name="base_url" value="${esc(e.base_url || "")}" placeholder="https://qa.example.com"></label>
    <label>Allowed hosts <input name="allowed_hosts" value="${esc((e.allowed_hosts || []).join(", "))}" placeholder="qa.example.com, api.example.com"></label>
    <label>Max concurrent users <input name="max_users" type="number" min="1" value="${e.max_users ?? 50}" required></label>
    <label>Max duration (s) <input name="max_duration_s" type="number" min="10" value="${e.max_duration_s ?? ""}" placeholder="No limit"></label>
    <label class="row" style="align-items:center"><input type="checkbox" name="third_party" ${e.third_party ? "checked" : ""}> Public site we do not own</label>
    <label>Notes <input name="notes" value="${esc(e.notes || "")}"></label>
    <div class="row"><button class="btn primary" type="submit">${e.id ? "Save" : "Add environment"}</button>
      ${e.id ? `<button class="link-btn" type="button" data-del-env="${esc(e.id)}">Delete</button>` : ""}</div></form>`;
}

async function environmentsCard(box) {
  const { items } = await api("/api/performance/environments");
  box.innerHTML = `<details ${items.length ? "" : "open"}><summary><strong>Target environments and load limits</strong> <span class="muted">(${items.length})</span></summary>
    <p class="muted">Only test systems you are authorized to load. Before every run the requests' hosts are checked against the environment's allowed hosts and the peak users against its limit. Scenarios without an environment are limited to 50 users.</p>
    ${items.map((e) => `<div style="border-top:1px solid var(--border);padding-top:6px">${envForm(e)}</div>`).join("")}
    <div style="border-top:1px solid var(--border);padding-top:6px"><strong>New environment</strong>${envForm()}</div></details>`;
  box.querySelectorAll("form.env-form").forEach((form) => form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const body = { name: f.get("name"), base_url: f.get("base_url"), notes: f.get("notes"), third_party: f.get("third_party") === "on",
      allowed_hosts: String(f.get("allowed_hosts") || "").split(",").map((h) => h.trim()).filter(Boolean),
      max_users: Number(f.get("max_users")), max_duration_s: num(f.get("max_duration_s")) };
    try {
      const id = form.dataset.id;
      await api(`/api/performance/environments${id ? `/${encodeURIComponent(id)}` : ""}`, { method: id ? "PUT" : "POST", body });
      toast("Environment saved", "success");
      environmentsCard(box);
    } catch (err) { toast(err.message, "error"); }
  }));
  box.querySelectorAll("[data-del-env]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Delete this environment?")) return;
    try { await api(`/api/performance/environments/${encodeURIComponent(b.dataset.delEnv)}`, { method: "DELETE" }); environmentsCard(box); }
    catch (err) { toast(err.message, "error"); }
  }));
}

async function scenarioList(view) {
  const p = params();
  const f = { q: p.get("q") || "", kind: p.get("kind") || "", environment_id: p.get("env") || "", archived: p.get("archived") === "1" };
  const qs = new URLSearchParams({ q: f.q, archived: String(f.archived), ...(f.kind ? { kind: f.kind } : {}), ...(f.environment_id ? { environment_id: f.environment_id } : {}) });
  const [{ items }, { items: envs }] = await Promise.all([api(`/api/performance/scenarios?${qs}`), api("/api/performance/environments")]);
  view.innerHTML = `
    <p class="muted">A scenario is one performance journey: its script, target environment, linked user story, and every run with its result. Saving or editing never starts a load test.</p>
    <div class="card" id="env"><p class="muted">Checking JMeter…</p></div>
    <div class="card" id="envs" style="margin-top:12px"></div>
    <form class="card row" id="scFilters" style="margin-top:12px;flex-wrap:wrap">
      <label>Search <input name="q" value="${esc(f.q)}" placeholder="Name, story, project, tag"></label>
      <label>Type <select name="kind"><option value="">All</option>${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${f.kind === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label>Environment <select name="env"><option value="">All</option>${envs.map((e) => `<option value="${esc(e.id)}" ${f.environment_id === e.id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></label>
      <label class="row" style="align-items:center"><input type="checkbox" name="archived" value="1" ${f.archived ? "checked" : ""}> Archived</label>
      <button class="btn" type="submit">Filter</button><span class="spacer"></span>
      <a class="btn primary" href="#/perf-scenarios?scenario=new">New scenario</a></form>
    <div class="card" style="margin-top:12px">${items.length ? `<table class="data"><thead><tr><th>Scenario</th><th>Type</th><th>Environment</th><th>Script</th><th>Peak users</th><th>Last run</th></tr></thead><tbody>
      ${items.map((s) => `<tr class="clickable" data-sc="${esc(s.id)}"><td>${esc(s.name)}<div class="muted">${esc(s.id)}${s.project ? ` · ${esc(s.project)}` : ""}${s.user_story_id ? ` · story ${esc(s.user_story_id)}` : ""}</div></td>
        <td>${s.kind ? esc(KINDS[s.kind]) : '<span class="muted">–</span>'}</td><td>${esc(s.environment_name || "–")}</td>
        <td>${s.plan_name ? `${esc(s.plan_name)} <span class="muted">${s.requests} requests</span>` : '<span class="muted">No script yet</span>'}</td>
        <td>${s.peak_users ?? ""}</td>
        <td>${s.last_run ? `<a href="#/perf-results?run=${esc(s.last_run.run_id)}">${statePill(s.last_run.state)}</a> ${verdictPill(s.last_run.verdict)}` : '<span class="muted">Never</span>'}</td></tr>`).join("")}
      </tbody></table>` : `<p class="muted">${f.archived ? "No archived scenarios." : "No scenarios yet. Create one, then add its script."}</p>`}</div>`;
  environmentCard(view.querySelector("#env"));
  environmentsCard(view.querySelector("#envs"));
  view.querySelector("#scFilters").addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = Object.fromEntries([...new FormData(e.target)].filter(([, v]) => v));
    location.hash = `#/perf-scenarios${Object.keys(fd).length ? "?" + new URLSearchParams(fd) : ""}`;
  });
  view.querySelectorAll("tr[data-sc]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    location.hash = `#/perf-scenarios?scenario=${encodeURIComponent(tr.dataset.sc)}`;
  }));
}

async function scenarioEditor(view, id) {
  const isNew = id === "new";
  const [{ items: envs }, { items: plans }, detail] = await Promise.all([
    api("/api/performance/environments"), api("/api/performance/plans"),
    isNew ? Promise.resolve({ scenario: { name: "", environment_id: null, plan_id: null, tags: [] }, runs: [] }) : api(`/api/performance/scenarios/${encodeURIComponent(id)}`)]);
  const sc = detail.scenario, runs = detail.runs;
  const input = (name, label, attrs = "") => `<label>${label}<input name="${name}" value="${esc(sc[name] || "")}" ${attrs}></label>`;
  view.innerHTML = `
    <p><a href="#/perf-scenarios">← Performance Scenarios</a></p>
    <form class="card" id="scForm">
      <div class="row"><h2 style="margin:0">${isNew ? "New performance scenario" : esc(sc.name)}</h2>
        ${isNew ? "" : `<span class="muted">${esc(sc.id)}</span>${sc.archived ? ' <span class="pill WARN">Archived</span>' : ""}`}<span class="spacer"></span>
        ${isNew ? "" : `<button class="btn" type="button" id="checkBtn">Check before run</button>
          <button class="btn" type="button" id="runBtn" ${sc.plan_id && !sc.archived ? "" : "disabled"}>Run now</button>
          <button class="btn" type="button" id="cloneBtn">Clone</button>
          <button class="btn" type="button" id="archiveBtn">${sc.archived ? "Restore" : "Archive"}</button>`}
        <button class="btn primary" type="submit">Save</button></div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin-top:10px">
        ${input("name", "Name", "required")}${input("project", "Project")}
        <label>Target environment <select name="environment_id"><option value="">None (limit 50 users)</option>${envs.map((e) => `<option value="${esc(e.id)}" ${sc.environment_id === e.id ? "selected" : ""}>${esc(e.name)} · max ${e.max_users} users</option>`).join("")}</select></label>
        <label>Script (performance plan) <select name="plan_id"><option value="">No script yet</option>${plans.map((p) => `<option value="${esc(p.id)}" ${sc.plan_id === p.id ? "selected" : ""}>${esc(p.name)} · ${esc(KINDS[p.kind])} · ${p.requests} requests</option>`).join("")}</select></label>
        ${input("user_story_id", "User story ID")}${input("test_suite_id", "Test suite ID")}
        ${input("functional_script_id", "Related functional script ID")}
        <label>Tags <input name="tags" value="${esc((sc.tags || []).join(", "))}" placeholder="checkout, nightly"></label>
      </div>
      <label style="display:block;margin-top:8px">Description <textarea name="description" rows="2" style="width:100%">${esc(sc.description || "")}</textarea></label>
      <label style="display:block;margin-top:8px">Acceptance criteria (performance) <textarea name="acceptance_criteria" rows="2" style="width:100%" placeholder="e.g. Search p95 under 2 s at 10 users">${esc(sc.acceptance_criteria || "")}</textarea></label>
      ${!isNew ? `<p class="muted">${sc.plan_id ? `<a href="#/${ROUTE_FOR_KIND[sc.kind] || "perf-plans"}?plan=${esc(sc.plan_id)}">Edit the script, workload and targets</a> · ` : ""}<a href="#/perf-plans?plan=new&kind=load&scenario=${esc(sc.id)}">Create a new script for this scenario</a> · The functional browser script and this protocol load script stay separate; JMeter does not run browser JavaScript.</p>` : ""}
    </form>
    <div class="card" id="checks" style="margin-top:12px" hidden><h3>Pre-run checks</h3><div id="checkBody"></div></div>
    ${isNew ? "" : `<div class="card" style="margin-top:12px"><h3>Run history</h3>${runs.length ? `<table class="data"><thead><tr><th>Run</th><th>State</th><th>Verdict</th><th>Samples</th><th>95th pct</th><th>Errors</th><th>Started by</th><th>Started</th></tr></thead><tbody>
      ${runs.map((r) => `<tr class="clickable" data-run="${esc(r.run_id)}"><td>${esc(r.run_id)}</td><td>${statePill(r.state)}${r.partial ? ' <span class="muted">partial</span>' : ""}</td><td>${verdictPill(r.verdict)}</td>
        <td>${r.overall ? r.overall.samples.toLocaleString() : ""}</td><td>${r.overall ? ms(r.overall.p95_ms) : ""}</td><td>${r.overall ? `${r.overall.error_pct}%` : ""}</td>
        <td>${esc(TRIGGERS[r.trigger] || r.trigger || "")}</td><td>${esc(dateTimeOf(r.started_at))}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">No runs yet.</p>'}</div>`}`;
  const form = view.querySelector("#scForm");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const body = { ...sc, ...Object.fromEntries(["name", "project", "user_story_id", "test_suite_id", "functional_script_id", "description", "acceptance_criteria"].map((k) => [k, String(fd.get(k) || "").trim()])),
      environment_id: fd.get("environment_id") || null, plan_id: fd.get("plan_id") || null,
      tags: String(fd.get("tags") || "").split(",").map((t) => t.trim()).filter(Boolean) };
    for (const k of ["kind", "plan_name", "requests", "peak_users", "duration_s", "environment_name", "last_run", "script_missing"]) delete body[k];
    try {
      const r = await api(`/api/performance/scenarios${isNew ? "" : `/${encodeURIComponent(sc.id)}`}`, { method: isNew ? "POST" : "PUT", body });
      toast("Scenario saved. Nothing was run.", "success");
      if (isNew) location.hash = `#/perf-scenarios?scenario=${encodeURIComponent(r.scenario.id)}`; else scenarioEditor(view, sc.id);
    } catch (err) {
      const detailMsg = Array.isArray(err.details) ? `: ${err.details.map((d) => d.msg || JSON.stringify(d)).join("; ")}` : "";
      toast(err.message + detailMsg, "error");
    }
  });
  if (isNew) return;
  const post = (path, body) => api(`/api/performance/scenarios/${encodeURIComponent(sc.id)}/${path}`, { method: "POST", body });
  view.querySelector("#checkBtn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    const box = view.querySelector("#checks");
    box.hidden = false;
    view.querySelector("#checkBody").innerHTML = '<p class="muted">Checking Java, JMeter, the plan, disk space and the environment limits… (no requests are sent)</p>';
    try { view.querySelector("#checkBody").innerHTML = checksTable(await post("check", {})); }
    catch (err) { view.querySelector("#checkBody").innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; }
    finally { e.target.disabled = false; }
  });
  view.querySelector("#runBtn").addEventListener("click", async () => {
    try { const r = await post("run", {}); toast("Queued. It starts when no other performance test is running.", "success"); location.hash = `#/perf-results?run=${encodeURIComponent(r.run_id)}`; }
    catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#cloneBtn").addEventListener("click", async () => {
    const name = prompt("Name of the copy", `Copy of ${sc.name}`);
    if (name === null) return;
    try { const r = await post("clone", { name }); toast("Scenario cloned with its own copy of the script", "success"); location.hash = `#/perf-scenarios?scenario=${encodeURIComponent(r.scenario.id)}`; }
    catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#archiveBtn").addEventListener("click", async () => {
    try { await post("archive", { archived: !sc.archived }); toast(sc.archived ? "Scenario restored" : "Scenario archived; its runs are kept", "success"); scenarioEditor(view, sc.id); }
    catch (err) { toast(err.message, "error"); }
  });
  view.querySelectorAll("tr[data-run]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/perf-results?run=${encodeURIComponent(tr.dataset.run)}`; }));
}

export const perfScenarios = {
  async mount(view) {
    const id = params().get("scenario");
    if (id) await scenarioEditor(view, id); else await scenarioList(view);
  },
  unmount() { cleanups.forEach((fn) => fn()); cleanups = []; },
};

function page(kind) {
  return {
    async mount(view) {
      const planId = params().get("plan");
      if (planId) await planEditor(view, planId); else await planList(view, kind);
    },
    unmount() { cleanups.forEach((fn) => fn()); cleanups = []; },
  };
}

export const perfPlans = page(null);
export const perfLoad = page("load");
export const perfStress = page("stress");
export const perfSpike = page("spike");
export const perfSoak = page("soak");
export const perfResults = {
  async mount(view) {
    const runId = params().get("run");
    if (runId) await runDetail(view, runId); else await runList(view);
  },
  unmount() { cleanups.forEach((fn) => fn()); cleanups = []; },
};
