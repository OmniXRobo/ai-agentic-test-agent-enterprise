// Test Data: suite-aware data rows generated from TFS test cases, filtered by sprint, user story and test case.
// Rows are traceable (story -> criterion -> suite -> test case -> dataset -> row), versioned and editable.

import { api } from "../api.js";
import { esc, toast, dateTimeOf, openModal } from "../util.js";

let filters = {};
let items = [];
let timer = null;

const short = (v) => {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 60 ? s.slice(0, 57) + "…" : s;
};

function query(extra = {}) {
  const q = new URLSearchParams();
  if (filters.iteration) q.set("iteration", filters.iteration);
  if (filters.user_story_id) q.set("user_story_id", filters.user_story_id);
  if (filters.test_case_id) q.set("test_case_ids", filters.test_case_id);
  for (const [k, v] of Object.entries(extra)) q.set(k, v);
  return q;
}

function valuesHtml(d) {
  const vals = d.dataset && typeof d.dataset === "object" ? d.dataset : {};
  const prev = d.meta?.preview || {};
  return Object.entries(vals).map(([k, v]) => {
    const p = prev[k];
    const dyn = typeof v === "string" && v.includes("{{") && p !== undefined && p !== v;
    return `<div><span class="muted">${esc(k)}:</span> <code>${esc(v === null ? "null" : short(v))}</code>${dyn ? ` <span class="muted" title="Preview resolved now from the system clock; the rule is resolved again when the test runs">→ ${esc(short(p))}</span>` : ""}</div>`;
  }).join("") || '<span class="muted">(no values)</span>';
}

async function render(view) {
  const box = view.querySelector("#list");
  box.innerHTML = '<p class="muted">Loading…</p>';
  try {
    ({ items } = await api(`/api/test-data?${query()}`));
  } catch (err) {
    box.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
    return;
  }
  box.innerHTML = items.length ? `
    <table class="data"><thead><tr><th><input type="checkbox" id="selAll" aria-label="Select all"></th><th>User Story</th><th>Test Case</th><th>Row</th><th>Scenario</th><th>Values</th><th>Expected outcome</th><th>AC</th><th>Updated</th><th></th></tr></thead><tbody>
    ${items.map((d) => `<tr data-id="${d.id}"><td><input type="checkbox" class="sel" value="${d.id}"></td>
      <td>${d.user_story_id ? `<a href="#/user-stories?id=${esc(d.user_story_id)}">US${esc(d.user_story_id)}</a>` : ""}</td>
      <td>${d.tfs_test_case_id ? `<a href="#/test-cases?id=${d.tfs_test_case_id}">#${d.tfs_test_case_id}</a>` : esc(d.test_case_ref)}<div class="muted">${esc(d.testing_type || "")}</div></td>
      <td>${esc(d.name)}<div class="muted">${esc(d.row_key || "")}${d.version > 1 ? ` · v${d.version}` : ""}${d.user_edited ? " · edited" : ""}</div></td>
      <td>${esc(d.scenario || d.kind || "")}${d.intended_valid === false ? `<div class="pill WARN" title="Intentionally invalid row">invalid: ${esc(d.violated_rule || "rule not stated")}</div>` : ""}</td>
      <td>${valuesHtml(d)}</td><td>${esc(d.expected_outcome || "")}</td><td>${esc((d.ac_ids || []).join(", "))}</td>
      <td>${esc(dateTimeOf(d.updated_at))}${d.review_reason ? `<div class="pill WARN" title="${esc(d.review_reason)}">review</div>` : ""}</td>
      <td style="white-space:nowrap"><button class="link-btn" data-edit="${d.id}">Edit</button> · <button class="link-btn" data-hist="${d.id}">History</button></td></tr>`).join("")}
    </tbody></table>`
    : '<p class="muted">No test data for these filters. Choose a sprint, user story or test case and click Generate test data.</p>';
  box.querySelector("#selAll")?.addEventListener("change", (e) => box.querySelectorAll(".sel").forEach((c) => (c.checked = e.target.checked)));
  box.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => editRow(view, items.find((x) => String(x.id) === b.dataset.edit))));
  box.querySelectorAll("[data-hist]").forEach((b) => b.addEventListener("click", async () => {
    const { history } = await api(`/api/test-data/${b.dataset.hist}/history`);
    openModal("Row history", history.length ? `<table class="data"><thead><tr><th>When</th><th>Change</th><th>Version</th><th>Values before</th></tr></thead><tbody>
      ${history.map((h) => `<tr><td>${esc(dateTimeOf(h.at))}</td><td>${esc(h.change)}</td><td>${h.version}</td><td><code>${esc(typeof h.snapshot.dataset === "string" ? h.snapshot.dataset : JSON.stringify(h.snapshot.dataset))}</code></td></tr>`).join("")}</tbody></table>` : '<p class="muted">Not changed since it was generated.</p>', { wide: true });
  }));
}

function selected(view) {
  return [...view.querySelectorAll(".sel:checked")].map((c) => Number(c.value));
}

function editRow(view, d) {
  const m = openModal(`Edit ${d.row_key || d.name}`, `<form class="settings">
      <label>Name<input name="name" value="${esc(d.name)}"></label>
      <label>Scenario<input name="scenario" value="${esc(d.scenario || "")}"></label>
      <label class="row" style="gap:6px"><input type="checkbox" name="intended_valid" ${d.intended_valid === false ? "" : "checked"}> Intended to be valid</label>
      <label>Violated rule (invalid rows)<input name="violated_rule" value="${esc(d.violated_rule || "")}"></label>
      <label>Expected outcome<input name="expected_outcome" value="${esc(d.expected_outcome || "")}"></label>
      <label>Values (JSON). Secrets as {{SECRET:name}}, dates as {{TODAY+3d:%d/%m/%Y}} or {{NOW+2h:%H:%M}}
        <textarea name="values" rows="8">${esc(JSON.stringify(d.dataset, null, 2))}</textarea></label>
      <div class="row"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" data-close>Cancel</button></div></form>`, { wide: true });
  m.el.querySelector("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await api(`/api/test-data/${d.id}`, { method: "PATCH", body: {
        name: f.get("name"), scenario: f.get("scenario") || null, intended_valid: f.get("intended_valid") === "on",
        violated_rule: f.get("violated_rule") || null, expected_outcome: f.get("expected_outcome") || null,
        values: JSON.parse(f.get("values")) } });
      toast("Test data saved", "success");
      m.close();
      render(view);
    } catch (err) { toast(err instanceof SyntaxError ? "Values are not valid JSON." : err.message, "error"); }
  });
}

let stories = [];
let cases = [];

async function generate(view) {
  const sel = [...new Set(items.filter((d) => selected(view).includes(d.id)).map((d) => d.tfs_test_case_id).filter(Boolean))];
  if (!filters.iteration && !filters.user_story_id && !sel.length) { toast("Choose a sprint or a user story first.", "warning"); return; }
  const caseList = cases.length ? cases : sel.map((id) => ({ id, title: "" }));
  const pre = new Set(filters.test_case_id ? [Number(filters.test_case_id)] : sel);
  const m = openModal("Generate test data", `<form class="gen-scope">
      ${filters.user_story_id || caseList.length ? `<label class="row" style="gap:6px"><input type="radio" name="scope" value="cases" ${filters.user_story_id || sel.length ? "checked" : ""}>
        <strong>Test cases${filters.user_story_id ? ` of US${esc(filters.user_story_id)}` : ""}</strong></label>
      <div class="row" style="margin-left:22px"><button class="link-btn" type="button" data-on="tc">Check all</button> · <button class="link-btn" type="button" data-off="tc">Uncheck all</button></div>
      <div class="choices" style="margin-left:22px;grid-template-columns:1fr">${caseList.map((t) => `<label class="choice"><input type="checkbox" name="tc" value="${t.id}" ${!pre.size || pre.has(t.id) ? "checked" : ""}><span>#${t.id} ${esc(t.title)}</span></label>`).join("")}</div>` : ""}
      ${stories.length ? `<label class="row" style="gap:6px"><input type="radio" name="scope" value="stories" ${filters.user_story_id || sel.length ? "" : "checked"}>
        <strong>Every test case of these user stories in ${esc((filters.iteration || "").split("\\").pop())}</strong></label>
      <div class="row" style="margin-left:22px"><button class="link-btn" type="button" data-on="us">Check all</button> · <button class="link-btn" type="button" data-off="us">Uncheck all</button></div>
      <div class="choices" style="margin-left:22px;grid-template-columns:1fr">${stories.map((st) => `<label class="choice"><input type="checkbox" name="us" value="${st.id}" ${String(st.id) === String(filters.user_story_id || "") || !filters.user_story_id ? "checked" : ""}><span>US${st.id} ${esc(st.title)}</span></label>`).join("")}</div>` : ""}
      <p class="muted">Each test case gets data for its suite's testing type, written as a Test Data Matrix (Combination 1, 2, …) when you write it to TFS.</p>
      <div class="row"><button class="btn primary" type="submit">Generate</button><button class="btn" type="button" data-close>Cancel</button></div></form>`, { wide: true });
  m.el.querySelectorAll("[data-on]").forEach((b) => b.addEventListener("click", () => m.el.querySelectorAll(`[name="${b.dataset.on}"]`).forEach((c) => (c.checked = true))));
  m.el.querySelectorAll("[data-off]").forEach((b) => b.addEventListener("click", () => m.el.querySelectorAll(`[name="${b.dataset.off}"]`).forEach((c) => (c.checked = false))));
  m.el.querySelector("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const scope = f.get("scope") || (stories.length ? "stories" : "cases");
    const body = { max_rows: Number(view.querySelector("#maxRows").value) || 12,
      seed: view.querySelector("#seed").value ? Number(view.querySelector("#seed").value) : null };
    if (scope === "cases") body.test_case_ids = f.getAll("tc").map(Number);
    else body.user_story_ids = f.getAll("us").map(Number);
    if (!(body.test_case_ids || body.user_story_ids).length) { toast("Check at least one item.", "warning"); return; }
    m.close();
    run(view, body);
  });
}

async function run(view, body) {
  const out = view.querySelector("#genOut");
  try {
    const r = await api("/api/test-data/generate", { method: "POST", body });
    out.innerHTML = `<div class="card"><p class="pill RUNNING">Generating test data for ${r.test_case_ids.length} test case(s)…</p><p class="muted">Progress appears on Agent Workflow.</p></div>`;
    const poll = async () => {
      const s = await api(`/api/test-data/generate/${r.run_id}`);
      if (s.status === "RUNNING") { timer = setTimeout(poll, 1500); return; }
      if (s.status === "FAILED") { out.innerHTML = `<div class="card"><p class="pill FAIL">Generation failed</p><p>${esc(s.result?.error || "")}</p></div>`; return; }
      out.innerHTML = `<div class="card"><p class="pill PASS">Test data generated</p>${s.result.results.map((x) => `<details><summary>#${x.test_case_id} ${esc(x.title)} · ${esc(x.testing_type)} · ${x.rows.length} rows (v${x.version}, seed ${x.seed})${x.kept_user_edited ? ` · ${x.kept_user_edited} edited rows kept` : ""}</summary>
        <p class="muted">Scenarios: ${esc(x.scenarios.join(", ") || "none")}</p>
        ${x.issues.length ? `<p><strong>Rules applied</strong></p><ul>${x.issues.map((i) => `<li>${esc(i.row)}: ${esc(i.message)}</li>`).join("")}</ul>` : ""}
        ${x.gaps.length ? `<p><strong>Gaps (not invented)</strong></p><ul>${x.gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}
        ${x.not_applicable.length ? `<p><strong>Not applicable</strong></p><ul>${x.not_applicable.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}</details>`).join("")}</div>`;
      render(view);
    };
    timer = setTimeout(poll, 1500);
  } catch (err) { toast(err.message, "error"); }
}

async function syncFromTfs(view, refresh = false) {
  const body = filters.test_case_id ? { test_case_ids: [Number(filters.test_case_id)] }
    : filters.user_story_id ? { user_story_ids: [Number(filters.user_story_id)] } : null;
  if (!body) {
    if (refresh) toast("Choose a user story or a test case to load its test data from TFS.", "warning");
    return;
  }
  const note = view.querySelector("#syncNote");
  note.textContent = "Reading the test data already in TFS…";
  try {
    const r = await api("/api/test-data/sync", { method: "POST", body: { ...body, refresh } });
    const imported = r.results.filter((x) => x.status === "imported");
    const found = r.results.filter((x) => x.source);
    note.textContent = found.length
      ? `TFS: ${found.length} test case(s) have test data (${[...new Set(found.map((x) => x.source.replace(/ \(.*\)/, "")))].join(", ")})${imported.length ? `; ${r.imported} row(s) loaded now` : ", already loaded"}.`
      : "TFS: no test data found in these test cases yet.";
    imported.flatMap((x) => x.notes || []).slice(0, 3).forEach((n) => toast(n, "info"));
    r.results.filter((x) => x.status === "error").forEach((x) => toast(`#${x.test_case_id}: ${x.error}`, "error"));
  } catch (err) { note.textContent = ""; toast(err.message, "error"); }
}

async function loadStories(view) {
  const sel = view.querySelector('[name="user_story_id"]');
  sel.innerHTML = '<option value="">All user stories</option>';
  stories = [];
  if (!filters.iteration) return;
  try {
    ({ stories } = await api(`/api/design/sprint-stories?iteration=${encodeURIComponent(filters.iteration)}`));
    sel.innerHTML += stories.map((s) => `<option value="${s.id}">US${s.id} ${esc(s.title)}</option>`).join("");
  } catch (err) { toast(err.message, "error"); }
}

async function loadCases(view) {
  const sel = view.querySelector('[name="test_case_id"]');
  sel.innerHTML = '<option value="">All test cases</option>';
  cases = [];
  if (!filters.user_story_id) return;
  try {
    const { item } = await api(`/api/tfs/work-items/${filters.user_story_id}`);
    cases = item.traceability.test_cases;
    sel.innerHTML += item.traceability.test_cases.map((t) => `<option value="${t.id}">#${t.id} ${esc(t.title)}</option>`).join("");
  } catch (err) { toast(err.message, "error"); }
}

export async function mount(view) {
  filters = {};
  let meta = { iterations: [] };
  try { meta = await api("/api/tfs/meta"); } catch { /* list still works offline from saved data */ }
  view.innerHTML = `
    <div class="card" style="margin-bottom:12px">
      <div class="row" style="flex-wrap:wrap">
        <select name="iteration" aria-label="Sprint"><option value="">All sprints</option>${meta.iterations.map((i) => `<option>${esc(i)}</option>`).join("")}</select>
        <select name="user_story_id" aria-label="User story"><option value="">All user stories</option></select>
        <select name="test_case_id" aria-label="Test case"><option value="">All test cases</option></select>
        <span class="spacer"></span>
        <label class="row" style="gap:6px">Rows <input id="maxRows" type="number" value="12" min="1" max="60" style="width:64px"></label>
        <label class="row" style="gap:6px">Seed <input id="seed" type="number" min="0" placeholder="auto" style="width:90px"></label>
        <button class="btn primary" id="gen" type="button">Generate test data</button>
      </div>
      <div class="row" style="flex-wrap:wrap;margin-top:8px">
        <button class="btn danger" id="del" type="button">Delete selected</button>
        <button class="btn" id="load" type="button" title="Read the test data already written in the TFS test cases (Test Data tab, Parameter values or Summary)">Load from TFS</button>
        <button class="btn" id="tfs" type="button" title="Writes a Test Data Matrix to the test case's Test Data tab when it has one, otherwise to its Parameter values or Summary">Write to TFS</button>
        <button class="btn" id="cov" type="button">Coverage matrix</button>
        <button class="btn" id="bind" type="button">Check parameter binding</button>
        <span class="spacer"></span>
        <button class="btn" id="csv" type="button">Export CSV</button>
        <button class="btn" id="json" type="button">Export JSON</button>
        <label class="btn" style="cursor:pointer">Import CSV/JSON<input id="imp" type="file" accept=".csv,.json" hidden></label>
      </div>
      <p class="muted" id="syncNote" style="margin:6px 0 0"></p>
      <p class="muted" style="margin:6px 0 0">Generation uses the test case's steps, its user story's acceptance criteria and its suite's testing type. Nothing is inserted into any database or sent to any API: rows are only saved here and written to TFS when you ask. Passwords and tokens are stored as secret references.</p>
    </div>
    <div id="genOut" style="margin-bottom:12px"></div>
    <div class="card" id="list"><p class="muted">Loading…</p></div>`;
  const f = (n) => view.querySelector(`[name="${n}"]`);
  f("iteration").addEventListener("change", async (e) => {
    filters = { iteration: e.target.value };
    await loadStories(view);
    await loadCases(view);
    render(view);
  });
  f("user_story_id").addEventListener("change", async (e) => {
    filters = { iteration: filters.iteration, user_story_id: e.target.value };
    await loadCases(view);
    await syncFromTfs(view);
    render(view);
  });
  f("test_case_id").addEventListener("change", async (e) => { filters.test_case_id = e.target.value; await syncFromTfs(view); render(view); });
  view.querySelector("#load").addEventListener("click", async () => { await syncFromTfs(view, true); render(view); });
  view.querySelector("#gen").addEventListener("click", () => generate(view));
  view.querySelector("#del").addEventListener("click", async () => {
    const ids = selected(view);
    if (!ids.length) { toast("Select the rows to delete.", "warning"); return; }
    if (!confirm(`Delete ${ids.length} selected test data row(s)? They are kept in the row history.`)) return;
    try {
      const r = await api("/api/test-data/delete", { method: "POST", body: { ids } });
      toast(`${r.deleted} row(s) deleted`, "success");
      render(view);
    } catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#tfs").addEventListener("click", async (e) => {
    const sel = selected(view);
    const cases = filters.test_case_id ? [Number(filters.test_case_id)]
      : [...new Set(items.filter((d) => !sel.length || sel.includes(d.id)).map((d) => d.tfs_test_case_id).filter(Boolean))];
    if (!cases.length) { toast("No test cases with data to write.", "warning"); return; }
    e.target.disabled = true;
    for (const tc of cases) {
      try {
        const r = await api(`/api/test-data/tfs/${tc}`, { method: "POST", body: { target: "auto" } });
        toast(`Test Case #${r.test_case_id}: ${r.rows} row(s) written to ${r.target}`, "success");
      } catch (err) { toast(`#${tc}: ${err.message}`, "error"); }
    }
    e.target.disabled = false;
  });
  view.querySelector("#cov").addEventListener("click", async () => {
    if (!filters.user_story_id) { toast("Choose a user story first.", "warning"); return; }
    const m = openModal(`Test data coverage for US${filters.user_story_id}`, '<p class="muted">Loading…</p>', { wide: true });
    try {
      const c = await api(`/api/test-data/coverage/${filters.user_story_id}`);
      m.el.querySelector(".modal-body").innerHTML = `<p class="muted">Missing data coverage (a test case without rows) is shown separately from missing test case coverage.</p>
        <table class="data"><thead><tr><th>AC</th><th>Criterion</th><th>Test cases → rows</th><th>Status</th></tr></thead><tbody>
        ${c.matrix.map((r) => `<tr><td>${esc(r.ac_id)}</td><td>${esc(r.text)}</td><td>${r.test_cases.map((t) => `<a href="#/test-cases?id=${t.test_case_id}" title="${esc(t.title || "")}">#${t.test_case_id}</a>${t.link === "inferred" ? ' <span class="muted" title="Created outside this application; matched to the criterion by its title and steps">(inferred)</span>' : ""}: ${t.rows.length} rows (${t.valid} valid, ${t.invalid} invalid) ${esc(t.scenarios.join(", "))}`).join("<br>") || "—"}</td>
          <td><span class="pill ${r.status === "Data covered" ? "PASS" : r.status === "Missing data coverage" ? "WARN" : "FAIL"}">${esc(r.status)}</span></td></tr>`).join("")}</tbody></table>
        ${c.unmapped?.length ? `<p class="muted">Linked to the story but not matched to a criterion: ${c.unmapped.map((u) => `#${u.tfs_test_case_id} ${esc(u.title)} (${u.rows} rows)`).join(", ")}</p>` : ""}`;
    } catch (err) { m.el.querySelector(".modal-body").innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; }
  });
  view.querySelector("#bind").addEventListener("click", async () => {
    if (!filters.test_case_id) { toast("Choose a test case first.", "warning"); return; }
    try {
      const b = await api(`/api/test-data/binding/${filters.test_case_id}`);
      openModal(`Parameter binding for #${b.test_case_id}`, `<p>Step parameters: ${esc(b.parameters.join(", ") || "none")} · rows: ${b.rows}</p>
        ${b.ok ? '<p class="pill PASS">Every row has a value for every parameter.</p>' : `<ul>${b.problems.map((p) => `<li>${esc(p.row)}: missing ${esc(p.missing_fields.join(", ") || "-")}; unresolved ${esc(p.unresolved_references.join(", ") || "-")}</li>`).join("")}</ul>`}`);
    } catch (err) { toast(err.message, "error"); }
  });
  const exportAs = (fmt) => {
    const sel = selected(view);
    location.href = `/api/test-data/export?${query({ fmt, ...(sel.length ? { ids: sel.join(",") } : {}) })}`;
  };
  view.querySelector("#csv").addEventListener("click", () => exportAs("csv"));
  view.querySelector("#json").addEventListener("click", () => exportAs("json"));
  view.querySelector("#imp").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const r = await api("/api/test-data/import", { method: "POST", body: {
        format: file.name.toLowerCase().endsWith(".json") ? "json" : "csv", content: await file.text(),
        test_case_id: filters.test_case_id ? Number(filters.test_case_id) : null } });
      toast(`${r.imported} row(s) imported`, "success");
      render(view);
    } catch (err) { toast(err.message, "error"); }
    e.target.value = "";
  });
  await render(view);
}

export function unmount() {
  clearTimeout(timer);
  timer = null;
}
