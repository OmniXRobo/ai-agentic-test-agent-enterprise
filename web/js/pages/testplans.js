// Test Plans: create (with or without a sprint), update and delete test plans in the active TFS / Azure DevOps project.

import { api } from "../api.js";
import { esc, toast, openModal } from "../util.js";

const sprintOf = (path) => (path || "").split("\\").pop();

function notConnected(view, err) {
  view.innerHTML = `<div class="card empty"><h2>TFS data is not available</h2><p>${esc(err.message)}</p>
    <p><a class="btn primary" href="#/tfs-settings">Open TFS connection settings</a></p></div>`;
}

function sprintOptions(meta, selected) {
  const project = meta.project || "";
  return `<option value="">No sprint (project level)</option>${meta.iterations.filter((i) => i !== project)
    .map((i) => `<option value="${esc(i)}" ${i === selected ? "selected" : ""}>${esc(i)}</option>`).join("")}`;
}

async function showSuites(view, plan) {
  const panel = view.querySelector("#detail");
  panel.innerHTML = '<p class="muted">Loading suites…</p>';
  try {
    const { suites } = await api(`/api/tfs/test-plans/${plan.id}/suites`);
    panel.innerHTML = `<div class="row"><h2 style="margin:0">#${plan.id} ${esc(plan.name)}</h2></div>
      <p class="muted">${esc(plan.state || "")} · ${plan.iteration && sprintOf(plan.iteration) !== plan.iteration ? `Sprint: ${esc(sprintOf(plan.iteration))}` : "No sprint"}</p>
      ${plan.description ? `<p>${esc(plan.description)}</p>` : ""}
      <div class="row" style="margin-bottom:8px">
        <button class="btn" data-act="edit">Edit plan</button>
        <a class="btn" href="#/test-suites?plan=${plan.id}">Manage suites</a>
        <button class="btn danger" data-act="delete">Delete plan</button>
      </div>
      ${suites.length ? `<table class="data"><thead><tr><th>ID</th><th>Suite</th><th>Testing type</th><th>Cases</th></tr></thead><tbody>
      ${suites.map((s) => `<tr class="clickable" data-suite="${s.id}"><td>${s.id}</td><td>${esc(s.name)}${s.parent_id ? "" : ' <span class="muted">(root)</span>'}</td><td>${esc(s.testing_type_name || "")}</td><td>${s.test_case_count ?? ""}</td></tr>`).join("")}
      </tbody></table>` : '<p class="muted">This plan has no suites.</p>'}
      <div id="cases"></div>`;
    panel.querySelector('[data-act="edit"]').addEventListener("click", () => editPlan(view, plan));
    panel.querySelector('[data-act="delete"]').addEventListener("click", () => deletePlan(view, plan));
    panel.querySelectorAll("tr[data-suite]").forEach((tr) => tr.addEventListener("click", async () => {
      const box = panel.querySelector("#cases");
      box.innerHTML = '<p class="muted">Loading test cases…</p>';
      try {
        const { test_cases } = await api(`/api/tfs/test-plans/${plan.id}/suites/${tr.dataset.suite}/test-cases`);
        box.innerHTML = `<h3>Test cases</h3>${test_cases.length ? `<ul>${test_cases.map((t) => `<li><a href="#/test-cases?id=${t.id}">#${t.id}</a> ${esc(t.title || "")}</li>`).join("")}</ul>` : '<p class="muted">No test cases in this suite.</p>'}`;
      } catch (err) {
        box.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
      }
    }));
  } catch (err) {
    panel.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
  }
}

let meta = null;

function editPlan(view, plan) {
  const m = openModal(`Edit test plan #${plan.id}`, `
    <form class="settings" id="editPlan">
      <label>Name<input name="name" required maxlength="256" value="${esc(plan.name)}"></label>
      <label>Sprint<select name="iteration">${sprintOptions(meta, plan.iteration)}</select></label>
      <label>State<select name="state">${["Active", "Inactive"].map((s) => `<option ${s === plan.state ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <label>Description<textarea name="description" rows="4">${esc(plan.description || "")}</textarea></label>
      <div class="row"><button class="btn primary" type="submit">Save to TFS</button><button class="btn" type="button" data-close>Cancel</button></div>
    </form>`);
  m.el.querySelector("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api(`/api/tfs/test-plans/${plan.id}`, { method: "PATCH", body: {
        name: f.get("name"), iteration: f.get("iteration") || "", state: f.get("state"), description: f.get("description") } });
      toast(`Test plan #${plan.id} updated in TFS`, "success");
      m.close();
      await mount(view, { route: "test-plans" }, plan.id);
    } catch (err) { toast(err.message, "error"); btn.disabled = false; }
  });
}

function deletePlan(view, plan) {
  const m = openModal(`Delete test plan #${plan.id}?`, `
    <p>This deletes <strong>${esc(plan.name)}</strong> and its suites in TFS for project <strong>${esc(meta.project)}</strong>.
    The test case work items stay in the project. TFS cannot undo this.</p>
    <div class="row"><button class="btn danger" data-yes>Delete in TFS</button><button class="btn" data-close>Cancel</button></div>`);
  m.el.querySelector("[data-yes]").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      await api(`/api/tfs/test-plans/${plan.id}`, { method: "DELETE" });
      toast(`Test plan #${plan.id} deleted from TFS`, "success");
      m.close();
      await mount(view, { route: "test-plans" });
    } catch (err) { toast(err.message, "error"); e.target.disabled = false; }
  });
}

export async function mount(view, entry, selectId) {
  view.innerHTML = '<p class="muted">Loading test plans…</p>';
  let plans, types;
  try {
    [meta, { plans }, { testing_types: types }] = await Promise.all([
      api("/api/tfs/meta"), api("/api/tfs/test-plans"), api("/api/tfs/testing-types")]);
  } catch (err) {
    notConnected(view, err);
    return;
  }
  const defaults = types.filter((t) => t.default);
  view.innerHTML = `<form class="card" id="createPlan" style="margin-bottom:12px">
      <div class="row" style="flex-wrap:wrap">
        <strong>New test plan</strong>
        <input name="name" placeholder="Plan name (optional: named after the sprint)" maxlength="256" style="min-width:280px">
        <select name="iteration" aria-label="Sprint">${sprintOptions(meta, "")}</select>
        <span class="spacer"></span><button class="btn primary" type="submit">Create test plan in TFS</button>
      </div>
      <div class="row" style="flex-wrap:wrap;margin-top:8px">
        <input name="description" placeholder="Description (optional)" style="flex:1;min-width:280px">
        <label class="row" style="gap:6px"><input type="checkbox" name="default_suites" checked> Add suites for the default testing types</label>
      </div>
      <p class="muted" style="margin:6px 0 0">Default suites: ${esc(defaults.map((t) => t.name).join(", "))}. Pick other types on the Test Suites page. A plan or suite with the same name is reused, never duplicated.</p>
    </form>
    <div class="split">
    <section class="card" id="list">${plans.length ? `<table class="data"><thead><tr><th>ID</th><th>Plan</th><th>State</th><th>Sprint</th><th></th></tr></thead><tbody>
      ${plans.map((p) => `<tr class="clickable" data-plan="${p.id}"><td>${p.id}</td><td>${esc(p.name)}</td><td>${esc(p.state || "")}</td>
        <td>${p.iteration && sprintOf(p.iteration) !== p.iteration ? esc(sprintOf(p.iteration)) : '<span class="muted">No sprint</span>'}</td>
        <td style="white-space:nowrap"><button class="link-btn" data-edit="${p.id}">Edit</button> · <button class="link-btn" data-del="${p.id}">Delete</button></td></tr>`).join("")}
      </tbody></table>` : '<p class="muted">No test plans in this project.</p>'}</section>
    <section class="card detail" id="detail"><p class="muted">Select a test plan to see its suites and test cases.</p></section>
  </div>`;
  view.querySelector("#createPlan").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const btn = e.target.querySelector("button");
    btn.disabled = true;
    try {
      const r = await api("/api/tfs/test-plans", { method: "POST", body: {
        name: f.get("name") || null, iteration: f.get("iteration") || null, description: f.get("description") || null,
        suite_types: f.get("default_suites") === "on" ? defaults.map((t) => t.key) : [] } });
      toast(`Test plan #${r.plan.id} ${r.plan.created ? "created" : "already existed"}; ${r.suites.filter((x) => x.created).length} suite(s) added`, "success");
      await mount(view, entry, r.plan.id);
    } catch (err) {
      toast(err.message, "error");
      btn.disabled = false;
    }
  });
  const byId = (id) => plans.find((p) => String(p.id) === String(id));
  view.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); editPlan(view, byId(b.dataset.edit)); }));
  view.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); deletePlan(view, byId(b.dataset.del)); }));
  view.querySelectorAll("tr[data-plan]").forEach((tr) => tr.addEventListener("click", () => {
    view.querySelectorAll("tr[data-plan]").forEach((x) => x.classList.toggle("selected", x === tr));
    showSuites(view, byId(tr.dataset.plan));
  }));
  if (selectId) view.querySelector(`tr[data-plan="${selectId}"]`)?.click();
}
