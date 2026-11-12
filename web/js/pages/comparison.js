// Application Data Comparison Agent (final patch): one module, one page per submenu.
// Every result shown here is decided by the deterministic comparison rules on the server; AI text only explains.

import { api } from "../api.js";
import { esc, toast, dateTimeOf, openModal } from "../util.js";
import { canEdit } from "../auth.js";

const KEY = "qa.cmp.project";
let project = null;
let timer = null;

function pid() { try { return localStorage.getItem(KEY); } catch { return null; } }
function setPid(v) { try { localStorage.setItem(KEY, v); } catch { /* storage blocked */ } }
const pill = (r) => `<span class="pill ${r === "PASS" || r === "PASS_EQUIVALENT" ? "PASS" : r === "FAIL" ? "FAIL" : "WARN"}">${esc(r || "-")}</span>`;
const table = (heads, rows) => rows.length ? `<div class="table-wrap"><table class="data"><thead><tr>${heads.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>` : '<p class="muted">Nothing yet.</p>';
const fmt = (v) => (v && typeof v === "object") ? esc(JSON.stringify(v)) : esc(v ?? "");
const opts = (items, sel, label = (x) => x.name) => items.map((x) => `<option value="${esc(x.id)}" ${x.id === sel ? "selected" : ""}>${esc(label(x))}</option>`).join("");

async function loadProject() {
  const id = pid();
  if (!id) return null;
  try { project = await api(`/api/comparison/projects/${encodeURIComponent(id)}`); }
  catch { project = null; setPid(""); }
  return project;
}

async function header(view, onChange) {
  const { items } = await api("/api/comparison/projects");
  if (!pid() && items.length) setPid(items[0].id);
  await loadProject();
  const bar = document.createElement("div");
  bar.className = "card row cmp-bar";
  bar.innerHTML = `<label>Comparison project <select id="cmpProj">${items.length ? opts(items, pid()) : '<option value="">No project yet</option>'}</select></label>
    <span class="spacer"></span>${canEdit() ? '<button class="btn" id="cmpNew">New project</button>' : ""}
    <span class="muted">Results are decided by deterministic rules. AI only suggests and explains.</span>`;
  view.prepend(bar);
  bar.querySelector("#cmpProj").addEventListener("change", async (e) => { setPid(e.target.value); await loadProject(); onChange(); });
  bar.querySelector("#cmpNew")?.addEventListener("click", () => newProject(async () => { await mountAgain(view, onChange); }));
}

async function mountAgain(view, onChange) { view.querySelector(".cmp-bar")?.remove(); await header(view, onChange); onChange(); }

function newProject(after) {
  const { el, close } = openModal("New comparison project", `<form class="settings" id="np">
    <label>Name <input name="name" required maxlength="120"></label>
    <label>Description <textarea name="description" maxlength="2000"></textarea></label>
    <label>Members (usernames, comma separated) <input name="members"></label>
    <div class="row"><button class="btn primary">Create</button></div></form>`);
  el.querySelector("#np").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      const { project: p } = await api("/api/comparison/projects", { method: "POST", body: { name: f.name, description: f.description,
        members: f.members.split(",").map((s) => s.trim()).filter(Boolean) } });
      setPid(p.id); close(); after();
    } catch (err) { toast(err.message, "error"); }
  });
}

// Add or edit an application: URL, sign-in method, username and password (the password goes to Windows Credential
// Manager and is never shown again), or the desktop window it is read from.
function appForm(app, after) {
  const a = app || { app_type: "web", auth_method: "form", config: {} };
  const { el, close } = openModal(app ? `Edit ${a.name}` : "Add application", `<form class="settings" id="af" autocomplete="off" style="max-width:none">
    <label>Name <input name="name" required maxlength="120" value="${esc(a.name || "")}"></label>
    <label>Type <select name="app_type">${[["web", "Web application"], ["desktop", "Windows desktop application"], ["rdp", "Remote desktop / Citrix (screen + OCR)"], ["screenshot", "Screenshots only"], ["file", "Exported files only"]]
      .map(([v, t]) => `<option value="${v}" ${v === a.app_type ? "selected" : ""}>${t}</option>`).join("")}</select></label>
    <div data-for="web"><label>Application URL <input name="base_url" value="${esc(a.base_url || "")}" placeholder="https://erp.company.local/invoices"></label>
      <label>Login URL <input name="login_url" value="${esc(a.config.login_url || "")}" placeholder="blank = application URL"></label>
      <label>Sign-in method <select name="auth_method">${[["form", "Login form (username and password)"], ["windows", "Windows / NTLM"], ["basic", "Basic"], ["manual", "Manual (SSO / MFA)"], ["none", "None"]]
        .map(([v, t]) => `<option value="${v}" ${v === a.auth_method ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <label>Username <input name="username" value="${esc(a.config.username || "")}" autocomplete="off"></label>
      <label>Password <input name="password" type="password" autocomplete="new-password" placeholder="${a.has_password ? "Saved. Leave blank to keep it" : "Kept in Windows Credential Manager"}"></label>
      <label>Other allowed hosts (comma separated) <input name="allowed_hosts" value="${esc((a.config.allowed_hosts || []).join(", "))}" placeholder="only these hosts can be reached"></label></div>
    <div data-for="desktop rdp"><label>Process name <input name="process" value="${esc(a.config.process || "")}" placeholder="e.g. mstsc or FinanceApp"></label>
      <label>Window title contains <input name="title_pattern" value="${esc(a.config.title_pattern || "")}"></label></div>
    <label>Environment <input name="environment" value="${esc(a.environment || "")}" placeholder="e.g. UAT"></label>
    <div class="row"><button class="btn primary">Save</button></div></form>`, { wide: true });
  const form = el.querySelector("#af");
  const sync = () => form.querySelectorAll("[data-for]").forEach((d) => { d.hidden = !d.dataset.for.split(" ").includes(form.app_type.value); });
  form.app_type.addEventListener("change", sync); sync();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(form));
    const body = { name: f.name, app_type: f.app_type, environment: f.environment || null,
      base_url: f.app_type === "web" ? f.base_url || null : null, login_url: f.app_type === "web" ? f.login_url || null : null,
      auth_method: f.app_type === "web" ? f.auth_method : "none", username: f.username || null, password: f.password || null,
      allowed_hosts: (f.allowed_hosts || "").split(",").map((s) => s.trim()).filter(Boolean),
      process: f.process || null, title_pattern: f.title_pattern || null };
    try {
      if (app) await api(`/api/comparison/applications/${app.id}`, { method: "PUT", body });
      else await api(`/api/comparison/projects/${project.project.id}/applications`, { method: "POST", body });
      form.password.value = "";
      close(); await loadProject(); after();
    } catch (err) { toast(err.message, "error"); }
  });
}

function needProject(body) {
  if (project) return false;
  body.innerHTML = '<div class="card"><p class="muted">Create or choose a comparison project first.</p></div>';
  return true;
}

function bars(items, label, value) {
  const max = Math.max(1, ...items.map(value));
  return items.map((x) => `<div class="cmp-bar-row"><span>${esc(label(x))}</span><div class="cmp-bar-track"><div class="cmp-bar-fill" style="width:${(value(x) / max) * 100}%"></div></div><b>${esc(value(x))}</b></div>`).join("") || '<p class="muted">No data yet.</p>';
}

// ---- views ------------------------------------------------------------------------------------------------------

const VIEWS = {
  async overview(body) {
    if (needProject(body)) return;
    const d = await api(`/api/comparison/projects/${project.project.id}/dashboard`);
    body.innerHTML = `<div class="grid cols-3">
      <div class="card"><h3>Executions</h3><div class="kpi">${esc(d.executions)}</div>${bars(Object.entries(d.by_result), (x) => x[0], (x) => x[1])}</div>
      <div class="card"><h3>Discrepancies by category</h3>${bars(d.by_category, (x) => x.category.replaceAll("_", " "), (x) => x.n)}</div>
      <div class="card"><h3>Defects by status</h3>${bars(d.defects, (x) => x.status.replaceAll("_", " "), (x) => x.n)}</div></div>
      <div class="card"><h3>Recent results</h3>${table(["When", "Result"], d.trend.slice(-15).reverse().map((t) => `<tr><td>${esc(dateTimeOf(t.started_at))}</td><td>${pill(t.result)}</td></tr>`))}</div>`;
  },

  async projects(body, view, refresh) {
    const { items } = await api("/api/comparison/projects");
    body.innerHTML = `<div class="card">${table(["Project", "Owner", "Members", "Applications", "Last result", ""], items.map((p) =>
      `<tr><td>${esc(p.name)}</td><td>${esc(p.owner)}</td><td>${esc(p.members.join(", "))}</td><td>${esc(p.applications)}</td>
       <td>${p.last_execution ? pill(p.last_execution.result) : '<span class="muted">-</span>'}</td>
       <td><button class="link-btn" data-open="${esc(p.id)}">Open</button>${canEdit() ? ` · <button class="link-btn" data-arch="${esc(p.id)}">Archive</button>` : ""}</td></tr>`))}</div>`;
    body.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", async () => { setPid(b.dataset.open); await mountAgain(view, refresh); }));
    body.querySelectorAll("[data-arch]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Archive this project? Its data and audit trail are kept.")) return;
      await api(`/api/comparison/projects/${b.dataset.arch}`, { method: "PATCH", body: { archived: true } }); await mountAgain(view, refresh);
    }));
  },

  async apps(body, view, refresh) {
    if (needProject(body)) return;
    const apps = project.applications;
    body.innerHTML = `<div class="card">${table(["Name", "Type", "URL / window", "Sign-in", "Password", "Connection", ""], apps.map((a) =>
      `<tr><td>${esc(a.name)}</td><td>${esc(a.app_type)}</td><td>${esc(a.base_url || a.config.title_pattern || a.config.process || "")}</td><td>${esc(a.auth_method)}${a.config.username ? ` (${esc(a.config.username)})` : ""}</td>
       <td>${a.has_password ? '<span class="pill PASS">Saved</span>' : '<span class="muted">-</span>'}</td>
       <td>${a.connection_status ? `${pill(a.connection_status === "ok" ? "PASS" : "FAIL")} <span class="muted">${esc(a.connection_detail || "")}</span>` : '<span class="muted">not tested</span>'}</td>
       <td><button class="link-btn" data-test="${esc(a.id)}">Test connection</button>${canEdit() ? ` · <button class="link-btn" data-edit="${esc(a.id)}">Edit</button> · <button class="link-btn" data-del="${esc(a.id)}">Delete</button>` : ""}</td></tr>`))}
      ${canEdit() ? '<button class="btn primary" id="addApp">Add application</button>' : ""}</div>`;
    body.querySelector("#addApp")?.addEventListener("click", () => appForm(null, refresh));
    body.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => appForm(apps.find((a) => a.id === b.dataset.edit), refresh)));
    body.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Delete this application and its datasets?")) return;
      await api(`/api/comparison/applications/${b.dataset.del}`, { method: "DELETE" }); await loadProject(); refresh();
    }));
    body.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true; b.textContent = "Testing…";
      try { const r = await api(`/api/comparison/applications/${b.dataset.test}/test`, { method: "POST" }); toast(`${r.status}: ${r.detail}`, r.status === "ok" ? "success" : "error"); }
      catch (err) { toast(err.message, "error"); }
      await loadProject(); refresh();
    }));
  },

  async discovery(body) {
    if (needProject(body)) return;
    body.innerHTML = `<div class="card settings"><label>Application <select id="app">${opts(project.applications.filter((a) => a.app_type !== "file" && a.app_type !== "screenshot"))}</select></label>
      <label>Screen name <input id="name" placeholder="e.g. Invoice list"></label>
      <label>Navigation steps (JSON, optional) <textarea id="steps" rows="4" placeholder='[{"action":"click","text":"Invoices"}]'></textarea></label>
      <div class="row"><button class="btn primary" id="go">Start discovery</button></div></div><div id="out"></div>`;
    const out = body.querySelector("#out");
    const list = async () => {
      const app = body.querySelector("#app").value; if (!app) return;
      const { items } = await api(`/api/comparison/applications/${app}/screens`);
      out.innerHTML = `<div class="card"><h3>Discovered screens</h3>${table(["Screen", "When", "Tables", "Filters", "Charts", "KPIs"], items.map((s) =>
        `<tr><td>${esc(s.name)}</td><td>${esc(dateTimeOf(s.created_at))}</td><td>${esc((s.discovery.tables || []).map((t) => (t.headers || []).join(" | ")).join(" ; "))}</td>
         <td>${esc((s.discovery.fields || []).map((f) => f.label).join(", "))}</td><td>${esc((s.discovery.charts || []).length ?? s.discovery.charts)}</td><td>${esc((s.discovery.kpis || []).map((k) => `${k.label}: ${k.value}`).join(", "))}</td></tr>`))}</div>`;
    };
    body.querySelector("#app").addEventListener("change", list);
    body.querySelector("#go").addEventListener("click", async (e) => {
      e.target.disabled = true;
      try {
        const steps = body.querySelector("#steps").value.trim() ? JSON.parse(body.querySelector("#steps").value) : [];
        await api(`/api/comparison/applications/${body.querySelector("#app").value}/discover`, { method: "POST", body: { steps, name: body.querySelector("#name").value || null } });
        toast("Screen discovered", "success"); list();
      } catch (err) { toast(err.message, "error"); }
      e.target.disabled = false;
    });
    list();
  },

  async extract(body) {
    if (needProject(body)) return;
    body.innerHTML = `<div class="card settings"><label>Application <select id="app">${opts(project.applications.filter((a) => ["web", "desktop", "rdp"].includes(a.app_type)))}</select></label>
      <label>What to read <select id="kind"><option value="table">Table / grid (all pages)</option><option value="dashboard">Dashboard (KPI cards and charts)</option><option value="kpis">KPI cards</option><option value="charts">Charts</option></select></label>
      <label>Record key column <input id="key" placeholder="e.g. Invoice No"></label>
      <label>Table selector (optional) <input id="sel" placeholder="#invoices"></label>
      <label>Next-page selector (optional) <input id="next" placeholder="auto-detected"></label>
      <label><input type="checkbox" id="scroll"> Infinite scroll (no pages)</label>
      <label><input type="checkbox" id="shots" checked> Screenshot every page as evidence</label>
      <label>Navigation and filter steps (JSON) <textarea id="steps" rows="4" placeholder='[{"action":"fill","label":"Start Date","value":"01/10/2026","filter":"Start Date"},{"action":"click","text":"Search"}]'></textarea></label>
      <div class="row"><button class="btn primary" id="go">Extract</button><button class="btn" id="stop" hidden>Stop</button><span id="prog" class="muted"></span></div></div>
      <div class="card"><h3>Datasets</h3><div id="ds"></div></div>`;
    renderDatasets(body.querySelector("#ds"));
    body.querySelector("#go").addEventListener("click", async () => {
      try {
        const steps = body.querySelector("#steps").value.trim() ? JSON.parse(body.querySelector("#steps").value) : [];
        const r = await api(`/api/comparison/applications/${body.querySelector("#app").value}/extract`, { method: "POST", body: {
          kind: body.querySelector("#kind").value, key_field: body.querySelector("#key").value || null, steps,
          screenshots: body.querySelector("#shots").checked,
          target: { selector: body.querySelector("#sel").value || null, next_selector: body.querySelector("#next").value || null, scroll: body.querySelector("#scroll").checked } } });
        const stop = body.querySelector("#stop"); stop.hidden = false;
        stop.onclick = () => api(`/api/comparison/jobs/${r.job}/cancel`, { method: "POST" });
        clearInterval(timer);
        timer = setInterval(async () => {
          const j = await api(`/api/comparison/jobs/${r.job}`);
          body.querySelector("#prog").textContent = j.status === "running" ? j.progress : j.status === "failed" ? `Failed: ${j.error}` : "Done";
          if (j.status !== "running") { clearInterval(timer); stop.hidden = true; await loadProject(); renderDatasets(body.querySelector("#ds")); }
        }, 1000);
      } catch (err) { toast(err.message, "error"); }
    });
  },

  async screenshots(body) {
    if (needProject(body)) return;
    const ocr = await api("/api/comparison/ocr");
    body.innerHTML = `<div class="card settings">
      <p class="${ocr.windows || ocr.tesseract ? "muted" : "pill WARN"}">OCR engines: Windows OCR ${ocr.windows ? "available" : "not available"}, Tesseract ${ocr.tesseract ? "available" : "not installed"}. Values read with low confidence are compared as REVIEW, never as PASS or FAIL.</p>
      <label>Application <select id="app">${opts(project.applications)}</select></label>
      <label>Content <select id="kind"><option value="table">Table</option><option value="kpis">KPI cards</option></select></label>
      <label>Record key column <input id="key"></label>
      <label>Add to dataset (multi-screenshot table) <select id="ds"><option value="">New dataset</option>${opts(project.datasets.filter((d) => d.method === "ocr"), null, (d) => `${d.application} · ${d.id}`)}</select></label>
      <label>PNG or JPEG screenshots (max 15 MB each) <input type="file" id="file" accept="image/png,image/jpeg" multiple></label>
      <div class="row"><button class="btn primary" id="go">Upload and read</button></div></div><div id="out"></div>`;
    body.querySelector("#go").addEventListener("click", async () => {
      let ds = body.querySelector("#ds").value || null;
      const files = [...body.querySelector("#file").files];
      for (const [i, f] of files.entries()) {
        try {
          const q = new URLSearchParams({ name: f.name, kind: body.querySelector("#kind").value, sequence: String(i + 1) });
          if (body.querySelector("#key").value) q.set("key_field", body.querySelector("#key").value);
          if (ds) q.set("dataset_id", ds);
          const r = await api(`/api/comparison/applications/${body.querySelector("#app").value}/screenshot?${q}`, { method: "POST", rawBody: f, contentType: f.type });
          ds = r.dataset_id;
          body.querySelector("#out").insertAdjacentHTML("beforeend", `<p>${esc(f.name)}: ${r.error ? `<span class="pill WARN">${esc(r.error)}</span>` : `read ${esc(r.ocr.lines)} lines (${esc(r.ocr.engine)})`} · evidence ${esc(r.evidence.id)}</p>`);
        } catch (err) { toast(`${f.name}: ${err.message}`, "error"); }
      }
      await loadProject();
    });
  },

  async import(body) {
    if (needProject(body)) return;
    body.innerHTML = `<div class="card settings"><p class="muted">Use an export of the application (CSV, XLSX or JSON) as a dataset.</p>
      <label>Application <select id="app">${opts(project.applications)}</select></label>
      <label>Record key column <input id="key"></label><label>Sheet (XLSX) <input id="sheet"></label>
      <label>File <input type="file" id="file" accept=".csv,.xlsx,.xlsm,.json"></label>
      <div class="row"><button class="btn primary" id="go">Import</button></div></div><div class="card"><h3>Datasets</h3><div id="ds"></div></div>`;
    renderDatasets(body.querySelector("#ds"));
    body.querySelector("#go").addEventListener("click", async () => {
      const f = body.querySelector("#file").files[0]; if (!f) return;
      const q = new URLSearchParams({ name: f.name });
      if (body.querySelector("#key").value) q.set("key_field", body.querySelector("#key").value);
      if (body.querySelector("#sheet").value) q.set("sheet", body.querySelector("#sheet").value);
      try { await api(`/api/comparison/applications/${body.querySelector("#app").value}/import?${q}`, { method: "POST", rawBody: f, contentType: "application/octet-stream" }); toast("Imported", "success"); await loadProject(); renderDatasets(body.querySelector("#ds")); }
      catch (err) { toast(err.message, "error"); }
    });
  },

  async mapping(body) {
    if (needProject(body)) return;
    const tables = project.datasets.filter((d) => d.kind === "table");
    body.innerHTML = `<div class="card settings row"><label>Source (A) <select id="s">${opts(tables, null, (d) => `${d.application} · ${d.id} · ${d.stats.records ?? "?"} rows`)}</select></label>
      <label>Target (B) <select id="t">${opts(tables, tables[1]?.id, (d) => `${d.application} · ${d.id} · ${d.stats.records ?? "?"} rows`)}</select></label>
      <label><input type="checkbox" id="ai"> Ask the local AI about unsure fields</label><button class="btn primary" id="go">Suggest mapping</button></div><div id="out"></div>`;
    body.querySelector("#go").addEventListener("click", async () => {
      const s = body.querySelector("#s").value, t = body.querySelector("#t").value;
      try {
        const r = await api("/api/comparison/mapping/suggest", { method: "POST", body: { source_dataset: s, target_dataset: t, use_ai: body.querySelector("#ai").checked } });
        const tf = Object.keys(r.target_profile);
        const out = body.querySelector("#out");
        out.innerHTML = `<div class="card"><p class="muted">Suggestions at or above 80% are auto-mapped; everything else, and every AI suggestion, needs your approval before it is used.</p>
          ${table(["Source field", "Target field", "Type", "Confidence", "Status", "Evidence"], r.suggestions.map((m, i) => `<tr>
            <td>${esc(m.source_field)}</td><td><select data-i="${i}" class="tf"><option value="">(not mapped)</option>${tf.map((f) => `<option ${f === m.target_field ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></td>
            <td>${esc(m.type)}</td><td>${Math.round((m.confidence || 0) * 100)}%</td>
            <td><select data-i="${i}" class="st">${["auto_mapped", "needs_review", "approved", "manually_mapped", "rejected", "unmapped"].map((x) => `<option ${x === m.status ? "selected" : ""}>${x}</option>`).join("")}</select></td>
            <td class="muted">${esc((m.evidence || []).join("; "))}</td></tr>`))}
          <div class="row"><label>Key: source <select id="ks">${Object.keys(r.source_profile).map((f) => `<option ${f === r.key_fields.source ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>
          <label>target <select id="kt">${tf.map((f) => `<option ${f === r.key_fields.target ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>
          <label>Note <input id="note"></label><button class="btn primary" id="save">Save mapping (new version)</button></div></div>`;
        out.querySelectorAll(".tf").forEach((sel) => sel.addEventListener("change", () => { const m = r.suggestions[sel.dataset.i]; m.target_field = sel.value || null; m.status = sel.value ? "manually_mapped" : "unmapped"; out.querySelector(`.st[data-i="${sel.dataset.i}"]`).value = m.status; }));
        out.querySelectorAll(".st").forEach((sel) => sel.addEventListener("change", () => { r.suggestions[sel.dataset.i].status = sel.value; }));
        out.querySelector("#save").addEventListener("click", async () => {
          const sd = tables.find((d) => d.id === s), td = tables.find((d) => d.id === t);
          try {
            const res = await api(`/api/comparison/projects/${project.project.id}/mapping`, { method: "PUT", body: {
              source_app: sd.application_id, target_app: td.application_id, mappings: r.suggestions.filter((m) => m.target_field),
              key_fields: { source: out.querySelector("#ks").value, target: out.querySelector("#kt").value }, filter_map: r.filter_map, note: out.querySelector("#note").value || null } });
            toast(`Mapping version ${res.mapping.version} saved`, "success");
          } catch (err) { toast(err.message, "error"); }
        });
      } catch (err) { toast(err.message, "error"); }
    });
  },

  async filters(body) {
    if (needProject(body)) return;
    body.innerHTML = `<div class="card"><p class="muted">The filters each dataset was read with. Both applications must use equivalent business conditions; otherwise the result is a configuration mismatch, never a data defect.</p>
      ${table(["Dataset", "Application", "Filters", ""], project.datasets.map((d) => `<tr><td>${esc(d.id)}</td><td>${esc(d.application)}</td><td><code>${fmt(d.filters)}</code></td>
        <td>${canEdit() ? `<button class="link-btn" data-f="${esc(d.id)}">Edit</button>` : ""}</td></tr>`))}</div>`;
    body.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", async () => {
      const d = project.datasets.find((x) => x.id === b.dataset.f);
      const v = prompt("Filters as JSON, e.g. {\"Start Date\": \"01/10/2026\"}", JSON.stringify(d.filters));
      if (v == null) return;
      try { await api(`/api/comparison/datasets/${d.id}/filters`, { method: "PUT", body: { filters: JSON.parse(v) } }); await loadProject(); VIEWS.filters(body); }
      catch (err) { toast(err.message, "error"); }
    }));
  },

  async rules(body) {
    if (needProject(body)) return;
    const s = project.project.settings;
    body.innerHTML = `<form class="card settings" id="f"><p class="muted">Normalization rules apply to every comparison of this project. Two values are equal only after these approved rules, never because they look alike.</p>
      <label>Dates are day-first (12/11/2026 = 12 November) <input type="checkbox" name="day_first" ${(s.rules?.day_first ?? true) ? "checked" : ""}></label>
      <label>Source time zone <input name="timezone" value="${esc(s.rules?.timezone || "")}" placeholder="+04:00"></label>
      <label>Decimals before comparing numbers <input name="decimals" type="number" min="0" max="8" value="${esc(s.rules?.decimals ?? "")}"></label>
      <label>Numeric tolerance (absolute) <input name="tolerance" value="${esc(s.rules?.tolerance ?? "")}"></label>
      <label>Numeric tolerance (%) <input name="tolerance_pct" value="${esc(s.rules?.tolerance_pct ?? "")}"></label>
      <label>Date tolerance (days) <input name="date_tolerance_days" value="${esc(s.rules?.date_tolerance_days ?? "")}"></label>
      <label>Approved reference mappings (JSON: {"Status": {"1": "Active"}}) <textarea name="reference" rows="6">${esc(JSON.stringify(s.reference || {}, null, 1))}</textarea></label>
      ${canEdit() ? '<div class="row"><button class="btn primary">Save rules</button></div>' : ""}</form>`;
    body.querySelector("#f").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target;
      const num = (v) => v === "" ? null : Number(v);
      try {
        await api(`/api/comparison/projects/${project.project.id}`, { method: "PATCH", body: { settings: {
          rules: { day_first: f.day_first.checked, timezone: f.timezone.value || null, decimals: num(f.decimals.value), tolerance: f.tolerance.value || null,
            tolerance_pct: f.tolerance_pct.value || null, date_tolerance_days: f.date_tolerance_days.value || null },
          reference: JSON.parse(f.reference.value || "{}") } } });
        toast("Rules saved", "success"); await loadProject();
      } catch (err) { toast(err.message, "error"); }
    });
  },

  async aggregates(body) {
    if (needProject(body)) return;
    const defs = project.definitions;
    body.innerHTML = `<div class="card"><p class="muted">Each displayed KPI or chart is recalculated from the source records with an explicit definition. A definition is used for PASS/FAIL only after a person approves it.</p>
      ${table(["Name", "Function", "Definition", "Approved", ""], defs.map((d) => `<tr><td>${esc(d.name)}</td><td>${esc(d.definition.function)}</td><td><code>${fmt(d.definition)}</code></td>
        <td>${d.approved ? `<span class="pill PASS">by ${esc(d.approved_by)}</span>` : '<span class="pill WARN">needs approval</span>'}</td>
        <td>${canEdit() ? `<button class="link-btn" data-ap="${esc(d.id)}" data-v="${d.approved ? 0 : 1}">${d.approved ? "Withdraw" : "Approve"}</button>` : ""}</td></tr>`))}
      ${canEdit() ? `<label>New definition (JSON) <textarea id="nd" rows="5" placeholder='{"name":"Pending invoices","function":"count","where":[{"field":"Status","op":"eq","value":"Pending"}],"decimals":0,"target":{"kpi":"Pending Invoices"}}'></textarea></label>
      <div class="row"><button class="btn primary" id="add">Add definition</button></div>` : ""}</div>`;
    body.querySelector("#add")?.addEventListener("click", async () => {
      try { await api(`/api/comparison/projects/${project.project.id}/definitions`, { method: "POST", body: { definition: JSON.parse(body.querySelector("#nd").value) } }); await loadProject(); VIEWS.aggregates(body); }
      catch (err) { toast(err.message, "error"); }
    });
    body.querySelectorAll("[data-ap]").forEach((b) => b.addEventListener("click", async () => {
      await api(`/api/comparison/projects/${project.project.id}/definitions/${b.dataset.ap}/approve`, { method: "POST", body: { approved: b.dataset.v === "1" } });
      await loadProject(); VIEWS.aggregates(body);
    }));
  },

  async run(body) {
    if (needProject(body)) return;
    const ds = project.datasets;
    body.innerHTML = `<div class="card settings"><label>Source (A) <select id="s">${opts(ds, null, (d) => `${d.application} · ${d.kind} · ${d.id}`)}</select></label>
      <label>Target (B) <select id="t">${opts(ds, ds[1]?.id, (d) => `${d.application} · ${d.kind} · ${d.id}`)}</select></label>
      <label>Mode <select id="m"><option value="record">Record by record</option><option value="aggregate">Source-to-aggregate (KPIs, charts)</option><option value="hybrid">Both</option></select></label>
      <label><input type="checkbox" id="ai"> Local AI summary when no defect is found</label>
      <div class="row"><button class="btn primary" id="go">Run comparison</button></div></div><div id="out"></div>`;
    body.querySelector("#go").addEventListener("click", async (e) => {
      e.target.disabled = true;
      try {
        const { execution } = await api(`/api/comparison/projects/${project.project.id}/run`, { method: "POST", body: {
          source_dataset: body.querySelector("#s").value, target_dataset: body.querySelector("#t").value, mode: body.querySelector("#m").value, use_ai: body.querySelector("#ai").checked } });
        await loadProject();
        renderExecution(body.querySelector("#out"), execution);
      } catch (err) { toast(err.message, "error"); }
      e.target.disabled = false;
    });
  },

  async results(body) {
    if (needProject(body)) return;
    body.innerHTML = `<div class="card">${table(["Execution", "Mode", "Result", "By", "Started", ""], project.executions.map((x) =>
      `<tr><td>${esc(x.id)}</td><td>${esc(x.mode)}</td><td>${pill(x.result)}</td><td>${esc(x.created_by)}</td><td>${esc(dateTimeOf(x.started_at))}</td><td><button class="link-btn" data-x="${esc(x.id)}">Open</button></td></tr>`))}</div><div id="out"></div>`;
    body.querySelectorAll("[data-x]").forEach((b) => b.addEventListener("click", async () => {
      const { execution } = await api(`/api/comparison/executions/${b.dataset.x}`); renderExecution(body.querySelector("#out"), execution);
    }));
  },

  async aggregateReport(body) {
    if (needProject(body)) return;
    const runs = project.executions.filter((x) => x.mode !== "record");
    if (!runs.length) { body.innerHTML = '<div class="card"><p class="muted">Run a source-to-aggregate comparison first.</p></div>'; return; }
    const { execution } = await api(`/api/comparison/executions/${runs[0].id}`);
    const rows = execution.detail.aggregates?.rows || [];
    body.innerHTML = `<div class="card"><h3>Source-to-aggregate validation ${pill(execution.result)}</h3><p class="muted">${esc(execution.id)} · ${esc(dateTimeOf(execution.started_at))}</p>
      ${table(["Metric", "Source calculated", "Target displayed", "Difference", "Result", "Reason"], rows.map((r) => `<tr><td>${esc(r.metric)}</td><td>${fmt(r.expected)}</td><td>${fmt(r.displayed)}</td><td>${fmt(r.difference)}</td><td>${pill(r.result)}</td><td>${esc(r.reason || "")}</td></tr>`))}</div>`;
  },

  async defects(body) {
    if (needProject(body)) return;
    const { items } = await api(`/api/comparison/projects/${project.project.id}/defects`);
    body.innerHTML = `<div class="card">${table(["Id", "Title", "Severity", "Status", "TFS", ""], items.map((d) =>
      `<tr><td>${esc(d.id)}</td><td>${esc(d.title)}</td><td>${esc(d.severity)}</td><td>${esc(d.status.replaceAll("_", " "))}</td><td>${d.tfs_id ? `#${esc(d.tfs_id)}` : d.tfs_sync?.queued ? '<span class="pill WARN">queued</span>' : ""}</td>
       <td><button class="link-btn" data-d="${esc(d.id)}">Review</button></td></tr>`))}</div><div id="out"></div>`;
    body.querySelectorAll("[data-d]").forEach((b) => b.addEventListener("click", () => openDefect(b.dataset.d, body)));
  },

  async investigation(body) {
    if (needProject(body)) return;
    const { items } = await api(`/api/comparison/projects/${project.project.id}/defects`);
    const done = items.filter((d) => d.investigation && d.investigation.summary);
    body.innerHTML = `<div class="card"><p class="muted">The local AI explains discrepancies that the rules already measured. It cannot change a result, a value or the reproduction steps.</p>
      ${table(["Defect", "Likely cause", "Category", "Confidence", "Origin"], done.map((d) => `<tr><td>${esc(d.title)}</td><td>${esc(d.investigation.likely_cause)}</td><td>${esc(d.investigation.cause_category)}</td><td>${Math.round((d.investigation.confidence || 0) * 100)}%</td><td class="muted">${esc(d.investigation.origin)}</td></tr>`))}</div>`;
  },

  async reports(body) {
    if (needProject(body)) return;
    body.innerHTML = `<div class="card">${table(["Execution", "Result", "Started", "Reports"], project.executions.map((x) =>
      `<tr><td>${esc(x.id)}</td><td>${pill(x.result)}</td><td>${esc(dateTimeOf(x.started_at))}</td><td>
        <a href="/api/comparison/executions/${esc(x.id)}/report.html" target="_blank" rel="noopener">HTML</a> ·
        <a href="/api/comparison/executions/${esc(x.id)}/report.pdf" target="_blank" rel="noopener">PDF</a> ·
        <a href="/api/comparison/executions/${esc(x.id)}/report.xlsx">Excel</a> ·
        <a href="/api/comparison/executions/${esc(x.id)}/evidence.zip">Evidence package</a></td></tr>`))}</div>`;
  },

  async evidence(body) {
    if (needProject(body)) return;
    const [{ items }, audit] = await Promise.all([api(`/api/comparison/projects/${project.project.id}/evidence`), api(`/api/comparison/projects/${project.project.id}/audit?limit=300`)]);
    body.innerHTML = `<div class="card"><h3>Evidence</h3>${table(["Id", "Source", "Size", "SHA-256", "Captured", ""], items.map((e) =>
      `<tr><td>${esc(e.id)}</td><td>${esc(e.source)}${e.original_name ? ` (${esc(e.original_name)})` : ""}</td><td>${esc(e.width)} × ${esc(e.height)}</td><td><code>${esc(e.sha256.slice(0, 16))}…</code></td>
       <td>${esc(dateTimeOf(e.captured_at))}</td><td><a href="/api/comparison/evidence/${esc(e.id)}" target="_blank" rel="noopener">Open</a></td></tr>`))}</div>
      <div class="card"><h3>Audit trail ${audit.chain.ok ? '<span class="pill PASS">chain intact</span>' : '<span class="pill FAIL">chain broken</span>'}</h3>
      ${table(["When", "Who", "Action", "Target", "Details"], audit.items.map((a) => `<tr><td>${esc(dateTimeOf(a.at))}</td><td>${esc(a.actor)}</td><td>${esc(a.action)}</td><td>${esc(a.target || "")}</td><td class="muted">${fmt(a.details)}</td></tr>`))}</div>`;
  },
};

async function renderDatasets(host) {
  await loadProject();
  host.innerHTML = table(["Dataset", "Application", "Kind", "Method", "Status", "Records", "Pages", "Complete", "When"], (project?.datasets || []).map((d) =>
    `<tr><td>${esc(d.id)}</td><td>${esc(d.application)}</td><td>${esc(d.kind)}</td><td>${esc(d.method)}</td><td>${esc(d.status)}</td><td>${esc(d.stats.records ?? "")}${d.stats.expected_total != null ? ` / shown ${esc(d.stats.expected_total)}` : ""}</td>
     <td>${esc(d.stats.pages_visited ?? "")}</td><td>${d.stats.complete === false ? `<span class="pill WARN">no</span> <span class="muted">${esc(d.stats.reason || "")}</span>` : "yes"}</td><td>${esc(dateTimeOf(d.created_at))}</td></tr>`));
}

function renderExecution(host, x) {
  const d = x.detail || {};
  const s = d.summary || {};
  host.innerHTML = `<div class="card"><h3>${esc(x.id)} ${pill(x.result)}</h3>
    <div class="grid cols-4">${Object.entries(s).map(([k, v]) => `<div><div class="muted">${esc(k.replaceAll("_", " "))}</div><b>${esc(v)}</b></div>`).join("")}</div>
    ${d.filters && d.filters.equivalent === false ? `<p class="pill WARN">Filters differ: ${esc(d.filters.issues.map((i) => i.problem).join("; "))}</p>` : ""}
    ${d.no_defect ? `<h3>No-defect analysis</h3><p>${esc(d.no_defect.summary)}</p>${d.no_defect.ai_summary ? `<p>${esc(d.no_defect.ai_summary)}</p>` : ""}${(d.no_defect.residual_risks || []).map((r) => `<p class="muted">Not covered: ${esc(r)}</p>`).join("")}` : ""}
    ${d.aggregates ? `<h3>Source-to-aggregate</h3>${table(["Metric", "Source calculated", "Target displayed", "Difference", "Result", "Reason"], d.aggregates.rows.map((r) => `<tr><td>${esc(r.metric)}</td><td>${fmt(r.expected)}</td><td>${fmt(r.displayed)}</td><td>${fmt(r.difference)}</td><td>${pill(r.result)}</td><td>${esc(r.reason || "")}</td></tr>`))}` : ""}
    <h3>Discrepancies</h3>${table(["Id", "Category", "Record", "Field", "A", "B", "Difference", "Note"], (d.discrepancies || []).slice(0, 500).map((r) =>
      `<tr><td>${esc(r.id)}</td><td>${esc(r.category.replaceAll("_", " "))}</td><td>${esc(r.record_key ?? r.metric ?? "")}</td><td>${esc(r.source_field || "")}</td><td>${fmt(r.source_value ?? r.expected)}</td><td>${fmt(r.target_value ?? r.displayed)}</td><td>${fmt(r.difference)}</td><td class="muted">${esc(r.note || r.how || "")}</td></tr>`))}
    <p><a href="/api/comparison/executions/${esc(x.id)}/report.html" target="_blank" rel="noopener">Open report</a></p></div>`;
}

async function openDefect(id, body) {
  const { defect: d } = await api(`/api/comparison/defects/${id}`);
  const inv = d.investigation || {};
  const { el, close } = openModal(d.title, `<p>${pill(d.status === "confirmed" ? "FAIL" : "REVIEW")} ${esc(d.status.replaceAll("_", " "))} · severity ${esc(d.severity)} · priority ${esc(d.priority)}</p>
    <h4>Facts (measured)</h4><pre class="code">${esc(JSON.stringify(Object.fromEntries(Object.entries(d.data).filter(([k]) => !["repro_steps", "names"].includes(k))), null, 1))}</pre>
    <h4>Steps to reproduce (from the recorded navigation)</h4><ol>${d.repro_steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
    ${inv.summary ? `<h4>Investigation <span class="muted">(${esc(inv.origin)})</span></h4><p>${esc(inv.likely_cause)}</p><ul>${(inv.checks || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    ${(d.comments || []).map((c) => `<p class="muted">${esc(c.by)} · ${esc(dateTimeOf(c.at))}: ${esc(c.text)}</p>`).join("")}
    ${canEdit() ? `<label>Comment <textarea id="c"></textarea></label><div class="row">
      <select id="st">${["new", "under_review", "confirmed", "not_a_defect", "duplicate", "closed"].map((s) => `<option ${s === d.status ? "selected" : ""}>${s}</option>`).join("")}</select>
      <button class="btn" id="save">Save</button><button class="btn" id="inv">Investigate</button>${d.tfs_id ? `<span>TFS #${esc(d.tfs_id)}</span>` : '<button class="btn primary" id="tfs">Create in TFS</button>'}</div>` : ""}`, { wide: true });
  el.querySelector("#save")?.addEventListener("click", async () => {
    try { await api(`/api/comparison/defects/${id}`, { method: "PATCH", body: { status: el.querySelector("#st").value, comment: el.querySelector("#c").value || null } }); close(); VIEWS.defects(body); }
    catch (err) { toast(err.message, "error"); }
  });
  el.querySelector("#inv")?.addEventListener("click", async (e) => { e.target.disabled = true; try { await api(`/api/comparison/defects/${id}/investigate`, { method: "POST" }); close(); openDefect(id, body); } catch (err) { toast(err.message, "error"); e.target.disabled = false; } });
  el.querySelector("#tfs")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    const send = async (force) => api(`/api/comparison/defects/${id}/tfs`, { method: "POST", body: { force } });
    try { const r = await send(false); toast(r.defect.tfs_id ? `Created TFS #${r.defect.tfs_id}` : "TFS is not reachable: queued", "success"); close(); VIEWS.defects(body); }
    catch (err) {
      if (err.code === "CMP_POSSIBLE_DUPLICATE" && confirm(`${err.message}\n\nCreate it anyway?`)) { try { await send(true); close(); VIEWS.defects(body); } catch (e2) { toast(e2.message, "error"); } }
      else toast(err.message, "error");
      e.target.disabled = false;
    }
  });
}

function page(viewName) {
  return {
    async mount(view) {
      const body = document.createElement("div");
      body.className = "cmp-body";
      view.appendChild(body);
      const refresh = async () => { body.innerHTML = '<p class="muted">Loading…</p>'; try { await VIEWS[viewName](body, view, refresh); } catch (err) { body.innerHTML = `<div class="card"><p class="pill FAIL">${esc(err.message)}</p></div>`; } };
      await header(view, refresh);
      await refresh();
    },
    unmount() { clearInterval(timer); },
  };
}

export const cmpPages = [
  ["cmp-dashboard", "Comparison Dashboard", page("overview")],
  ["cmp-projects", "Comparison Projects", page("projects")],
  ["cmp-applications", "Applications & Connections", page("apps")],
  ["cmp-discovery", "Screen Discovery", page("discovery")],
  ["cmp-extract", "Data Extraction", page("extract")],
  ["cmp-screenshots", "Screenshots & OCR", page("screenshots")],
  ["cmp-import", "File Import", page("import")],
  ["cmp-mapping", "AI Field Mapping", page("mapping")],
  ["cmp-filters", "Filters & Business Conditions", page("filters")],
  ["cmp-rules", "Normalization & Reference Data", page("rules")],
  ["cmp-aggregates", "KPI & Chart Definitions", page("aggregates")],
  ["cmp-run", "Run Comparison", page("run")],
  ["cmp-results", "Comparison Results", page("results")],
  ["cmp-aggregate-report", "Source-to-Aggregate Validation", page("aggregateReport")],
  ["cmp-defects", "Data Defect Review Center", page("defects")],
  ["cmp-investigation", "AI Defect Investigation", page("investigation")],
  ["cmp-reports", "Comparison Reports", page("reports")],
  ["cmp-evidence", "Evidence & Audit Trail", page("evidence")],
];
