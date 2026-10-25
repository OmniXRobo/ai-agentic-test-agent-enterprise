// Automation Studio: AI-Driven, Guided Browser, Discovery & Update and Hybrid automation,
// plus the shared script editor (steps, Playwright view, versions, self-healing, results).

import { api } from "../api.js";
import { subscribe } from "../store.js";
import { esc, toast, timeOf, dateTimeOf, isRepeat } from "../util.js";
import { discoveryPanel as discoveryPanelImpl } from "./discovery.js";

const ACTIONS = ["goto", "click", "dblclick", "fill", "type", "select", "check", "uncheck", "press", "hover", "wait",
  "expect_visible", "expect_hidden", "expect_text", "expect_value", "expect_url", "screenshot"];
const MODE_NAMES = { ai: "AI-Driven", guided: "Guided", discovery: "Discovery", hybrid: "Hybrid", manual: "Manual" };
const ROUTE_FOR_MODE = { ai: "ai-automation-classic", guided: "guided-automation", discovery: "discovery", hybrid: "hybrid-automation", manual: "guided-automation" };

let cleanups = [];
const onCleanup = (fn) => cleanups.push(fn);

function params() {
  return new URLSearchParams(location.hash.split("?")[1] || "");
}

export function evidenceUrl(path) {
  return `/api/automation/evidence?path=${encodeURIComponent(path)}`;
}

// Download the pytest bundle (zip). api() only reads JSON, so this uses fetch directly.
export async function downloadTests(body, fileName) {
  try {
    const resp = await fetch("/api/automation/export-bundle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.message || `Download failed (HTTP ${resp.status})`);
    }
    const url = URL.createObjectURL(await resp.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: fileName });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (err) { toast(err.message, "error"); }
}

function routeName() {
  return location.hash.replace(/^#\/?/, "").split("?")[0];
}

// Live activity feed for one run id, fed by the event stream.
function liveFeed(container, getRunId, onEvent) {
  let last = null;
  const unsub = subscribe((c) => {
    if (c.type !== "event" || !getRunId() || c.event.run_id !== getRunId()) return;
    const repeat = isRepeat(last, c.event);
    last = c.event;
    if (!repeat) {
      const failed = c.event.name.endsWith(".failed");
      container.insertAdjacentHTML("beforeend", `<div class="feed-item ${failed ? "failed" : ""}"><span class="feed-time">${esc(timeOf(c.event.timestamp))}</span><span class="feed-text">${esc((c.event.agent_name ? c.event.agent_name + " · " : "") + (c.event.activity || c.event.name))}</span></div>`);
      container.scrollTop = container.scrollHeight;
    }
    if (onEvent) onEvent(c.event);
  });
  onCleanup(unsub);
}

function scriptTable(items, emptyText) {
  if (!items.length) return `<p class="muted">${esc(emptyText)}</p>`;
  return `<table class="data"><thead><tr><th>Script</th><th>Mode</th><th>Test Case</th><th>Steps</th><th>Version</th><th>Updated</th></tr></thead><tbody>
    ${items.map((s) => `<tr class="clickable" data-script="${esc(s.id)}"><td>${esc(s.name)}<div class="muted">${esc(s.id)}</div></td>
      <td>${esc(MODE_NAMES[s.mode] || s.mode)}</td><td>${s.test_case_id ? `<a href="#/test-cases?id=${esc(s.test_case_id)}">#${esc(s.test_case_id)}</a>` : ""}</td>
      <td>${s.steps}</td><td>v${s.version}</td><td>${esc(dateTimeOf(s.updated_at))}</td></tr>`).join("")}
    </tbody></table>`;
}

function bindScriptRows(root) {
  root.querySelectorAll("tr[data-script]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    location.hash = `#/${routeName()}?script=${encodeURIComponent(tr.dataset.script)}`;
  }));
}

async function renderScriptList(box, modes, emptyText) {
  const { items } = await api("/api/automation/scripts");
  const mine = modes ? items.filter((s) => modes.includes(s.mode)) : items;
  box.innerHTML = `<div class="row"><h3 style="margin:0">Scripts</h3><span class="spacer"></span>
      ${mine.length ? `<button class="btn" id="zipAll" title="pytest + Playwright files, one per script">Download Python tests (.zip)</button>
        <button class="btn" id="runAll">Run all ${mine.length}</button>` : ""}</div>${scriptTable(mine, emptyText)}`;
  bindScriptRows(box);
  box.querySelector("#zipAll")?.addEventListener("click", () => downloadTests({ script_ids: mine.map((s) => s.id) }, "automated-tests.zip"));
  box.querySelector("#runAll")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/automation/run-many", { method: "POST", body: { script_ids: mine.map((s) => s.id) } });
      toast(`Queued ${r.run_ids.length} runs. Follow them on Agent Workflow.`, "success");
    } catch (err) { toast(err.message, "error"); }
  });
}

// ---- script editor ------------------------------------------------------------------------

function stepRow(s, i) {
  const t = s.target || {};
  return `<tr data-i="${i}">
    <td>${i + 1}</td>
    <td><select data-f="action">${ACTIONS.map((a) => `<option ${a === s.action ? "selected" : ""}>${a}</option>`).join("")}</select></td>
    <td><input data-f="selector" value="${esc(t.selector || "")}" placeholder="selector" style="width:100%">
      ${t.alternates?.length ? `<div class="muted" title="${esc(t.alternates.join("\n"))}">${t.alternates.length} alternate locator(s)</div>` : ""}</td>
    <td><input data-f="value" value="${esc(s.value ?? "")}" placeholder="value" style="width:100%"></td>
    <td><input data-f="description" value="${esc(s.description || "")}" style="width:100%"></td>
    <td><input type="checkbox" data-f="optional" ${s.optional ? "checked" : ""} title="Optional step"></td>
    <td class="row" style="gap:4px;flex-wrap:nowrap">
      <button class="link-btn" data-move="-1" title="Move up">↑</button><button class="link-btn" data-move="1" title="Move down">↓</button>
      <button class="link-btn" data-del title="Delete step">✕</button></td></tr>`;
}

function readSteps(table, original) {
  return [...table.querySelectorAll("tbody tr")].map((tr) => {
    const orig = original[Number(tr.dataset.i)] || {};
    const get = (f) => tr.querySelector(`[data-f="${f}"]`);
    const selector = get("selector").value.trim();
    const target = selector ? { ...(orig.target || {}), selector } : null;
    if (target && orig.target && orig.target.selector !== selector) {
      // A hand-edited selector no longer matches the recorded fingerprint's alternates.
      target.alternates = [];
    }
    return {
      ...orig,
      action: get("action").value,
      target,
      value: get("value").value === "" ? null : get("value").value,
      description: get("description").value || null,
      optional: get("optional").checked,
    };
  });
}

function resultsHtml(items) {
  if (!items.length) return '<p class="muted">This script has not run yet.</p>';
  return items.map((r) => `
    <details class="tc">
      <summary><span class="pill ${esc(r.status)}">${esc(r.status)}</span> <a href="#/history?run=${esc(r.run_id)}">${esc(r.run_id)}</a>
        <span class="muted">${esc(dateTimeOf(r.started_at))}${r.failed_step !== null && r.failed_step !== undefined ? ` · failed at step ${r.failed_step + 1}` : ""}</span></summary>
      ${r.error ? `<p class="pill FAIL">${esc(r.error)}</p>` : ""}
      ${executionStepsHtml(r)}
    </details>`).join("");
}

export function executionStepsHtml(r) {
  const shots = r.evidence?.screenshots || [];
  return `<table class="data"><thead><tr><th>#</th><th>Step</th><th>Result</th><th>Time</th></tr></thead><tbody>
    ${(r.steps || []).map((s) => `<tr><td>${s.index + 1}</td><td>${esc(s.description || s.action)}
      ${s.healed ? `<div class="pill WARN">Self-healed: ${esc(s.healed.original)} → ${esc(s.healed.healed)} (${Number(s.healed.confidence).toFixed(2)})</div>` : ""}
      ${s.error ? `<div class="muted">${esc(s.error)}</div>` : ""}</td>
      <td><span class="pill ${esc(s.status)}">${esc(s.status)}</span></td><td>${s.duration_ms ?? ""} ms</td></tr>`).join("")}
    </tbody></table>
    ${shots.length ? `<div class="shots">${shots.map((p) => `<a href="${evidenceUrl(p)}" target="_blank" rel="noopener"><img src="${evidenceUrl(p)}" alt="${esc(p.split(/[\\/]/).pop())}" loading="lazy"><span>${esc(p.split(/[\\/]/).pop())}</span></a>`).join("")}</div>` : ""}
    ${r.evidence?.video ? `<p><a href="${evidenceUrl(r.evidence.video)}" target="_blank" rel="noopener">Video recording</a></p>` : ""}`;
}

function healingHtml(items) {
  if (!items.length) return '<p class="muted">No self-healing events for this script.</p>';
  return `<table class="data"><thead><tr><th>When</th><th>Step</th><th>Original</th><th>Healed</th><th>Confidence</th><th>Result</th><th>Evidence</th><th></th></tr></thead><tbody>
    ${items.map((h) => `<tr><td>${esc(dateTimeOf(h.timestamp))}</td><td>${h.step_index + 1}</td><td><code>${esc(h.original_selector)}</code></td>
      <td>${h.healed_selector ? `<code>${esc(h.healed_selector)}</code>` : '<span class="muted">none</span>'}</td>
      <td>${Number(h.confidence).toFixed(2)}</td><td><span class="pill ${h.result === "failed" ? "FAIL" : h.result === "validated" ? "PASS" : "WARN"}">${esc(h.result)}</span></td>
      <td>${h.screenshot ? `<a href="${evidenceUrl(h.screenshot)}" target="_blank" rel="noopener">Screenshot</a>` : ""}
        <details><summary class="muted">Candidates</summary>${(h.candidates || []).map((c) => `<div><code>${esc(c.selector)}</code> ${Number(c.confidence).toFixed(2)}</div>`).join("")}</details></td>
      <td>${h.applied ? '<span class="muted">Applied</span>' : h.result === "validated" ? `<button class="btn" data-apply="${h.id}">Update script</button>` : ""}</td></tr>`).join("")}
    </tbody></table>
    <p class="muted">A healed locator only updates the script after a run using it passed, and only when you choose to apply it. The previous version is kept.</p>`;
}

async function renderEditor(view, scriptId) {
  const sid = encodeURIComponent(scriptId);
  const [{ script, python, python_file, versions }, { items: results }, { items: healing }, data] = await Promise.all([
    api(`/api/automation/scripts/${sid}`),
    api(`/api/automation/results?script_id=${sid}&limit=20`),
    api(`/api/automation/healing?script_id=${sid}`),
    api("/api/test-data").catch(() => ({ items: [] })),
  ]);
  const datasets = (data.items || []).filter((d) => !script.test_case_id || String(d.tfs_test_case_id) === String(script.test_case_id));
  let steps = script.steps;
  let runId = null;
  view.innerHTML = `
    <p><a href="#/${routeName()}">← ${esc(document.getElementById("pageTitle").textContent)}</a></p>
    <div class="card">
      <div class="row"><h2 style="margin:0">${esc(script.name)}</h2><span class="pill WAITING">${esc(MODE_NAMES[script.mode] || script.mode)}</span>
        <span class="muted">${esc(script.id)} · v${script.version}</span><span class="spacer"></span>
        ${datasets.length ? `<select id="dataset"><option value="">No test data</option>${datasets.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("")}</select>` : ""}
        <label class="row" style="gap:4px"><input type="checkbox" id="headless"> Headless</label>
        <button class="btn primary" id="run">Run</button><button class="btn" id="stop" hidden>Stop</button></div>
      <form class="grid" id="meta" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin-top:10px">
        <label>Name <input name="name" value="${esc(script.name)}" required></label>
        <label>Start URL <input name="base_url" value="${esc(script.base_url || "")}"></label>
        <label>TFS Test Case ID <input name="test_case_id" value="${esc(script.test_case_id || "")}" inputmode="numeric"></label>
        <label>Application <input name="application" value="${esc(script.application || "")}"></label>
        <label>Credential role <input name="credential_role" value="${esc(script.credential_role || "")}" placeholder="e.g. admin"></label>
        <label>Browser <select name="browser">${["chromium", "msedge", "chrome", "firefox", "webkit"].map((b) => `<option ${b === script.browser ? "selected" : ""}>${b}</option>`).join("")}</select></label>
      </form>
      ${script.notes ? `<p class="pill WARN" style="white-space:pre-wrap">${esc(script.notes)}</p>` : ""}
    </div>
    <div class="card" id="runBox" style="margin-top:12px" hidden><h3>Live run</h3><div class="feed" id="feed"></div></div>
    <div class="card" style="margin-top:12px">
      <div class="row"><h3 style="margin:0">Steps</h3><span class="spacer"></span>
        <button class="btn" id="addStep">Add step</button>
        <a class="btn" href="#/hybrid-automation?append=${esc(script.id)}">Record more steps</a>
        <button class="btn primary" id="save">Save</button></div>
      <p class="muted">Runtime values: <code>{{TODAY}}</code>, <code>{{TODAY+3d:%d/%m/%Y}}</code>, <code>{{NOW:%H:%M}}</code>, <code>{{DATA:field}}</code>, <code>{{CRED:role.username}}</code>, <code>{{CRED:role.password}}</code>. Dates come from the Windows system clock.</p>
      <div style="overflow-x:auto"><table class="data steps" id="steps"><thead><tr><th>#</th><th>Action</th><th>Locator</th><th>Value</th><th>Description</th><th>Opt.</th><th></th></tr></thead>
        <tbody>${steps.map(stepRow).join("")}</tbody></table></div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(360px,1fr));margin-top:12px">
      <div class="card"><h3>Recent runs</h3><div id="results">${resultsHtml(results)}</div></div>
      <div class="card"><h3>Self-healing</h3><div id="healing">${healingHtml(healing)}</div></div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(360px,1fr));margin-top:12px">
      <div class="card"><div class="row"><h3 style="margin:0">Python test (pytest + Playwright)</h3><span class="spacer"></span>
        <a class="btn" href="/api/automation/scripts/${esc(script.id)}/export">Download ${esc(python_file)}</a>
        <button class="btn" id="zipOne" title="With pytest.ini, requirements.txt, README.txt and RUN_TESTS.cmd">Download ready-to-run .zip</button></div>
        <p class="muted">Runs with <code>pytest</code> (one test per data set of the Test Case) or with <code>python ${esc(python_file)}</code>. Passwords are read from environment variables, never stored in the file.</p>
        <pre class="code">${esc(python)}</pre></div>
      <div class="card"><h3>Version history</h3>
        <table class="data"><tbody>${versions.slice().reverse().map((v) => `<tr><td>v${v.version}</td><td>${esc(v.reason)}</td><td>${esc(dateTimeOf(v.timestamp))}</td>
          <td>${v.version < script.version ? `<button class="link-btn" data-restore="${v.version}">Restore</button>` : '<span class="muted">current</span>'}</td></tr>`).join("")}</tbody></table>
        <p><button class="link-btn" id="archive">Archive this script</button></p></div>
    </div>`;

  view.querySelector("#zipOne").addEventListener("click", () => downloadTests({ script_ids: [script.id] }, python_file.replace(/\.py$/, ".zip")));
  const table = view.querySelector("#steps");
  const rebind = () => {
    table.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => {
      steps = readSteps(table, steps); steps.splice(Number(b.closest("tr").dataset.i), 1); redraw();
    }));
    table.querySelectorAll("[data-move]").forEach((b) => (b.onclick = () => {
      steps = readSteps(table, steps);
      const i = Number(b.closest("tr").dataset.i), j = i + Number(b.dataset.move);
      if (j < 0 || j >= steps.length) return;
      [steps[i], steps[j]] = [steps[j], steps[i]]; redraw();
    }));
  };
  const redraw = () => { table.querySelector("tbody").innerHTML = steps.map(stepRow).join(""); rebind(); };
  rebind();
  view.querySelector("#addStep").addEventListener("click", () => {
    steps = readSteps(table, steps); steps.push({ action: "click", target: null, value: null, description: null, optional: false, timeout_ms: 10000 }); redraw();
  });
  const save = async () => {
    const f = new FormData(view.querySelector("#meta"));
    const body = { ...script, name: f.get("name"), base_url: f.get("base_url") || null, test_case_id: f.get("test_case_id") || null,
      application: f.get("application") || null, credential_role: f.get("credential_role") || null, browser: f.get("browser"),
      steps: readSteps(table, steps) };
    for (const k of ["id", "version", "created_at", "updated_at"]) delete body[k];
    await api(`/api/automation/scripts/${encodeURIComponent(script.id)}`, { method: "PUT", body });
  };
  view.querySelector("#save").addEventListener("click", async () => {
    try { await save(); toast("Script saved as a new version", "success"); renderEditor(view, scriptId); }
    catch (err) { toast(err.message + (err.details ? ` (${JSON.stringify(err.details).slice(0, 200)})` : ""), "error"); }
  });
  const runBtn = view.querySelector("#run"), stopBtn = view.querySelector("#stop");
  runBtn.addEventListener("click", async () => {
    try {
      const ds = view.querySelector("#dataset")?.value;
      const r = await api(`/api/automation/scripts/${encodeURIComponent(script.id)}/run`, { method: "POST", body: {
        test_data_id: ds ? Number(ds) : null, headless: view.querySelector("#headless").checked ? true : null } });
      runId = r.run_id;
      view.querySelector("#runBox").hidden = false;
      view.querySelector("#feed").innerHTML = "";
      runBtn.disabled = true; stopBtn.hidden = false;
    } catch (err) { toast(err.message, "error"); }
  });
  stopBtn.addEventListener("click", () => runId && api(`/api/automation/runs/${encodeURIComponent(runId)}/stop`, { method: "POST", body: {} }).catch((err) => toast(err.message, "error")));
  liveFeed(view.querySelector("#feed"), () => runId, (ev) => {
    if (ev.name === "execution.completed" || ev.name === "execution.failed") {
      runBtn.disabled = false; stopBtn.hidden = true;
      setTimeout(async () => {
        const [{ items }, { items: heal }] = await Promise.all([
          api(`/api/automation/results?script_id=${sid}&limit=20`),
          api(`/api/automation/healing?script_id=${sid}`),
        ]);
        view.querySelector("#results").innerHTML = resultsHtml(items);
        view.querySelector("#healing").innerHTML = healingHtml(heal);
        bindHealing();
      }, 400);
    }
  });
  const bindHealing = () => view.querySelectorAll("[data-apply]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/automation/healing/${b.dataset.apply}/apply`, { method: "POST", body: {} }); toast("Script updated with the healed locator", "success"); renderEditor(view, scriptId); }
    catch (err) { toast(err.message, "error"); }
  }));
  bindHealing();
  view.querySelectorAll("[data-restore]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Restore version ${b.dataset.restore}? The current version is kept in history.`)) return;
    try { await api(`/api/automation/scripts/${encodeURIComponent(script.id)}/restore/${b.dataset.restore}`, { method: "POST", body: {} }); renderEditor(view, scriptId); }
    catch (err) { toast(err.message, "error"); }
  }));
  view.querySelector("#archive").addEventListener("click", async () => {
    if (!confirm("Archive this script? Its files, history and evidence are kept in the archive folder.")) return;
    try { await api(`/api/automation/scripts/${encodeURIComponent(script.id)}`, { method: "DELETE" }); location.hash = `#/${routeName()}`; }
    catch (err) { toast(err.message, "error"); }
  });
}

// ---- mode panels ------------------------------------------------------------------------------

async function aiPanel(view) {
  let discoveries = [];
  try { ({ items: discoveries } = await api("/api/automation/discoveries")); } catch { /* optional */ }
  const tc = params().get("tc") || "";
  view.innerHTML = `
    <form class="card grid" id="gen" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      <label>TFS Test Case ID <input name="test_case_id" value="${esc(tc)}" inputmode="numeric"></label>
      <label>or User Story ID (all its Test Cases) <input name="user_story_id" value="${esc(params().get("story") || "")}" inputmode="numeric"></label>
      <label>Application URL <input name="base_url" required placeholder="https://your-app/"></label>
      <label>Credential role <input name="credential_role" placeholder="e.g. admin"></label>
      <label>Application name <input name="application" placeholder="as saved in Application Credentials"></label>
      <label>Element inventory <select name="discovery_id"><option value="">Latest discovery for this URL (or discover now)</option>
        ${discoveries.map((d) => `<option value="${esc(d.id)}">${esc(d.base_url)} · ${esc(dateTimeOf(d.created_at))}</option>`).join("")}</select></label>
      <div class="row" style="align-self:end"><button class="btn primary" type="submit">Create automation</button></div>
      <p class="muted" style="grid-column:1/-1">The Automation Engineering Agent reads the test case steps from TFS and maps them to elements discovered on your application. It never invents selectors: anything it could not verify is listed for review on the script.</p>
    </form>
    <div class="card" id="progress" style="margin-top:12px" hidden><h3>Activity</h3><div class="feed" id="feed"></div><p id="done"></p></div>
    <div class="card" id="list" style="margin-top:12px"><p class="muted">Loading…</p></div>`;
  let runId = null, storyId = null, pending = 0;
  view.querySelector("#gen").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (!f.test_case_id === !f.user_story_id) { toast("Enter a Test Case ID or a User Story ID.", "warning"); return; }
    try {
      const r = await api("/api/automation/generate", { method: "POST", body: {
        test_case_id: f.test_case_id ? Number(f.test_case_id) : null, user_story_id: f.user_story_id ? Number(f.user_story_id) : null,
        base_url: f.base_url.trim(), credential_role: f.credential_role || null,
        application: f.application || null, discovery_id: f.discovery_id || null } });
      runId = r.run_id;
      storyId = f.user_story_id || null;
      pending = r.test_case_ids ? r.test_case_ids.length : 1;
      view.querySelector("#progress").hidden = false;
      view.querySelector("#feed").innerHTML = "";
      view.querySelector("#done").innerHTML = "";
    } catch (err) { toast(err.message, "error"); }
  });
  liveFeed(view.querySelector("#feed"), () => runId, async (ev) => {
    // Discovery runs under the same run id and can fail without ending generation; count only this agent's results.
    if (ev.agent_id !== "automation_engineering" || !["agent.completed", "agent.failed"].includes(ev.name)) return;
    pending -= 1;
    const s = await api(`/api/automation/generate/${encodeURIComponent(runId)}`);
    const done = view.querySelector("#done");
    if (storyId) {
      const ids = s.script_ids || [];
      done.innerHTML = `${ids.length} script(s) created${pending > 0 ? `, ${pending} test case(s) to go…` : "."}
        ${ids.length && pending <= 0 ? '<button class="btn primary" id="zipStory">Download the Python tests (.zip)</button>' : ""}`;
      const sid = storyId;
      done.querySelector("#zipStory")?.addEventListener("click", () => downloadTests({ script_ids: ids }, `automated-tests-US${sid}.zip`));
    } else {
      done.innerHTML = s.script_id
        ? `<a class="btn primary" href="#/ai-automation-classic?script=${esc(s.script_id)}">Open the generated script</a>`
        : '<span class="pill FAIL">No script was created. See the activity above.</span>';
    }
    renderScriptList(view.querySelector("#list"), ["ai"], "");
  });
  await renderScriptList(view.querySelector("#list"), ["ai"], "No AI-generated scripts yet.");
}

async function guidedPanel(view, { appendTo = null } = {}) {
  let creds = [];
  try { ({ items: creds } = await api("/api/automation/credentials")); } catch { /* optional */ }
  let target = null;
  if (appendTo) {
    try { target = (await api(`/api/automation/scripts/${encodeURIComponent(appendTo)}`)).script; } catch (err) { toast(err.message, "error"); }
  }
  const roles = [...new Set(creds.map((c) => c.role))];
  view.innerHTML = `
    <form class="card grid" id="rec" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      ${target ? `<p style="grid-column:1/-1"><strong>Adding recorded steps to:</strong> <a href="#/hybrid-automation?script=${esc(target.id)}">${esc(target.name)}</a> (${target.steps.length} steps)</p>` : ""}
      <label>Start URL <input name="start_url" required value="${esc(target?.base_url || "")}" placeholder="https://your-app/"></label>
      ${target ? `<label>Insert at step <input name="insert_at" type="number" min="1" max="${target.steps.length + 1}" value="${target.steps.length + 1}"></label>`
        : `<label>Script name <input name="name" placeholder="e.g. Login and search"></label>
           <label>TFS Test Case ID <input name="test_case_id" inputmode="numeric"></label>`}
      <label>Credential role <input name="credential_role" list="roles" value="${esc(target?.credential_role || "")}" placeholder="passwords become {{CRED:role.password}}"></label>
      <datalist id="roles">${roles.map((r) => `<option value="${esc(r)}">`).join("")}</datalist>
      <label>Application <input name="application" value="${esc(target?.application || "")}"></label>
      <label>Browser <select name="browser"><option>chromium</option><option>msedge</option><option>chrome</option><option>firefox</option></select></label>
      <div class="row" style="align-self:end"><button class="btn primary" type="submit">Start recording</button></div>
      <p class="muted" style="grid-column:1/-1">A browser window opens on this PC. Perform the test steps there; each action is recorded with a fingerprint for self-healing. Typed passwords are never stored.</p>
    </form>
    <div class="card" id="live" style="margin-top:12px" hidden>
      <div class="row"><h3 style="margin:0">Recording</h3><span class="pill RUNNING" id="recState">RECORDING</span><span class="spacer"></span>
        <button class="btn primary" id="stopRec">Stop and save</button></div>
      <form class="row" id="assertForm" style="margin:10px 0">
        <strong>Add a check</strong><input name="selector" placeholder="CSS selector, e.g. #welcome" required><input name="text" placeholder="Expected text (optional)">
        <button class="btn" type="submit">Add check</button></form>
      <table class="data"><thead><tr><th>#</th><th>Action</th><th>Element</th><th>Value</th></tr></thead><tbody id="recSteps"></tbody></table>
    </div>
    <div class="card" id="list" style="margin-top:12px"><p class="muted">Loading…</p></div>`;
  let runId = null, timer = null;
  const stopPolling = () => { if (timer) clearInterval(timer); timer = null; };
  onCleanup(stopPolling);
  const poll = async () => {
    try {
      const s = await api(`/api/automation/recordings/${encodeURIComponent(runId)}`);
      view.querySelector("#recSteps").innerHTML = s.steps.map((st, i) => `<tr><td>${i + 1}</td><td>${esc(st.action)}</td>
        <td>${esc(st.target?.description || st.target?.selector || "")}</td><td>${esc(st.value ?? "")}</td></tr>`).join("");
      if (!s.active) { view.querySelector("#recState").textContent = s.error ? "ERROR" : "BROWSER CLOSED"; view.querySelector("#recState").className = `pill ${s.error ? "FAIL" : "WARN"}`; stopPolling(); if (s.error) toast(s.error, "error"); }
    } catch (err) { stopPolling(); toast(err.message, "error"); }
  };
  view.querySelector("#rec").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      const r = await api("/api/automation/recordings", { method: "POST", body: {
        start_url: f.start_url.trim(), name: f.name || null, test_case_id: f.test_case_id || null, append_to: target?.id || null,
        insert_at: target && f.insert_at ? Number(f.insert_at) - 1 : null, credential_role: f.credential_role || null,
        application: f.application || null, browser: f.browser } });
      runId = r.run_id;
      view.querySelector("#live").hidden = false;
      e.target.querySelector("button").disabled = true;
      timer = setInterval(poll, 1000);
    } catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#assertForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try { await api(`/api/automation/recordings/${encodeURIComponent(runId)}/assert`, { method: "POST", body: { selector: f.selector, text: f.text || null } }); e.target.reset(); poll(); }
    catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#stopRec").addEventListener("click", async () => {
    stopPolling();
    try {
      const { script } = await api(`/api/automation/recordings/${encodeURIComponent(runId)}/stop`, { method: "POST", body: {} });
      toast(`Saved ${script.steps.length} steps`, "success");
      location.hash = `#/${ROUTE_FOR_MODE[script.mode] || routeName()}?script=${encodeURIComponent(script.id)}`;
    } catch (err) { toast(err.message, "error"); }
  });
  await renderScriptList(view.querySelector("#list"), appendTo || routeName() === "hybrid-automation" ? ["hybrid"] : ["guided", "manual"],
    routeName() === "hybrid-automation" ? "No hybrid scripts yet. Open any script and choose Record more steps, or record extra steps into an AI-generated script." : "No recorded scripts yet.");
}

async function hybridPanel(view) {
  const appendTo = params().get("append");
  if (appendTo) return guidedPanel(view, { appendTo });
  const { items } = await api("/api/automation/scripts");
  view.innerHTML = `
    <div class="card"><h3>Combine AI-generated and recorded steps</h3>
      <p class="muted">Pick a script, then record additional steps in the browser and insert them at any position. AI-generated scripts become Hybrid once recorded steps are added; every change is kept as a version.</p>
      ${items.length ? `<form class="row" id="pick"><select name="sid" required>${items.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} (${esc(MODE_NAMES[s.mode] || s.mode)}, ${s.steps} steps)</option>`).join("")}</select>
        <button class="btn primary" type="submit">Record more steps</button></form>` : '<p class="muted">Create a script first with AI-Driven or Guided Browser Automation.</p>'}
    </div>
    <div class="card" id="list" style="margin-top:12px"><p class="muted">Loading…</p></div>`;
  view.querySelector("#pick")?.addEventListener("submit", (e) => {
    e.preventDefault();
    location.hash = `#/hybrid-automation?append=${encodeURIComponent(new FormData(e.target).get("sid"))}`;
  });
  await renderScriptList(view.querySelector("#list"), ["hybrid"], "No hybrid scripts yet.");
}

function discoveryPanel(view) {
  return discoveryPanelImpl(view, { onCleanup, liveFeed, renderScriptList });
}

function page(panel) {
  return {
    async mount(view) {
      const scriptId = params().get("script");
      if (scriptId) await renderEditor(view, scriptId); else await panel(view);
    },
    unmount() {
      cleanups.forEach((fn) => fn());
      cleanups = [];
    },
  };
}

export const aiAutomation = page(aiPanel);
export const guidedAutomation = page((view) => guidedPanel(view));
export const hybridAutomation = page(hybridPanel);
export const discovery = page(discoveryPanel);
