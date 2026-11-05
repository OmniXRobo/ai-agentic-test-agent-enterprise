// 23.3.23: performance script editor. The JMX is the script; the tree, property panel and Raw JMX view all edit a
// working copy held here, and Save keeps it as a new version. Saving never starts a run.

import { api } from "../api.js";
import { esc, toast, dateTimeOf, openModal } from "../util.js";
import { fmtDuration } from "../charts.js";
import { secretsHtml, bindSecrets } from "./perf-record.js";
import { dataTab } from "./perf-data.js";
import { workloadTab } from "./perf-workload.js";

const KINDS = { load: "Load", stress: "Stress", spike: "Spike", soak: "Soak" };
const CATEGORY_ICON = { plan: "▣", thread_group: "◉", sampler: "➜", controller: "▤", extractor: "⇲", assertion: "✓",
  timer: "⏱", config: "⚙", pre: "◂", post: "▸", listener: "◎", other: "•" };
const CATEGORY_LABEL = { plan: "Test plan", thread_group: "Thread group", sampler: "Request", controller: "Controller",
  extractor: "Extractor", assertion: "Assertion", timer: "Timer", config: "Configuration", pre: "Pre-processor",
  post: "Post-processor", listener: "Listener (off during runs)", other: "Element" };
const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];

// The fields asked for when adding an element (name: [label, type, default, options])
const ADD_FIELDS = {
  thread_group: [["name", "Name", "text", "Users"], ["users", "Users", "number", 1], ["ramp_up_s", "Ramp-up (s)", "number", 0], ["duration_s", "Duration (s)", "number", 60]],
  http_request: [["name", "Name", "text", ""], ["method", "Method", "select", "GET", METHODS], ["url", "URL (or /path with HTTP request defaults)", "text", "https://"], ["body", "Body (POST, PUT, PATCH)", "textarea", ""]],
  transaction: [["name", "Name", "text", "Transaction"], ["parent", "Report as one sample", "checkbox", false]],
  regex_extractor: [["variable", "Variable name", "text", ""], ["expression", "Regular expression (one group)", "text", ""], ["default", "Default value", "text", "NOT_FOUND"],
    ["field", "Field", "select", "false", [["false", "Body"], ["true", "Headers"], ["URL", "URL"], ["code", "Response code"], ["message", "Response message"]]]],
  json_extractor: [["variable", "Variable name", "text", ""], ["expression", "JSON path", "text", "$.id"], ["default", "Default value", "text", "NOT_FOUND"]],
  boundary_extractor: [["variable", "Variable name", "text", ""], ["left", "Left boundary", "text", ""], ["right", "Right boundary", "text", ""], ["default", "Default value", "text", "NOT_FOUND"]],
  response_assertion: [["field", "Field", "select", "code", [["code", "Response code"], ["text", "Response body"], ["headers", "Response headers"], ["message", "Response message"], ["url", "URL"]]],
    ["type", "Test", "select", "contains", [["contains", "Contains"], ["matches", "Matches (regular expression)"], ["equals", "Equals"], ["substring", "Substring"]]],
    ["negate", "Not", "checkbox", false], ["pattern", "Pattern", "text", "200"]],
  duration_assertion: [["duration_ms", "Maximum duration (ms)", "number", 2000]],
  json_assertion: [["expression", "JSON path", "text", "$.status"], ["expected", "Expected value (empty: the path exists)", "text", ""]],
  constant_timer: [["delay_ms", "Delay (ms)", "number", 1000]],
  uniform_timer: [["delay_ms", "Constant delay (ms)", "number", 1000], ["range_ms", "Random range (ms)", "number", 1000]],
  header_manager: [["name", "Name", "text", "Headers"]],
  http_defaults: [["url", "Base URL", "text", "https://"]],
  cookie_manager: [["name", "Name", "text", "Cookies"]],
  user_variables: [["name", "Name", "text", "Variables"]],
};

function fieldHtml([key, label, type, def, options]) {
  if (type === "select") {
    const opts = options.map((o) => (Array.isArray(o) ? o : [o, o]));
    return `<label>${esc(label)} <select name="${key}">${opts.map(([v, l]) => `<option value="${esc(v)}" ${v === def ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
  }
  if (type === "checkbox") return `<label class="row" style="gap:6px"><input type="checkbox" name="${key}" ${def ? "checked" : ""}> ${esc(label)}</label>`;
  if (type === "textarea") return `<label>${esc(label)} <textarea name="${key}" rows="3">${esc(def)}</textarea></label>`;
  return `<label>${esc(label)} <input name="${key}" type="${type}" value="${esc(def)}"></label>`;
}

function pairsEditor(id, rows, nameLabel = "Name", valueLabel = "Value") {
  const row = (r = {}) => `<tr><td><input data-k="name" value="${esc(r.name || "")}"></td><td><input data-k="value" value="${esc(r.value || "")}"></td>
    <td><button class="link-btn" type="button" data-rm>Remove</button></td></tr>`;
  return `<table class="data pairs" id="${id}"><thead><tr><th>${esc(nameLabel)}</th><th>${esc(valueLabel)}</th><th></th></tr></thead>
    <tbody>${rows.map(row).join("")}</tbody></table><button class="btn" type="button" data-add="${id}">Add</button>
    <template data-row="${id}">${row()}</template>`;
}

function readPairs(box, id) {
  return [...box.querySelectorAll(`#${id} tbody tr`)].map((tr) => ({ name: tr.querySelector('[data-k="name"]').value.trim(), value: tr.querySelector('[data-k="value"]').value }))
    .filter((p) => p.name);
}

function diffView(text) {
  if (!text) return '<p class="muted">No differences.</p>';
  return `<pre class="code diff jdiff">${text.replace(/\n$/, "").split("\n").map((l) => `<span class="${l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : ""}">${esc(l) || " "}</span>`).join("")}</pre>`;
}

function findNode(nodes, id) {
  for (const n of nodes) {
    if (n.id === id) return n;
    const f = findNode(n.children, id);
    if (f) return f;
  }
  return null;
}

export async function scriptEditor(view, planId, backHref) {
  const data = await api(`/api/performance/plans/${encodeURIComponent(planId)}/script`);
  const st = { script: data.script, version: data.version, tree: data.tree, summary: data.summary, problems: data.problems,
    plan: data.plan, templates: data.templates, selected: "0", dirty: false, tab: "tree" };
  const s = st.plan.sla || {};
  const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
  const nf = (name, label, value, attrs = "") => `<label>${label}<input name="${name}" type="number" value="${value ?? ""}" ${attrs}></label>`;
  view.innerHTML = `
    <p><a href="${esc(backHref)}">← ${esc(document.getElementById("pageTitle").textContent)}</a></p>
    <div class="card">
      <div class="row"><h2 style="margin:0" id="title">${esc(st.plan.name)}</h2>
        <span class="muted">${esc(st.plan.id)} · script version <b id="ver">${st.version}</b>${st.plan.source ? ` · ${esc(st.plan.source)}` : ""}</span>
        <span class="pill WARN" id="dirty" hidden>Unsaved changes</span>
        <span class="spacer"></span>
        <a class="btn" href="/api/performance/plans/${esc(st.plan.id)}/jmx" id="dl">Download JMX</a>
        <button class="btn" type="button" id="runBtn">Run with JMeter</button>
        <button class="btn primary" type="button" id="saveBtn">Save</button></div>
      <p class="muted">The JMX is the script. Elements without a form here are kept exactly as they are. Saving keeps a new version and never starts a test.</p>
      <div id="summary"></div>
      <div id="secrets"></div>
      <details style="margin-top:8px"><summary>Name, test type and targets</summary>
        <form id="meta" class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-top:8px">
          <label>Name <input name="name" value="${esc(st.plan.name)}" required></label>
          <label>Test type <select name="kind">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === st.plan.kind ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          ${nf("avg_ms", "Average ≤ (ms)", s.avg_ms)}${nf("p90_ms", "90th pct ≤ (ms)", s.p90_ms)}${nf("p95_ms", "95th pct ≤ (ms)", s.p95_ms)}
          ${nf("p99_ms", "99th pct ≤ (ms)", s.p99_ms)}${nf("max_error_pct", "Error rate ≤ (%)", s.max_error_pct, 'step="0.1" min="0" max="100"')}
          ${nf("min_throughput", "Throughput ≥ (req/s)", s.min_throughput, 'step="0.1"')}
        </form><p class="muted">Users and duration come from the Workload tab (or the script's thread groups). Leave a target empty for no limit.</p></details>
    </div>
    <div class="tabs" role="tablist" style="margin-top:12px">
      <a class="tab active" href="#" data-tab="tree">Tree</a><a class="tab" href="#" data-tab="raw">Raw JMX</a><a class="tab" href="#" data-tab="data">Test data</a><a class="tab" href="#" data-tab="workload">Workload</a><a class="tab" href="#" data-tab="versions">Versions</a></div>
    <div id="pane"></div>`;
  const pane = view.querySelector("#pane");
  const meta = view.querySelector("#meta");
  const markDirty = (on) => { st.dirty = on; view.querySelector("#dirty").hidden = !on; };
  meta.addEventListener("input", () => markDirty(true));

  const renderSummary = () => {
    const sm = st.summary;
    // 23.3.26: a workload shape sets users, duration and loops of the main thread groups in the run's copy
    const shaped = st.plan.workload && st.plan.workload.shape !== "script";
    const problems = shaped ? st.problems.filter((p) => !/loops forever without a duration|number of users is not a number/.test(p)) : st.problems;
    const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
    const groups = sm.thread_groups.map((g) => `${esc(g.name)}: ${g.users === null ? "?" : plural(g.users, "user", "users")}${g.scheduler ? ` for ${fmtDuration(g.duration_s || 0)}${g.delay_s ? ` after ${fmtDuration(g.delay_s)}` : ""}` : `, ${g.loops < 0 ? "loops forever" : `${g.loops ?? "?"} loop(s)`}`}`).join(" · ");
    view.querySelector("#summary").innerHTML = `
      <p>${plural(sm.requests.length, "HTTP request", "HTTP requests")} · peak ${plural(sm.peak_users, "concurrent user", "concurrent users")} · ${sm.duration_known ? `duration ${fmtDuration(sm.total_duration_s)}` : "duration depends on loop counts"}
        · hosts: ${sm.hosts.length ? sm.hosts.map((h) => `<code>${esc(h)}</code>`).join(" ") : '<span class="muted">none</span>'}</p>
      ${groups ? `<p class="muted">${groups}</p>` : ""}
      ${st.plan.workload && st.plan.workload.shape !== "script" ? `<p>Workload: <b>${esc(st.plan.workload.shape)}</b>${st.plan.workload.model === "rate" ? `, ${esc(st.plan.workload.target_rps)} requests per second with at most ${st.plan.workload.users} users` : ""}. It replaces the thread group settings above in the copy JMeter runs (Workload tab).</p>` : ""}
      ${sm.unresolved_urls.length ? `<p class="muted">Host not known before the run: ${sm.unresolved_urls.slice(0, 5).map((u) => `<code>${esc(u)}</code>`).join(" ")}</p>` : ""}
      ${sm.listeners.length ? `<p class="muted">${sm.listeners.length} listener(s) are switched off during runs; the run records its own results.</p>` : ""}
      ${problems.length ? `<div class="problems"><b>Fix before running:</b><ul>${problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>` : '<p><span class="pill PASS">Valid</span> <span class="muted">The full pre-run checks (hosts, load limits, tools) run when the test is queued.</span></p>'}`;
  };

  const take = (r) => {  // a response with script + analysis
    st.script = r.script; st.tree = r.tree; st.summary = r.summary; st.problems = r.problems;
    if (r.selected !== undefined && r.selected !== null) st.selected = r.selected;
    else if (!findNode(st.tree, st.selected)) st.selected = "0";
    renderSummary();
  };

  const applyOps = async (ops) => {
    try {
      take(await api("/api/performance/script/apply", { method: "POST", body: { script: st.script, ops } }));
      markDirty(true);
      renderTab();
    } catch (err) { toast(err.message, "error"); }
  };

  // ---- tree tab ----
  const treeHtml = (nodes) => `<ul class="jtree">${nodes.map((n) => `<li>
      <div class="jnode ${n.id === st.selected ? "sel" : ""} ${n.active ? "" : "off"}" data-node="${esc(n.id)}" tabindex="0" title="${esc(CATEGORY_LABEL[n.category] || n.tag)}${n.plugin ? " (plugin)" : ""}">
        <span class="jicon">${CATEGORY_ICON[n.category] || "•"}</span> ${esc(n.name)} <span class="muted">${esc(n.tag)}</span>${n.plugin ? ' <span class="pill FAIL">plugin</span>' : ""}${n.enabled ? "" : ' <span class="pill WAITING">off</span>'}</div>
      ${n.children.length ? treeHtml(n.children) : ""}</li>`).join("")}</ul>`;

  const renderTree = async () => {
    const node = findNode(st.tree, st.selected) || st.tree[0];
    st.selected = node.id;
    const allowed = st.templates.filter((t) => t.parents.includes(node.category));
    pane.innerHTML = `<div class="grid split-2">
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:6px">
          <select id="addType"><option value="">Add under “${esc(node.name)}”…</option>${allowed.map((t) => `<option value="${t.type}">${esc(t.label)}</option>`).join("")}</select>
          <button class="btn" type="button" data-act="duplicate" ${node.id === "0" ? "disabled" : ""}>Duplicate</button>
          <button class="btn" type="button" data-act="up" ${node.id === "0" ? "disabled" : ""}>Move up</button>
          <button class="btn" type="button" data-act="down" ${node.id === "0" ? "disabled" : ""}>Move down</button>
          <button class="btn" type="button" data-act="toggle" ${node.id === "0" ? "disabled" : ""}>${node.enabled ? "Disable" : "Enable"}</button>
          <button class="btn danger" type="button" data-act="delete" ${node.id === "0" ? "disabled" : ""}>Delete</button></div>
        <div id="addForm"></div>
        <div class="jtree-wrap">${treeHtml(st.tree)}</div></div>
      <div class="card" id="props"><p class="muted">Loading…</p></div></div>`;
    pane.querySelectorAll("[data-node]").forEach((el) => {
      el.addEventListener("click", () => { st.selected = el.dataset.node; renderTree(); });
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") { st.selected = el.dataset.node; renderTree(); } });
    });
    pane.querySelector('[data-act="duplicate"]').onclick = () => applyOps([{ op: "duplicate", node: node.id }]);
    pane.querySelector('[data-act="up"]').onclick = () => applyOps([{ op: "move", node: node.id, direction: "up" }]);
    pane.querySelector('[data-act="down"]').onclick = () => applyOps([{ op: "move", node: node.id, direction: "down" }]);
    pane.querySelector('[data-act="toggle"]').onclick = () => applyOps([{ op: "set", node: node.id, enabled: !node.enabled }]);
    pane.querySelector('[data-act="delete"]').onclick = () => { if (confirm(`Delete “${node.name}” and everything under it?`)) applyOps([{ op: "delete", node: node.id }]); };
    pane.querySelector("#addType").addEventListener("change", (e) => {
      const type = e.target.value;
      const box = pane.querySelector("#addForm");
      if (!type) { box.innerHTML = ""; return; }
      const fields = ADD_FIELDS[type] || [];
      box.innerHTML = `<form class="card" style="margin:8px 0"><h4 style="margin-top:0">${esc(st.templates.find((t) => t.type === type).label)}</h4>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">${fields.map(fieldHtml).join("")}</div>
        <div class="row" style="margin-top:8px"><button class="btn primary" type="submit">Add</button><button class="btn" type="button" data-cancel>Cancel</button></div></form>`;
      const f = box.querySelector("form");
      f.querySelector("[data-cancel]").onclick = () => { box.innerHTML = ""; e.target.value = ""; };
      f.addEventListener("submit", (ev) => {
        ev.preventDefault();
        const values = {};
        for (const [key, , t] of fields) {
          const input = f.elements[key];
          values[key] = t === "checkbox" ? input.checked : t === "number" ? num(input.value) : input.value;
        }
        if (["regex_extractor", "json_extractor", "boundary_extractor"].includes(type) && !values.variable) { toast("Enter the variable name.", "error"); return; }
        applyOps([{ op: "add", parent: node.id, type, values }]);
      });
    });
    renderProps(node);
  };

  const renderProps = async (node) => {
    const box = pane.querySelector("#props");
    let d;
    try { d = (await api("/api/performance/script/node", { method: "POST", body: { script: st.script, node: node.id } })).node; }
    catch (err) { box.innerHTML = `<p class="muted">${esc(err.message)}</p>`; return; }
    const isHttp = ["HTTPSamplerProxy", "HTTPSampler", "HTTPSampler2"].includes(d.tag);
    const hidden = new Set(isHttp ? ["HTTPSampler.domain", "HTTPSampler.port", "HTTPSampler.protocol", "HTTPSampler.path", "HTTPSampler.method", "HTTPSampler.postBodyRaw"] : []);
    const props = d.props.filter((p) => !hidden.has(p.name));
    const propInput = (p) => (["true", "false"].includes(p.value) ? `<select data-prop="${esc(p.name)}"><option ${p.value === "true" ? "selected" : ""}>true</option><option ${p.value === "false" ? "selected" : ""}>false</option></select>`
      : `<input data-prop="${esc(p.name)}" value="${esc(p.value)}">`);
    box.innerHTML = `<form id="propForm">
      <div class="row"><h3 style="margin:0">${esc(CATEGORY_LABEL[d.category] || d.tag)}</h3><span class="muted">${esc(d.testclass)}</span>
        ${d.plugin ? '<span class="pill FAIL">JMeter plugin: not installed here</span>' : ""}</div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin-top:8px">
        <label>Name <input name="name" value="${esc(d.name)}" required></label>
        ${isHttp ? `<label>Method <select name="method">${METHODS.map((m) => `<option ${m === (d.props.find((p) => p.name === "HTTPSampler.method")?.value || "GET") ? "selected" : ""}>${m}</option>`).join("")}</select></label>
          <label style="grid-column:1/-1">URL <input name="url" value="${esc(d.url || "")}" placeholder="https://host/path or /path"></label>` : ""}
      </div>
      <label style="display:block;margin-top:8px">Comments <textarea name="comments" rows="2" style="width:100%">${esc(d.comments || "")}</textarea></label>
      ${isHttp && d.body !== undefined ? `<label style="display:block;margin-top:8px">Body <textarea name="body" rows="5" class="code-edit" style="min-height:120px;width:100%">${esc(d.body)}</textarea></label>` : ""}
      ${isHttp && d.params !== undefined ? `<h4>Parameters</h4>${pairsEditor("pParams", d.params)}<p class="muted"><button class="link-btn" type="button" id="toBody">Send a raw body instead</button></p>` : ""}
      ${d.headers !== undefined ? `<h4>Headers</h4>${pairsEditor("pHeaders", d.headers, "Header", "Value")}` : ""}
      ${d.variables !== undefined ? `<h4>Variables</h4>${pairsEditor("pVars", d.variables, "Variable", "Value")}<p class="muted">Use them as \${name}, e.g. a host variable in the request URLs.</p>` : ""}
      ${d.patterns !== undefined ? `<h4>Patterns to test</h4><textarea name="patterns" rows="3" style="width:100%" placeholder="One per line">${esc(d.patterns.join("\n"))}</textarea>
        <p class="muted">Test type: 1 matches, 2 contains, 8 equals, 16 substring; add 4 for “not”.</p>` : ""}
      ${props.length ? `<h4>Properties</h4><table class="data"><tbody>${props.map((p) => `<tr><td title="${esc(p.name)}">${esc(p.label)}</td><td>${propInput(p)}</td></tr>`).join("")}</tbody></table>` : ""}
      ${d.other_xml ? `<details><summary class="muted">Other settings (edit in Raw JMX)</summary><pre class="code" style="max-height:240px;overflow:auto">${esc(d.other_xml)}</pre></details>` : ""}
      <div class="row" style="margin-top:10px"><button class="btn primary" type="submit">Apply</button><span class="muted">Applies to the working copy; Save keeps it.</span></div></form>`;
    box.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => {
      const tpl = box.querySelector(`template[data-row="${b.dataset.add}"]`);
      box.querySelector(`#${b.dataset.add} tbody`).insertAdjacentHTML("beforeend", tpl.innerHTML);
      bindRemove();
    }));
    const bindRemove = () => box.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => b.closest("tr").remove()));
    bindRemove();
    box.querySelector("#toBody")?.addEventListener("click", () => applyOps([{ op: "set", node: node.id, body: "" }]));
    box.querySelector("#propForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const f = e.target;
      const op = { op: "set", node: node.id, name: f.elements.name.value, comments: f.elements.comments.value, props: {} };
      f.querySelectorAll("[data-prop]").forEach((i) => {
        const orig = d.props.find((p) => p.name === i.dataset.prop);
        if (!orig || orig.value !== i.value) op.props[i.dataset.prop] = i.value;
      });
      if (isHttp) {
        op.url = f.elements.url.value;
        op.props["HTTPSampler.method"] = f.elements.method.value;
        if (d.body !== undefined) op.body = f.elements.body.value;
        if (d.params !== undefined) op.params = readPairs(box, "pParams");
      }
      if (d.headers !== undefined) op.headers = readPairs(box, "pHeaders");
      if (d.variables !== undefined) op.variables = readPairs(box, "pVars");
      if (d.patterns !== undefined) op.patterns = f.elements.patterns.value.split("\n").filter((x) => x.trim());
      applyOps([op]);
    });
  };

  // ---- raw tab ----
  const renderRaw = () => {
    pane.innerHTML = `<div class="card">
      <p class="muted">The complete JMX. Validate checks it and updates the tree; nothing is saved until you press Save.</p>
      <textarea class="code-edit" id="raw" spellcheck="false">${esc(st.script)}</textarea>
      <div class="row" style="margin-top:8px"><button class="btn primary" type="button" id="validate">Validate and use</button>
        <button class="btn" type="button" id="revert">Discard raw changes</button><span id="rawMsg" class="muted"></span></div></div>`;
    const raw = pane.querySelector("#raw");
    raw.addEventListener("input", () => (pane.querySelector("#rawMsg").textContent = "Edited; press Validate and use."));
    pane.querySelector("#revert").onclick = () => { raw.value = st.script; pane.querySelector("#rawMsg").textContent = ""; };
    pane.querySelector("#validate").onclick = async () => {
      try {
        const r = await api("/api/performance/script/analyze", { method: "POST", body: { script: raw.value } });
        const changed = r.script !== st.script;
        take(r);
        if (changed) markDirty(true);
        raw.value = st.script;
        pane.querySelector("#rawMsg").innerHTML = r.problems.length ? `<span class="pill WARN">Valid XML, ${r.problems.length} problem(s) to fix before running</span>` : '<span class="pill PASS">Valid</span>';
      } catch (err) { pane.querySelector("#rawMsg").innerHTML = `<span class="pill FAIL">Not used</span> ${esc(err.message)}`; }
    };
  };

  // ---- versions tab ----
  const renderVersions = async () => {
    const { items } = await api(`/api/performance/plans/${encodeURIComponent(planId)}/versions`);
    const opts = (sel) => items.map((v) => `<option value="${v.version}" ${v.version === sel ? "selected" : ""}>Version ${v.version}</option>`).join("");
    pane.innerHTML = `<div class="card">
      ${items.length > 1 ? `<form class="row" id="cmp">Compare <select name="a">${opts(items[1].version)}</select> with <select name="b">${opts(items[0].version)}</select>
        <button class="btn" type="submit">Show differences</button></form><div id="diff" style="margin-top:8px"></div>` : ""}
      <table class="data" style="margin-top:8px"><thead><tr><th>Version</th><th>Saved</th><th>Note</th><th>Size</th><th></th></tr></thead><tbody>
      ${items.map((v) => `<tr><td>${v.version}${v.version === st.version ? ' <span class="pill PASS">current</span>' : ""}</td><td>${esc(dateTimeOf(v.created_at))}</td><td>${esc(v.note || "")}</td>
        <td>${(v.size / 1024).toFixed(1)} KB</td><td><a href="/api/performance/plans/${esc(planId)}/versions/${v.version}">Download</a>
        ${v.version !== st.version ? ` · <button class="link-btn" type="button" data-restore="${v.version}">Restore</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
    pane.querySelector("#cmp")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try {
        const { diff } = await api(`/api/performance/plans/${encodeURIComponent(planId)}/diff?a=${f.get("a")}&b=${f.get("b")}`);
        pane.querySelector("#diff").innerHTML = diffView(diff);
      } catch (err) { toast(err.message, "error"); }
    });
    pane.querySelectorAll("[data-restore]").forEach((b) => (b.onclick = async () => {
      if (st.dirty && !confirm("Restoring discards your unsaved changes. Continue?")) return;
      try {
        await api(`/api/performance/plans/${encodeURIComponent(planId)}/versions/${b.dataset.restore}/restore`, { method: "POST", body: {} });
        toast(`Version ${b.dataset.restore} restored as a new version`, "success");
        await reload();
      } catch (err) { toast(err.message, "error"); }
    }));
  };

  const renderTab = () => {
    view.querySelectorAll("[data-tab]").forEach((t) => t.classList.toggle("active", t.dataset.tab === st.tab));
    ({ tree: renderTree, raw: renderRaw, versions: renderVersions, data: () => dataTab(pane, st, applyOps),
      workload: () => workloadTab(pane, st, planId, { save, onSaved: (plan) => {
        st.plan = { ...st.plan, ...plan };
        meta.elements.kind.value = st.plan.kind;
        renderSummary();
      } }) })[st.tab]();
  };
  view.querySelectorAll("[data-tab]").forEach((t) => t.addEventListener("click", (e) => {
    e.preventDefault();
    if (st.tab === "raw" && t.dataset.tab !== "raw") {
      const raw = pane.querySelector("#raw");
      if (raw && raw.value !== st.script && !confirm("The Raw JMX has edits that were not validated. Leave them?")) return;
    }
    st.tab = t.dataset.tab;
    renderTab();
  }));

  const reload = async () => {
    const r = await api(`/api/performance/plans/${encodeURIComponent(planId)}/script`);
    st.version = r.version; st.plan = r.plan;
    take({ ...r, selected: null });
    view.querySelector("#ver").textContent = st.version;
    view.querySelector("#title").textContent = st.plan.name;
    markDirty(false);
    renderTab();
  };

  const save = async () => {
    const f = new FormData(meta);
    if (!f.get("name")) { toast("Enter a name.", "error"); return false; }
    const note = prompt("Note for this version (optional)", "") ?? null;
    if (note === null) return false;
    const sla = {};
    for (const k of ["avg_ms", "p90_ms", "p95_ms", "p99_ms", "max_error_pct", "min_throughput"]) sla[k] = num(f.get(k));
    try {
      const r = await api(`/api/performance/plans/${encodeURIComponent(planId)}/script`, { method: "PUT",
        body: { script: st.script, base_version: st.version, note, name: f.get("name"), kind: f.get("kind"), sla } });
      st.version = r.version; st.plan = r.plan;
      take({ ...r, selected: st.selected });
      view.querySelector("#ver").textContent = st.version;
      view.querySelector("#title").textContent = st.plan.name;
      markDirty(false);
      toast(`Saved as version ${st.version}`, "success");
      renderTab();
      return true;
    } catch (err) {
      const detail = Array.isArray(err.details) ? `: ${err.details.map((d) => d.msg || JSON.stringify(d)).join("; ")}` : "";
      toast(err.message + detail, "error");
      return false;
    }
  };
  view.querySelector("#saveBtn").addEventListener("click", save);
  view.querySelector("#dl").addEventListener("click", (e) => {
    if (st.dirty && !confirm("The download is the saved version; your unsaved changes are not in it. Continue?")) e.preventDefault();
  });
  view.querySelector("#runBtn").addEventListener("click", async () => {
    if (st.dirty) {
      if (!confirm("Save your changes first? The run uses the saved script.")) return;
      if (!(await save())) return;
    }
    try {
      const r = await api(`/api/performance/plans/${encodeURIComponent(planId)}/run`, { method: "POST", body: {} });
      toast("Queued. The pre-run checks run before any request is sent.", "success");
      location.hash = `#/perf-live?run=${encodeURIComponent(r.run_id)}`;
    } catch (err) { toast(err.message, "error"); }
  });
  const renderSecrets = (items) => {
    const box = view.querySelector("#secrets");
    box.innerHTML = secretsHtml(items, st.plan.recording);
    bindSecrets(box, st.plan.id, renderSecrets);
  };
  renderSecrets(data.secrets || []);
  renderSummary();
  renderTab();
}

// The Import JMX card on the plan lists
export function importCard(onDone, kind) {
  return {
    html: `<form class="card" id="importJmx"><h3>Import a JMX file</h3>
      <p class="muted">A script saved by JMeter (5.x). Every element is kept, also the ones without a form here. Nothing is run.</p>
      <div class="row"><input type="file" name="file" accept=".jmx,.xml" required>
        <select name="kind">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === (kind || "load") ? "selected" : ""}>${v}</option>`).join("")}</select>
        <button class="btn primary" type="submit">Import</button></div></form>`,
    bind(root) {
      root.querySelector("#importJmx").addEventListener("submit", async (e) => {
        e.preventDefault();
        const file = e.target.file.files[0];
        if (!file) return;
        if (file.size > 20 * 1024 * 1024) { toast("The file is larger than 20 MB.", "error"); return; }
        try {
          const jmx = await file.text();
          const scenario = new URLSearchParams(location.hash.split("?")[1] || "").get("scenario");
          const { plan } = await api(`/api/performance/plans/import${scenario ? `?scenario_id=${encodeURIComponent(scenario)}` : ""}`,
            { method: "POST", body: { jmx, filename: file.name, kind: e.target.kind.value } });
          toast(`Imported “${plan.name}”`, "success");
          onDone(plan);
        } catch (err) { toast(err.message, "error"); }
      });
    },
  };
}
