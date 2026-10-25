// AI-Driven Automation Script Studio: generate Python Playwright automation (Page Object Model, business
// flows, test data) from TFS User Stories and Test Cases, then view, edit, play and review each script's evidence.

import { api } from "../api.js";
import { subscribe } from "../store.js";
import { esc, toast, dateTimeOf, elapsed } from "../util.js";

const BASE = "/api/automation/studio";
const FINAL_JOB = new Set(["COMPLETED", "FAILED", "STOPPED", "INTERRUPTED"]);
const PILL = { GENERATED: "PASSED", UPDATED: "PASSED", UNCHANGED: "PASSED", COMPLETED: "PASSED", SKIPPED: "WAITING",
  SAVED: "WAITING", RESOLVING: "RUNNING", "FILE MISSING": "FAILED", ERROR: "FAILED", STOPPED: "WARN", CANCELLED: "WARN",
  INTERRUPTED: "WARN" };
const TABS = [["view", "View Script"], ["edit", "Edit Script"], ["flow", "Flow Logic"], ["data", "Test Data"],
  ["credentials", "Credentials"], ["components", "Components"], ["history", "History"], ["results", "Result"],
  ["evidence", "Evidence"], ["logs", "Logs"]];

let cleanups = [];
const onCleanup = (fn) => cleanups.push(fn);

const params = () => new URLSearchParams(location.hash.split("?")[1] || "");
const link = (query) => `#/ai-automation${query ? `?${query}` : ""}`;
const go = (query) => { location.hash = link(query); };
const evidenceUrl = (p) => `/api/automation/evidence?path=${encodeURIComponent(p)}`;
const baseName = (p) => String(p || "").split(/[\\/]/).pop();
const pill = (s) => (s ? `<span class="pill ${esc(PILL[s] || s)}">${esc(s)}</span>` : '<span class="muted">-</span>');
const sid = (id) => encodeURIComponent(id);

function storage(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem(`qa.studio.${key}`) || "null");
    localStorage.setItem(`qa.studio.${key}`, JSON.stringify(value));
  } catch { /* storage may be blocked */ }
  return null;
}

function errorText(err) {
  const checks = Array.isArray(err.details) ? err.details : err.details?.checks;
  const failed = (checks || []).filter((c) => c.status === "FAIL");
  return failed.length ? `${err.message}` : err.message;
}

function checksHtml(checks) {
  if (!checks?.length) return "";
  return `<table class="data"><thead><tr><th>Validation</th><th>Result</th><th>Detail</th></tr></thead><tbody>
    ${checks.map((c) => `<tr><td>${esc(c.name)}</td><td>${pill(c.status === "PASS" ? "PASSED" : c.status === "FAIL" ? "FAILED" : c.status)}</td><td>${esc(c.detail)}</td></tr>`).join("")}
    </tbody></table>`;
}

// ---- Python syntax highlighting (offline, no library) ----------------------------------------

const PY_KW = new Set(("False None True and as assert async await break class continue def del elif else except finally " +
  "for from global if import in is lambda nonlocal not or pass raise return try while with yield").split(" "));

export function highlightPython(src) {
  const re = /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(@[A-Za-z_][\w.]*)|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_]\w*)\b/g;
  let out = "", last = 0, prev = "", m;
  while ((m = re.exec(src))) {
    out += esc(src.slice(last, m.index));
    const t = m[0];
    if (m[1]) out += `<span class="py-c">${esc(t)}</span>`;
    else if (m[2]) out += `<span class="py-s">${esc(t)}</span>`;
    else if (m[3]) out += `<span class="py-d">${esc(t)}</span>`;
    else if (m[4]) out += `<span class="py-n">${esc(t)}</span>`;
    else if (PY_KW.has(t)) out += `<span class="py-k">${esc(t)}</span>`;
    else if (prev === "def" || prev === "class") out += `<span class="py-f">${esc(t)}</span>`;
    else out += esc(t);
    prev = m[5] ? t : "";
    last = re.lastIndex;
  }
  return out + esc(src.slice(last));
}

function codeView(src) {
  const lines = String(src || "").split("\n").length;
  return `<div class="codeview"><pre class="gutter" aria-hidden="true">${Array.from({ length: lines }, (_, i) => i + 1).join("\n")}</pre>
    <pre class="code py">${highlightPython(src || "")}</pre></div>`;
}

function diffView(lines) {
  if (!lines?.length) return '<p class="muted">No differences.</p>';
  return `<pre class="code diff">${lines.map((l) => `<span class="${l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : ""}">${esc(l)}</span>`).join("\n")}</pre>`;
}

// ---- date/time runtime rules (the Windows system clock) ---------------------------------------

const FORMATS = [["%d/%m/%Y", "dd/mm/yyyy"], ["%Y-%m-%d", "yyyy-mm-dd"], ["%m/%d/%Y", "mm/dd/yyyy"], ["%d-%b-%Y", "dd-Mon-yyyy"],
  ["%H:%M", "HH:MM"], ["%H:%M:%S", "HH:MM:SS"], ["%d/%m/%Y %H:%M", "dd/mm/yyyy HH:MM"]];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n, w = 2) => String(n).padStart(w, "0");

function strftime(d, fmt) {
  return fmt.replace(/%([dmYyHMSbB%])/g, (_, k) => ({ d: pad(d.getDate()), m: pad(d.getMonth() + 1), Y: d.getFullYear(),
    y: pad(d.getFullYear() % 100), H: pad(d.getHours()), M: pad(d.getMinutes()), S: pad(d.getSeconds()),
    b: MONTHS[d.getMonth()], B: d.toLocaleString("en", { month: "long" }), "%": "%" })[k]);
}

// Preview of a rule such as {{TODAY+7d:%d/%m/%Y}} or {{NOW+2h:%H:%M}} (the test resolves it the same way at run time).
export function previewRule(text) {
  return String(text ?? "").replace(/\{\{\s*(TODAY|DATE|NOW)((?:\s*[+-]\s*\d+\s*(?:min|[dwmyh]))*)\s*(?::([^}]*))?\}\}/g, (_, kind, offs, fmt) => {
    const d = new Date();
    for (const [, sign, n, unit] of offs.matchAll(/([+-])\s*(\d+)\s*(min|[dwmyh])/g)) {
      const v = Number(n) * (sign === "-" ? -1 : 1);
      if (unit === "d") d.setDate(d.getDate() + v);
      else if (unit === "w") d.setDate(d.getDate() + 7 * v);
      else if (unit === "m") d.setMonth(d.getMonth() + v);
      else if (unit === "y") d.setFullYear(d.getFullYear() + v);
      else if (unit === "h") d.setHours(d.getHours() + v);
      else d.setMinutes(d.getMinutes() + v);
    }
    return strftime(d, fmt || (kind === "NOW" ? "%Y-%m-%d %H:%M" : "%Y-%m-%d"));
  });
}

function ruleBuilder(current, onApply) {
  const m = modal("Date / time rule", `
    <p class="muted">Values come from the Windows system clock of the PC that runs the test, at the moment it runs.</p>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
      <label>Value <select name="base">
        <option value="TODAY">Current System Date</option><option value="NOW">Current System Time</option></select></label>
      <label>Change <select name="sign"><option value="">none</option><option value="+">plus</option><option value="-">minus</option></select></label>
      <label>Amount <input name="amount" type="number" min="0" value="1"></label>
      <label>Unit <select name="unit"><option value="d">days</option><option value="w">weeks</option><option value="m">months</option>
        <option value="y">years</option><option value="h">hours</option><option value="min">minutes</option></select></label>
      <label>Format <select name="fmt">${FORMATS.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}
        <option value="custom">Custom…</option></select></label>
      <label>Custom format <input name="custom" placeholder="%d.%m.%Y"></label>
    </div>
    <p>Rule <code id="rule"></code> → today this gives <strong id="preview"></strong></p>
    <div class="row"><span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="apply">Use this rule</button></div>`);
  const f = (n) => m.el.querySelector(`[name=${n}]`);
  const rule = () => {
    const sign = f("sign").value, fmt = f("fmt").value === "custom" ? f("custom").value : f("fmt").value;
    return `{{${f("base").value}${sign ? `${sign}${Number(f("amount").value) || 0}${f("unit").value}` : ""}${fmt ? `:${fmt}` : ""}}}`;
  };
  const show = () => { m.el.querySelector("#rule").textContent = rule(); m.el.querySelector("#preview").textContent = previewRule(rule()); };
  const parsed = /\{\{(TODAY|NOW)(?:([+-])(\d+)(min|[dwmyh]))?(?::([^}]*))?\}\}/.exec(current || "");
  if (parsed) {
    f("base").value = parsed[1]; f("sign").value = parsed[2] || ""; f("amount").value = parsed[3] || 1; f("unit").value = parsed[4] || "d";
    if (FORMATS.some(([v]) => v === parsed[5])) f("fmt").value = parsed[5]; else if (parsed[5]) { f("fmt").value = "custom"; f("custom").value = parsed[5]; }
  } else if (/time/i.test(current || "")) f("base").value = "NOW";
  f("base").addEventListener("change", () => { if (f("base").value === "NOW") { f("fmt").value = "%H:%M"; f("unit").value = "h"; } show(); });
  m.el.querySelectorAll("select,input").forEach((el) => el.addEventListener("input", show));
  show();
  m.el.querySelector("#apply").addEventListener("click", () => { onApply(rule()); m.close(); });
}

// ---- dialogs ------------------------------------------------------------------------------

function modal(title, body, { wide = false } = {}) {
  const el = document.createElement("div");
  el.className = "modal-backdrop";
  el.innerHTML = `<div class="modal card ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <div class="row"><h3 style="margin:0">${esc(title)}</h3><span class="spacer"></span>
      <button class="icon-btn" data-close aria-label="Close">✕</button></div>
    <div class="modal-body">${body}</div></div>`;
  let onClose = null;
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => {
    if (!el.isConnected) return;
    el.remove();
    document.removeEventListener("keydown", onKey);
    if (onClose) onClose();
  };
  el.addEventListener("click", (e) => { if (e.target === el || e.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(el);
  onCleanup(close);
  return { el, close, onClose: (fn) => { onClose = fn; } };
}

// "How would you like to run this automation?" - asked before every Play.
function playDialog({ count = 1, combinations = [] } = {}) {
  return new Promise((resolve) => {
    const many = count > 1;
    const m = modal(many ? `Play ${count} automation scripts` : "Play automation", `
      <p>How would you like to run ${many ? "these automations" : "this automation"}?</p>
      <label class="choice"><input type="radio" name="mode" value="visible" ${many ? "" : "checked"}>
        <span><strong>Visible Browser Execution</strong><br><span class="muted">Watch the browser: the cursor moves to each element, the element is highlighted and text is typed visibly.</span></span></label>
      <label class="choice"><input type="radio" name="mode" value="headless" ${many ? "checked" : ""}>
        <span><strong>Background / Headless Execution</strong>${many ? ' <span class="pill WAITING">Recommended for several tests</span>' : ""}<br>
        <span class="muted">The same script and checks without a browser window. Step screenshots and evidence are still captured.</span></span></label>
      ${combinations.length > 1 ? `<label>Test data <select name="combination"><option value="">All ${combinations.length} combinations</option>
        ${combinations.map((c) => `<option>${esc(c)}</option>`).join("")}</select></label>` : ""}
      <div class="row" style="margin-top:12px"><span class="spacer"></span><button class="btn" data-cancel>Cancel</button>
        <button class="btn primary" data-run>${many ? "Run Tests" : "Run Test"}</button></div>`);
    let answered = false;
    const answer = (v) => { if (!answered) { answered = true; resolve(v); } m.close(); };
    m.onClose(() => { if (!answered) { answered = true; resolve(null); } });
    m.el.querySelector("[data-cancel]").addEventListener("click", () => answer(null));
    m.el.querySelector("[data-run]").addEventListener("click", () => answer({
      headless: m.el.querySelector("input[name=mode]:checked").value === "headless",
      combination: m.el.querySelector("[name=combination]")?.value || null,
    }));
  });
}

async function play(scriptId, combinations) {
  const choice = await playDialog({ combinations });
  if (!choice) return null;
  const r = await api(`${BASE}/scripts/${sid(scriptId)}/run`, { method: "POST", body: choice });
  return { runId: r.run_id, mode: choice.headless ? "Background / Headless" : "Visible Browser" };
}

// ---- page -----------------------------------------------------------------------------------

export async function mount(view) {
  const p = params();
  const scriptId = p.get("script");
  if (scriptId) {
    if (!scriptId.startsWith("FWS-")) { location.hash = `#/ai-automation-classic?script=${sid(scriptId)}`; return; }
    return scriptView(view, scriptId, p.get("tab") || "view", p.get("run"));
  }
  if (p.get("job")) return jobView(view, p.get("job"));
  return home(view, p.get("tab") || "scripts");
}

export function unmount() {
  cleanups.forEach((fn) => { try { fn(); } catch { /* ignore */ } });
  cleanups = [];
}

function every(ms, fn) {
  const t = setInterval(fn, ms);
  onCleanup(() => clearInterval(t));
  return () => clearInterval(t);
}

async function home(view, tab) {
  view.innerHTML = `<div class="tabs" role="tablist">
    ${[["scripts", "Script Inventory"], ["generate", "Generate Automation"], ["jobs", "Generation Jobs"]].map(([k, l]) =>
      `<a class="tab ${k === tab ? "active" : ""}" role="tab" aria-selected="${k === tab}" href="${link(`tab=${k}`)}">${l}</a>`).join("")}
    </div><div id="pane"></div>`;
  const pane = view.querySelector("#pane");
  if (tab === "generate") return generatePane(pane);
  if (tab === "jobs") return jobsPane(pane);
  return inventoryPane(pane);
}

// ---- script inventory -------------------------------------------------------------------------

function actionsHtml(s) {
  if (!s.framework) return `<a href="#/ai-automation-classic?script=${sid(s.id)}">Open</a>`;
  const q = (tab) => link(`script=${sid(s.id)}&tab=${tab}`);
  return `<span class="actions">
    <a href="${q("view")}">View Script</a><a href="${q("edit")}">Edit Script</a><a href="${q("flow")}">Flow Logic</a>
    ${s.file_verified ? `<button class="link-btn" data-play="${esc(s.id)}">Play</button>` : ""}
    ${s.last_run_id ? `<a href="${link(`script=${sid(s.id)}&tab=results&run=${sid(s.last_run_id)}`)}">Result</a>
      <a href="${link(`script=${sid(s.id)}&tab=logs&run=${sid(s.last_run_id)}`)}">Logs</a>
      <a href="${link(`script=${sid(s.id)}&tab=evidence&run=${sid(s.last_run_id)}`)}">Evidence</a>` : ""}
    <button class="link-btn" data-regen="${esc(s.id)}">Regenerate / Update</button></span>`;
}

async function inventoryPane(pane) {
  let items = (await api(`${BASE}/inventory`)).items;
  const saved = storage("filters") || {};
  const sprints = [...new Set(items.map((i) => i.sprint).filter(Boolean))].sort();
  const methods = [...new Set(items.map((i) => i.method_name))];
  pane.innerHTML = `<div class="card">
    <div class="row" id="filters" style="flex-wrap:wrap">
      <input id="q" placeholder="Search User Story, Test Case or script" style="flex:1;min-width:200px" value="${esc(saved.q || "")}">
      <select id="fSprint" aria-label="Sprint"><option value="">All sprints</option>${sprints.map((s) => `<option>${esc(s)}</option>`).join("")}</select>
      <select id="fMethod" aria-label="Method"><option value="">All methods</option>${methods.map((s) => `<option>${esc(s)}</option>`).join("")}</select>
      <select id="fStatus" aria-label="Status"><option value="">Any status</option><option>GENERATED</option><option>FILE MISSING</option><option>SAVED</option></select>
      <select id="fResult" aria-label="Last result"><option value="">Any result</option><option>PASSED</option><option>FAILED</option>
        <option>RUNNING</option><option>STOPPED</option><option>ERROR</option><option value="none">Not run yet</option></select>
      <span class="spacer"></span>
      <button class="btn" id="playSel" disabled>Play Selected</button>
      <a class="btn" href="${BASE}/export" title="Page Objects, flows, tests and test data with RUN_TESTS.cmd">Download framework (.zip)</a>
      <a class="btn primary" href="${link("tab=generate")}">Generate Automation</a>
    </div>
    <div style="overflow-x:auto"><table class="data" id="inv"><thead><tr><th><input type="checkbox" id="all" aria-label="Select all"></th>
      <th>Method</th><th>User Story</th><th>User Story Title</th><th>Test Case</th><th>Test Case Title</th><th>Sprint</th><th>Script</th>
      <th>Generated</th><th>Status</th><th>Last Result</th><th>Actions</th></tr></thead><tbody></tbody></table></div>
    <p class="muted" id="count"></p></div>`;
  const $ = (id) => pane.querySelector(`#${id}`);
  for (const [id, key] of [["fSprint", "sprint"], ["fMethod", "method"], ["fStatus", "status"], ["fResult", "result"]]) $(id).value = saved[key] || "";
  const selected = new Set();
  const visible = () => {
    const q = $("q").value.trim().toLowerCase();
    return items.filter((s) => (!q || [s.user_story_id, s.user_story_title, s.test_case_id, s.test_case_title, s.script].some((v) => String(v ?? "").toLowerCase().includes(q)))
      && (!$("fSprint").value || s.sprint === $("fSprint").value) && (!$("fMethod").value || s.method_name === $("fMethod").value)
      && (!$("fStatus").value || s.status === $("fStatus").value)
      && (!$("fResult").value || ($("fResult").value === "none" ? !s.last_result : s.last_result === $("fResult").value)));
  };
  const render = () => {
    storage("filters", { q: $("q").value, sprint: $("fSprint").value, method: $("fMethod").value, status: $("fStatus").value, result: $("fResult").value });
    const rows = visible();
    pane.querySelector("tbody").innerHTML = rows.map((s) => `<tr>
      <td>${s.framework && s.file_verified ? `<input type="checkbox" data-sel="${esc(s.id)}" ${selected.has(s.id) ? "checked" : ""} aria-label="Select ${esc(s.script)}">` : ""}</td>
      <td>${esc(s.method_name)}</td>
      <td>${s.user_story_id ? `<a href="#/user-stories?id=${esc(s.user_story_id)}">US${esc(s.user_story_id)}</a>` : ""}</td><td>${esc(s.user_story_title || "")}</td>
      <td>${s.test_case_id ? `<a href="#/test-cases?id=${esc(s.test_case_id)}">TC${esc(s.test_case_id)}</a>` : ""}</td><td>${esc(s.test_case_title || "")}</td>
      <td>${esc(s.sprint || "")}</td>
      <td>${s.framework ? `<a href="${link(`script=${sid(s.id)}&tab=view`)}"><code>${esc(s.script)}</code></a>` : `<code>${esc(s.script)}</code>`}
        ${s.warnings?.length ? `<div class="pill WARN" title="${esc(s.warnings.join("\n"))}">${s.warnings.length} warning(s)</div>` : ""}</td>
      <td>${esc(dateTimeOf(s.generated_at))}</td><td>${pill(s.status)}</td><td>${pill(s.last_result)}</td><td>${actionsHtml(s)}</td></tr>`).join("")
      || `<tr><td colspan="12" class="muted">${items.length ? "No script matches these filters." : "No automation yet. Choose Generate Automation to create it from your User Stories and Test Cases."}</td></tr>`;
    $("count").textContent = `${rows.length} of ${items.length} script(s), newest first.`;
    $("playSel").disabled = !selected.size;
    $("playSel").textContent = selected.size ? `Play Selected (${selected.size})` : "Play Selected";
  };
  pane.addEventListener("input", (e) => { if (e.target.closest("#filters")) render(); });
  pane.addEventListener("change", (e) => {
    if (e.target.dataset.sel) { e.target.checked ? selected.add(e.target.dataset.sel) : selected.delete(e.target.dataset.sel); render(); }
    if (e.target.id === "all") { visible().filter((s) => s.framework && s.file_verified).forEach((s) => (e.target.checked ? selected.add(s.id) : selected.delete(s.id))); render(); }
  });
  pane.addEventListener("click", async (e) => {
    const playBtn = e.target.closest("[data-play]"), regen = e.target.closest("[data-regen]");
    try {
      if (playBtn) {
        const id = playBtn.dataset.play;
        const detail = await api(`${BASE}/scripts/${sid(id)}`);
        const r = await play(id, (detail.data?.combinations || []).map((c) => c.name));
        if (r) go(`script=${sid(id)}&tab=results&run=${sid(r.runId)}&live=1`);
      } else if (regen) {
        if (!confirm("Regenerate this automation from the current Test Case? Only what changed is updated, and the current version stays in History.")) return;
        const r = await api(`${BASE}/scripts/${sid(regen.dataset.regen)}/regenerate`, { method: "POST", body: {} });
        go(`job=${sid(r.job_id)}`);
      }
    } catch (err) { toast(errorText(err), "error"); }
  });
  $("playSel").addEventListener("click", async () => {
    const choice = await playDialog({ count: selected.size });
    if (!choice) return;
    try {
      const { runs } = await api(`${BASE}/run-many`, { method: "POST", body: { script_ids: [...selected], headless: choice.headless } });
      const failed = runs.filter((r) => !r.run_id);
      toast(`${runs.length - failed.length} test(s) queued; each runs separately with its own result.` +
        (failed.length ? ` ${failed.length} could not start: ${failed.map((f) => f.error).join("; ")}` : ""), failed.length ? "warning" : "success", 8000);
      selected.clear();
      items = (await api(`${BASE}/inventory`)).items;
      render();
    } catch (err) { toast(err.message, "error"); }
  });
  render();
  every(4000, async () => {
    if (!items.some((s) => s.last_result === "RUNNING")) return;
    items = (await api(`${BASE}/inventory`)).items;
    render();
  });
}

// ---- generate --------------------------------------------------------------------------------

async function generatePane(pane) {
  let ctx;
  try { ctx = await api(`${BASE}/context`); } catch (err) {
    pane.innerHTML = `<div class="card"><h3>TFS is not available</h3><p class="muted">${esc(err.message)}</p>
      <a class="btn primary" href="#/tfs-settings">TFS Connection</a></div>`;
    return;
  }
  const [disc, cred] = await Promise.all([api("/api/automation/discoveries").catch(() => ({ items: [] })),
    api("/api/automation/credentials").catch(() => ({ items: [] }))]);
  const last = storage("settings") || {};
  const uniq = (key) => [...new Set(cred.items.map((c) => c[key]).filter(Boolean))];
  pane.innerHTML = `<div class="gen-grid">
    <div class="card"><h3>1. Choose User Stories and Test Cases</h3>
      <div class="row" style="flex-wrap:wrap">
        <label>Sprint <select id="sprint"><option value="">All sprints</option>${(ctx.sprints || []).map((s) => `<option value="${esc(s)}" ${s === last.sprint ? "selected" : ""}>${esc(s.split("\\").pop())}</option>`).join("")}</select></label>
        <input id="text" placeholder="Filter by title or ID" style="flex:1;min-width:160px">
        <button class="btn" id="load">Show User Stories</button></div>
      <p class="muted">Tick a User Story to automate all its Test Cases, or open it to pick Test Cases. Several User Stories can be generated together.</p>
      <div id="stories"><p class="muted">Loading…</p></div>
    </div>
    <form class="card settings-form" id="settings"><h3>2. Application</h3>
      <label>Application URL <input name="base_url" required placeholder="https://your-app/" value="${esc(last.base_url || "")}"></label>
      <label>Application <input name="application" list="apps" placeholder="as saved in Application Credentials" value="${esc(last.application || "")}"></label>
      <label>Environment <input name="environment" list="envs" placeholder="optional, e.g. UAT" value="${esc(last.environment || "")}"></label>
      <label>Credential role <input name="credential_role" list="roles" placeholder="e.g. admin" value="${esc(last.credential_role || "")}"></label>
      <datalist id="apps">${uniq("application").map((v) => `<option value="${esc(v)}">`).join("")}</datalist>
      <datalist id="envs">${uniq("environment").map((v) => `<option value="${esc(v)}">`).join("")}</datalist>
      <datalist id="roles">${uniq("role").map((v) => `<option value="${esc(v)}">`).join("")}</datalist>
      <label>Element inventory <select name="discovery_id"><option value="">Latest discovery of this URL (discover it if needed)</option>
        ${disc.items.map((d) => `<option value="${esc(d.id)}">${esc(d.base_url)} · ${esc(dateTimeOf(d.created_at))}</option>`).join("")}</select></label>
      <p class="muted">AI runtime: <strong>${esc(ctx.ai_runtime)}</strong> · <a href="#/ai-settings">change</a>. The Automation Engineering Agent reuses existing
        Page Objects, flows and fixtures, adds what is missing, validates everything and saves a file only when it passes.</p>
      <div id="summary" class="muted">Nothing selected yet.</div>
      <button class="btn primary" type="submit" id="genBtn" disabled>Generate Automation</button>
    </form></div>`;
  const $ = (id) => pane.querySelector(`#${id}`);
  const stories = new Map(); // id -> { story, checked, cases: null | [{id,title,existing}], picked: Set }

  const summary = () => {
    const chosen = [...stories.values()].filter((s) => s.checked || s.picked.size);
    const cases = chosen.reduce((n, s) => n + (s.cases ? s.picked.size : 0), 0);
    const btn = $("genBtn");
    btn.disabled = !chosen.length;
    if (!chosen.length) { $("summary").textContent = "Nothing selected yet."; btn.textContent = "Generate Automation"; return; }
    $("summary").textContent = `${chosen.length} User Stor${chosen.length === 1 ? "y" : "ies"}, ${cases} Test Case(s) selected.`;
    btn.textContent = cases === 1 ? "Generate Automation" : chosen.length > 1 ? `Generate Automation for ${chosen.length} User Stories` : `Generate Selected Automation (${cases})`;
  };

  const casesHtml = (s) => !s.cases ? '<p class="muted">Loading Test Cases…</p>' : !s.cases.length ? '<p class="muted">No Test Cases are linked to this User Story in TFS.</p>'
    : `<table class="data"><tbody>${s.cases.map((c) => `<tr><td><input type="checkbox" data-tc="${c.id}" data-story="${s.story.id}" ${s.picked.has(c.id) ? "checked" : ""} aria-label="Test Case ${c.id}"></td>
        <td>TC${c.id}</td><td>${esc(c.title)}</td><td>${esc(c.state || "")}</td>
        <td>${c.existing.length ? c.existing.map((e) => `<a class="pill ${e.file_exists ? "PASSED" : "FAILED"}" href="${link(`script=${sid(e.id)}&tab=view`)}">${e.file_exists ? "Automated" : "File missing"} v${e.version}</a>`).join(" ") : '<span class="muted">Not automated</span>'}</td></tr>`).join("")}
      </tbody></table>${s.criteria?.length ? `<p class="muted">Acceptance Criteria: ${s.criteria.map((a) => esc(`${a.id} ${a.title}`)).join(" · ")}</p>` : ""}`;

  const loadCases = async (s) => {
    if (s.cases) return;
    const r = await api(`${BASE}/stories/${s.story.id}/test-cases`);
    s.cases = r.test_cases;
    s.criteria = r.acceptance_criteria;
    if (s.checked) s.picked = new Set(s.cases.map((c) => c.id));
  };

  const draw = () => {
    const box = $("stories");
    if (!stories.size) { box.innerHTML = '<p class="muted">No User Stories found for this sprint and filter.</p>'; return; }
    box.innerHTML = [...stories.values()].map((s) => `<div class="story ${s.open ? "open" : ""}">
      <div class="row story-head"><input type="checkbox" data-us="${s.story.id}" ${s.checked ? "checked" : ""} aria-label="User Story ${s.story.id}">
        <button class="link-btn" data-open="${s.story.id}" aria-expanded="${!!s.open}">${s.open ? "▾" : "▸"} US${s.story.id}</button>
        <span>${esc(s.story.title)}</span><span class="spacer"></span><span class="muted">${esc(s.story.sprint || "")} · ${esc(s.story.state || "")}</span></div>
      ${s.open ? `<div class="story-cases">${casesHtml(s)}</div>` : ""}</div>`).join("");
    for (const s of stories.values()) {
      const cb = box.querySelector(`[data-us="${s.story.id}"]`);
      if (cb && s.cases && s.picked.size && s.picked.size < s.cases.length) cb.indeterminate = true;
    }
    summary();
  };

  const loadStories = async () => {
    $("stories").innerHTML = '<p class="muted">Loading User Stories from TFS…</p>';
    try {
      const { items } = await api(`${BASE}/stories?${new URLSearchParams({ sprint: $("sprint").value, text: $("text").value.trim() })}`);
      stories.clear();
      items.forEach((story) => stories.set(story.id, { story, checked: false, cases: null, picked: new Set(), open: false }));
      draw();
    } catch (err) { $("stories").innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; }
  };
  $("load").addEventListener("click", loadStories);
  $("text").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); loadStories(); } });
  $("sprint").addEventListener("change", loadStories);

  pane.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-open]");
    if (!btn) return;
    const s = stories.get(Number(btn.dataset.open));
    s.open = !s.open;
    draw();
    try { await loadCases(s); } catch (err) { toast(err.message, "error"); }
    draw();
  });
  pane.addEventListener("change", async (e) => {
    if (e.target.dataset.us) {
      const s = stories.get(Number(e.target.dataset.us));
      s.checked = e.target.checked;
      try { await loadCases(s); } catch (err) { toast(err.message, "error"); }
      s.picked = s.checked ? new Set((s.cases || []).map((c) => c.id)) : new Set();
      draw();
    } else if (e.target.dataset.tc) {
      const s = stories.get(Number(e.target.dataset.story));
      const id = Number(e.target.dataset.tc);
      e.target.checked ? s.picked.add(id) : s.picked.delete(id);
      s.checked = s.picked.size > 0 && s.picked.size === s.cases.length;
      draw();
    }
  });

  $("settings").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (!/^https?:\/\//i.test(f.base_url.trim())) { toast("The application URL must start with http:// or https://.", "warning"); return; }
    storage("settings", { ...f, sprint: $("sprint").value });
    const chosen = [...stories.values()].filter((s) => s.checked || s.picked.size);
    const selections = chosen.map((s) => (s.cases && s.picked.size < s.cases.length
      ? { user_story_id: s.story.id, test_case_ids: [...s.picked] } : { user_story_id: s.story.id }));
    const existing = chosen.flatMap((s) => (s.cases || []).filter((c) => s.picked.has(c.id) && c.existing.length).map((c) => ({ ...c, story: s.story.id })));
    let onExisting = "skip";
    if (existing.length) {
      onExisting = await duplicateDialog(existing);
      if (!onExisting) return;
      if (onExisting === "open") { go(`script=${sid(existing[0].existing[0].id)}&tab=view`); return; }
    }
    try {
      const r = await api(`${BASE}/jobs`, { method: "POST", body: {
        selections, base_url: f.base_url.trim(), application: f.application || null, environment: f.environment || null,
        credential_role: f.credential_role || null, discovery_id: f.discovery_id || null, on_existing: onExisting } });
      go(`job=${sid(r.job_id)}`);
    } catch (err) { toast(err.message, "error"); }
  });
  await loadStories();
}

function duplicateDialog(existing) {
  return new Promise((resolve) => {
    const one = existing.length === 1;
    const m = modal("Automation already exists", `
      <p>${one ? "This Test Case already has" : `${existing.length} selected Test Cases already have`} generated automation:</p>
      <ul>${existing.slice(0, 8).map((c) => `<li>TC${c.id} ${esc(c.title)} (${c.existing.map((x) => `v${x.version}`).join(", ")})</li>`).join("")}
        ${existing.length > 8 ? `<li>and ${existing.length - 8} more</li>` : ""}</ul>
      <div class="choices">
        ${one ? '<button class="btn" data-v="open">Open Existing</button>' : ""}
        <button class="btn" data-v="regenerate" title="Update the existing scripts; only what changed is rewritten and the old version stays in History">Regenerate</button>
        <button class="btn" data-v="new_version" title="Keep the existing scripts and create additional ones">Create New Version</button>
        ${one ? "" : '<button class="btn" data-v="skip" title="Generate only the Test Cases that have no automation">Skip existing</button>'}
        <button class="btn" data-v="">Cancel</button></div>`);
    let answered = false;
    m.onClose(() => { if (!answered) { answered = true; resolve(null); } });
    m.el.querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", () => { answered = true; resolve(b.dataset.v || null); m.close(); }));
  });
}

// ---- generation jobs ------------------------------------------------------------------------

async function jobsPane(pane) {
  const { items } = await api(`${BASE}/jobs?limit=50`);
  pane.innerHTML = `<div class="card">${items.length ? `<table class="data"><thead><tr><th>Job</th><th>Action</th><th>Status</th><th>Progress</th><th>Started</th><th>Finished</th></tr></thead><tbody>
    ${items.map((j) => `<tr class="clickable" data-job="${esc(j.id)}"><td><code>${esc(j.id)}</code></td><td>${esc(j.action === "regenerate" ? "Regenerate / Update" : "Generate")}</td>
      <td>${pill(j.status)}</td><td>${j.summary.completed}/${j.summary.total} done${j.summary.failed ? `, ${j.summary.failed} failed` : ""}</td>
      <td>${esc(dateTimeOf(j.created_at))}</td><td>${esc(dateTimeOf(j.finished_at))}</td></tr>`).join("")}</tbody></table>`
    : '<p class="muted">No generation jobs yet.</p>'}</div>`;
  pane.querySelectorAll("[data-job]").forEach((tr) => tr.addEventListener("click", () => go(`job=${sid(tr.dataset.job)}`)));
}

async function jobView(view, jobId) {
  view.innerHTML = `<p><a href="${link("tab=jobs")}">← Generation Jobs</a> · <a href="${link("tab=scripts")}">Script Inventory</a></p><div id="job"><p class="muted">Loading…</p></div>`;
  const box = view.querySelector("#job");
  let job = null;
  const render = () => {
    const s = job.summary;
    box.innerHTML = `<div class="card">
      <div class="row"><h2 style="margin:0">${job.action === "regenerate" ? "Regenerate / Update" : "Generate Automation"}</h2>${pill(job.status)}
        <span class="muted"><code>${esc(job.id)}</code> · started ${esc(dateTimeOf(job.created_at))}${job.finished_at ? ` · finished ${esc(dateTimeOf(job.finished_at))}` : ""}</span>
        <span class="spacer"></span>${FINAL_JOB.has(job.status) ? `<a class="btn primary" href="${link("tab=scripts")}">Open Script Inventory</a>` : '<button class="btn" id="stop">Stop</button>'}</div>
      <div class="bar" role="progressbar" aria-valuenow="${s.progress}" aria-valuemin="0" aria-valuemax="100"><span style="width:${s.progress}%"></span></div>
      <p><strong>${s.progress}%</strong> · Completed ${s.completed} · Running ${s.running} · Waiting ${s.waiting} · Failed ${s.failed}${s.stopped ? ` · Stopped ${s.stopped}` : ""} · Total ${s.total}</p>
      ${job.error ? `<p class="pill FAIL">${esc(job.error_code || "")} ${esc(job.error)}</p>` : ""}
      ${job.status === "STOPPED" ? '<p class="muted">Generation was stopped. Scripts that finished before Stop are kept.</p>' : ""}</div>
      ${job.stories.map((st) => `<div class="card" style="margin-top:12px">
        <div class="row"><h3 style="margin:0">${st.user_story_id ? `US${esc(st.user_story_id)} ${esc(st.user_story_title || "")}` : "Selected Test Cases"}</h3><span class="spacer"></span>
          <span class="muted">${st.generated}/${st.total} done${st.failed ? ` · ${st.failed} failed` : ""}${st.running ? " · running" : ""}</span></div>
        <table class="data"><thead><tr><th>Test Case</th><th>Title</th><th>Status</th><th>Result</th></tr></thead><tbody>
        ${job.items.filter((i) => (i.user_story_id || "-") === (st.user_story_id || "-")).map((i) => `<tr>
          <td>TC${esc(i.test_case_id)}</td><td>${esc(i.test_case_title || "")}</td><td>${pill(i.status)}</td>
          <td>${i.script_id && !["FAILED", "STOPPED"].includes(i.status) ? `<a href="${link(`script=${sid(i.script_id)}&tab=view`)}">View Script</a> · ` : ""}
            ${i.details?.file ? `<code>${esc(baseName(i.details.file))}</code> ${i.details.file_verified ? '<span class="muted">(file verified)</span>' : ""}` : ""}
            ${i.error ? `<div class="error-text">${esc(i.error)}</div>` : ""}
            <span class="actions">${i.status === "FAILED" || i.status === "STOPPED" ? `<button class="link-btn" data-retry="${i.id}">Retry</button>` : ""}
            ${i.details || i.error ? `<button class="link-btn" data-details="${i.id}">View Details</button>` : ""}
            ${i.log?.length ? `<button class="link-btn" data-log="${i.id}">Open Logs</button>` : ""}</span></td></tr>`).join("")}
        </tbody></table></div>`).join("")}`;
    box.querySelector("#stop")?.addEventListener("click", async () => {
      try { await api(`${BASE}/jobs/${sid(jobId)}/stop`, { method: "POST", body: {} }); toast("Stopping after the current Test Case…", "info"); }
      catch (err) { toast(err.message, "error"); }
    });
  };
  box.addEventListener("click", async (e) => {
    const item = (attr) => job.items.find((i) => String(i.id) === e.target.closest(`[${attr}]`)?.getAttribute(attr));
    if (e.target.closest("[data-retry]")) {
      try { const r = await api(`${BASE}/job-items/${item("data-retry").id}/retry`, { method: "POST", body: {} }); go(`job=${sid(r.job_id)}`); }
      catch (err) { toast(err.message, "error"); }
    } else if (e.target.closest("[data-details]")) {
      const i = item("data-details"), d = i.details || {};
      const checks = d.validation || d.details;
      modal(`TC${i.test_case_id}: details`, `
        ${i.error ? `<p class="pill FAIL">${esc(i.error_code || "")}</p><p>${esc(i.error)}</p>` : ""}
        ${Array.isArray(checks) ? checksHtml(checks) : d.details ? `<pre class="code">${esc(JSON.stringify(d.details, null, 2))}</pre>` : ""}
        ${d.components?.length ? `<h4>Framework components</h4><ul>${d.components.map((c) => `<li>${esc(c.type.replace("_", " "))} <strong>${esc(c.name)}</strong> (${esc(c.module)}): ${esc(c.change)}${c.added?.length ? ` · added ${esc(c.added.join(", "))}` : ""}</li>`).join("")}</ul>` : ""}
        ${d.impact?.length ? `<h4>Impact analysis</h4><ul>${d.impact.map((x) => `<li><strong>${esc(x.component)}</strong> is used by ${x.affected_tests.length} test(s). ${esc(x.risk)}</li>`).join("")}</ul>` : ""}
        ${d.files?.length ? `<h4>Files saved</h4><ul>${d.files.map((f) => `<li><code>${esc(f)}</code></li>`).join("")}</ul>` : ""}
        ${d.notes?.length ? `<h4>Notes for review</h4><ul>${d.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}`, { wide: true });
    } else if (e.target.closest("[data-log]")) {
      const i = item("data-log");
      modal(`TC${i.test_case_id}: generation log`, `<pre class="code">${esc(i.log.join("\n"))}</pre>`, { wide: true });
    }
  });
  const refresh = async () => {
    try { job = (await api(`${BASE}/jobs/${sid(jobId)}`)).job; render(); } catch (err) { box.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; stop(); return; }
    if (FINAL_JOB.has(job.status)) stop();
  };
  const stop = every(1000, refresh);
  await refresh();
}

// ---- one script -------------------------------------------------------------------------------

async function scriptView(view, scriptId, tab, runId) {
  let data = await api(`${BASE}/scripts/${sid(scriptId)}`);
  const s = data.script;
  const ac = (s.acceptance_criteria || []).map((a) => `${a.id} ${a.title}`).join(" · ");
  view.innerHTML = `<p><a href="${link("tab=scripts")}">← Script Inventory</a></p>
    <div class="card">
      <div class="row" style="flex-wrap:wrap"><h2 style="margin:0">TC${esc(s.test_case_id)} ${esc(s.test_case_title || "")}</h2>${pill(s.status)}
        <span class="muted">Last result</span><span id="lastResult">${pill(s.last_result)}</span><span class="spacer"></span>
        <button class="btn primary" id="play" ${s.file_verified ? "" : "disabled"}>${s.last_run_id ? "Play Again" : "Play"}</button>
        <button class="btn" id="stop" hidden>Stop</button>
        <button class="btn" id="regen">Regenerate / Update</button><button class="link-btn" id="archive">Archive</button></div>
      <dl class="meta">
        <dt>Script</dt><dd><code>${esc(s.file)}</code> ${s.file_verified ? '<span class="muted">(file verified)</span>' : '<span class="pill FAIL">FILE MISSING</span>'}</dd>
        <dt>User Story</dt><dd>${s.user_story_id ? `US${esc(s.user_story_id)} ${esc(s.user_story_title || "")}` : "-"}</dd>
        <dt>Acceptance Criteria</dt><dd>${esc(ac || "-")}</dd>
        <dt>Sprint</dt><dd>${esc(s.sprint || "-")}</dd>
        <dt>Generated</dt><dd>${esc(dateTimeOf(s.generated_at))} · v${s.version}${s.updated_at !== s.generated_at ? ` · updated ${esc(dateTimeOf(s.updated_at))}` : ""}</dd>
        <dt>Automation method</dt><dd>${esc(s.method_name)} (Python + Playwright + Page Object Model)</dd>
        <dt>Application</dt><dd>${esc(s.base_url || "")}${s.application ? ` · ${esc(s.application)}` : ""}${s.environment ? ` · ${esc(s.environment)}` : ""}</dd></dl>
      ${(s.warnings || []).map((w) => `<p class="pill WARN">${esc(w)}</p>`).join("")}
      ${s.notes ? `<details><summary class="muted">Notes from generation</summary><pre class="code">${esc(s.notes)}</pre></details>` : ""}
    </div>
    <div class="card live" id="live" hidden></div>
    <div class="tabs" role="tablist" style="margin-top:12px">${TABS.map(([k, l]) => `<a class="tab ${k === tab ? "active" : ""}" role="tab" aria-selected="${k === tab}"
      href="${link(`script=${sid(scriptId)}&tab=${k}${runId && ["results", "evidence", "logs"].includes(k) ? `&run=${sid(runId)}` : ""}`)}">${l}</a>`).join("")}</div>
    <div id="tabPane" class="card"></div>`;
  const pane = view.querySelector("#tabPane");
  const reload = async () => { data = await api(`${BASE}/scripts/${sid(scriptId)}`); renderTab(); };
  const renderTab = async () => {
    try {
      const fn = { view: viewTab, edit: editTab, flow: flowTab, data: dataTab, credentials: credentialsTab, components: componentsTab,
        history: historyTab, results: resultsTab, evidence: evidenceTab, logs: logsTab }[tab] || viewTab;
      await fn(pane, scriptId, data, reload, runId);
    } catch (err) { pane.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; }
  };

  // Play / Play Again / Stop, with the live Test Execution Agent status.
  const playBtn = view.querySelector("#play"), stopBtn = view.querySelector("#stop"), live = view.querySelector("#live");
  let liveRun = null;
  const startLive = (run) => {
    liveRun = { ...run, started: new Date().toISOString(), step: "Starting the browser", agent: "Test Execution Agent", agentStatus: "RUNNING" };
    playBtn.disabled = true; stopBtn.hidden = false; live.hidden = false;
    view.querySelector("#lastResult").innerHTML = pill("RUNNING");
    drawLive();
  };
  const drawLive = () => {
    if (!liveRun) return;
    live.innerHTML = `<div class="row"><span class="agent-dot ${liveRun.agentStatus === "RUNNING" ? "on" : ""}"></span>
      <strong>${esc(liveRun.agent)} - ${esc(liveRun.agentStatus)}</strong><span class="spacer"></span><code>${esc(liveRun.runId)}</code></div>
      <dl class="meta"><dt>Test Case</dt><dd>TC${esc(s.test_case_id)}</dd><dt>Execution Mode</dt><dd>${esc(liveRun.mode)}</dd>
      <dt>Current Step</dt><dd>${esc(liveRun.step)}</dd><dt>Elapsed Time</dt><dd>${esc(elapsed(liveRun.started))}</dd></dl>`;
  };
  const finishLive = async (status) => {
    if (!liveRun) return;
    const id = liveRun.runId;
    liveRun = null;
    live.hidden = true; stopBtn.hidden = true; playBtn.disabled = !s.file_verified; playBtn.textContent = "Play Again";
    view.querySelector("#lastResult").innerHTML = pill(status);
    toast(`TC${s.test_case_id}: ${status}`, status === "PASSED" ? "success" : "warning");
    go(`script=${sid(scriptId)}&tab=results&run=${sid(id)}`);
  };
  playBtn.addEventListener("click", async () => {
    try { const r = await play(scriptId, (data.data?.combinations || []).map((c) => c.name)); if (r) startLive(r); }
    catch (err) { toast(errorText(err), "error"); }
  });
  stopBtn.addEventListener("click", () => liveRun && api(`${BASE}/runs/${sid(liveRun.runId)}/stop`, { method: "POST", body: {} })
    .then(() => { liveRun.step = "Stopping and keeping the evidence captured so far"; drawLive(); })
    .catch((err) => toast(err.message, "error")));
  onCleanup(subscribe((c) => {
    if (c.type !== "event" || !liveRun || c.event.run_id !== liveRun.runId) return;
    const ev = c.event;
    if (ev.name === "execution.step") liveRun.step = (ev.activity || "").replace(/^Step \d+: /, "");
    if (ev.agent_id === "failure_analysis") {
      liveRun.agent = "Failure & Quality Analysis Agent";
      liveRun.agentStatus = ev.name === "agent.started" || ev.name === "agent.progress" ? "RUNNING" : ev.name === "agent.failed" ? "FAILED" : "COMPLETED";
      liveRun.step = ev.activity || liveRun.step;
    }
    drawLive();
  }));
  every(1000, drawLive);
  every(2500, async () => {
    if (!liveRun) return;
    try {
      const r = (await api(`${BASE}/scripts/${sid(scriptId)}/runs/${sid(liveRun.runId)}`)).result;
      if (!r.running && r.status && r.status !== "RUNNING") finishLive(r.status);
    } catch { /* the result row appears as soon as the run starts */ }
  });
  if (params().get("live") && runId) startLive({ runId, mode: "" });

  view.querySelector("#regen").addEventListener("click", async () => {
    if (!confirm("Regenerate this automation from the current Test Case? Only what changed is updated, and the current version stays in History.")) return;
    try { const r = await api(`${BASE}/scripts/${sid(scriptId)}/regenerate`, { method: "POST", body: {} }); go(`job=${sid(r.job_id)}`); }
    catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#archive").addEventListener("click", async () => {
    if (!confirm("Archive this script? It leaves the inventory; its files, versions and evidence are kept.")) return;
    try { await api(`${BASE}/scripts/${sid(scriptId)}`, { method: "DELETE" }); go("tab=scripts"); } catch (err) { toast(err.message, "error"); }
  });
  await renderTab();
}

function viewTab(pane, scriptId, data) {
  pane.innerHTML = data.source == null ? '<p class="pill FAIL">The script file does not exist. Regenerate it or restore a version from History.</p>'
    : `<div class="row"><strong><code>${esc(data.script.file_name)}</code></strong><span class="muted">${data.source.split("\n").length} lines · v${data.script.version}</span>
      <span class="spacer"></span><button class="btn" id="copy">Copy</button><a class="btn" href="${link(`script=${sid(scriptId)}&tab=edit`)}">Edit Script</a></div>${codeView(data.source)}`;
  pane.querySelector("#copy")?.addEventListener("click", () => navigator.clipboard?.writeText(data.source).then(() => toast("Copied", "success")));
}

function editTab(pane, scriptId, data, reload) {
  pane.innerHTML = `<div class="row"><strong><code>${esc(data.script.file_name)}</code></strong><span class="spacer"></span>
      <input id="reason" placeholder="What changed (kept in History)" style="min-width:240px">
      <button class="btn" id="revert">Revert</button><button class="btn primary" id="save">Save Changes</button></div>
    <p class="muted">Before saving, syntax, indentation, imports, Page Object, flow, fixture and test data references are validated in a copy of the
      framework; the current version is backed up first. A script that fails validation is not saved.</p>
    <textarea id="src" class="code-edit" spellcheck="false" aria-label="Python script">${esc(data.source || "")}</textarea><div id="out"></div>`;
  const ta = pane.querySelector("#src");
  ta.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    e.preventDefault();
    const { selectionStart: a, selectionEnd: b } = ta;
    ta.setRangeText("    ", a, b, "end");
  });
  pane.querySelector("#revert").addEventListener("click", () => { ta.value = data.source || ""; pane.querySelector("#out").innerHTML = ""; });
  pane.querySelector("#save").addEventListener("click", async () => {
    const out = pane.querySelector("#out");
    try {
      const r = await api(`${BASE}/scripts/${sid(scriptId)}/source`, { method: "PUT", body: { source: ta.value, reason: pane.querySelector("#reason").value || null } });
      toast(r.saved ? `Saved as version ${r.script.script.version}` : "No changes to save", "success");
      out.innerHTML = r.validation ? checksHtml(r.validation) : "";
      await reload();
    } catch (err) {
      out.innerHTML = `<p class="pill FAIL">Not saved</p><p>${esc(err.message)}</p>${checksHtml(Array.isArray(err.details) ? err.details : [])}`;
    }
  });
}

function flowTab(pane, scriptId, data, reload) {
  const original = data.flow_logic.map((st, i) => ({ ...st, orig: i }));
  let items = original.map((x) => ({ ...x }));
  const ops = [];
  const cat = data.catalog || { page_objects: [], flows: [] };
  const fields = Object.keys((data.data?.combinations || [])[0]?.values || {});
  const options = [...cat.flows.map((f) => [`flow:${f.name}`, `Flow: ${f.name}(${f.params.join(", ")})`, f.params]),
    ...cat.page_objects.flatMap((p) => p.methods.map((m) => [`${p.name}.${m.name}`, `${p.name}.${m.name}(${m.params.join(", ")})${m.doc ? ` - ${m.doc}` : ""}`, m.params]))];
  const draw = () => {
    pane.innerHTML = `<p class="muted">The business steps of the test, in order, with the page actions each performs. Rename, reorder, remove or add steps, then
      save: the Python script is rewritten, validated and versioned.</p>
      <ol class="flow">${items.map((st, i) => `<li>
        <div class="row"><input value="${esc(st.title)}" data-title="${i}" aria-label="Step ${i + 1} title" style="flex:1">
          ${st.checkpoint ? '<span class="pill WAITING">Check</span>' : ""}${st.tc_step ? `<span class="muted">TC step ${esc(st.tc_step)}</span>` : ""}
          <button class="link-btn" data-up="${i}" ${i ? "" : "disabled"} title="Move up">↑</button>
          <button class="link-btn" data-down="${i}" ${i < items.length - 1 ? "" : "disabled"} title="Move down">↓</button>
          <button class="link-btn" data-del="${i}" title="Remove step">✕</button></div>
        ${st.actions?.length ? `<ul class="muted">${st.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : ""}
        ${st.code?.length ? `<code class="muted small">${esc(st.code.join("; "))}</code>` : st.isNew ? '<span class="pill WAITING">New step</span>' : ""}</li>`).join("")}</ol>
      <details class="card-sub"><summary>Add a step</summary>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">
          <label>Title <input id="nTitle" placeholder="e.g. Verify the order total"></label>
          <label>Action <select id="nCall">${options.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select></label>
          <label>Position <input id="nPos" type="number" min="1" max="${items.length + 1}" value="${items.length + 1}"></label>
          <label class="row"><input type="checkbox" id="nCheck"> Checkpoint (screenshot as evidence)</label></div>
        <div id="nArgs" class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))"></div>
        <button class="btn" id="nAdd">Add step</button></details>
      <div class="row" style="margin-top:12px"><span class="spacer"></span><button class="btn" id="reset">Discard changes</button>
        <button class="btn primary" id="save" ${ops.length || items.some((x) => x.title !== x.origTitle) ? "" : ""}>Save Flow Logic</button></div><div id="out"></div>`;
    const argsFor = () => {
      const opt = options.find(([v]) => v === pane.querySelector("#nCall").value);
      pane.querySelector("#nArgs").innerHTML = (opt?.[2] || []).map((p) => `<label>${esc(p)} <input data-arg list="argList" placeholder="text, data:field or credentials.username"></label>`).join("")
        + `<datalist id="argList">${fields.map((f) => `<option value="data:${esc(f)}">`).join("")}<option value="credentials.username"><option value="credentials.password"></datalist>`;
    };
    pane.querySelector("#nCall")?.addEventListener("change", argsFor);
    if (options.length) argsFor();
  };
  original.forEach((x) => { x.origTitle = x.title; });
  items.forEach((x) => { x.origTitle = x.title; });
  draw();
  pane.addEventListener("input", (e) => { if (e.target.dataset.title !== undefined) items[Number(e.target.dataset.title)].title = e.target.value; });
  pane.addEventListener("click", async (e) => {
    const t = e.target;
    if (t.dataset.up !== undefined || t.dataset.down !== undefined) {
      const i = Number(t.dataset.up ?? t.dataset.down), j = t.dataset.up !== undefined ? i - 1 : i + 1;
      ops.push({ op: "move", index: i, to: j });
      [items[i], items[j]] = [items[j], items[i]];
      draw();
    } else if (t.dataset.del !== undefined) {
      const i = Number(t.dataset.del);
      if (items.length === 1) { toast("Keep at least one step.", "warning"); return; }
      ops.push({ op: "remove", index: i });
      items.splice(i, 1);
      draw();
    } else if (t.id === "nAdd") {
      const title = pane.querySelector("#nTitle").value.trim(), call = pane.querySelector("#nCall").value;
      if (!title || !call) { toast("A new step needs a title and an action.", "warning"); return; }
      const pos = Math.max(0, Math.min(items.length, Number(pane.querySelector("#nPos").value) - 1));
      const args = [...pane.querySelectorAll("[data-arg]")].map((i) => i.value);
      ops.push({ op: "add", index: pos, title, call, args, checkpoint: pane.querySelector("#nCheck").checked });
      items.splice(pos, 0, { title, origTitle: title, isNew: true, actions: [call.replace("flow:", "Flow ")], code: [] });
      draw();
    } else if (t.id === "reset") {
      ops.length = 0; items = original.map((x) => ({ ...x })); draw();
    } else if (t.id === "save") {
      const renames = items.map((x, i) => (!x.isNew && x.title !== x.origTitle ? { op: "rename", index: i, title: x.title } : null)).filter(Boolean);
      const all = [...ops, ...renames];
      if (!all.length) { toast("No changes to save.", "info"); return; }
      try { await api(`${BASE}/scripts/${sid(scriptId)}/flow`, { method: "POST", body: { operations: all } }); toast("Flow Logic saved as a new version", "success"); await reload(); }
      catch (err) { pane.querySelector("#out").innerHTML = `<p class="pill FAIL">Not saved</p><p>${esc(err.message)}</p>${checksHtml(Array.isArray(err.details) ? err.details : [])}`; }
    }
  });
}

function dataTab(pane, scriptId, data, reload) {
  const d = data.data;
  if (!d) { pane.innerHTML = '<p class="muted">This script has no test data file.</p>'; return; }
  let combos = JSON.parse(JSON.stringify(d.combinations || []));
  const labels = d.fields || {};
  const fieldNames = () => [...new Set(combos.flatMap((c) => Object.keys(c.values || {})))];
  const draw = () => {
    const names = fieldNames();
    pane.innerHTML = `<p class="muted">The test reads these values from <code>${esc(data.script.data_file)}</code> and runs once per combination. Use
      ⏱ for date/time rules that are worked out from the Windows system clock when the test runs. Passwords are never stored here; see Credentials.</p>
      <div style="overflow-x:auto"><table class="data data-grid"><thead><tr><th>Field</th>
        ${combos.map((c, i) => `<th><input value="${esc(c.name)}" data-name="${i}" aria-label="Combination name">
          ${combos.length > 1 ? `<button class="link-btn" data-drop="${i}" title="Remove combination">✕</button>` : ""}</th>`).join("")}</tr></thead>
      <tbody>${names.map((f) => `<tr><th>${esc(labels[f]?.label || f)}<div class="muted small"><code>${esc(f)}</code></div></th>
        ${combos.map((c, i) => { const v = c.values?.[f] ?? ""; return `<td><div class="row" style="gap:4px;flex-wrap:nowrap">
          <input value="${esc(typeof v === "string" ? v : JSON.stringify(v))}" data-f="${esc(f)}" data-c="${i}" style="flex:1;min-width:120px">
          <button class="link-btn" data-rule="${esc(f)}" data-rc="${i}" title="Date / time rule">⏱</button></div>
          ${/\{\{/.test(String(v)) ? `<div class="muted small">today: ${esc(previewRule(String(v)))}</div>` : ""}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="row" style="margin-top:10px"><button class="btn" id="addCombo">Add combination</button><button class="btn" id="addField">Add field</button>
        <span class="spacer"></span><button class="btn" id="reset">Discard changes</button><button class="btn primary" id="save">Save Test Data</button></div><div id="out"></div>`;
  };
  const read = () => {
    pane.querySelectorAll("[data-name]").forEach((i) => { combos[Number(i.dataset.name)].name = i.value; });
    pane.querySelectorAll("[data-f]").forEach((i) => { combos[Number(i.dataset.c)].values[i.dataset.f] = i.value; });
  };
  draw();
  pane.addEventListener("click", async (e) => {
    const t = e.target;
    if (t.dataset.rule) {
      read();
      const c = Number(t.dataset.rc), f = t.dataset.rule;
      ruleBuilder(combos[c].values[f], (rule) => { combos[c].values[f] = rule; draw(); });
    } else if (t.dataset.drop) { read(); combos.splice(Number(t.dataset.drop), 1); draw(); }
    else if (t.id === "addCombo") { read(); combos.push({ name: `Combination ${combos.length + 1}`, values: { ...(combos[0]?.values || {}) } }); draw(); }
    else if (t.id === "addField") {
      const name = (prompt("Field name (letters, digits and _)") || "").trim();
      if (!/^\w+$/.test(name)) return;
      read(); combos.forEach((c) => { c.values[name] = c.values[name] ?? ""; }); draw();
    } else if (t.id === "reset") { combos = JSON.parse(JSON.stringify(d.combinations || [])); draw(); }
    else if (t.id === "save") {
      read();
      try { await api(`${BASE}/scripts/${sid(scriptId)}/data`, { method: "PUT", body: { combinations: combos } }); toast("Test Data saved as a new version", "success"); await reload(); }
      catch (err) { pane.querySelector("#out").innerHTML = `<p class="pill FAIL">Not saved</p><p>${esc(err.message)}</p>`; }
    }
  });
}

function credentialsTab(pane, scriptId, data, reload) {
  const c = data.credentials;
  if (!c) { pane.innerHTML = '<p class="muted">This script does not sign in, so it needs no credentials.</p>'; return; }
  const source = { script: "This script's own credentials (override)", environment: `Application credentials for ${c.environment}`, application: "Application credentials" }[c.source] || "None saved yet";
  pane.innerHTML = `<p>Used at run time: <strong>${esc(source)}</strong> · user <strong>${esc(c.username || "-")}</strong> · password ${c.has_password ? "saved" : '<span class="pill WARN">missing</span>'}</p>
    <p class="muted">Passwords are kept in the Windows Credential Manager and passed to the test at run time; they are never written into the script, its data or the logs.
      Credentials saved for this script override the application credentials only for this script; other applications and scripts are not changed.</p>
    ${c.store_available ? "" : '<p class="pill WARN">The Windows Credential Manager is not available on this PC, so passwords cannot be saved.</p>'}
    <form id="cred" class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      <fieldset style="grid-column:1/-1"><legend>Save for</legend>
        <label class="row"><input type="radio" name="scope" value="script" ${c.source !== "application" && c.source !== "environment" ? "checked" : ""}> Only this script</label>
        <label class="row"><input type="radio" name="scope" value="application" ${c.source === "application" || c.source === "environment" ? "checked" : ""}> The application (every script of this application and environment)</label></fieldset>
      <label>Application <input name="application" value="${esc(c.application || "")}"></label>
      <label>Environment <input name="environment" value="${esc(c.environment || "")}" placeholder="optional"></label>
      <label>Role <input name="role" value="${esc(c.role || "")}"></label>
      <label>Username <input name="username" value="${esc(c.username || "")}" autocomplete="off"></label>
      <label>Password <input name="password" type="password" autocomplete="new-password" placeholder="${c.has_password ? "saved; leave blank to keep" : ""}"></label>
      <div class="row" style="align-self:end"><button class="btn primary" type="submit">Save credentials</button>
        ${c.script_override ? '<button class="btn" type="button" id="clear">Use the application credentials</button>' : ""}</div></form>`;
  pane.querySelector("#cred").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      await api(`${BASE}/scripts/${sid(scriptId)}/credentials`, { method: "PUT", body: { scope: f.scope, username: f.username || null,
        password: f.password || null, application: f.application, environment: f.environment, role: f.role || null } });
      toast("Credentials saved", "success"); await reload();
    } catch (err) { toast(err.message, "error"); }
  });
  pane.querySelector("#clear")?.addEventListener("click", async () => {
    try { await api(`${BASE}/scripts/${sid(scriptId)}/credentials`, { method: "PUT", body: { scope: "clear_script" } }); toast("This script now uses the application credentials", "success"); await reload(); }
    catch (err) { toast(err.message, "error"); }
  });
}

function componentsTab(pane, scriptId, data, reload) {
  pane.innerHTML = data.components.length ? `<p class="muted">Shared framework components this test uses. A change to a component affects every test that uses it, so
      impact analysis runs and all those tests are validated before the change is saved.</p>
    <table class="data"><thead><tr><th>Component</th><th>Type</th><th>File</th><th>Used by</th><th></th></tr></thead><tbody>
    ${data.components.map((c) => `<tr><td><strong>${esc(c.name)}</strong><div class="muted">${esc(c.doc || "")}</div></td><td>${c.type === "flow" ? "Business flow" : "Page Object"}</td>
      <td><code>${esc(c.module)}</code></td><td>${c.used_by.length} test(s)</td><td><button class="btn" data-open="${esc(c.module)}">Open</button></td></tr>`).join("")}</tbody></table>`
    : '<p class="muted">No shared components were found for this script.</p>';
  pane.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", async () => {
    try {
      const comp = await api(`${BASE}/components?path=${encodeURIComponent(b.dataset.open)}`);
      const m = modal(comp.path, `<p class="muted">Used by ${comp.affected_tests.length} test(s): ${comp.affected_tests.map((t) => `<code>${esc(baseName(t))}</code>`).join(", ") || "none"}.</p>
        <textarea class="code-edit" id="csrc" spellcheck="false">${esc(comp.source)}</textarea><div id="cout"></div>
        <div class="row"><span class="spacer"></span><button class="btn" data-close>Close</button><button class="btn primary" id="csave">Save component</button></div>`, { wide: true });
      m.el.querySelector("#csave").addEventListener("click", async () => {
        try {
          const r = await api(`${BASE}/components`, { method: "PUT", body: { path: comp.path, source: m.el.querySelector("#csrc").value } });
          toast("Component saved; every test that uses it was validated", "success");
          m.el.querySelector("#cout").innerHTML = checksHtml(r.validation);
          await reload();
        } catch (err) { m.el.querySelector("#cout").innerHTML = `<p class="pill FAIL">Not saved</p><p>${esc(err.message)}</p>`; }
      });
    } catch (err) { toast(err.message, "error"); }
  }));
}

function historyTab(pane, scriptId, data, reload) {
  const current = data.script.version;
  pane.innerHTML = `<table class="data"><thead><tr><th>Version</th><th>Change</th><th>When</th><th></th></tr></thead><tbody>
    ${data.versions.slice().reverse().map((v) => `<tr><td>v${v.version}</td><td>${esc(v.reason)}</td><td>${esc(dateTimeOf(v.created_at))}</td>
      <td class="actions"><button class="link-btn" data-view="${v.version}">View</button>
      ${v.version !== current ? `<button class="link-btn" data-compare="${v.version}">Compare with current</button><button class="link-btn" data-restore="${v.version}">Restore</button>` : '<span class="muted">current</span>'}</td></tr>`).join("")}</tbody></table>`;
  pane.addEventListener("click", async (e) => {
    const t = e.target;
    try {
      if (t.dataset.view) {
        const v = await api(`${BASE}/scripts/${sid(scriptId)}/versions/${t.dataset.view}`);
        modal(`Version ${v.version}: ${v.reason}`, `${v.source != null ? codeView(v.source) : ""}${v.data ? `<h4>Test data</h4><pre class="code">${esc(v.data)}</pre>` : ""}`, { wide: true });
      } else if (t.dataset.compare) {
        const d = await api(`${BASE}/scripts/${sid(scriptId)}/compare?a=${t.dataset.compare}`);
        modal(`Version ${d.left} compared with the current script`, `<h4>Script</h4>${diffView(d.script_diff)}<h4>Test data</h4>${diffView(d.data_diff)}`, { wide: true });
      } else if (t.dataset.restore) {
        if (!confirm(`Restore version ${t.dataset.restore}? It is validated first, and the current version stays in History.`)) return;
        await api(`${BASE}/scripts/${sid(scriptId)}/restore/${t.dataset.restore}`, { method: "POST", body: {} });
        toast(`Version ${t.dataset.restore} restored`, "success");
        await reload();
      }
    } catch (err) { toast(err.message, "error"); }
  });
}

async function runsList(pane, scriptId, tab) {
  const { items } = await api(`${BASE}/scripts/${sid(scriptId)}/results`);
  if (!items.length) { pane.innerHTML = '<p class="muted">This script has not run yet. Choose Play.</p>'; return null; }
  pane.innerHTML = `<table class="data"><thead><tr><th>Run</th><th>Result</th><th>Started</th><th>Finished</th><th></th></tr></thead><tbody>
    ${items.map((r) => `<tr><td><code>${esc(r.run_id)}</code></td><td>${pill(r.status)}</td><td>${esc(dateTimeOf(r.started_at))}</td><td>${esc(dateTimeOf(r.finished_at))}</td>
      <td class="actions"><a href="${link(`script=${sid(scriptId)}&tab=results&run=${sid(r.run_id)}`)}">Result</a>
      <a href="${link(`script=${sid(scriptId)}&tab=evidence&run=${sid(r.run_id)}`)}">Evidence</a>
      <a href="${link(`script=${sid(scriptId)}&tab=logs&run=${sid(r.run_id)}`)}">Logs</a></td></tr>`).join("")}</tbody></table>`;
  return items;
}

function durationText(ms) {
  if (ms == null) return "";
  const s = Math.round(ms / 1000);
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${(ms / 1000).toFixed(1)}s`;
}

async function resultsTab(pane, scriptId, data, reload, runId) {
  if (!runId) { await runsList(pane, scriptId); return; }
  const r = (await api(`${BASE}/scripts/${sid(scriptId)}/runs/${sid(runId)}`)).result;
  const fa = r.failure_analysis;
  const ac = (r.script.acceptance_criteria || []).map((a) => a.id).join(", ");
  pane.innerHTML = `<p><a href="${link(`script=${sid(scriptId)}&tab=results`)}">← All runs</a></p>
    <div class="row"><h3 style="margin:0">Result ${pill(r.running ? "RUNNING" : r.status)}</h3><span class="spacer"></span>
      <a class="btn" href="${link(`script=${sid(scriptId)}&tab=evidence&run=${sid(runId)}`)}">Evidence</a>
      <a class="btn" href="${link(`script=${sid(scriptId)}&tab=logs&run=${sid(runId)}`)}">Logs</a>
      ${(r.evidence?.trace || []).map((t) => `<a class="btn" href="${evidenceUrl(t)}">Trace</a>`).join("")}</div>
    <dl class="meta">
      <dt>Overall Result</dt><dd>${pill(r.running ? "RUNNING" : r.status)}${r.error ? ` <span class="error-text">${esc(r.error)}</span>` : ""}</dd>
      <dt>Execution Mode</dt><dd>${esc(r.execution_mode || "")}</dd><dt>Start</dt><dd>${esc(dateTimeOf(r.started_at))}</dd>
      <dt>Finish</dt><dd>${esc(dateTimeOf(r.finished_at))}</dd><dt>Duration</dt><dd>${esc(durationText(r.duration_ms))}</dd>
      <dt>Test Case</dt><dd>TC${esc(r.script.test_case_id)} ${esc(r.script.test_case_title || "")}</dd>
      <dt>User Story</dt><dd>${r.script.user_story_id ? `US${esc(r.script.user_story_id)} ${esc(r.script.user_story_title || "")}` : "-"}</dd>
      <dt>Acceptance Criteria</dt><dd>${esc(ac || "-")}</dd><dt>Automation Script</dt><dd><code>${esc(r.script.file)}</code></dd>
      <dt>Run</dt><dd><code>${esc(r.run_id)}</code> · <a href="#/history?run=${sid(r.run_id)}">Execution History</a></dd></dl>
    ${fa ? `<div class="analysis ${fa.likely_application_problem ? "app" : ""}"><h4>Failure &amp; Quality Analysis</h4>
      <p><strong>${esc(fa.classification)}</strong>${fa.confidence != null ? ` · confidence ${fa.confidence}%` : ""}${fa.likely_application_problem ? ' · <span class="pill FAIL">Possible application problem</span>' : fa.automation_problem ? ' · <span class="pill WARN">Automation or setup problem</span>' : ""}</p>
      ${fa.failed_step_title ? `<p><strong>Failed step:</strong> Step ${Number(fa.failed_step) + 1} - ${esc(fa.failed_step_title)}</p>` : ""}
      ${fa.expected ? `<p><strong>Expected:</strong> ${esc(fa.expected)}</p>` : ""}${fa.actual ? `<p><strong>Actual:</strong> ${esc(fa.actual)}</p>` : ""}
      ${fa.observed?.length ? `<p><strong>Observed:</strong></p><ul>${fa.observed.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>` : ""}
      ${fa.reason ? `<p><strong>Why:</strong> ${esc(fa.reason)}</p>` : ""}${fa.next_step ? `<p><strong>Next:</strong> ${esc(fa.next_step)}</p>` : ""}
      ${fa.likely_application_problem ? '<p class="muted">A defect draft was added to <a href="#/defect-review">Defect Review</a> for you to confirm; nothing is raised in TFS without review unless automatic creation is on.</p>' : ""}</div>` : ""}
    <h4>Step-by-step result</h4>
    <div style="overflow-x:auto"><table class="data"><thead><tr><th>#</th><th>Step</th><th>Expected result</th><th>Actual result</th><th>Status</th><th>Time</th><th>Duration</th><th>Screenshot</th></tr></thead><tbody>
    ${(r.steps || []).map((st) => `<tr><td>${st.index + 1}</td><td>${esc(st.description)}${st.healed ? `<div class="pill WARN">SELF-HEALED: ${esc(st.healed.original)} → ${esc(st.healed.healed)} (${Math.round(st.healed.confidence * 100)}%)</div>` : ""}</td>
      <td>${esc(st.expected || "")}</td><td>${esc(st.actual || st.error || "")}</td><td>${pill(st.status)}</td>
      <td>${esc(st.started_at ? new Date(st.started_at).toLocaleTimeString([], { hour12: false }) : "")}</td><td>${esc(durationText(st.duration_ms))}</td>
      <td>${st.screenshot ? `<a href="${evidenceUrl(st.screenshot)}" target="_blank" rel="noopener">${st.status === "FAILED" ? "Failure screenshot" : "View"}</a>` : ""}
        ${st.full_page ? ` · <a href="${evidenceUrl(st.full_page)}" target="_blank" rel="noopener">Full page</a>` : ""}</td></tr>`).join("") || '<tr><td colspan="8" class="muted">No steps were recorded.</td></tr>'}
    </tbody></table></div>
    ${r.healing?.length ? `<h4>Self-healing events</h4><table class="data"><thead><tr><th>Step</th><th>Original locator</th><th>Replacement</th><th>Confidence</th><th>Page</th><th>Result</th><th></th></tr></thead><tbody>
      ${r.healing.map((h) => `<tr><td>${h.step_index + 1}</td><td><code>${esc(h.original_selector)}</code></td><td>${h.healed_selector ? `<code>${esc(h.healed_selector)}</code>` : '<span class="muted">none</span>'}</td>
        <td>${Math.round(h.confidence * 100)}%</td><td>${esc(h.page_url || "")}</td><td>${pill(h.result === "failed" ? "FAILED" : h.result === "validated" ? "PASSED" : "WARN")} ${esc(h.result)}</td>
        <td>${h.applied ? '<span class="muted">Applied</span>' : h.result === "validated" ? `<button class="btn" data-apply="${h.id}" title="Update the Page Object locator (the old one is kept as an alternate)">Update Page Object</button>` : ""}</td></tr>`).join("")}</tbody></table>
      <p class="muted">A healed locator never changes the source by itself. It can update the Page Object only after a run using it passed, and only when you choose to.</p>` : ""}`;
  pane.querySelectorAll("[data-apply]").forEach((b) => b.addEventListener("click", async () => {
    try { const res = await api(`/api/automation/healing/${b.dataset.apply}/apply`, { method: "POST", body: {} }); toast(`Page Object updated: ${res.framework?.component || ""}`, "success"); await reload(); }
    catch (err) { toast(err.message, "error"); }
  }));
}

async function latestRun(scriptId, runId) {
  if (runId) return runId;
  const { items } = await api(`${BASE}/scripts/${sid(scriptId)}/results?limit=1`);
  return items[0]?.run_id || null;
}

async function evidenceTab(pane, scriptId, data, reload, runId) {
  const id = await latestRun(scriptId, runId);
  if (!id) { pane.innerHTML = '<p class="muted">No evidence yet: this script has not run.</p>'; return; }
  const r = (await api(`${BASE}/scripts/${sid(scriptId)}/runs/${sid(id)}`)).result;
  const shots = [];
  (r.steps || []).forEach((st) => {
    if (st.screenshot) shots.push({ path: st.screenshot, label: `Step ${st.index + 1} - ${st.description}${st.status === "FAILED" ? " - FAILED" : ""}`, status: st.status });
    if (st.full_page) shots.push({ path: st.full_page, label: `Step ${st.index + 1} - full page at the failure`, status: st.status });
  });
  (r.healing || []).forEach((h) => h.screenshot && shots.push({ path: h.screenshot, label: `Self-healing at step ${h.step_index + 1}`, status: "WARN" }));
  pane.innerHTML = `<div class="row"><h3 style="margin:0">Evidence ${pill(r.status)}</h3><span class="muted"><code>${esc(r.run_id)}</code> · ${esc(r.execution_mode || "")} · ${esc(dateTimeOf(r.started_at))}</span>
      <span class="spacer"></span><a href="${link(`script=${sid(scriptId)}&tab=results&run=${sid(id)}`)}">Result</a></div>
    <p class="muted">Folder: <code>${esc(r.evidence?.folder || "")}</code></p>
    ${shots.length ? `<div class="evidence-list">${shots.map((x, i) => `<button class="shot ${x.status === "FAILED" ? "failed" : ""}" data-i="${i}">
      <img src="${evidenceUrl(x.path)}" alt="${esc(x.label)}" loading="lazy"><span>${esc(x.label)}</span></button>`).join("")}</div>` : '<p class="muted">No screenshots were captured.</p>'}
    <p>${(r.evidence?.trace || []).map((t) => `<a href="${evidenceUrl(t)}">Playwright trace (${esc(baseName(t))})</a>`).join(" · ")}
      ${r.evidence?.video ? ` · <a href="${evidenceUrl(r.evidence.video)}" target="_blank" rel="noopener">Video</a>` : ""}</p>`;
  pane.querySelectorAll(".shot").forEach((b) => b.addEventListener("click", () => viewer(shots, Number(b.dataset.i))));
}

function viewer(shots, start) {
  let i = start;
  const m = modal("Evidence", '<div class="viewer"><p id="vLabel"></p><img id="vImg" alt=""><div class="row"><button class="btn" id="prev">← Previous</button><span class="spacer"></span><span id="vPos" class="muted"></span><span class="spacer"></span><button class="btn" id="next">Next →</button></div></div>', { wide: true });
  const show = () => {
    m.el.querySelector("#vLabel").textContent = shots[i].label;
    m.el.querySelector("#vImg").src = evidenceUrl(shots[i].path);
    m.el.querySelector("#vImg").alt = shots[i].label;
    m.el.querySelector("#vPos").textContent = `${i + 1} of ${shots.length}`;
  };
  m.el.querySelector("#prev").addEventListener("click", () => { i = (i - 1 + shots.length) % shots.length; show(); });
  m.el.querySelector("#next").addEventListener("click", () => { i = (i + 1) % shots.length; show(); });
  m.el.addEventListener("keydown", (e) => { if (e.key === "ArrowLeft") m.el.querySelector("#prev").click(); if (e.key === "ArrowRight") m.el.querySelector("#next").click(); });
  show();
}

async function logsTab(pane, scriptId, data, reload, runId) {
  const id = await latestRun(scriptId, runId);
  const logs = await api(`${BASE}/scripts/${sid(scriptId)}/logs${id ? `?run_id=${sid(id)}` : ""}`);
  pane.innerHTML = `${logs.run ? `<div class="row"><h3 style="margin:0">Execution log ${pill(logs.run.status)}</h3><span class="muted"><code>${esc(logs.run.run_id)}</code></span></div>
      <pre class="code log">${esc(logs.run.output || "(empty)")}</pre>` : '<p class="muted">This script has not run yet.</p>'}
    <h3>Generation log</h3>${logs.generation.length ? `<pre class="code log">${esc(logs.generation.join("\n"))}</pre>` : '<p class="muted">No generation log recorded.</p>'}
    <p class="muted">Passwords and other secrets are masked in every log.</p>`;
}
