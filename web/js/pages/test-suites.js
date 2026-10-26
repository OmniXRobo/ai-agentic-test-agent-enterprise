// Test Suites: create one suite per testing type, rename / change type, and delete suites of a TFS test plan.

import { api } from "../api.js";
import { esc, toast, openModal } from "../util.js";

let types = [];
let meta = null;

function params() {
  return new URLSearchParams(location.hash.split("?")[1] || "");
}

function typeOptions(selected) {
  return `<option value="">(not set)</option>${types.map((t) => `<option value="${t.key}" ${t.key === selected ? "selected" : ""}>${esc(t.name)}</option>`).join("")}`;
}

async function loadSuites(view, plan) {
  const box = view.querySelector("#suites");
  const form = view.querySelector("#createSuites");
  box.innerHTML = '<p class="muted">Loading suites…</p>';
  let suites;
  try {
    ({ suites } = await api(`/api/tfs/test-plans/${plan.id}/suites`));
  } catch (err) {
    box.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
    return;
  }
  const root = suites.find((s) => !s.parent_id);
  form.querySelector('[name="parent"]').innerHTML = suites.map((s) => `<option value="${s.id}" ${s === root ? "selected" : ""}>${esc(s.name)}${s === root ? " (root)" : ""}</option>`).join("");
  const existing = new Set(suites.filter((s) => s.parent_id && s.testing_type).map((s) => s.testing_type));
  form.querySelectorAll('[name="type"]').forEach((c) => {
    c.closest("label").querySelector(".exists").textContent = existing.has(c.value) ? " (exists, reused)" : "";
  });
  box.innerHTML = suites.length ? `<table class="data"><thead><tr><th>ID</th><th>Suite</th><th>Testing type</th><th>Cases</th><th></th></tr></thead><tbody>
    ${suites.map((s) => `<tr><td>${s.id}</td><td>${esc(s.name)}${s.parent_id ? "" : ' <span class="muted">(root)</span>'}</td>
      <td>${s.testing_type_name ? esc(s.testing_type_name) : s.parent_id ? '<span class="muted">not set</span>' : "—"}</td><td>${s.test_case_count ?? ""}</td>
      <td style="white-space:nowrap">${s.parent_id ? `<button class="link-btn" data-edit="${s.id}">Edit</button> · <button class="link-btn" data-del="${s.id}">Delete</button> · <a href="#/test-cases?plan=${plan.id}&suite=${s.id}">Generate test cases</a>` : ""}</td></tr>`).join("")}
    </tbody></table>` : '<p class="muted">This plan has no suites.</p>';
  const byId = (id) => suites.find((s) => String(s.id) === String(id));
  box.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const s = byId(b.dataset.edit);
    const m = openModal(`Edit suite #${s.id}`, `<form class="settings">
        <label>Name<input name="name" required maxlength="256" value="${esc(s.name)}"></label>
        <label>Testing type<select name="testing_type">${typeOptions(s.testing_type)}</select></label>
        <p class="muted">The testing type decides which kind of test cases and test data the agents generate for this suite.</p>
        <div class="row"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" data-close>Cancel</button></div></form>`);
    m.el.querySelector("form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const body = {};
      if (f.get("name") !== s.name) body.name = f.get("name");
      if (f.get("testing_type") && f.get("testing_type") !== s.testing_type) body.testing_type = f.get("testing_type");
      if (!Object.keys(body).length) { m.close(); return; }
      try {
        await api(`/api/tfs/test-plans/${plan.id}/suites/${s.id}`, { method: "PATCH", body });
        toast(`Suite #${s.id} updated${body.name ? " in TFS" : ""}`, "success");
        m.close();
        loadSuites(view, plan);
      } catch (err) { toast(err.message, "error"); }
    });
  }));
  box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => {
    const s = byId(b.dataset.del);
    const m = openModal(`Delete suite #${s.id}?`, `<p>This deletes <strong>${esc(s.name)}</strong> (and any child suites) from test plan
      <strong>${esc(plan.name)}</strong> in TFS for project <strong>${esc(meta.project)}</strong>. Its test case work items stay in the project. TFS cannot undo this.</p>
      <div class="row"><button class="btn danger" data-yes>Delete in TFS</button><button class="btn" data-close>Cancel</button></div>`);
    m.el.querySelector("[data-yes]").addEventListener("click", async (e) => {
      e.target.disabled = true;
      try {
        await api(`/api/tfs/test-plans/${plan.id}/suites/${s.id}`, { method: "DELETE" });
        toast(`Suite #${s.id} deleted from TFS`, "success");
        m.close();
        loadSuites(view, plan);
      } catch (err) { toast(err.message, "error"); e.target.disabled = false; }
    });
  }));
}

export async function mount(view) {
  view.innerHTML = '<p class="muted">Loading test plans…</p>';
  let plans;
  try {
    [meta, { plans }, { testing_types: types }] = await Promise.all([
      api("/api/tfs/meta"), api("/api/tfs/test-plans"), api("/api/tfs/testing-types")]);
  } catch (err) {
    view.innerHTML = `<div class="card empty"><h2>TFS data is not available</h2><p>${esc(err.message)}</p>
      <p><a class="btn primary" href="#/tfs-settings">Open TFS connection settings</a></p></div>`;
    return;
  }
  if (!plans.length) {
    view.innerHTML = '<div class="card empty"><h2>No test plans yet</h2><p>A test suite belongs to a test plan.</p><p><a class="btn primary" href="#/test-plans">Create a test plan</a></p></div>';
    return;
  }
  const wanted = params().get("plan");
  view.innerHTML = `
    <div class="card row" style="margin-bottom:12px">
      <label class="row" style="gap:6px"><strong>Test plan</strong>
        <select id="plan">${plans.map((p) => `<option value="${p.id}" ${String(p.id) === wanted ? "selected" : ""}>#${p.id} ${esc(p.name)}</option>`).join("")}</select></label>
      <span class="spacer"></span><a class="btn" href="#/test-plans">Manage test plans</a>
    </div>
    <div class="split">
      <form class="card" id="createSuites">
        <h3 style="margin-top:0">New test suites</h3>
        <p class="muted" style="margin-top:0">Check the testing types to create; each becomes its own suite in TFS.</p>
        <div class="row" style="margin-bottom:6px"><button class="link-btn" type="button" data-all>Check all</button> · <button class="link-btn" type="button" data-none>Uncheck all</button> · <button class="link-btn" type="button" data-default>Defaults</button></div>
        <div class="choices">
          ${types.map((t) => `<label class="choice"><input type="checkbox" name="type" value="${t.key}" ${t.default ? "checked" : ""}>
            <span><strong>${esc(t.name)}<span class="muted exists"></span></strong><span class="muted">${esc(t.focus)}</span></span></label>`).join("")}
        </div>
        <label class="row" style="gap:6px">Under suite <select name="parent"></select></label>
        <label class="row" style="gap:6px;margin-top:6px">Name prefix <input name="prefix" placeholder="optional, e.g. Checkout" maxlength="120"></label>
        <div class="row" style="margin-top:10px"><button class="btn primary" type="submit">Create selected suites in TFS</button></div>
      </form>
      <section class="card detail" id="suites"></section>
    </div>`;
  const form = view.querySelector("#createSuites");
  const planSel = view.querySelector("#plan");
  const current = () => plans.find((p) => String(p.id) === planSel.value);
  form.querySelector("[data-all]").addEventListener("click", () => form.querySelectorAll('[name="type"]').forEach((c) => (c.checked = true)));
  form.querySelector("[data-none]").addEventListener("click", () => form.querySelectorAll('[name="type"]').forEach((c) => (c.checked = false)));
  form.querySelector("[data-default]").addEventListener("click", () => form.querySelectorAll('[name="type"]').forEach((c) => (c.checked = types.find((t) => t.key === c.value).default)));
  planSel.addEventListener("change", () => {
    history.replaceState(null, "", `#/test-suites?plan=${planSel.value}`);
    loadSuites(view, current());
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const keys = f.getAll("type");
    if (!keys.length) { toast("Check at least one testing type.", "warning"); return; }
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const r = await api(`/api/tfs/test-plans/${planSel.value}/suites`, { method: "POST", body: {
        testing_types: keys, parent_suite_id: Number(f.get("parent")) || null, name_prefix: f.get("prefix") || null } });
      const made = r.suites.filter((s) => s.created).length;
      toast(`${made} suite(s) created in TFS${r.suites.length - made ? `, ${r.suites.length - made} reused` : ""}`, "success");
      loadSuites(view, current());
    } catch (err) { toast(err.message, "error"); }
    btn.disabled = false;
  });
  await loadSuites(view, current());
}
