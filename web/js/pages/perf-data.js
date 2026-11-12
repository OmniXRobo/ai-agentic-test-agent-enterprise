// 23.3.25: Performance Test Data. Data sets (typed columns, rows of text) that scripts read with a CSV Data Set
// Config, and the "Test data" tab of the script editor that adds a data set to a script and binds request values
// (username, password, ids) to its columns. Secret columns are never shown: their values stay in the Windows
// Credential Manager and are written only for the duration of a run.

import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";

const params = () => new URLSearchParams(location.hash.split("?")[1] || "");
const TYPES = { text: "Text", integer: "Whole number", number: "Number", email: "Email", date: "Date", boolean: "true/false", secret: "Secret" };
const RULE_FIELDS = {
  sequence: [["prefix", "Prefix", "user"], ["width", "Digits", "4"]],
  email: [["prefix", "Prefix", "user"], ["domain", "Domain", "example.test"], ["width", "Digits", "4"]],
  range: [["min", "Min", "1"], ["max", "Max", "100"]],
  decimal: [["min", "Min", "0"], ["max", "Max", "100"]],
  list: [["values", "Values (comma separated)", "basic, pro"]],
  date: [["from", "From", "2026-01-01"], ["to", "To", "2026-12-31"]],
  uuid: [], fixed: [["value", "Value", ""]], empty: [],
};
const defaultRule = (c) => (c.type === "secret" ? "empty" : c.type === "email" ? "email" : c.type === "integer" ? "range"
  : c.type === "number" ? "decimal" : c.type === "date" ? "date" : c.type === "boolean" ? "list" : "sequence");

function readFile(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve("base64:" + String(r.result).split(",", 2)[1]);
    r.onerror = () => reject(new Error("The file could not be read."));
    r.readAsDataURL(file);  // bytes, so the server can tell UTF-8 from Windows-1252
  });
}

const problemsHtml = (problems) => (problems.length
  ? `<div class="problems"><b>Check the values:</b><ul>${problems.map((p) => `<li><span class="pill ${p.level}">${p.level}</span> ${esc(p.message)}</li>`).join("")}</ul></div>`
  : '<p><span class="pill PASS">Valid</span> <span class="muted">Every value matches its column type.</span></p>');

// ---- list ---------------------------------------------------------------------------------------------------------

async function listView(view) {
  const { items } = await api("/api/performance/datasets");
  view.innerHTML = `
    <p class="muted">Test data for performance scripts: user names, passwords, ids or search terms that each virtual user reads, one row at a time. Values are kept exactly as entered (leading zeros, accents, other scripts). Secret columns are kept in the Windows Credential Manager and never shown again.</p>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">
      <form class="card" id="newDs"><h3>New data set</h3>
        <label>Name <input name="name" required placeholder="Shop users"></label>
        <label>Columns <input name="cols" required placeholder="username, password, account_id"></label>
        <p class="muted">Separate columns with commas. Columns named like password or token become secret. Change types on the next page.</p>
        <button class="btn primary" type="submit">Create</button></form>
      <form class="card" id="impDs"><h3>Import a CSV or Excel file</h3>
        <p class="muted">The first line (or the first row of the first sheet) names the columns. CSV: UTF-8 or Windows-1252; comma, semicolon or tab separated. Excel: .xlsx, cell text kept as shown.</p>
        <input type="file" name="file" accept=".csv,.txt,.xlsx" required>
        <label>Name <input name="name" placeholder="From the file name"></label>
        <button class="btn primary" type="submit">Import</button></form>
    </div>
    <div class="card" style="margin-top:12px"><h3>Data sets</h3>
      ${items.length ? `<table class="data"><thead><tr><th>Data set</th><th>Columns</th><th>Rows</th><th>Used by</th><th>Updated</th></tr></thead><tbody>
        ${items.map((d) => `<tr class="clickable" data-ds="${esc(d.id)}"><td>${esc(d.name)}<div class="muted">${esc(d.id)} · v${d.version}${d.source ? ` · ${esc(d.source)}` : ""}</div></td>
          <td>${d.columns.map((c) => `<code>${esc(c.name)}</code>${c.type === "secret" ? " 🔒" : ""}`).join(" ")}</td><td>${d.rows}</td>
          <td>${d.used_by.length ? d.used_by.map((p) => esc(p.name)).join(", ") : '<span class="muted">No script</span>'}</td><td>${esc(dateTimeOf(d.updated_at))}</td></tr>`).join("")}
        </tbody></table>` : '<p class="muted">No data sets yet.</p>'}</div>`;
  view.querySelectorAll("tr[data-ds]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/perf-data?ds=${encodeURIComponent(tr.dataset.ds)}`; }));
  view.querySelector("#newDs").addEventListener("submit", async (e) => {
    e.preventDefault();
    const names = e.target.cols.value.split(",").map((s) => s.trim()).filter(Boolean);
    const columns = names.map((n) => ({ name: n, type: /pass(word)?|pwd|secret|token|api[_-]?key|pin$/i.test(n) ? "secret" : "text" }));
    try {
      const { dataset } = await api("/api/performance/datasets", { method: "POST", body: { name: e.target.name.value.trim(), columns } });
      location.hash = `#/perf-data?ds=${encodeURIComponent(dataset.id)}`;
    } catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#impDs").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = e.target.file.files[0];
    if (!file) return;
    if (file.size > 30 * 1024 * 1024) { toast("The file is larger than 30 MB.", "error"); return; }
    try {
      const { dataset } = await api("/api/performance/datasets/import", { method: "POST", body: { csv: await readFile(file), filename: file.name, name: e.target.name.value.trim() } });
      toast(`Imported ${dataset.rows_total} rows${dataset.import.notes.length ? ` · ${dataset.import.notes.join(" ")}` : ""}`, "success", 9000);
      location.hash = `#/perf-data?ds=${encodeURIComponent(dataset.id)}`;
    } catch (err) { toast(err.message, "error"); }
  });
}

// ---- one data set -------------------------------------------------------------------------------------------------

async function detailView(view, id) {
  let ds = (await api(`/api/performance/datasets/${encodeURIComponent(id)}`)).dataset;
  let cols, rows, dirty = false;
  const reset = () => {
    cols = ds.columns.map((c) => ({ ...c, from: c.name }));
    rows = ds.rows.map((r) => ({ id: r.id, values: [...r.values] }));
    dirty = false;
  };
  reset();

  const render = () => {
    const secretSet = (rid, name) => (ds.secret_set[String(rid)] || []).includes(name);
    view.innerHTML = `
      <p><a href="#/perf-data">← Performance Test Data</a></p>
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:8px"><h2 style="margin:0">${esc(ds.name)}</h2>
        <span class="muted">${esc(ds.id)} · version ${ds.version} · ${ds.rows_total} row(s)${ds.source ? ` · ${esc(ds.source)}` : ""}</span>
        <span class="pill WARN" id="dirty" ${dirty ? "" : "hidden"}>Unsaved changes</span><span class="spacer"></span>
        <a class="btn" href="/api/performance/datasets/${esc(ds.id)}/csv">Export CSV</a>
        <a class="btn" href="/api/performance/datasets/${esc(ds.id)}/xlsx">Export Excel</a>
        <button class="btn" type="button" id="del">Delete</button>
        <button class="btn primary" type="button" id="save" ${ds.editable ? "" : "disabled"}>Save</button></div>
        <p class="muted">${ds.used_by.length ? `Used by ${ds.used_by.map((p) => `<a href="#/perf-plans?script=${esc(p.id)}">${esc(p.name)}</a>`).join(", ")}.` : "No script uses this data set yet: add it on the Test data tab of a script."}
          Exports leave secret columns empty.</p>
        <label>Description <input id="desc" value="${esc(ds.description || "")}" placeholder="What the rows are for"></label>
        <div id="problems">${problemsHtml(ds.problems)}</div></div>
      <div class="card" style="margin-top:12px"><h3>Rows</h3>
        ${ds.editable ? "" : `<p class="problems">This data set has ${ds.rows_total} rows; the first ${ds.rows.length} are shown. Change it by importing a CSV file below.</p>`}
        <div class="table-scroll"><table class="data dsgrid"><thead><tr><th>#</th>${cols.map((c, i) => `<th><input data-col="${i}" value="${esc(c.name)}" aria-label="Column name">
          <select data-type="${i}">${Object.entries(TYPES).map(([k, v]) => `<option value="${k}" ${k === c.type ? "selected" : ""}>${v}</option>`).join("")}</select>
          <button class="link-btn" type="button" data-rmcol="${i}" title="Remove column">✕</button></th>`).join("")}<th><button class="link-btn" type="button" id="addCol">+ Column</button></th></tr></thead>
        <tbody>${rows.map((r, ri) => `<tr><td class="muted">${ri + 1}</td>${cols.map((c, ci) => c.type === "secret"
          ? `<td><input type="password" autocomplete="new-password" data-cell="${ri}:${ci}" value="${r.values[ci] ? esc(r.values[ci]) : ""}" placeholder="${secretSet(r.id, c.from) && r.values[ci] === null ? "saved" : "not set"}"></td>`
          : `<td><input data-cell="${ri}:${ci}" value="${esc(r.values[ci] ?? "")}"></td>`).join("")}<td><button class="link-btn" type="button" data-rmrow="${ri}" title="Remove row">✕</button></td></tr>`).join("")}</tbody></table></div>
        ${ds.editable ? '<button class="btn" type="button" id="addRow" style="margin-top:8px">+ Row</button>' : ""}</div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr));margin-top:12px">
        <form class="card" id="gen"><h3>Generate rows</h3>
          <table class="data"><tbody>${ds.columns.map((c) => `<tr data-gen="${esc(c.name)}"><td><code>${esc(c.name)}</code></td>
            <td><select data-rule>${Object.keys(RULE_FIELDS).map((k) => `<option value="${k}" ${k === defaultRule(c) ? "selected" : ""}>${k}</option>`).join("")}</select></td><td data-opts></td></tr>`).join("")}</tbody></table>
          <div class="row"><label>Rows <input type="number" name="count" value="10" min="1" max="10000" style="width:90px"></label>
            <label><span><input type="checkbox" name="replace"> Replace the rows</span></label>
            <button class="btn primary" type="submit">Generate</button></div>
          <p class="muted">Save or discard your table edits first. Secret columns: use “fixed” or leave them empty and type the values.</p></form>
        <div class="card"><h3>Ask the AI for rows</h3>
          <form id="ai"><label>What the rows describe <input name="description" placeholder="Customers of a web shop in Germany"></label>
            <div class="row"><label>Rows <input type="number" name="count" value="10" min="1" max="50" style="width:80px"></label><button class="btn" type="submit">Generate with AI</button></div></form>
          <p class="muted">Uses the AI model set up under AI settings (on this PC). Invented values only; secret columns stay empty; rows that do not match the column types are dropped.</p>
          <form id="imp" style="margin-top:12px"><h4>Import a CSV file into this data set</h4>
            <input type="file" name="file" accept=".csv,.txt,.xlsx" required>
            <div class="row"><select name="mode"><option value="append">Add the rows</option><option value="replace">Replace columns and rows</option></select><button class="btn" type="submit">Import</button></div></form></div></div>`;
    const mark = () => { dirty = true; view.querySelector("#dirty").hidden = false; };
    view.querySelector("#desc").addEventListener("input", mark);
    view.querySelectorAll("[data-col]").forEach((inp) => inp.addEventListener("input", () => { cols[+inp.dataset.col].name = inp.value.trim(); mark(); }));
    view.querySelectorAll("[data-type]").forEach((sel) => sel.addEventListener("change", () => { cols[+sel.dataset.type].type = sel.value; mark(); render(); }));
    view.querySelectorAll("[data-cell]").forEach((inp) => inp.addEventListener("input", () => {
      const [ri, ci] = inp.dataset.cell.split(":").map(Number);
      rows[ri].values[ci] = inp.value; mark();
    }));
    view.querySelectorAll("[data-rmrow]").forEach((b) => b.addEventListener("click", () => { rows.splice(+b.dataset.rmrow, 1); mark(); render(); }));
    view.querySelectorAll("[data-rmcol]").forEach((b) => b.addEventListener("click", () => {
      const i = +b.dataset.rmcol;
      if (cols.length === 1) { toast("A data set needs at least one column.", "error"); return; }
      cols.splice(i, 1); rows.forEach((r) => r.values.splice(i, 1)); mark(); render();
    }));
    view.querySelector("#addCol").addEventListener("click", () => {
      let n = cols.length + 1;
      while (cols.some((c) => c.name === `column${n}`)) n += 1;
      cols.push({ name: `column${n}`, type: "text", from: null }); rows.forEach((r) => r.values.push("")); mark(); render();
    });
    view.querySelector("#addRow")?.addEventListener("click", () => { rows.push({ id: null, values: cols.map((c) => (c.type === "secret" ? "" : "")) }); mark(); render(); });
    view.querySelector("#save").addEventListener("click", async () => {
      try {
        ds = (await api(`/api/performance/datasets/${encodeURIComponent(ds.id)}`, { method: "PUT", body: {
          name: ds.name, description: view.querySelector("#desc").value, base_version: ds.version,
          columns: cols.map((c) => ({ name: c.name, type: c.type, from: c.from })), rows } })).dataset;
        reset(); render(); toast("Data set saved", "success");
      } catch (err) { toast(err.message, "error"); }
    });
    view.querySelector("#del").addEventListener("click", async () => {
      if (!confirm(`Delete ${ds.name}? Its secret values are removed from the Credential Manager too.`)) return;
      try { await api(`/api/performance/datasets/${encodeURIComponent(ds.id)}`, { method: "DELETE" }); location.hash = "#/perf-data"; } catch (err) { toast(err.message, "error"); }
    });
    // generator options per rule
    view.querySelectorAll("tr[data-gen]").forEach((tr) => {
      const sel = tr.querySelector("[data-rule]");
      const opts = () => { tr.querySelector("[data-opts]").innerHTML = RULE_FIELDS[sel.value].map(([k, label, def]) => `<input data-opt="${k}" placeholder="${esc(label)}" value="${esc(k === "prefix" && sel.value === "sequence" ? tr.dataset.gen : def)}" title="${esc(label)}" style="width:${k === "values" ? 180 : 110}px">`).join(" "); };
      sel.addEventListener("change", opts); opts();
    });
    view.querySelector("#gen").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (dirty && !confirm("Generating discards your unsaved table edits. Continue?")) return;
      const rules = {};
      view.querySelectorAll("tr[data-gen]").forEach((tr) => {
        const r = { rule: tr.querySelector("[data-rule]").value };
        tr.querySelectorAll("[data-opt]").forEach((i) => { r[i.dataset.opt] = i.value; });
        rules[tr.dataset.gen] = r;
      });
      try {
        ds = (await api(`/api/performance/datasets/${encodeURIComponent(ds.id)}/generate`, { method: "POST", body: { rules, count: Number(e.target.count.value), replace: e.target.replace.checked } })).dataset;
        reset(); render(); toast("Rows generated", "success");
      } catch (err) { toast(err.message, "error"); }
    });
    view.querySelector("#ai").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (dirty && !confirm("This discards your unsaved table edits. Continue?")) return;
      const btn = e.target.querySelector("button"); btn.disabled = true; btn.textContent = "Asking the AI…";
      try {
        ds = (await api(`/api/performance/datasets/${encodeURIComponent(ds.id)}/generate-ai`, { method: "POST", body: { description: e.target.description.value, count: Number(e.target.count.value) } })).dataset;
        reset(); render(); toast(`${ds.ai.added} row(s) added${ds.ai.rejected ? `, ${ds.ai.rejected} dropped (wrong types)` : ""}`, "success");
      } catch (err) { toast(err.message, "error"); btn.disabled = false; btn.textContent = "Generate with AI"; }
    });
    view.querySelector("#imp").addEventListener("submit", async (e) => {
      e.preventDefault();
      const file = e.target.file.files[0];
      if (!file) return;
      try {
        ds = (await api(`/api/performance/datasets/${encodeURIComponent(ds.id)}/import`, { method: "POST", body: { csv: await readFile(file), filename: file.name, mode: e.target.mode.value } })).dataset;
        reset(); render(); toast(`Imported ${ds.import.rows} row(s)`, "success");
      } catch (err) { toast(err.message, "error"); }
    });
  };
  render();
}

export const perfData = {
  async mount(view) {
    const id = params().get("ds");
    if (id) await detailView(view, id); else await listView(view);
  },
  unmount() {},
};

// ---- script editor: Test data tab ---------------------------------------------------------------------------------

const RAW_TYPES = new Set(["integer", "number", "boolean"]);

export async function dataTab(pane, st, applyOps) {
  pane.innerHTML = '<div class="card"><p class="muted">Reading the script…</p></div>';
  let info, all;
  try {
    [info, { items: all }] = await Promise.all([
      api("/api/performance/script/fields", { method: "POST", body: { script: st.script } }),
      api("/api/performance/datasets")]);
  } catch (err) { pane.innerHTML = `<div class="card"><p class="problems">${esc(err.message)}</p></div>`; return; }
  const byId = Object.fromEntries(all.map((d) => [d.id, d]));
  const groups = [];
  const walk = (nodes) => nodes.forEach((n) => { if (n.category === "thread_group" || n.category === "plan") groups.push(n); walk(n.children || []); });
  walk(st.tree);
  const sets = info.data_sets;
  const columnType = {};
  sets.forEach((b) => (byId[b.dataset_id]?.columns || []).forEach((c) => { columnType[c.name] = c.type; }));
  const vars = [...new Set(sets.filter((b) => b.enabled).flatMap((b) => b.variables))];
  pane.innerHTML = `
    <div class="card"><h3>Data sets this script reads</h3>
      ${sets.length ? `<table class="data"><thead><tr><th>Element</th><th>Data set</th><th>For</th><th>Variables</th><th>Rows are</th><th>At the end of the rows</th></tr></thead><tbody>
        ${sets.map((b) => `<tr class="${b.enabled ? "" : "muted"}"><td>${esc(b.name)}${b.enabled ? "" : " (disabled)"}</td>
          <td>${b.dataset_id ? (byId[b.dataset_id] ? `<a href="#/perf-data?ds=${esc(b.dataset_id)}">${esc(byId[b.dataset_id].name)}</a> <span class="muted">${byId[b.dataset_id].rows} rows</span>` : `<span class="pill FAIL">${esc(b.dataset_id)} not found</span>`) : `<code>${esc(b.filename)}</code> <span class="muted">(a file of this PC)</span>`}</td>
          <td>${esc(b.scope)}</td><td>${b.variables.map((v) => `<code>\${${esc(v)}}</code>`).join(" ")}</td>
          <td>${b.share === "thread" ? "read by each user from the start" : b.share === "group" ? "shared in the thread group" : "shared by all users (one row each)"}</td><td>${b.recycle ? "start again" : b.stop_thread ? "the user stops" : "&lt;EOF&gt;"}</td></tr>`).join("")}</tbody></table>`
        : '<p class="muted">None yet. Add a data set so each virtual user logs in or searches with its own values.</p>'}
      <form id="addDs" class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin-top:8px">
        <label>Data set <select name="ds" required>${all.length ? all.map((d) => `<option value="${esc(d.id)}">${esc(d.name)} (${d.rows} rows)</option>`).join("") : '<option value="">Create one on the Performance Test Data page</option>'}</select></label>
        <label>For <select name="parent">${groups.map((g) => `<option value="${esc(g.id)}" ${g.category === "thread_group" ? "selected" : ""}>${g.category === "plan" ? "Whole test plan" : esc(g.name)}</option>`).join("")}</select></label>
        <label>Rows are <select name="share"><option value="all">Shared by all users (each takes the next row)</option><option value="thread">Read by each user from the start</option></select></label>
        <label><span><input type="checkbox" name="recycle" checked> Start again at the end of the rows</span></label>
        <div><button class="btn primary" type="submit" ${all.length ? "" : "disabled"}>Add to the script</button></div></form>
      <p class="muted">The column names become variables, e.g. <code>\${username}</code>. The rows are written next to the script for each run; secret columns are removed again when JMeter ends.</p></div>
    <div class="card" style="margin-top:12px"><h3>Bind request values to columns</h3>
      ${!vars.length ? '<p class="muted">Add a data set first.</p>' : info.fields.length ? `<table class="data"><thead><tr><th>Request</th><th>Value</th><th>Now</th><th>Use column</th></tr></thead><tbody>
        ${info.fields.map((f, i) => `<tr><td>${esc(f.sampler)}</td><td>${esc(f.label)}</td><td><code style="word-break:break-all">${esc(String(f.value).slice(0, 80))}</code>${f.bound ? ' <span class="pill PASS">variable</span>' : ""}</td>
          <td><div class="row"><select data-field="${i}"><option value="">—</option>${vars.map((v) => `<option value="${esc(v)}" ${f.value.includes(`\${${v}}`) || f.value.includes(`'${v}'`) ? "selected" : ""}>\${${esc(v)}}${columnType[v] === "secret" ? " 🔒" : ""}</option>`).join("")}</select>
          <button class="btn" type="button" data-bind="${i}">Bind</button></div></td></tr>`).join("")}</tbody></table>
        <p class="muted">Form and query values are URL-encoded when sent; JSON text values are written by JMeter's JSON encoder, so quotes in the data are safe. Press Save to keep the changes.</p>`
        : '<p class="muted">The script has no form, query, JSON or header values to bind.</p>'}</div>`;
  // final patch (defect 10): hard-coded values the script should not send as recorded
  const scan = document.createElement("div");
  scan.className = "card"; scan.style.marginTop = "12px";
  scan.innerHTML = '<h3>Parameters to review</h3><p class="muted">Looking for recorded dates, users, ids, passwords and tokens…</p>';
  pane.appendChild(scan);
  api("/api/performance/script/parameters", { method: "POST", body: { script: st.script } }).then(({ suggestions: sg }) => {
    const TYPE = { date: "Date", data: "Data", secret: "Password", correlation: "Server value" };
    scan.innerHTML = `<h3>Parameters to review</h3>${sg.length ? `<table class="data"><thead><tr><th>Request</th><th>Value</th><th>Recorded</th><th>Kind</th><th>Suggestion</th><th></th></tr></thead><tbody>
      ${sg.map((x, i) => `<tr><td>${esc(x.sampler)}</td><td>${esc(x.label)}</td><td><code>${esc(String(x.value).slice(0, 60))}</code></td><td><span class="pill WARN">${TYPE[x.type] || esc(x.type)}</span></td>
        <td>${esc(x.suggestion)}${x.expr ? `<br><code>${esc(x.expr)}</code>` : ""}${x.column ? `<br><span class="muted">column</span> <code>\${${esc(x.column)}}</code>` : ""}</td>
        <td>${x.type === "date" ? `<button class="btn" type="button" data-dyn="${i}">Make dynamic</button>` : ""}</td></tr>`).join("")}</tbody></table>
      <p class="muted">Dates become relative to the day the test runs, so a script recorded today still books a future date next month. For users, ids and passwords add the column to a data set (passwords as a secret column) and bind it above.</p>`
      : '<p class="muted">No recorded dates, users, ids, passwords or tokens were found in the request values.</p>'}`;
    scan.querySelectorAll("[data-dyn]").forEach((b) => b.addEventListener("click", async () => {
      const x = sg[+b.dataset.dyn];
      await applyOps([{ op: "bind", node: x.node, kind: x.kind, key: x.key, expr: x.expr }]);
    }));
  }).catch((err) => { scan.innerHTML = `<h3>Parameters to review</h3><p class="problems">${esc(err.message)}</p>`; });
  pane.querySelector("#addDs").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, d = byId[f.ds.value];
    if (!d) return;
    if (sets.some((b) => b.dataset_id === d.id)) { toast("The script already reads this data set.", "error"); return; }
    await applyOps([{ op: "add", parent: f.parent.value, type: "csv_dataset", values: {
      dataset_id: d.id, name: `Test data: ${d.name}`, variables: d.columns.map((c) => c.name), share: f.share.value, recycle: f.recycle.checked } }]);
  });
  pane.querySelectorAll("[data-bind]").forEach((b) => b.addEventListener("click", async () => {
    const f = info.fields[+b.dataset.bind];
    const v = pane.querySelector(`[data-field="${b.dataset.bind}"]`).value;
    if (!v) { toast("Choose a column.", "error"); return; }
    await applyOps([{ op: "bind", node: f.node, kind: f.kind, key: f.key, var: v, raw: f.kind === "json" && RAW_TYPES.has(columnType[v]) }]);
  }));
}
