// Work item pages backed by the active TFS / Azure DevOps profile.
// One factory serves TFS Explorer, User Stories, Acceptance Criteria, Test Cases and Defects.

import { api } from "../api.js";
import { esc, safeHtml, toast, debounce, dateTimeOf } from "../util.js";

const SECTIONS = [
  ["acceptance_criteria", "Acceptance Criteria"],
  ["test_cases", "Test Cases"],
  ["defects", "Defects"],
  ["other", "Other links"],
];

export function workItemPage(config) {
  // config: { category: string|null, explorer?: bool, focus?: "acceptance_criteria", createLabel?: string }
  let meta = null;
  let selectedId = null;
  let view = null;

  function notConnected(err) {
    const cfg = ["TFS_NOT_CONFIGURED", "TFS_AUTH_FAILED", "TFS_UNREACHABLE", "TFS_TIMEOUT", "TFS_FORBIDDEN"].includes(err.code);
    view.innerHTML = `
      <div class="card empty">
        <h2>TFS data is not available</h2>
        <p>${esc(err.message)}</p>
        ${cfg ? '<p><a class="btn primary" href="#/tfs-settings">Open TFS connection settings</a></p>' : ""}
        <p class="muted">This page only shows real work items from your configured TFS / Azure DevOps project.</p>
      </div>`;
  }

  function typesForPage() {
    if (!config.category) return meta.work_item_types;
    return meta.categories[config.category]?.types || [];
  }

  function filtersHtml() {
    const types = typesForPage();
    const states = [...new Set(types.flatMap((t) => meta.states[t] || []))];
    return `
      <form class="row card" id="filters" style="margin-bottom:12px">
        ${config.explorer ? `<select name="category" aria-label="Category">
          <option value="">All categories</option>
          ${Object.keys(meta.categories).map((c) => `<option value="${c}">${esc(meta.categories[c].default || c)}</option>`).join("")}
        </select>` : ""}
        <select name="type" aria-label="Work item type"><option value="">All types</option>${types.map((t) => `<option>${esc(t)}</option>`).join("")}</select>
        <select name="state" aria-label="State"><option value="">Any state</option>${states.map((s) => `<option>${esc(s)}</option>`).join("")}</select>
        <select name="iteration" aria-label="Sprint / Iteration"><option value="">Any sprint</option>${meta.iterations.map((i) => `<option>${esc(i)}</option>`).join("")}</select>
        <input name="assigned_to" placeholder="Assigned to (name or @me)" aria-label="Assigned to">
        <input name="q" placeholder="ID or title" aria-label="Search by ID or title">
        <span class="spacer"></span>
        ${config.createLabel ? `<button class="btn primary" type="button" id="createBtn">${esc(config.createLabel || "New")}</button>` : ""}
      </form>`;
  }

  async function loadList() {
    const f = new FormData(view.querySelector("#filters"));
    const params = new URLSearchParams();
    const category = config.category || f.get("category");
    if (category) params.set("category", category);
    for (const k of ["type", "state", "iteration", "assigned_to", "q"]) if (f.get(k)) params.set(k, f.get(k));
    const list = view.querySelector("#list");
    list.innerHTML = '<p class="muted">Loading…</p>';
    try {
      const { items, truncated } = await api(`/api/tfs/work-items?${params}`);
      list.innerHTML = items.length ? `
        <table class="data"><thead><tr><th>ID</th><th>Title</th><th>Type</th><th>State</th><th>Assigned To</th><th>Sprint</th></tr></thead><tbody>
        ${items.map((i) => `<tr class="clickable${i.id === selectedId ? " selected" : ""}" data-id="${i.id}"><td>${i.id}</td><td>${esc(i.title)}</td><td>${esc(i.type)}</td>
          <td>${esc(i.state)}</td><td>${esc(i.assigned_to || "")}</td><td>${esc((i.iteration_path || "").split("\\").pop())}</td></tr>`).join("")}
        </tbody></table>${truncated ? '<p class="muted">Showing the most recent 200. Narrow the filters to see more.</p>' : ""}`
        : '<p class="muted">No work items match these filters.</p>';
      list.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => openDetail(Number(tr.dataset.id))));
    } catch (err) {
      list.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
    }
  }

  function linkRow(l) {
    return `<li><a href="#" data-open="${l.id}">#${l.id}</a> ${esc(l.title)} <span class="muted">· ${esc(l.type)} · ${esc(l.state)}${l.relation ? " · " + esc(l.relation) : ""}</span></li>`;
  }

  async function openDetail(id) {
    selectedId = id;
    view.querySelectorAll("tr[data-id]").forEach((tr) => tr.classList.toggle("selected", Number(tr.dataset.id) === id));
    const panel = view.querySelector("#detail");
    panel.innerHTML = '<p class="muted">Loading…</p>';
    try {
      const { item } = await api(`/api/tfs/work-items/${id}`);
      const t = item.traceability;
      panel.innerHTML = `
        <div class="row"><h2 style="margin:0">#${item.id} ${esc(item.title)}</h2></div>
        <p class="muted">${esc(item.type)} · ${esc(item.state)} · ${esc(item.assigned_to || "Unassigned")} · ${esc(item.iteration_path || "")}</p>
        <div class="row" style="margin-bottom:8px">
          <button class="btn" id="editBtn">Edit</button>
          ${item.web_url ? `<a class="btn" href="${esc(item.web_url)}" target="_blank" rel="noopener">Open in TFS</a>` : ""}
        </div>
        ${item.acceptance_criteria ? `<h3>Acceptance Criteria</h3><div class="rich">${safeHtml(item.acceptance_criteria)}</div>` : ""}
        ${item.description ? `<h3>Description</h3><div class="rich">${safeHtml(item.description)}</div>` : ""}
        ${item.repro_steps ? `<h3>Repro Steps</h3><div class="rich">${safeHtml(item.repro_steps)}</div>` : ""}
        <h3>Traceability</h3>
        ${SECTIONS.map(([k, label]) => `<div><strong>${label}</strong> <span class="muted">(${t[k].length})</span>
          ${t[k].length ? `<ul>${t[k].map(linkRow).join("")}</ul>` : '<p class="muted" style="margin:2px 0 8px">None linked.</p>'}</div>`).join("")}
        <p class="muted">Test data, automation, latest result and performance links are added as those modules are delivered.</p>
        ${item.created ? `<p class="muted">Created ${esc(dateTimeOf(item.created))}${item.created_by ? " by " + esc(item.created_by) : ""}${item.changed ? " · changed " + esc(dateTimeOf(item.changed)) : ""}</p>` : ""}`;
      panel.querySelectorAll("[data-open]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); openDetail(Number(a.dataset.open)); }));
      panel.querySelector("#editBtn").addEventListener("click", () => editForm(item));
    } catch (err) {
      panel.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
    }
  }

  function editForm(item) {
    const panel = view.querySelector("#detail");
    const isNew = !item;
    const types = typesForPage();
    const type = item?.type || meta.categories[config.category]?.default || types[0];
    const states = meta.states[type] || [];
    const bugLike = (meta.categories.bug?.types || []).includes(type);
    panel.innerHTML = `
      <form class="settings" id="wiForm">
        <h2>${isNew ? `New ${esc(type)}` : `Edit #${item.id}`}</h2>
        ${isNew && types.length > 1 ? `<label>Type<select name="type">${types.map((t) => `<option ${t === type ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>` : `<input type="hidden" name="type" value="${esc(type)}">`}
        <label>Title<input name="title" required value="${esc(item?.title || "")}"></label>
        ${isNew ? "" : `<label>State<select name="state">${states.map((s) => `<option ${s === item.state ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label>`}
        <label>Assigned To<input name="assigned_to" value="${esc(item?.assigned_to || "")}" placeholder="Name or email"></label>
        <label>Sprint / Iteration<select name="iteration_path"><option value="">(project default)</option>
          ${meta.iterations.map((i) => `<option ${i === item?.iteration_path ? "selected" : ""}>${esc(i)}</option>`).join("")}</select></label>
        <label>Tags<input name="tags" value="${esc(item?.tags || "")}" placeholder="tag1; tag2"></label>
        <label>Priority<select name="priority"><option value="">(none)</option>${[1, 2, 3, 4].map((p) => `<option ${p === item?.priority ? "selected" : ""}>${p}</option>`).join("")}</select></label>
        <label>${bugLike ? "Repro Steps" : "Description"}<textarea name="${bugLike ? "repro_steps" : "description"}" rows="6"></textarea></label>
        ${config.category === "requirement" ? '<label>Acceptance Criteria<textarea name="acceptance_criteria" rows="5"></textarea></label>' : ""}
        <p class="muted">Fields that this work item type does not have in your process template are skipped, not rejected.</p>
        <div class="row"><button class="btn primary" type="submit">${isNew ? "Create in TFS" : "Save to TFS"}</button><button class="btn" type="button" id="cancelBtn">Cancel</button></div>
      </form>`;
    const ta = panel.querySelector("textarea");
    if (item) ta.value = (bugLike ? item.repro_steps : item.description) || "";
    const ac = panel.querySelector('textarea[name="acceptance_criteria"]');
    if (ac && item) ac.value = item.acceptance_criteria || "";
    panel.querySelector("#cancelBtn").addEventListener("click", () => (item ? openDetail(item.id) : (panel.innerHTML = "")));
    panel.querySelector("#wiForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const fields = {};
      for (const [k, v] of f.entries()) if (k !== "type" && v !== "") fields[k] = k === "priority" ? Number(v) : v;
      try {
        const r = isNew
          ? await api("/api/tfs/work-items", { method: "POST", body: { type: f.get("type"), fields } })
          : await api(`/api/tfs/work-items/${item.id}`, { method: "PATCH", body: { fields } });
        toast(`${isNew ? "Created" : "Saved"} #${r.item.id}${r.skipped_fields.length ? ` (skipped: ${r.skipped_fields.join(", ")})` : ""}`, "success");
        await loadList();
        await openDetail(r.item.id);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  return {
    async mount(v) {
      view = v;
      selectedId = null;
      view.innerHTML = '<p class="muted">Loading TFS project…</p>';
      try {
        meta = await api("/api/tfs/meta");
      } catch (err) {
        notConnected(err);
        return;
      }
      if (config.category && !meta.categories[config.category]) {
        view.innerHTML = `<div class="card empty"><h2>Not available in this project</h2><p>The process template of ${esc(meta.project)} has no work item type for this page.</p></div>`;
        return;
      }
      view.innerHTML = `
        ${filtersHtml()}
        <div class="split">
          <section class="card" id="list"></section>
          <section class="card detail" id="detail"><p class="muted">Select a work item to see its details${config.focus ? " and acceptance criteria" : ""}.</p></section>
        </div>`;
      const filters = view.querySelector("#filters");
      filters.addEventListener("change", loadList);
      filters.addEventListener("input", debounce((e) => { if (e.target.tagName === "INPUT") loadList(); }, 400));
      filters.addEventListener("submit", (e) => { e.preventDefault(); loadList(); });
      view.querySelector("#createBtn")?.addEventListener("click", () => editForm(null));
      const q = new URLSearchParams(location.hash.split("?")[1] || "").get("id");
      await loadList();
      if (q) openDetail(Number(q));
    },
  };
}
