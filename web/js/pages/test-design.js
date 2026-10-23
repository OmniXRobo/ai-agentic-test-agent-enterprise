// AI Test Design: user story -> requirements -> strategy -> test cases -> test data -> publish to TFS.

import { api } from "../api.js";
import { subscribe } from "../store.js";
import { esc, toast, timeOf, isRepeat } from "../util.js";

let unsub = null;
let currentRun = null;
let lastEvent = null;

function params() {
  return new URLSearchParams(location.hash.split("?")[1] || "");
}

function listHtml(items) {
  return items && items.length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="muted">None.</p>';
}

// Default testing types; each test case goes into the suite of its type (same list as the server).
const SUITES = ["Functional Testing", "Positive Testing", "Negative Testing", "Boundary / Edge Case Testing", "End-to-End Testing", "Smoke Testing"];
const SUITE_OF = { functional: SUITES[0], regression: SUITES[0], positive: SUITES[1], negative: SUITES[2], boundary: SUITES[3], edge: SUITES[3], end_to_end: SUITES[4], smoke: SUITES[5] };

// Same naming as the server: "<project> - <sprint> - Test Plan" (the project is the iteration's first part).
function planTitle(r) {
  const parts = (r.story.iteration || "").split("\\");
  const project = parts[0] || "Project", sprint = parts.length > 1 ? parts[parts.length - 1] : "";
  return sprint ? `${project} - ${sprint} - Test Plan` : `${project} - Test Plan`;
}

function suiteOptions(suites) {
  return `<option value="">One suite per testing type (recommended)</option>${suites.filter((s) => s.parent_id).map((s) => `<option value="${s.id}">Only "${esc(s.name)}"</option>`).join("")}`;
}

function publishedHtml(r) {
  return `<div class="card"><p class="pill PASS">Created in TFS</p>
    ${r.plan_id ? `<p>Test plan <a href="#/test-plans">#${r.plan_id}${r.plan ? ` ${esc(r.plan.name)}` : ""}</a>${r.plan ? (r.plan.created ? " (new)" : " (reused)") : ""}</p>` : ""}
    <table class="data"><thead><tr><th>Test case</th><th>Suite</th><th>Test data</th></tr></thead><tbody>
    ${r.created.map((c) => `<tr><td><a href="#/test-cases?id=${c.id}">#${c.id}</a> ${esc(c.title)}</td><td>${esc(c.suite || (c.suite_id ? `#${c.suite_id}` : "—"))}</td><td>${c.datasets} data set(s)</td></tr>`).join("")}
    </tbody></table>
    ${r.created.some((c) => c.skipped_fields?.length) ? `<p class="muted">Fields this TFS project does not have were skipped: ${esc([...new Set(r.created.flatMap((c) => c.skipped_fields))].join(", "))}</p>` : ""}
    <p><a class="btn" href="#/ai-automation">Next: create the automation scripts</a></p></div>`;
}

function resultHtml(r, plans) {
  const req = r.requirements;
  const plan = r.plan;
  const dataFor = (ref) => (r.test_data || []).filter((d) => d.test_case_ref === ref);
  return `
    <div class="card"><h2>#${r.story.id} ${esc(r.story.title)}</h2>
      <p class="muted">${r.story.criteria.length} acceptance criteria · ${req.requirements.length} requirements · ${r.test_cases.length} test cases · ${r.test_data.length} datasets</p>
      ${r.uncovered_requirements?.length ? `<p class="pill WARN">Requirements without a test case: ${esc(r.uncovered_requirements.join(", "))}</p>` : ""}
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr));margin-top:12px">
      <div class="card"><h3>Requirements</h3>
        <table class="data"><tbody>${req.requirements.map((x) => `<tr><td>${esc(x.id)}</td><td>${esc(x.text)}</td><td><span class="pill ${x.risk === "high" ? "FAIL" : x.risk === "medium" ? "WARN" : "PASS"}">${esc(x.risk)}</span></td></tr>`).join("")}</tbody></table>
        ${req.ambiguities?.length ? `<h3>Ambiguities to clarify</h3>${listHtml(req.ambiguities)}` : ""}
      </div>
      <div class="card"><h3>Test strategy</h3>
        <p><strong>Scope:</strong> ${esc(plan.scope)}</p><p><strong>Approach:</strong> ${esc(plan.approach)}</p>
        <p><strong>Test types:</strong> ${esc(plan.test_types.join(", "))}</p>
        <strong>Risks</strong>${listHtml(plan.risks)}
        ${plan.performance_slas?.length ? `<strong>Performance SLAs</strong>${listHtml(plan.performance_slas)}` : ""}
        <p><strong>Suggested suite:</strong> ${esc(plan.suite_name)}</p>
      </div>
    </div>
    <div class="card" style="margin-top:12px">
      <div class="row"><h3 style="margin:0">Test cases</h3><span class="spacer"></span>
        <label class="row" style="gap:6px"><input type="checkbox" id="selAll" checked> Select all</label></div>
      ${r.test_cases.map((tc) => `
        <details class="tc" open>
          <summary><label><input type="checkbox" class="sel" value="${esc(tc.ref)}" checked></label>
            <strong>${esc(tc.ref)}</strong> ${esc(tc.title)} <span class="pill WAITING">${esc(SUITE_OF[tc.category] || tc.category)}</span> <span class="muted">P${tc.priority} · ${esc(tc.requirement_ids.join(", "))}</span></summary>
          ${tc.preconditions ? `<p class="muted">Preconditions: ${esc(tc.preconditions)}</p>` : ""}
          <table class="data"><thead><tr><th>#</th><th>Action</th><th>Expected result</th></tr></thead><tbody>
          ${tc.steps.map((s, i) => `<tr><td>${i + 1}</td><td>${esc(s.action)}</td><td>${esc(s.expected)}</td></tr>`).join("")}</tbody></table>
          ${dataFor(tc.ref).length ? `<p><strong>Test data</strong></p>${dataFor(tc.ref).map((d) => `<p class="muted">${esc(d.name)} (${esc(d.kind)}): <code>${esc(JSON.stringify(d.values))}</code></p>`).join("")}` : ""}
        </details>`).join("")}
    </div>
    <form class="card" id="publishForm" style="margin-top:12px">
      <h3>Save to TFS</h3>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr))">
        <label>Test plan <select name="plan">
          <option value="new">Create or reuse "${esc(planTitle(r))}"</option>
          ${plans.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}
          <option value="">No test plan (test cases only)</option></select></label>
        <label>Test suites <select name="suite_id">${suiteOptions([])}</select></label>
      </div>
      <p class="muted">One suite per testing type puts each test case into ${esc(SUITES.join(", "))}. Suites that already exist are reused.</p>
      <div class="row" style="flex-wrap:wrap">
        <label class="row" style="gap:6px"><input type="checkbox" name="data_in_summary" checked> Write the test data into each test case's Summary</label>
        <label class="row" style="gap:6px"><input type="checkbox" name="data_as_parameters" checked> Also fill the Parameter values (one row per data set)</label>
        <span class="spacer"></span>
        <button class="btn primary" type="submit">Create selected test cases in TFS</button>
      </div>
    </form>
    <div id="published"></div>`;
}

async function showResult(view, runId) {
  const box = view.querySelector("#design");
  const body = await api(`/api/design/runs/${encodeURIComponent(runId)}`);
  if (body.status === "RUNNING") return;
  if (body.status === "FAILED" || !body.result?.test_cases) {
    box.innerHTML = `<div class="card"><p class="pill FAIL">Test design failed</p><p>${esc(body.result?.error || "See the activity log.")}</p>
      <p class="muted">No test cases are generated without the On-Premise AI. <a href="#/ai-settings">Check On-Premise AI settings</a>.</p></div>`;
    return;
  }
  let plans = [];
  try { ({ plans } = await api("/api/tfs/test-plans")); } catch { /* plans optional */ }
  box.innerHTML = resultHtml(body.result, plans);
  const form = box.querySelector("#publishForm");
  const suiteSel = form.querySelector('[name="suite_id"]');
  box.querySelector("#selAll").addEventListener("change", (e) => box.querySelectorAll(".sel").forEach((c) => (c.checked = e.target.checked)));
  form.querySelector('[name="plan"]').addEventListener("change", async (e) => {
    const plan = e.target.value;
    suiteSel.disabled = !plan;
    suiteSel.innerHTML = suiteOptions([]);
    if (!plan || plan === "new") return;
    try {
      const { suites } = await api(`/api/tfs/test-plans/${plan}/suites`);
      suiteSel.innerHTML = suiteOptions(suites);
    } catch (err) { toast(err.message, "error"); }
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const refs = [...box.querySelectorAll(".sel:checked")].map((c) => c.value);
    if (!refs.length) { toast("Select at least one test case.", "warning"); return; }
    const f = new FormData(form);
    const plan = f.get("plan");
    const btn = form.querySelector("button");
    btn.disabled = true;
    btn.textContent = "Creating in TFS…";
    try {
      const r = await api(`/api/design/runs/${encodeURIComponent(runId)}/publish`, { method: "POST", body: {
        refs, create_plan: plan === "new", plan_id: plan && plan !== "new" ? Number(plan) : null,
        suite_id: plan && f.get("suite_id") ? Number(f.get("suite_id")) : null,
        data_in_summary: f.get("data_in_summary") === "on", data_as_parameters: f.get("data_as_parameters") === "on",
      } });
      box.querySelector("#published").innerHTML = publishedHtml(r);
      toast(`Created ${r.created.length} test cases in TFS`, "success");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Create selected test cases in TFS";
    }
  });
}

export async function mount(view) {
  const story = params().get("story") || "";
  view.innerHTML = `
    <form class="card row" id="startForm">
      <label class="row" style="gap:6px">User Story ID <input name="story" value="${esc(story)}" required inputmode="numeric" style="width:120px"></label>
      <label class="row" style="gap:6px">Max test cases <input name="max" type="number" value="12" min="1" max="40" style="width:80px"></label>
      <button class="btn primary" type="submit">Generate test design</button>
      <span class="spacer"></span><span class="muted">Runs on your On-Premise AI. Progress appears below and on Agent Workflow.</span>
    </form>
    <div class="card" id="progress" style="margin-top:12px" hidden><h3>Activity</h3><div class="feed" id="feed"></div></div>
    <div id="design" style="margin-top:12px"></div>`;
  const feed = view.querySelector("#feed");
  view.querySelector("#startForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const r = await api("/api/design/runs", { method: "POST", body: { user_story_id: Number(f.get("story")), max_cases: Number(f.get("max")) } });
      currentRun = r.run_id;
      feed.innerHTML = "";
      view.querySelector("#progress").hidden = false;
      view.querySelector("#design").innerHTML = "";
      history.replaceState(null, "", `#/test-design?story=${f.get("story")}&run=${r.run_id}`);
    } catch (err) { toast(err.message, "error"); }
  });
  unsub = subscribe((c) => {
    if (c.type !== "event" || !currentRun || c.event.run_id !== currentRun) return;
    const repeat = isRepeat(lastEvent, c.event);
    lastEvent = c.event;
    if (!repeat) feed.insertAdjacentHTML("beforeend", `<div class="feed-item ${c.event.name === "agent.failed" ? "failed" : ""}"><span class="feed-time">${esc(timeOf(c.event.timestamp))}</span><span>${esc((c.event.agent_name ? c.event.agent_name + " · " : "") + (c.event.activity || c.event.name))}</span></div>`);
    feed.scrollTop = feed.scrollHeight;
    const terminal = c.event.name === "agent.failed" || (c.event.name === "agent.completed" && c.event.agent_id === "test_data");
    if (terminal) setTimeout(() => showResult(view, currentRun), 300);
  });
  const run = params().get("run");
  if (run) { currentRun = run; await showResult(view, run); }
}

export function unmount() {
  if (unsub) unsub();
  unsub = null;
  currentRun = null;
}
