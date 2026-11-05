// 23.3.26: the Workload tab of the script editor. A shape (load, stress, spike, soak) replaces the users, ramp-up
// and duration of the script's thread groups in the copy JMeter runs; the saved script is unchanged. The request-rate
// model paces a fixed pool of users to a number of requests per second (it never adds users). The one-user validation
// run checks the script with one user before any load is sent.

import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";
import { fmtDuration, lineChart } from "../charts.js";

const SHAPES = {
  script: ["As in the script", "The users, ramp-up and duration set on the script's thread groups."],
  load: ["Load", "Ramp up to the users you expect, then hold. Checks the response times at normal load."],
  stress: ["Stress", "Add users in steps until something gives. Shows where the limit is."],
  spike: ["Spike", "A sudden burst on top of a steady baseline. Shows how the system copes and recovers."],
  soak: ["Soak", "A steady load for hours. Shows memory leaks and slow degradation."],
};
const RATE_SHAPES = ["load", "soak"];
const DEFAULTS = { shape: "script", model: "users", users: 10, ramp_up_s: 60, hold_s: 300, steps: 4, step_s: 120,
  spike_users: 50, spike_at_s: 120, spike_s: 60, spike_ramp_s: 5, target_rps: null };
const SHAPE_DEFAULTS = { soak: { hold_s: 4 * 3600, ramp_up_s: 300 } };

function fields(w) {
  const n = (name, label, attrs = 'min="0"') => `<label>${label}<input name="${name}" type="number" value="${w[name] ?? ""}" ${attrs}></label>`;
  const rate = w.model === "rate";
  if (w.shape === "load" || w.shape === "soak") {
    return `${rate ? n("target_rps", "Target requests per second (all requests)", 'min="0.01" step="0.01"') : ""}
      ${n("users", rate ? "User pool (most users busy at once)" : "Users", 'min="1"')}${n("ramp_up_s", "Ramp-up (s)")}
      ${n("hold_s", "Hold at full load (s)", 'min="10"')}`;
  }
  if (w.shape === "stress") {
    return `${n("users", "Users added per step", 'min="1"')}${n("steps", "Steps", 'min="1" max="50"')}
      ${n("step_s", "Each step lasts (s)", 'min="10"')}${n("ramp_up_s", "Ramp-up of each step (s)")}`;
  }
  if (w.shape === "spike") {
    return `${n("users", "Baseline users", 'min="1"')}${n("ramp_up_s", "Baseline ramp-up (s)")}${n("hold_s", "Baseline hold (s)", 'min="10"')}
      ${n("spike_users", "Extra users in the spike", 'min="1"')}${n("spike_at_s", "Spike starts at (s from the start)")}
      ${n("spike_s", "Spike lasts (s)", 'min="5"')}${n("spike_ramp_s", "Spike ramp-up (s)")}`;
  }
  return "";
}

function validationHtml(v, version, dirty) {
  if (!v) return '<p class="muted">This script has not had a validation run yet.</p>';
  const pill = { PASS: "PASS", FAIL: "FAIL", INCOMPLETE: "WARN" }[v.status] || "WAITING";
  const stale = v.script_version !== version;
  return `<p><span class="pill ${pill}">${esc(v.status)}</span> Version ${esc(v.script_version)} · ${v.requests} request(s), ${v.failed} failed${v.correlation_failures ? ` (${v.correlation_failures} correlation)` : ""} · one iteration took ${esc(v.iteration_s)} s · ${esc(dateTimeOf(v.at))}
    · <a href="#/perf-results?run=${encodeURIComponent(v.run_id)}">Open the run</a></p>
    ${stale ? `<p class="pill WARN">The script changed since (now version ${version}). Validate it again.</p>` : ""}
    ${dirty ? '<p class="muted">You have unsaved changes; the validation run uses the saved script.</p>' : ""}`;
}

export function workloadTab(pane, st, planId, { onSaved, save }) {
  const w = { ...DEFAULTS, ...(st.plan.workload || {}) };
  pane.innerHTML = `<div class="grid split-2">
      <div class="card"><h3 style="margin-top:0">Workload</h3>
        <form id="wl">
          <fieldset class="row" style="gap:16px;border:0;padding:0;margin:0 0 8px"><legend class="muted" style="padding:0">Load model</legend>
            <label class="row" style="gap:6px"><input type="radio" name="model" value="users" ${w.model === "users" ? "checked" : ""}> Concurrent users</label>
            <label class="row" style="gap:6px"><input type="radio" name="model" value="rate" ${w.model === "rate" ? "checked" : ""}> Request-rate target</label></fieldset>
          <p class="muted" id="modelHelp"></p>
          <div class="wl-shapes" role="radiogroup" aria-label="Shape">${Object.entries(SHAPES).map(([k, [label, help]]) => `
            <label class="wl-shape"><input type="radio" name="shape" value="${k}" ${w.shape === k ? "checked" : ""}><b>${label}</b><span class="muted">${help}</span></label>`).join("")}</div>
          <div class="grid" id="wlFields" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-top:8px"></div>
          <div class="row" style="margin-top:10px"><button class="btn primary" type="submit">Save workload</button>
            <span class="muted">Saving changes no script version and starts nothing.</span></div>
        </form></div>
      <div class="card" id="wlPreview"><p class="muted">Loading…</p></div></div>
    <div class="card" style="margin-top:12px"><div class="row"><h3 style="margin:0">One-user validation run</h3><span class="spacer"></span>
        <button class="btn" type="button" id="validateBtn">Run validation (1 user)</button></div>
      <p class="muted">Runs every thread group once with one user, one after another, against the targets of the scenario's environment. It checks status codes and assertions, and that each value a request's extractor should find was found (correlation). Throughput timers are off and no load is applied. Do this before a load test, after each change to the script.</p>
      <div id="wlValidation">${validationHtml(st.plan.validation, st.version, st.dirty)}</div></div>`;
  const form = pane.querySelector("#wl");
  const read = () => {
    const f = new FormData(form);
    const out = { ...w, shape: f.get("shape"), model: f.get("model") };
    for (const k of ["users", "ramp_up_s", "hold_s", "steps", "step_s", "spike_users", "spike_at_s", "spike_s", "spike_ramp_s", "target_rps"]) {
      if (form.elements[k]) out[k] = form.elements[k].value === "" ? null : Number(form.elements[k].value);
    }
    return out;
  };
  const renderFields = () => {
    const rate = w.model === "rate";
    form.querySelectorAll('input[name="shape"]').forEach((r) => {
      const off = rate && !RATE_SHAPES.includes(r.value) && r.value !== "script";
      r.disabled = off;
      r.closest("label").classList.toggle("off", off);
      r.closest("label").classList.toggle("sel", r.checked);
    });
    pane.querySelector("#modelHelp").textContent = rate
      ? "A fixed pool of users is paced to a number of requests per second (JMeter's Precise Throughput Timer). Users are never added to reach the rate: if responses slow down, the achieved rate drops, and the results show it next to the target. Works with the load and soak shapes."
      : "Each user repeats the script's flow. The number of requests per second follows from the response times and think time; it is not set here.";
    pane.querySelector("#wlFields").innerHTML = w.shape === "script" ? '<p class="muted" style="grid-column:1/-1">Edit the thread groups in the Tree tab to change users and duration.</p>' : fields(w);
  };
  let timer = null;
  const preview = async () => {
    const box = pane.querySelector("#wlPreview");
    if (w.shape === "script") {
      const sm = st.summary;
      box.innerHTML = `<h3 style="margin-top:0">As in the script</h3><p>Peak ${sm.peak_users} concurrent user(s) · ${sm.duration_known ? `duration ${fmtDuration(sm.total_duration_s)}` : "duration depends on loop counts"}.</p>
        <p class="muted">Load model: concurrent users. The request rate is not set; it follows from the response times.</p>`;
      return;
    }
    let p;
    try { p = await api("/api/performance/workload/preview", { method: "POST", body: { workload: w, plan_id: planId } }); }
    catch (err) { box.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; return; }
    box.innerHTML = `<h3 style="margin-top:0">${esc(SHAPES[w.shape][0])}${w.model === "rate" ? " · request-rate target" : ""}</h3>
      <div id="wlChart"></div>
      <p>Peak <b>${p.peak_users}</b> concurrent user(s) · duration <b>${fmtDuration(p.duration_s)}</b>${w.model === "rate" && w.target_rps ? ` · target <b>${esc(w.target_rps)}</b> requests per second` : ""}</p>
      ${p.problems.length ? `<div class="problems"><b>Fix before saving:</b><ul>${p.problems.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
      ${p.notes.length ? `<ul class="muted">${p.notes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <details><summary class="muted">Thread groups in the run</summary><table class="data"><thead><tr><th>Phase</th><th>Users</th><th>Starts at</th><th>Ramp-up</th><th>Runs for</th>${w.model === "rate" ? "<th>Rate</th>" : ""}</tr></thead><tbody>
        ${p.groups.map((g) => `<tr><td>${esc(g.name)}</td><td>${g.users}</td><td>${fmtDuration(g.delay_s)}</td><td>${fmtDuration(g.ramp_up_s)}</td><td>${fmtDuration(g.duration_s)}</td>${w.model === "rate" ? `<td>${g.rate_rps ?? "–"} req/s</td>` : ""}</tr>`).join("")}</tbody></table>
        <p class="muted">Each of the script's thread groups is copied per phase in the run's copy of the script, with all its requests. The users are split over the thread groups in proportion to the users each has in the script.</p></details>`;
    lineChart(box.querySelector("#wlChart"), p.curve.map(([t, u]) => ({ t, users: u })), { key: "users", title: "Planned concurrent users", unit: "users", integer: true });
  };
  const schedule = () => { clearTimeout(timer); timer = setTimeout(preview, 250); };
  form.addEventListener("change", (e) => {
    const prevShape = w.shape;
    Object.assign(w, read());
    if (e.target.name === "model" && w.model === "rate" && !RATE_SHAPES.includes(w.shape)) w.shape = "load";
    if (e.target.name === "shape" && w.shape !== prevShape && SHAPE_DEFAULTS[w.shape] && !(st.plan.workload?.shape === w.shape)) Object.assign(w, SHAPE_DEFAULTS[w.shape]);
    if (["model", "shape"].includes(e.target.name)) {
      form.querySelectorAll('input[name="shape"]').forEach((r) => (r.checked = r.value === w.shape));
      renderFields();
    }
    schedule();
  });
  form.addEventListener("input", (e) => { if (e.target.type === "number") { Object.assign(w, read()); schedule(); } });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    Object.assign(w, read());
    try {
      const r = await api(`/api/performance/plans/${encodeURIComponent(planId)}/workload`, { method: "PUT", body: w });
      st.plan = { ...st.plan, ...r.plan };
      onSaved(r.plan);
      toast("Workload saved. Nothing was run.", "success");
    } catch (err) { toast(err.message, "error"); }
  });
  pane.querySelector("#validateBtn").addEventListener("click", async () => {
    if (st.dirty) {
      if (!confirm("Save your changes first? The validation run uses the saved script.")) return;
      if (!(await save())) return;
    }
    try {
      const r = await api(`/api/performance/plans/${encodeURIComponent(planId)}/validate`, { method: "POST", body: {} });
      toast("Validation run queued: one user, one iteration.", "success");
      location.hash = `#/perf-results?run=${encodeURIComponent(r.run_id)}`;
    } catch (err) { toast(err.message, "error"); }
  });
  renderFields();
  preview();
}

// The validation result on the run page
export function validationCard(v) {
  if (!v) return "";
  const pill = { PASS: "PASS", FAIL: "FAIL", INCOMPLETE: "WARN" }[v.status] || "WAITING";
  return `<div class="card" style="margin-top:12px"><div class="row"><h3 style="margin:0">Validation result</h3><span class="pill ${pill}">${esc(v.status)}</span></div>
    <p>${v.requests} request(s) with one user, ${v.failed} failed${v.correlation_failures ? `, ${v.correlation_failures} because a value the next requests need was not found (correlation)` : ""}. One iteration took ${esc(v.iteration_s)} s. ${v.status === "PASS" ? "The script is ready for a load test." : "Fix the failing requests before a load test: under load they would only fail more."}</p>
    <div style="overflow-x:auto"><table class="data"><thead><tr><th>#</th><th>Request</th><th>Result</th><th>Code</th><th>Time</th><th>Why it failed</th></tr></thead><tbody>
    ${v.samples.map((s, i) => `<tr${s.transaction ? ' class="muted"' : ""}><td>${i + 1}</td><td>${esc(s.label)}${s.transaction ? " (transaction)" : ""}</td>
      <td><span class="pill ${s.ok ? "PASS" : "FAIL"}">${s.ok ? "OK" : s.correlation ? "Correlation" : "Failed"}</span></td><td>${esc(s.code)}</td><td>${s.elapsed_ms} ms</td>
      <td>${esc(s.failure || (s.ok ? "" : s.message))}</td></tr>`).join("")}</tbody></table></div>
    ${v.truncated ? '<p class="muted">Only the first 1,000 samples are listed.</p>' : ""}
    <p class="muted">Rows are in the order JMeter wrote them (a transaction is written after its requests). Response bodies are not kept, because they can hold personal data or secrets.</p></div>`;
}

export function rateKpi(rate) {
  if (!rate) return "";
  return `<div class="card"><div class="kpi-label">Request rate (target ${esc(rate.target_rps)}/s)</div><div class="kpi-value">${rate.achieved_rps ?? "–"} req/s</div>
    <div class="kpi-note">${rate.reached ? "Target reached" : "Below the target"} with ${rate.users} users, ${esc(rate.window)}</div></div>`;
}
