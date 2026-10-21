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
      const r = isNew ? await api("/api/performance/plans", { method: "POST", body })
        : await api(`/api/performance/plans/${encodeURIComponent(plan.id)}`, { method: "PUT", body });
      toast("Plan saved", "success");
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
      view.querySelector("#done").innerHTML = "";
      runBtn.disabled = true; stopBtn.hidden = false;
    } catch (err) { toast(err.message, "error"); }
  });
  stopBtn.addEventListener("click", () => runId && api(`/api/performance/runs/${encodeURIComponent(runId)}/stop`, { method: "POST", body: {} })
    .then(() => toast("Stopping JMeter; results so far will be kept", "info")).catch((err) => toast(err.message, "error")));
  feed(view.querySelector("#feed"), () => runId, (ev) => {
    const finished = (ev.agent_id === "performance_engineering" && ev.name === "agent.completed") || (ev.agent_id === "jmeter_load" && ev.name === "agent.failed");
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

async function runDetail(view, runId) {
  const { run } = await api(`/api/performance/runs/${encodeURIComponent(runId)}`);
  const m = run.metrics, sla = run.sla;
  const o = m?.overall;
  const file = (path) => `/api/performance/runs/${encodeURIComponent(run.run_id)}/files/${path}`;
  view.innerHTML = `
    <p><a href="#/perf-results">← Performance Results</a></p>
    <div class="card"><div class="row"><h2 style="margin:0">${esc(KINDS[run.kind])} test: ${esc(run.name)}</h2>
      <span class="pill ${esc(run.status)}">${esc(run.status)}</span>
      ${sla ? `<span class="pill ${sla.status === "FAIL" ? "FAIL" : sla.status === "PASS" ? "PASS" : "WAITING"}">SLA ${esc(sla.status.replace("_", " "))}</span>` : ""}
      <span class="spacer"></span>
      ${sla?.status === "FAIL" ? '<button class="btn" id="analyzeFailure">Analyze failure</button>' : ""}
      ${run.has_report ? `<a class="btn" href="${file("report/index.html")}" target="_blank" rel="noopener">JMeter HTML report</a>` : ""}
      <a class="btn" href="#/${ROUTE_FOR_KIND[run.kind]}?plan=${esc(run.plan_id)}">Open plan</a></div>
      <p class="muted">${esc(run.run_id)} · started ${esc(dateTimeOf(run.started_at))}${m ? ` · ${fmtDuration(Math.round(m.duration_s))} · peak ${m.peak_threads} users` : ""}${m?.stopped_by_user ? " · stopped by user" : ""}</p>
      ${run.error ? `<p class="pill FAIL">${esc(run.error)}</p>` : ""}
      <p class="muted">Files: ${run.files.map((f) => `<a href="${file(f)}" target="_blank" rel="noopener">${esc(f)}</a>`).join(" · ")}</p></div>
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
      </tbody></table>` : '<p class="muted">No SLA limits were set on this plan.</p>'}</div>
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
  const { items } = await api("/api/performance/runs?limit=200");
  view.innerHTML = `<div class="card">${items.length ? `<table class="data"><thead><tr><th>Run</th><th>Type</th><th>Plan</th><th>Status</th><th>SLA</th><th>Samples</th><th>Errors</th><th>95th pct</th><th>Req/s</th><th>Started</th></tr></thead><tbody>
    ${items.map((r) => `<tr class="clickable" data-run="${esc(r.run_id)}"><td>${esc(r.run_id)}</td><td>${esc(KINDS[r.kind])}</td><td>${esc(r.name)}</td>
      <td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td>
      <td>${r.sla_status ? `<span class="pill ${r.sla_status === "FAIL" ? "FAIL" : r.sla_status === "PASS" ? "PASS" : "WAITING"}">${esc(r.sla_status.replace("_", " "))}</span>` : ""}</td>
      <td>${r.overall ? r.overall.samples.toLocaleString() : ""}</td><td>${r.overall ? `${r.overall.error_pct}%` : ""}</td>
      <td>${r.overall ? ms(r.overall.p95_ms) : ""}</td><td>${r.overall ? r.overall.throughput : ""}</td><td>${esc(dateTimeOf(r.started_at))}</td></tr>`).join("")}
    </tbody></table>` : '<p class="muted">No performance runs yet. Create a plan under Performance Test Plans and run it with JMeter.</p>'}</div>`;
  view.querySelectorAll("tr[data-run]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/perf-results?run=${encodeURIComponent(tr.dataset.run)}`; }));
}

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
