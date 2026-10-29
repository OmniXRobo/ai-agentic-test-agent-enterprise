// Work item pages backed by the active TFS / Azure DevOps profile.
// One factory serves TFS Explorer, User Stories, Acceptance Criteria, Test Cases and Defects.

import { api } from "../api.js";
import { esc, safeHtml, toast, debounce, dateTimeOf, openModal } from "../util.js";

const SECTIONS = [
  ["acceptance_criteria", "Acceptance Criteria"],
  ["test_cases", "Test Cases"],
  ["defects", "Defects"],
  ["other", "Other links"],
];

export function workItemPage(config) {
  // config: { category: string|null, explorer?: bool, focus?: "acceptance_criteria", createLabel?: string,
  //           manage?: bool (check boxes, select all, per-row Edit / Delete and Delete selected) }
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
      const m = config.manage;
      const tags = (t) => (t || "").split(";").map((x) => x.trim()).filter(Boolean).map((x) => `<span class="tag">${esc(x)}</span>`).join(" ");
      list.innerHTML = items.length ? `
        ${m ? `<div class="row" style="margin-bottom:8px"><label class="row" style="gap:6px"><input type="checkbox" id="wiAll"> Select all</label>
          <span class="muted" id="wiCount">0 selected</span><span class="spacer"></span>
          <button class="btn danger" type="button" id="wiDel" disabled>Delete selected</button></div>` : ""}
        <table class="data"><thead><tr>${m ? '<th><span class="sr-only">Select</span></th>' : ""}<th>ID</th><th>Title</th>${m ? "<th>Tags</th>" : "<th>Type</th>"}<th>State</th><th>Assigned To</th><th>Sprint</th>${m ? "<th></th>" : ""}</tr></thead><tbody>
        ${items.map((i) => `<tr class="clickable${i.id === selectedId ? " selected" : ""}" data-id="${i.id}">${m ? `<td><input type="checkbox" class="wiSel" value="${i.id}" aria-label="Select #${i.id}"></td>` : ""}<td>${i.id}</td><td>${esc(i.title)}</td>${m ? `<td>${tags(i.tags)}</td>` : `<td>${esc(i.type)}</td>`}
          <td>${esc(i.state)}</td><td>${esc(i.assigned_to || "")}</td><td>${esc((i.iteration_path || "").split("\\").pop())}</td>
          ${m ? `<td style="white-space:nowrap"><button class="link-btn" type="button" data-wi-edit="${i.id}">Edit</button> · <button class="link-btn" type="button" data-wi-del="${i.id}">Delete</button></td>` : ""}</tr>`).join("")}
        </tbody></table>${truncated ? '<p class="muted">Showing the most recent 200. Narrow the filters to see more.</p>' : ""}`
        : '<p class="muted">No work items match these filters.</p>';
      list.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", (e) => {
        if (e.target.closest("input, button")) return;
        openDetail(Number(tr.dataset.id));
      }));
      if (m) manageList(list, items);
    } catch (err) {
      list.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
    }
  }

  function manageList(list, items) {
    const boxes = () => [...list.querySelectorAll(".wiSel")];
    const picked = () => boxes().filter((c) => c.checked).map((c) => Number(c.value));
    const sync = () => {
      const n = picked().length;
      list.querySelector("#wiCount").textContent = `${n} selected`;
      list.querySelector("#wiDel").disabled = !n;
      list.querySelector("#wiAll").checked = n > 0 && n === boxes().length;
    };
    list.querySelector("#wiAll").addEventListener("change", (e) => { boxes().forEach((c) => (c.checked = e.target.checked)); sync(); });
    boxes().forEach((c) => c.addEventListener("change", sync));
    list.querySelectorAll("[data-wi-edit]").forEach((b) => b.addEventListener("click", async () => {
      selectedId = Number(b.dataset.wiEdit);
      try {
        const { item } = await api(`/api/tfs/work-items/${selectedId}`);
        editForm(item);
      } catch (err) { toast(err.message, "error"); }
    }));
    list.querySelectorAll("[data-wi-del]").forEach((b) => b.addEventListener("click", () => confirmDelete([Number(b.dataset.wiDel)], items)));
    list.querySelector("#wiDel").addEventListener("click", () => confirmDelete(picked(), items));
  }

  function confirmDelete(ids, items) {
    const titles = ids.map((id) => items.find((i) => i.id === id)).filter(Boolean);
    const testCases = config.category === "testcase";
    const m = openModal(ids.length === 1 ? `Delete #${ids[0]}?` : `Delete ${ids.length} work items?`, `
      <p>This deletes ${ids.length === 1 ? "this work item" : `these ${ids.length} work items`} from project <strong>${esc(meta.project)}</strong> in TFS.
      ${testCases ? "Test cases are deleted <strong>permanently</strong> with their test results and suite memberships; TFS cannot restore them." : "They go to the project's Recycle Bin, where they can be restored."}</p>
      <ul style="max-height:240px;overflow:auto">${titles.map((i) => `<li>#${i.id} ${esc(i.title)}</li>`).join("")}</ul>
      <div class="row"><button class="btn danger" type="button" data-yes>Delete in TFS</button><button class="btn" type="button" data-close>Cancel</button></div>`);
    m.el.querySelector("[data-yes]").addEventListener("click", async (e) => {
      e.target.disabled = true;
      e.target.textContent = "Deleting…";
      try {
        const r = await api("/api/tfs/work-items/delete", { method: "POST", body: { ids } });
        if (r.deleted.length) toast(`${r.deleted.length} deleted in TFS`, "success");
        r.failed.forEach((f) => toast(`#${f.id}: ${f.error}`, "error"));
        m.close();
        if (ids.includes(selectedId)) view.querySelector("#detail").innerHTML = '<p class="muted">Select a work item to see its details.</p>';
        await loadList();
      } catch (err) { toast(err.message, "error"); e.target.disabled = false; e.target.textContent = "Delete in TFS"; }
    });
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
          ${config.manage ? '<button class="btn danger" id="delBtn">Delete</button>' : ""}
          ${(meta.categories.requirement?.types || []).includes(item.type) ? `<a class="btn primary" href="#/test-design?story=${item.id}">Generate test design</a>` : ""}
          ${item.web_url ? `<a class="btn" href="${esc(item.web_url)}" target="_blank" rel="noopener">Open in TFS</a>` : ""}
        </div>
        ${item.acceptance_criteria ? `<h3>Acceptance Criteria</h3><div class="rich">${safeHtml(item.acceptance_criteria)}</div>` : ""}
        ${item.description ? `<h3>Description</h3><div class="rich">${safeHtml(item.description)}</div>` : ""}
        ${item.repro_steps ? `<h3>Repro Steps</h3><div class="rich">${safeHtml(item.repro_steps)}</div>` : ""}
        ${item.step_list?.length ? `<h3>Steps</h3><table class="data"><thead><tr><th>Step</th><th>Action</th><th>Expected Result</th></tr></thead><tbody>
          ${item.step_list.map((st, n) => `<tr><td>${n + 1}</td><td>${esc(st.action)}</td><td>${esc(st.expected)}</td></tr>`).join("")}</tbody></table>` : ""}
        ${item.tags ? `<p>${item.tags.split(";").map((x) => x.trim()).filter(Boolean).map((x) => `<span class="tag">${esc(x)}</span>`).join(" ")}</p>` : ""}
        <h3>Traceability</h3>
        ${SECTIONS.map(([k, label]) => `<div><strong>${label}</strong> <span class="muted">(${t[k].length})</span>
          ${t[k].length ? `<ul>${t[k].map(linkRow).join("")}</ul>` : '<p class="muted" style="margin:2px 0 8px">None linked.</p>'}</div>`).join("")}
        <p class="muted">Automation, latest result and performance links are added as those modules are delivered.</p>
        ${item.created ? `<p class="muted">Created ${esc(dateTimeOf(item.created))}${item.created_by ? " by " + esc(item.created_by) : ""}${item.changed ? " · changed " + esc(dateTimeOf(item.changed)) : ""}</p>` : ""}`;
      panel.querySelectorAll("[data-open]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); openDetail(Number(a.dataset.open)); }));
      panel.querySelector("#editBtn").addEventListener("click", () => editForm(item));
      panel.querySelector("#delBtn")?.addEventListener("click", () => confirmDelete([item.id], [item]));
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
    const testCaseLike = (meta.categories.testcase?.types || []).includes(type);
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
        ${testCaseLike ? `<label>Steps, one per line: Action || Expected Result<textarea name="steps" rows="8" placeholder="Enter 'standard_user' in the Username field. || The Username field shows 'standard_user'.">${esc((item?.step_list || []).map((st) => `${st.action} || ${st.expected}`).join("\n"))}</textarea></label>` : ""}
        <label>${bugLike ? "Repro Steps" : testCaseLike ? "Summary" : "Description"}<textarea name="${bugLike ? "repro_steps" : "description"}" rows="6"></textarea></label>
        ${config.category === "requirement" ? '<label>Acceptance Criteria<textarea name="acceptance_criteria" rows="5"></textarea></label>' : ""}
        <p class="muted">Fields that this work item type does not have in your process template are skipped, not rejected.</p>
        <div class="row"><button class="btn primary" type="submit">${isNew ? "Create in TFS" : "Save to TFS"}</button><button class="btn" type="button" id="cancelBtn">Cancel</button></div>
      </form>`;
    const ta = panel.querySelector(`textarea[name="${bugLike ? "repro_steps" : "description"}"]`);
    if (item) ta.value = (bugLike ? item.repro_steps : item.description) || "";
    const ac = panel.querySelector('textarea[name="acceptance_criteria"]');
    if (ac && item) ac.value = item.acceptance_criteria || "";
    panel.querySelector("#cancelBtn").addEventListener("click", () => (item ? openDetail(item.id) : (panel.innerHTML = "")));
    panel.querySelector("#wiForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const fields = {};
      for (const [k, v] of f.entries()) if (k !== "type" && k !== "steps" && v !== "") fields[k] = k === "priority" ? Number(v) : v;
      if (testCaseLike && f.get("steps") !== null && (String(f.get("steps")).trim() || item?.step_list?.length)) {
        fields.steps = String(f.get("steps")).split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
          const [action, ...rest] = l.split("||");
          return { action: action.trim(), expected: rest.join("||").trim() };
        });
      }
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
      const hashParams = new URLSearchParams(location.hash.split("?")[1] || "");
      const q = hashParams.get("id");
      // Filters given in the address (e.g. by a voice search: sprint, state, words in the title) are applied.
      for (const k of ["state", "iteration", "q"]) {
        const v = hashParams.get(k), el = filters.elements[k];
        if (!v || !el) continue;
        if (el.tagName === "SELECT" && ![...el.options].some((o) => o.value === v)) el.add(new Option(v, v));
        el.value = v;
      }
      await loadList();
      if (q) openDetail(Number(q));
    },
  };
}
