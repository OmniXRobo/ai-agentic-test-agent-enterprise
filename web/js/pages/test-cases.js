// Test Cases: generate suite-aware test cases from the acceptance criteria of selected sprint user stories,
// review them (coverage matrix, validation, duplicates), publish them into the TFS suite; plus the TFS test case list.

import { api } from "../api.js";
import { subscribe } from "../store.js";
import { esc, toast, timeOf, openModal } from "../util.js";
import { workItemPage } from "./workitems.js";

const list = workItemPage({ category: "testcase", createLabel: "New test case" });
const STATUS_PILL = { "Covered": "PASS", "Partially Covered": "WARN", "Not Covered": "FAIL", "Not Applicable": "WAITING", "Needs Clarification": "WARN" };
let unsub = null;
let timer = null;
let runId = null;

function params() {
  return new URLSearchParams(location.hash.split("?")[1] || "");
}

function stop() {
  if (unsub) unsub();
  unsub = null;
  clearTimeout(timer);
  timer = null;
}

function ul(items) {
  return items?.length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
}

function matrixHtml(story) {
  if (!story.coverage.length) return "";
  return `<h4>Coverage matrix <span class="muted">(requirement coverage, not execution results)</span></h4>
    <table class="data"><thead><tr><th>AC</th><th>Criterion</th><th>Testing type</th><th>Test cases</th><th>Coverage</th><th>Missing scenarios / reason</th></tr></thead><tbody>
    ${story.coverage.map((r) => `<tr><td><strong>${esc(r.ac_id)}</strong></td><td>${esc(r.text)}
        <div class="muted">${r.conditions.map((c) => `${esc(c.id)} ${r.covered_conditions.includes(c.id) ? "✓" : "○"} ${esc(c.text)}`).join("<br>")}</div></td>
      <td>${esc(r.testing_type)}</td><td>${esc(r.test_cases.join(", ")) || "—"}</td>
      <td><span class="pill ${STATUS_PILL[r.status] || "WAITING"}">${esc(r.status)}</span></td>
      <td>${esc([...(r.missing || []), ...(r.reason ? [r.reason] : []), ...(r.clarifications || [])].join("; ")) || "—"}</td></tr>`).join("")}
    </tbody></table>`;
}

function caseHtml(story, tc) {
  const sid = story.story.id;
  const issues = story.validation.per_case[tc.ref] || [];
  const dups = story.validation.duplicates.filter((d) => d.ref === tc.ref && d.kind === "existing");
  const texts = Object.fromEntries(story.criteria.map((c) => [c.id, c.text]));
  return `<details class="tc" open>
    <summary><label><input type="checkbox" class="sel" data-story="${sid}" value="${esc(tc.ref)}" ${tc.published_id || issues.some((i) => i.level === "error") ? "" : "checked"} ${tc.published_id ? "disabled" : ""}></label>
      <strong>${esc(tc.ref)}</strong> ${esc(tc.title)}
      ${tc.published_id ? `<a class="pill PASS" href="#/test-cases?id=${tc.published_id}">TFS #${tc.published_id}</a>` : ""}
      ${tc.edited ? '<span class="pill WAITING">edited</span>' : ""}
      <span class="muted">P${tc.priority} · ${esc(tc.risk)} risk · ${esc(tc.scenario)} · ${esc(tc.automation)}</span></summary>
    <p><strong>Title:</strong> ${esc(tc.title)}<br><strong>User Story:</strong> US${esc(sid)}<br>
      <strong>Acceptance criteria:</strong> ${tc.ac_ids.map((a) => `<span title="${esc(texts[a] || "")}">${esc(a)}</span>`).join(", ") || '<span class="pill FAIL">none</span>'}
      ${tc.condition_ids.length ? `<span class="muted">(${esc(tc.condition_ids.join(", "))})</span>` : ""}<br>
      ${tc.objective ? `<strong>Objective:</strong> ${esc(tc.objective)}<br>` : ""}
      <strong>Precondition:</strong> ${esc(tc.preconditions || "None stated")}</p>
    <table class="data"><thead><tr><th>Step</th><th>Action</th><th>Expected Result</th></tr></thead><tbody>
      ${tc.steps.map((s, i) => `<tr><td>${i + 1}</td><td>${esc(s.action)}</td><td>${esc(s.expected)}</td></tr>`).join("")}</tbody></table>
    ${tc.test_data.length ? `<p><strong>Test data:</strong> ${esc(tc.test_data.join("; "))}</p>` : ""}
    ${tc.dependencies.length ? `<p><strong>Dependencies:</strong> ${esc(tc.dependencies.join("; "))}</p>` : ""}
    <p class="muted">Risk: ${esc(tc.risk_rationale || tc.risk)}${tc.automation_reason ? ` · Automation: ${esc(tc.automation_reason)}` : ""}</p>
    ${tc.gaps.length ? `<p class="pill WARN" style="display:inline-block">Gaps to review</p>${ul(tc.gaps)}` : ""}
    ${issues.length ? `<p>${issues.map((i) => `<span class="pill ${i.level === "error" ? "FAIL" : i.level === "warning" ? "WARN" : "WAITING"}" title="${esc(i.rule)}">${esc(i.message)}</span>`).join(" ")}</p>` : ""}
    <div class="row">
      ${dups.length && !tc.published_id ? `<label class="row" style="gap:6px">On publish <select class="reuse" data-key="${sid}:${esc(tc.ref)}">
        <option value="">Create a new test case</option>${dups.map((d) => `<option value="${d.duplicate_of}" selected>Reuse existing #${d.duplicate_of}</option>`).join("")}</select></label>` : ""}
      ${tc.published_id ? "" : `<button class="btn" type="button" data-edit="${sid}|${esc(tc.ref)}">Edit</button>`}
    </div>
  </details>`;
}

function storyHtml(story) {
  const s = story.story;
  return `<div class="card" style="margin-top:12px" data-story-card="${s.id}">
    <div class="row"><h3 style="margin:0">US${s.id} ${esc(s.title)}</h3><span class="spacer"></span>
      ${story.refined ? '<span class="pill WAITING" title="The agent reviewed its first draft against the validation rules and refined it">refined after review</span>' : ""}
      <button class="btn" type="button" data-trace="${s.id}">Traceability</button></div>
    <p class="muted">${story.criteria.length} acceptance criteria · ${story.test_cases.length} test cases · ${story.validation.errors} errors · ${story.validation.warnings} warnings</p>
    ${story.limitations.length ? `<p class="pill WARN" style="display:inline-block">Limitation</p>${ul(story.limitations)}` : ""}
    ${story.impact.length ? `<p class="pill WARN" style="display:inline-block">Existing test cases to review (criteria changed)</p>${ul(story.impact.map((i) => `#${i.tfs_test_case_id} ${i.title || ""}: ${i.reason}`))}` : ""}
    ${story.clarifications.length ? `<h4>Clarifications needed</h4>${ul(story.clarifications.map((q) => (q.ac_id ? q.ac_id + ": " : "") + q.question))}` : ""}
    ${story.analysis?.gaps?.length ? `<h4>Requirement gaps</h4>${ul(story.analysis.gaps)}` : ""}
    ${story.not_applicable.length ? `<h4>Not applicable to this testing type</h4>${ul(story.not_applicable.map((n) => `${n.ac_id}: ${n.reason}`))}` : ""}
    ${story.notes?.length ? ul(story.notes) : ""}
    ${matrixHtml(story)}
    <h4>Test cases</h4>
    ${story.test_cases.length ? story.test_cases.map((tc) => caseHtml(story, tc)).join("") : '<p class="muted">No applicable test cases for this testing type.</p>'}
  </div>`;
}

function editCase(view, story, tc) {
  const m = openModal(`Edit ${tc.ref}`, `<form class="settings">
      <label>Title<input name="title" required value="${esc(tc.title)}"></label>
      <label>Acceptance criteria (comma-separated)<input name="ac_ids" value="${esc(tc.ac_ids.join(", "))}"></label>
      <label>Precondition<input name="preconditions" value="${esc(tc.preconditions || "")}"></label>
      <label>Steps, one per line: Action || Expected Result<textarea name="steps" rows="8">${esc(tc.steps.map((s) => `${s.action} || ${s.expected}`).join("\n"))}</textarea></label>
      <label>Priority<select name="priority">${[1, 2, 3, 4].map((p) => `<option ${p === tc.priority ? "selected" : ""}>${p}</option>`).join("")}</select></label>
      <div class="row"><button class="btn primary" type="submit">Save and re-validate</button><button class="btn" type="button" data-close>Cancel</button></div></form>`, { wide: true });
  m.el.querySelector("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const steps = String(f.get("steps")).split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
      const [action, ...rest] = l.split("||");
      return { action: action.trim(), expected: rest.join("||").trim() };
    });
    try {
      await api(`/api/design/suite-runs/${encodeURIComponent(runId)}/stories/${story.story.id}/cases/${encodeURIComponent(tc.ref)}`, { method: "PATCH", body: { case: {
        title: f.get("title"), ac_ids: String(f.get("ac_ids")).split(",").map((x) => x.trim()).filter(Boolean),
        preconditions: f.get("preconditions") || null, steps, priority: Number(f.get("priority")) } } });
      m.close();
      showResult(view);
    } catch (err) { toast(err.message, "error"); }
  });
}

async function traceDialog(storyId) {
  const m = openModal(`Traceability for US${storyId}`, '<p class="muted">Loading…</p>', { wide: true });
  try {
    const t = await api(`/api/design/traceability/${storyId}`);
    m.el.querySelector(".modal-body").innerHTML = `<p class="muted">${esc(t.note)}</p>
      ${t.impact.length ? `<p class="pill WARN" style="display:inline-block">Needs review</p>${ul(t.impact.map((i) => `#${i.tfs_test_case_id}: ${i.reason}`))}` : ""}
      <table class="data"><thead><tr><th>AC</th><th>Criterion</th><th>Linked test cases by testing type</th></tr></thead><tbody>
      ${t.matrix.map((r) => `<tr><td>${esc(r.ac_id)}</td><td>${esc(r.text)}</td><td>${Object.keys(r.by_testing_type).length ? Object.entries(r.by_testing_type).map(([k, v]) => `<strong>${esc(k)}:</strong> ${v.map((x) => `<a href="#/test-cases?id=${x.id}">#${x.id}</a>${x.status === "review" ? ' <span class="pill WARN">review</span>' : ""}`).join(", ")}`).join("<br>") : '<span class="muted">none</span>'}</td></tr>`).join("")}
      </tbody></table>`;
  } catch (err) { m.el.querySelector(".modal-body").innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; }
}

async function showResult(view) {
  const box = view.querySelector("#genResult");
  const body = await api(`/api/design/suite-runs/${encodeURIComponent(runId)}`);
  if (body.status === "RUNNING") { timer = setTimeout(() => showResult(view), 1500); return; }
  stop();
  const r = body.result;
  view.querySelector("#genProgress").hidden = true;
  if (body.status === "FAILED" || !r?.stories) {
    box.innerHTML = `<div class="card"><p class="pill FAIL">Generation failed</p><p>${esc(r?.error || "See the activity log.")}</p>
      <p class="muted">Test cases are only generated by your configured AI. <a href="#/ai-settings">Check the AI settings</a>.</p></div>`;
    return;
  }
  const m = r.meta || {};
  box.innerHTML = `<div class="card" style="margin-top:12px"><div class="row"><h3 style="margin:0">${esc(r.suite.testing_type_name)} · suite "${esc(r.suite.name)}"</h3><span class="spacer"></span>
      <label class="row" style="gap:6px"><input type="checkbox" id="selAll" checked> Select all</label>
      <button class="btn primary" id="publish">Create selected test cases in this suite</button></div>
      <p class="muted">Generated by ${esc(m.agent || "")} (${esc(m.agent_version || "")}, prompt ${esc(m.prompt_version || "")}) with ${esc(m.model?.label || "")} ${esc(m.model?.model || "")} · ${esc(timeOf(m.generated_at))}.
      Each case is checked with explicit rules (source links, specific expected results, cleanup, thresholds, duplicates), not by a confidence score.
      TFS links each test case to its user story; acceptance-criterion links are kept by this application and written into the test case Summary.</p></div>
    ${r.stories.map(storyHtml).join("")}
    <div id="published"></div>`;
  box.querySelector("#selAll").addEventListener("change", (e) => box.querySelectorAll(".sel:not(:disabled)").forEach((c) => (c.checked = e.target.checked)));
  box.querySelectorAll("[data-trace]").forEach((b) => b.addEventListener("click", () => traceDialog(b.dataset.trace)));
  box.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const [sid, ref] = b.dataset.edit.split("|");
    const story = r.stories.find((s) => String(s.story.id) === sid);
    editCase(view, story, story.test_cases.find((t) => t.ref === ref));
  }));
  box.querySelector("#publish").addEventListener("click", async (e) => {
    const selection = {};
    box.querySelectorAll(".sel:checked").forEach((c) => (selection[c.dataset.story] ||= []).push(c.value));
    if (!Object.keys(selection).length) { toast("Select at least one test case.", "warning"); return; }
    const reuse = {};
    box.querySelectorAll("select.reuse").forEach((s) => { if (s.value) reuse[s.dataset.key] = Number(s.value); });
    e.target.disabled = true;
    e.target.textContent = "Creating in TFS…";
    try {
      const p = await api(`/api/design/suite-runs/${encodeURIComponent(runId)}/publish`, { method: "POST", body: { selection, reuse } });
      toast(`${p.created.filter((c) => c.action === "created").length} created, ${p.created.filter((c) => c.action === "reused").length} reused in suite "${p.suite}"`, "success");
      await showResult(view);
    } catch (err) {
      toast(err.message, "error");
      e.target.disabled = false;
      e.target.textContent = "Create selected test cases in this suite";
    }
  });
}

async function generator(view) {
  const card = view.querySelector("#gen");
  let meta, plans, types;
  try {
    [meta, { plans }, { testing_types: types }] = await Promise.all([
      api("/api/tfs/meta"), api("/api/tfs/test-plans"), api("/api/tfs/testing-types")]);
  } catch { card.hidden = true; return; }
  const p = params();
  card.innerHTML = `<form id="genForm">
    <div class="row" style="flex-wrap:wrap">
      <strong>Generate test cases with AI</strong>
      <select name="plan" aria-label="Test plan" required><option value="">Test plan…</option>${plans.map((x) => `<option value="${x.id}" ${String(x.id) === p.get("plan") ? "selected" : ""}>#${x.id} ${esc(x.name)}</option>`).join("")}</select>
      <select name="suite" aria-label="Test suite" required><option value="">Test suite…</option></select>
      <select name="type" aria-label="Testing type" hidden><option value="">Testing type…</option>${types.map((t) => `<option value="${t.key}">${esc(t.name)}</option>`).join("")}</select>
      <select name="iteration" aria-label="Sprint" required><option value="">Sprint…</option>${meta.iterations.map((i) => `<option ${i === p.get("iteration") ? "selected" : ""}>${esc(i)}</option>`).join("")}</select>
      <label class="row" style="gap:6px">Max per story <input name="max" type="number" value="8" min="1" max="30" style="width:70px"></label>
      <span class="spacer"></span><button class="btn primary" type="submit">Generate test cases</button>
    </div>
    <p class="muted" id="suiteInfo" style="margin:6px 0 0">Choose the suite: its testing type decides which kind of test cases are written.</p>
    <div id="stories" style="margin-top:8px"></div>
  </form>`;
  const form = card.querySelector("#genForm");
  let suites = [];
  const loadSuites = async () => {
    const sel = form.elements.suite;
    sel.innerHTML = '<option value="">Test suite…</option>';
    if (!form.elements.plan.value) return;
    try {
      ({ suites } = await api(`/api/tfs/test-plans/${form.elements.plan.value}/suites`));
      sel.innerHTML += suites.filter((s) => s.parent_id).map((s) => `<option value="${s.id}" ${String(s.id) === p.get("suite") ? "selected" : ""}>${esc(s.name)}${s.testing_type_name ? ` (${esc(s.testing_type_name)})` : ""}</option>`).join("");
      suiteInfo();
    } catch (err) { toast(err.message, "error"); }
  };
  const suiteInfo = () => {
    const s = suites.find((x) => String(x.id) === form.elements.suite.value);
    const t = s && types.find((x) => x.key === s.testing_type);
    form.elements.type.hidden = !(s && !s.testing_type);
    card.querySelector("#suiteInfo").textContent = t ? `${t.name}: ${t.focus}` : s ? "This suite has no testing type yet: choose one." : "Choose the suite: its testing type decides which kind of test cases are written.";
  };
  const loadStories = async () => {
    const box = card.querySelector("#stories");
    if (!form.elements.iteration.value) { box.innerHTML = ""; return; }
    box.innerHTML = '<p class="muted">Loading user stories…</p>';
    try {
      const { stories } = await api(`/api/design/sprint-stories?iteration=${encodeURIComponent(form.elements.iteration.value)}`);
      box.innerHTML = stories.length ? `<div class="row"><strong>User stories in ${esc(form.elements.iteration.value.split("\\").pop())}</strong>
          <button class="link-btn" type="button" data-all>Check all</button> · <button class="link-btn" type="button" data-none>Uncheck all</button></div>
        <div class="choices" style="grid-template-columns:repeat(auto-fill,minmax(320px,1fr))">${stories.map((s) => `<label class="choice"><input type="checkbox" name="story" value="${s.id}">
          <span><strong>US${s.id} ${esc(s.title)}</strong><span class="muted">${esc(s.state || "")}${s.traced_test_cases ? ` · ${s.traced_test_cases} traced test cases` : ""}</span></span></label>`).join("")}</div>`
        : '<p class="muted">No user stories in this sprint.</p>';
      box.querySelector("[data-all]")?.addEventListener("click", () => box.querySelectorAll('[name="story"]').forEach((c) => (c.checked = true)));
      box.querySelector("[data-none]")?.addEventListener("click", () => box.querySelectorAll('[name="story"]').forEach((c) => (c.checked = false)));
    } catch (err) { box.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; }
  };
  form.elements.plan.addEventListener("change", loadSuites);
  form.elements.suite.addEventListener("change", suiteInfo);
  form.elements.iteration.addEventListener("change", loadStories);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const ids = f.getAll("story").map(Number);
    if (!ids.length) { toast("Check at least one user story.", "warning"); return; }
    try {
      const r = await api("/api/design/suite-runs", { method: "POST", body: {
        plan_id: Number(f.get("plan")), suite_id: Number(f.get("suite")), user_story_ids: ids,
        testing_type: f.get("type") || null, max_cases: Number(f.get("max")) } });
      runId = r.run_id;
      history.replaceState(null, "", `#/test-cases?plan=${f.get("plan")}&suite=${f.get("suite")}&run=${runId}`);
      const prog = view.querySelector("#genProgress");
      prog.hidden = false;
      prog.querySelector(".feed").innerHTML = "";
      view.querySelector("#genResult").innerHTML = "";
      stop();
      unsub = subscribe((c) => {
        if (c.type !== "event" || c.event.run_id !== runId) return;
        const feed = prog.querySelector(".feed");
        feed.insertAdjacentHTML("beforeend", `<div class="feed-item"><span class="feed-time">${esc(timeOf(c.event.timestamp))}</span><span>${esc((c.event.agent_name ? c.event.agent_name + " · " : "") + (c.event.activity || c.event.name))}</span></div>`);
        feed.scrollTop = feed.scrollHeight;
      });
      timer = setTimeout(() => showResult(view), 1500);
    } catch (err) { toast(err.message, "error"); }
  });
  await loadSuites();
  if (form.elements.iteration.value) loadStories();
}

export async function mount(view) {
  stop();
  view.innerHTML = `<div class="card" id="gen" style="margin-bottom:12px"><p class="muted">Loading…</p></div>
    <div class="card" id="genProgress" hidden style="margin-bottom:12px"><h3>Activity</h3><div class="feed"></div></div>
    <div id="genResult" style="margin-bottom:12px"></div>
    <div id="tcList"></div>`;
  await generator(view);
  runId = params().get("run");
  if (runId) showResult(view);
  await list.mount(view.querySelector("#tcList"));
}

export function unmount() {
  stop();
}
