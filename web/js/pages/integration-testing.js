// 23.14.0: Integration Testing > Red Hat Fuse Testing and ActiveMQ Testing.
// Fuse: Fuse Connections, Route Discovery, Route Mapping, AI Test Designer, Test Execution, Route Monitoring,
// Transformation Comparison, Results & Defects.
// ActiveMQ: Broker Connections, Queue & Topic Discovery, Message Test Designer, Publish & Verify, Consumer Verification,
// Retry & Dead-Letter Testing, Broker Monitoring, Results & Defects.
// Management access is read-only on the server; publishing goes only to authorized test destinations; business
// messages are browsed, never consumed; routes are never restarted, redeployed or changed.

import { api } from "../api.js";
import { esc, toast, openModal } from "../util.js";
import { canEdit, isAdmin } from "../auth.js";
import { enc, pill, short, guarded, stored, targetPicker, envBadge, authorizationHtml, authorizationFields, runsTable,
  wireRuns, runDetail, wireResultActions, resultsTable, statusFilter, lines } from "./itest-ui.js";

function tabsHtml(tabs, current) {
  return `<div class="row tabs" style="gap:6px;flex-wrap:wrap;margin-top:12px">${tabs.map(([k, l]) => `<button class="chip ${current === k ? "on" : ""}" data-tab="${k}">${esc(l)}</button>`).join("")}</div>`;
}

function makePage(M) {
  let view = null; let stop = null;
  const st = { targets: [], t: null, tab: M.tabs[0][0] };
  const load = async () => {
    st.targets = (await api(M.listUrl))[M.listKey];
    const want = stored(`sg.${M.key}.target`);
    st.t = st.targets.find((x) => x.id === want) || st.targets[0] || null;
    const qtab = new URLSearchParams(location.hash.split("?")[1] || "").get("tab");
    if (qtab && M.tabs.some(([k]) => k === qtab)) st.tab = qtab;
    render();
  };
  const render = () => {
    if (stop) { stop(); stop = null; }
    view.innerHTML = `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap"><h3 style="margin:0">${esc(M.title)}</h3>
      ${targetPicker(st.targets, st.t?.id, { label: M.targetLabel, id: "itTarget", empty: `No ${M.targetLabel.toLowerCase()} yet` })}${envBadge(st.t)}<span class="spacer"></span>
      <span class="muted small">${st.t ? esc(M.describe(st.t)) : ""}</span></div><p class="muted" style="margin:6px 0 0">${M.intro}</p>
      ${st.t ? authorizationHtml(st.t.config) : ""}</div>${tabsHtml(M.tabs, st.tab)}<div id="itBody" style="margin-top:12px"></div>`;
    view.querySelector("#itTarget").addEventListener("change", (e) => { st.t = st.targets.find((x) => x.id === e.target.value) || null; stored(`sg.${M.key}.target`, st.t?.id || ""); render(); });
    view.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { st.tab = b.dataset.tab; render(); }));
    const body = view.querySelector("#itBody");
    const fn = M.draw[st.tab];
    if (!st.t && !M.noTarget.includes(st.tab)) { body.innerHTML = `<div class="card"><p class="muted">Add a ${esc(M.targetLabel.toLowerCase())} in ${esc(M.tabs[0][1])} first.</p></div>`; return; }
    Promise.resolve(fn(body, st, { reload: load, rerender: render, setStop: (s) => { stop = s; } }))
      .catch((err) => { body.innerHTML = `<div class="card"><p class="pill FAIL">${esc(err.message)}</p></div>`; });
  };
  return { async mount(v) { view = v; await load(); }, unmount() { if (stop) stop(); stop = null; } };
}

function credentialsCard(base, t, reload) {
  return `<div class="card" style="margin-top:12px"><h3 style="margin:0 0 6px">Credentials</h3><p class="muted small">Kept in the Windows Credential Manager; never shown, sent to the AI, put in reports, emails or logs.</p>
    <table class="data compact"><tbody>${Object.entries(t.secrets).map(([n, ok]) => `<tr><td>${esc(n)}</td><td>${ok ? '<span class="pill PASS">saved</span>' : '<span class="pill WAITING">not saved</span>'}</td>
    <td>${canEdit() ? `<input type="password" data-sec="${esc(n)}" autocomplete="new-password" placeholder="new value"><button class="btn small" data-save-sec="${esc(n)}">Save</button>${ok ? `<button class="btn small" data-clear-sec="${esc(n)}">Remove</button>` : ""}` : ""}</td></tr>`).join("")}</tbody></table></div>`;
}

function wireCredentials(body, base, t, reload) {
  body.querySelectorAll("[data-save-sec]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
    const v = body.querySelector(`[data-sec="${b.dataset.saveSec}"]`).value; if (!v) return;
    await api(`${base}/${enc(t.id)}/credentials`, { method: "POST", body: { name: b.dataset.saveSec, value: v } }); toast("Saved.", "success"); reload(); })));
  body.querySelectorAll("[data-clear-sec]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
    await api(`${base}/${enc(t.id)}/credentials`, { method: "POST", body: { name: b.dataset.clearSec, value: null } }); reload(); })));
}

function connectionSteps(r) {
  return `<ul>${r.steps.map((s) => `<li>${s.ok ? pill("PASSED") : pill("FAILED")} ${esc(s.step)}: ${esc(s.detail)}</li>`).join("")}</ul>`;
}

async function casesTab(body, st, ctx, { listModule, base, kinds, template, describe }) {
  const cases = (await api(`/api/itest/cases?module=${listModule}&target_id=${enc(st.t.id)}`)).cases;
  const shown = cases.filter((c) => !kinds || kinds.includes(c.definition.case?.type));
  body.innerHTML = `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap">${canEdit() ? `<button class="btn" data-new>New test</button><button class="btn" data-draft>Draft tests with AI</button><button class="btn primary" data-run>Run approved tests</button>` : ""}
    <span class="muted small">AI drafts and edited tests stay drafts until a person approves them.</span></div>
    <table class="data compact" style="margin-top:8px"><thead><tr><th>Test</th><th>Type</th><th>Details</th><th>Status</th><th></th></tr></thead><tbody>
    ${shown.map((c) => `<tr><td>${esc(c.name)}<div class="muted small">${esc(c.origin || "")}</div></td><td>${esc(c.definition.case?.type)}</td><td class="small">${esc(describe(c.definition.case))}</td>
      <td>${c.status === "approved" ? '<span class="pill PASS">approved</span>' : '<span class="pill WAITING">draft</span>'}</td>
      <td>${canEdit() ? `<button class="btn small" data-edit="${esc(c.id)}">Edit</button>${c.status !== "approved" ? `<button class="btn small" data-ok="${esc(c.id)}">Approve</button>` : ""}<button class="btn small" data-del="${esc(c.id)}">Delete</button>` : ""}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">No tests yet.</td></tr>'}</tbody></table></div>`;
  const edit = (c) => {
    const { el, close } = openModal(c ? `Edit ${c.name}` : "New test", `<textarea id="tcJson" rows="20" style="width:100%;font-family:monospace">${esc(JSON.stringify(c?.definition.case || template(st.t), null, 2))}</textarea>
      <div class="row"><span class="spacer"></span><button class="btn primary" id="tcSave">Save</button></div>`, { wide: true });
    el.querySelector("#tcSave").addEventListener("click", (e) => guarded(e.target, async () => {
      let v; try { v = JSON.parse(el.querySelector("#tcJson").value); } catch { throw new Error("The test is not valid JSON."); }
      if (c) await api(`${base}/${enc(st.t.id)}/cases/${enc(c.id)}`, { method: "PUT", body: v }); else await api(`${base}/${enc(st.t.id)}/cases`, { method: "POST", body: v });
      close(); ctx.rerender(); }));
  };
  body.querySelector("[data-new]")?.addEventListener("click", () => edit(null));
  body.querySelector("[data-draft]")?.addEventListener("click", (e) => guarded(e.target, async () => { e.target.textContent = "Drafting… (local AI)"; const r = await api(`${base}/${enc(st.t.id)}/cases/draft`, { method: "POST", body: {} }); toast(`${r.cases.length} draft(s) added.`, "success"); ctx.rerender(); }));
  body.querySelector("[data-run]")?.addEventListener("click", (e) => guarded(e.target, async () => {
    const ids = shown.filter((c) => c.status === "approved").map((c) => c.id);
    if (!ids.length) throw new Error("No approved test on this screen.");
    const r = await api(`${base}/${enc(st.t.id)}/runs`, { method: "POST", body: { kind: "cases", options: { case_ids: ids } } });
    toast(`Run ${r.run.id} started.`, "success"); st.tab = "results"; st.openRun = r.run.id; ctx.rerender(); }));
  body.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => edit(cases.find((c) => c.id === b.dataset.edit))));
  body.querySelectorAll("[data-ok]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => { await api(`/api/itest/cases/${enc(b.dataset.ok)}/approve`, { method: "POST" }); ctx.rerender(); })));
  body.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => { if (!window.confirm("Delete this test?")) return; await api(`/api/itest/cases/${enc(b.dataset.del)}`, { method: "DELETE" }); ctx.rerender(); })));
}

async function resultsTab(body, st, ctx, module) {
  const runs = (await api(`/api/itest/runs?module=${module}&target_id=${enc(st.t.id)}`)).runs;
  body.innerHTML = `<div class="card"><h3 style="margin:0 0 6px">Runs</h3><div id="runsBox">${runsTable(runs, module)}</div></div><div id="runBox" style="margin-top:12px"></div>
    <div class="card" style="margin-top:12px"><div class="row" style="gap:8px"><h3 style="margin:0">All results</h3><span class="spacer"></span>${statusFilter("allSt")}</div><div id="allBox"></div></div>`;
  const open = async (id) => { if (st.stopRun) st.stopRun(); st.stopRun = await runDetail(body.querySelector("#runBox"), id); ctx.setStop(st.stopRun); };
  wireRuns(body.querySelector("#runsBox"), open, ctx.rerender);
  wireResultActions(body, ctx.rerender);
  const all = async () => {
    const s = body.querySelector("#allSt").value;
    const res = (await api(`/api/itest/results?module=${module}${s ? `&status=${enc(s)}` : "&status=FAILED,ERROR"}&limit=200`)).results.filter((r) => r.target_id === st.t.id);
    body.querySelector("#allBox").innerHTML = resultsTable(res);
  };
  body.querySelector("#allSt").value = "FAILED,ERROR";
  body.querySelector("#allSt").addEventListener("change", all);
  await all();
  if (st.openRun || runs[0]) { await open(st.openRun || runs[0].id); st.openRun = null; }
}

// ======================================== Red Hat Fuse ===================================================================
const FB = "/api/fuse/connections";

function fuseForm(t, brokers) {
  const c = t?.config || {};
  return `<form class="card grid" id="fForm" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px">
    <h3 style="grid-column:1/-1;margin:0">${t ? `Connection ${esc(t.name)}` : "New Fuse connection"}</h3>
    <label>Name <input name="name" required minlength="2" value="${esc(t?.name || "")}"></label>
    <label>Environment <select name="environment">${["Development", "QA", "Test", "UAT", "Staging", "Production"].map((e) => `<option ${t?.environment === e || (!t && e === "Test") ? "selected" : ""}>${e}</option>`).join("")}</select></label>
    <label>Runtime <select name="runtime">${[["auto", "Detect (Jolokia)"], ["karaf", "Fuse on Karaf"], ["springboot", "Fuse / Camel on Spring Boot"], ["eap", "Fuse on JBoss EAP"], ["blackbox", "Black-box (no management access)"]].map(([v, l]) => `<option value="${v}" ${c.runtime === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <label>Jolokia URL (read-only) <input name="jolokia_url" value="${esc(c.jolokia_url || "")}" placeholder="http://fuse-qa:8181/hawtio/jolokia"></label>
    <label>Actuator URL (Spring Boot) <input name="actuator_url" value="${esc(c.actuator_url || "")}" placeholder="http://orders-svc:8080/actuator"></label>
    <label>Management user <input name="management_user" value="${esc(c.management_user || "")}"></label>
    <label>Linked ActiveMQ broker <select name="broker_id"><option value="">None</option>${brokers.map((b) => `<option value="${esc(b.id)}" ${c.broker_id === b.id ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></label>
    <label>Route filter (id contains) <input name="route_filter" value="${esc(c.route_filter || "")}"></label>
    <label>Authorized hosts (one per line) <textarea name="targets" rows="2">${esc((c.targets || []).join("\n"))}</textarea></label>
    <label>Test URLs for HTTP input (prefixes) <textarea name="test_urls" rows="2" placeholder="http://fuse-qa:8080/test/">${esc((c.test_urls || []).join("\n"))}</textarea></label>
    <label class="row" style="gap:6px"><input type="checkbox" name="allow_state_changing" ${c.allow_state_changing ? "checked" : ""}> Allow sending test input by HTTP POST / PUT</label>
    <label class="row" style="gap:6px"><input type="checkbox" name="verify_tls" ${c.verify_tls === false ? "" : "checked"}> Verify TLS</label>
    <label>Company CA file <input name="ca_file" value="${esc(c.ca_file || "")}"></label>
    <label style="grid-column:1/-1">Black-box routes (id | from | to, to …) <textarea name="blackbox" rows="3" placeholder="orders | jms:queue:ORDERS.IN | jms:queue:ORDERS.OUT, jms:queue:ORDERS.REVIEW">${esc((c.blackbox_routes || []).map((r) => `${r.id} | ${r.from} | ${(r.to || []).join(", ")}`).join("\n"))}</textarea></label>
    ${authorizationFields()}
    ${t?.environment === "Production" && isAdmin() ? '<label style="grid-column:1/-1">Admin approval for test messages through Production routes <input name="production_approval" maxlength="300"></label>' : ""}
    <div class="row" style="grid-column:1/-1;gap:8px"><span class="spacer"></span>${t ? '<button class="btn" type="button" data-test>Test connection</button><button class="btn" type="button" data-del>Delete</button>' : ""}<button class="btn primary">Save</button></div>
    <div data-steps style="grid-column:1/-1"></div></form>`;
}

async function fuseConnections(body, st, ctx) {
  const brokers = (await api("/api/activemq/brokers").catch(() => ({ brokers: [] }))).brokers || [];
  body.innerHTML = `${canEdit() ? '<div class="row" style="margin-bottom:8px"><button class="btn" data-new>New connection</button></div>' : ""}<div data-form>${fuseForm(st.t, brokers)}</div>${st.t ? credentialsCard(FB, st.t) : ""}`;
  const wire = () => {
    const f = body.querySelector("#fForm");
    f.addEventListener("submit", (ev) => { ev.preventDefault(); guarded(f.querySelector(".primary"), async () => {
      const fd = new FormData(f);
      const bb = String(fd.get("blackbox") || "").split(/\r?\n/).map((l) => l.split("|").map((x) => x.trim())).filter((p) => p[0])
        .map(([id, from, to]) => ({ id, from, to: (to || "").split(",").map((x) => x.trim()).filter(Boolean) }));
      const payload = { name: fd.get("name"), environment: fd.get("environment"), config: { runtime: fd.get("runtime"), jolokia_url: fd.get("jolokia_url"),
        actuator_url: fd.get("actuator_url"), management_user: fd.get("management_user"), broker_id: fd.get("broker_id"), route_filter: fd.get("route_filter"),
        targets: lines(fd.get("targets")), test_urls: lines(fd.get("test_urls")), allow_state_changing: !!fd.get("allow_state_changing"), verify_tls: !!fd.get("verify_tls"),
        ca_file: fd.get("ca_file"), blackbox_routes: bb, authorization_confirmed: !!fd.get("authorization_confirmed"),
        authorization_statement: fd.get("authorization_statement"), authorization_reference: fd.get("authorization_reference"), production_approval: fd.get("production_approval") || undefined } };
      const r = st.t ? await api(`${FB}/${enc(st.t.id)}`, { method: "PUT", body: payload }) : await api(FB, { method: "POST", body: payload });
      stored("sg.fuse.target", r.connection.id); toast("Saved.", "success"); ctx.reload(); }); });
    f.querySelector("[data-test]")?.addEventListener("click", (e) => guarded(e.target, async () => { f.querySelector("[data-steps]").innerHTML = connectionSteps(await api(`${FB}/${enc(st.t.id)}/test-connection`, { method: "POST" })); }));
    f.querySelector("[data-del]")?.addEventListener("click", (e) => guarded(e.target, async () => { if (!window.confirm(`Delete ${st.t.name}?`)) return; await api(`${FB}/${enc(st.t.id)}`, { method: "DELETE" }); stored("sg.fuse.target", ""); ctx.reload(); }));
  };
  wire();
  body.querySelector("[data-new]")?.addEventListener("click", () => { st.t = null; body.querySelector("[data-form]").innerHTML = fuseForm(null, brokers); wire(); });
  if (st.t) wireCredentials(body, FB, st.t, ctx.reload);
}

async function fuseDiscovery(body, st, ctx) {
  const r = await api(`${FB}/${enc(st.t.id)}/routes`);
  body.innerHTML = `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap"><b>${esc(r.runtime.runtime)}</b><span class="muted small">${esc(r.mode)} · ${r.contexts.map((c) => `Camel ${esc(c.camel_version || "?")} (${esc(c.name)}, ${esc(c.state || "")})`).join(" · ")}</span>
    <span class="spacer"></span>${canEdit() ? '<button class="btn primary" data-disc>Run route discovery (records results)</button>' : ""}</div>
    <table class="data compact" style="margin-top:8px"><thead><tr><th>Route</th><th>State</th><th>From</th><th>Exchanges</th><th>Failed</th><th>Mean ms</th><th></th></tr></thead><tbody>
    ${r.routes.map((x) => `<tr><td>${esc(x.id)}<div class="muted small">${esc(x.context || x.description || "")}</div></td><td>${pill(x.state === "Started" ? "PASSED" : x.state ? "FAILED" : "")} ${esc(x.state || "")}</td><td class="small">${esc(short(x.from, 70))}</td>
      <td>${esc(x.exchanges ?? "-")}</td><td>${esc(x.failed ?? "-")}</td><td>${esc(x.mean_ms ?? "-")}</td><td><button class="btn small" data-map="${esc(x.id)}">Route map</button></td></tr>`).join("") || '<tr><td colspan="7" class="muted">No routes.</td></tr>'}</tbody></table>
    <p class="muted small">Read-only: SkyGuard never starts, stops, restarts, redeploys or changes a route.</p></div>`;
  body.querySelector("[data-disc]")?.addEventListener("click", (e) => guarded(e.target, async () => { const x = await api(`${FB}/${enc(st.t.id)}/runs`, { method: "POST", body: { kind: "discovery" } }); st.tab = "results"; st.openRun = x.run.id; ctx.rerender(); }));
  body.querySelectorAll("[data-map]").forEach((b) => b.addEventListener("click", () => { st.route = b.dataset.map; st.tab = "mapping"; ctx.rerender(); }));
}

async function fuseMapping(body, st) {
  const routes = (await api(`${FB}/${enc(st.t.id)}/routes`)).routes;
  const rid = st.route || routes[0]?.id;
  body.innerHTML = `<div class="card"><label>Route <select data-r>${routes.map((x) => `<option ${x.id === rid ? "selected" : ""}>${esc(x.id)}</option>`).join("")}</select></label><div data-m style="margin-top:8px"></div></div>`;
  const show = async (id) => {
    if (!id) return;
    const r = (await api(`${FB}/${enc(st.t.id)}/routes/${enc(id)}`)).route; const m = r.map || {};
    const eh = m.error_handling || {};
    body.querySelector("[data-m]").innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px">
      <div class="card"><h4 style="margin:0">Entry points</h4><ul>${(m.from || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <h4 style="margin:0">Destinations</h4><ul>${(m.to || []).map((x) => `<li>${esc(x.uri)} <span class="muted small">${esc(x.kind)}${x.when ? ` · when ${esc(x.when)}` : ""}</span></li>`).join("") || "<li class='muted'>None found</li>"}</ul></div>
      <div class="card"><h4 style="margin:0">Content-based routing</h4><ul>${(m.choices || []).map((c) => `<li><b>${esc(c.when)}</b> → ${esc((c.to || []).join(", "))}</li>`).join("") || "<li class='muted'>None</li>"}</ul>
        <h4 style="margin:0">Transformations</h4><ul>${(m.transformations || []).map((x) => `<li>${esc(x.kind)} ${esc(x.detail)}</li>`).join("") || "<li class='muted'>None</li>"}</ul></div>
      <div class="card"><h4 style="margin:0">Error handling</h4>${eh.has_error_handling === false ? '<p class="pill WARN">None found in the route definition</p>' : ""}
        <ul>${(eh.on_exception || []).map((o) => `<li>onException ${esc((o.exceptions || []).join(", "))} ${o.redelivery ? `redelivery ${esc(JSON.stringify(o.redelivery))}` : ""} → ${esc((o.to || []).join(", "))}</li>`).join("")}
        ${eh.dead_letter ? `<li>Dead-letter channel ${esc(eh.dead_letter)}</li>` : ""}${eh.do_try ? "<li>doTry / doCatch</li>" : ""}</ul>
        <p class="small">Statistics: ${esc(r.stats.exchanges ?? "-")} exchanges, ${esc(r.stats.failed ?? "-")} failed, ${esc(r.stats.redeliveries ?? "-")} redeliveries.</p></div></div>
      ${r.xml ? `<details style="margin-top:8px"><summary>Route definition (XML, read-only)</summary><pre class="code" style="max-height:50vh;overflow:auto">${esc(r.xml)}</pre></details>` : '<p class="muted small">No route definition available (black-box or not exposed).</p>'}`;
  };
  body.querySelector("[data-r]").addEventListener("change", (e) => { st.route = e.target.value; show(e.target.value); });
  await show(rid);
}

const FUSE_TEMPLATE = (t) => ({ type: "transformation", name: "Order is transformed for system B", route_id: "", description: "",
  input: { channel: "queue", destination: "SKYGUARD.TEST.ORDERS.IN", body: JSON.stringify({ id: 1, amount: 100 }), properties: {} },
  expect: { outputs: [{ name: "System B", destination: "SKYGUARD.TEST.ORDERS.OUT", expected_body: { orderId: 1, total: 105.0 }, rules: { numeric_tolerance: 0.01, ignore: ["$.generatedAt"] } }], route_failures: "none", within_seconds: 20 } });

async function fuseMonitoring(body, st) {
  const r = await api(`${FB}/${enc(st.t.id)}/monitor`);
  body.innerHTML = `<div class="card"><div class="row"><h3 style="margin:0">Route monitoring</h3><span class="spacer"></span><span class="muted small">${esc(r.at)}</span><button class="btn small" data-ref>Refresh</button></div>
    ${r.alerts.length ? `<ul>${r.alerts.map((a) => `<li>${pill(a.level)} ${esc(a.route)}: ${esc(a.text)}</li>`).join("")}</ul>` : '<p class="pill PASS">No alert.</p>'}
    <table class="data compact"><thead><tr><th>Route</th><th>State</th><th>Exchanges</th><th>Failed</th><th>Handled</th><th>Redeliveries</th><th>In flight</th><th>Mean / max ms</th></tr></thead><tbody>
    ${r.routes.map((x) => `<tr><td>${esc(x.id)}</td><td>${esc(x.state || "")}</td><td>${esc(x.exchanges ?? "-")}</td><td>${esc(x.failed ?? "-")}</td><td>${esc(x.failures_handled ?? "-")}</td><td>${esc(x.redeliveries ?? "-")}</td><td>${esc(x.inflight ?? "-")}</td><td>${esc(x.mean_ms ?? "-")} / ${esc(x.max_ms ?? "-")}</td></tr>`).join("")}</tbody></table></div>`;
  body.querySelector("[data-ref]").addEventListener("click", () => fuseMonitoring(body, st));
}

async function fuseCompare(body) {
  body.innerHTML = `<div class="card grid" style="gap:8px"><p class="muted small">Compare an expected message with the one a route produced (JSON, XML or text). Nothing is sent anywhere.</p>
    <div class="row" style="gap:8px;align-items:stretch"><label style="flex:1">Expected <textarea data-e rows="12" dir="auto">{"orderId": 1, "total": 105.00, "date": "2026-01-31"}</textarea></label>
      <label style="flex:1">Actual <textarea data-a rows="12" dir="auto">{"orderId": 1, "total": 105, "date": "31/01/2026", "extra": true}</textarea></label></div>
    <label>Rules (JSON: ignore, mappings, numeric_tolerance, dates, case_insensitive, trim, ignore_order, ignore_extra) <input data-r value='{"numeric_tolerance": 0.01, "ignore_extra": false}'></label>
    <div class="row"><button class="btn primary" data-go>Compare</button></div><div data-cmp-out></div></div>`;
  body.querySelector("[data-go]").addEventListener("click", (e) => guarded(e.target, async () => {
    let rules = {}; try { rules = JSON.parse(body.querySelector("[data-r]").value || "{}"); } catch { throw new Error("The rules are not valid JSON."); }
    const r = await api("/api/fuse/compare", { method: "POST", body: { expected: body.querySelector("[data-e]").value, actual: body.querySelector("[data-a]").value, rules } });
    body.querySelector("[data-cmp-out]").innerHTML = `<p>${r.match ? pill("PASSED") + " The messages match." : pill("FAILED") + ` ${r.differences.length} difference(s) in ${r.compared} compared field(s) (${esc(r.format)}).`}</p>
      ${r.differences.length ? `<table class="data compact"><thead><tr><th>Field</th><th>Expected</th><th>Actual</th><th>Kind</th></tr></thead><tbody>${r.differences.map((d) => `<tr><td>${esc(d.path)}${d.actual_path ? ` → ${esc(d.actual_path)}` : ""}</td><td>${esc(JSON.stringify(d.expected))}</td><td>${esc(JSON.stringify(d.actual))}</td><td>${esc(d.kind)}</td></tr>`).join("")}</tbody></table>` : ""}`;
  }));
}

export const fuseTesting = makePage({
  key: "fuse", title: "Red Hat Fuse Testing", targetLabel: "Fuse connection", listUrl: FB, listKey: "connections",
  describe: (t) => `${t.config.runtime}${t.config.jolokia_url ? ` · ${t.config.jolokia_url}` : ""}`,
  intro: "Version-aware, read-only access to Fuse on Karaf, Spring Boot or EAP (Jolokia / actuator), or black-box testing. Routing, transformation, reliability and end-to-end A → Fuse → ActiveMQ → B / C tests with correlation IDs.",
  noTarget: ["connections", "compare"],
  tabs: [["connections", "Fuse Connections"], ["discovery", "Route Discovery"], ["mapping", "Route Mapping"], ["designer", "AI Test Designer"], ["execution", "Test Execution"],
    ["monitoring", "Route Monitoring"], ["compare", "Transformation Comparison"], ["results", "Results & Defects"]],
  draw: {
    connections: fuseConnections, discovery: fuseDiscovery, mapping: fuseMapping, monitoring: fuseMonitoring, compare: fuseCompare,
    designer: (b, st, ctx) => casesTab(b, st, ctx, { listModule: "fuse", base: FB, template: FUSE_TEMPLATE,
      describe: (c) => `${c?.input?.channel} ${c?.input?.destination || c?.input?.url || ""} → ${(c?.expect?.outputs || []).map((o) => o.destination).join(", ")}` }),
    execution: (b, st, ctx) => casesTab(b, st, ctx, { listModule: "fuse", base: FB, template: FUSE_TEMPLATE,
      describe: (c) => `route ${c?.route_id || "-"}; within ${c?.expect?.within_seconds}s; failures: ${c?.expect?.route_failures}` }),
    results: (b, st, ctx) => resultsTab(b, st, ctx, "fuse"),
  },
});

// ======================================== ActiveMQ =======================================================================
const AB = "/api/activemq/brokers";

function brokerForm(t) {
  const c = t?.config || {};
  return `<form class="card grid" id="bForm" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px">
    <h3 style="grid-column:1/-1;margin:0">${t ? `Broker ${esc(t.name)}` : "New broker connection"}</h3>
    <label>Name <input name="name" required minlength="2" value="${esc(t?.name || "")}"></label>
    <label>Environment <select name="environment">${["Development", "QA", "Test", "UAT", "Staging", "Production"].map((e) => `<option ${t?.environment === e || (!t && e === "Test") ? "selected" : ""}>${e}</option>`).join("")}</select></label>
    <label>Broker type <select name="flavor">${[["auto", "Detect"], ["classic", "ActiveMQ Classic"], ["artemis", "ActiveMQ Artemis / AMQ Broker 7"]].map(([v, l]) => `<option value="${v}" ${c.flavor === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <label>Management (Jolokia) URL <input name="management_url" value="${esc(c.management_url || "")}" placeholder="Classic: http://broker:8161/api/jolokia · Artemis: http://broker:8161/console/jolokia"></label>
    <label>Management user <input name="management_user" value="${esc(c.management_user || "")}"></label>
    <label>STOMP host <input name="stomp_host" value="${esc(c.stomp_host || "")}"></label>
    <label>STOMP port <input name="stomp_port" type="number" value="${esc(c.stomp_port || 61613)}"></label>
    <label>STOMP user <input name="stomp_user" value="${esc(c.stomp_user || "")}"></label>
    <label class="row" style="gap:6px"><input type="checkbox" name="stomp_tls" ${c.stomp_tls ? "checked" : ""}> STOMP over TLS</label>
    <label class="row" style="gap:6px"><input type="checkbox" name="verify_tls" ${c.verify_tls === false ? "" : "checked"}> Verify TLS</label>
    <label>Company CA file <input name="ca_file" value="${esc(c.ca_file || "")}"></label>
    <label>Dead-letter queue (empty = broker default) <input name="dlq" value="${esc(c.dlq || "")}"></label>
    <label>Authorized test destinations (name or PREFIX*) <textarea name="test_destinations" rows="3" placeholder="SKYGUARD.TEST.*">${esc((c.test_destinations || []).join("\n"))}</textarea></label>
    <label>SkyGuard's own destinations it may consume from <textarea name="consume_destinations" rows="3" placeholder="SKYGUARD.TEST.SELF.*">${esc((c.consume_destinations || []).join("\n"))}</textarea></label>
    <label class="row" style="gap:6px"><input type="checkbox" name="allow_publish" ${c.allow_publish ? "checked" : ""}> Allow publishing test messages to the test destinations</label>
    <label>Performance: max messages <input name="max_messages" type="number" min="1" max="1000" value="${esc(c.max_messages || 200)}"></label>
    <label>Performance: messages per second <input name="rate" type="number" min="1" max="200" value="${esc(c.rate || 20)}"></label>
    ${authorizationFields()}
    ${t?.environment === "Production" && isAdmin() ? '<label style="grid-column:1/-1">Admin approval for publishing on Production <input name="production_approval" maxlength="300"></label>' : ""}
    <p class="muted small" style="grid-column:1/-1;margin:0">Never: purge, delete, move or retry messages; consume business messages; publish to business, dead-letter or broker destinations.</p>
    <div class="row" style="grid-column:1/-1;gap:8px"><span class="spacer"></span>${t ? '<button class="btn" type="button" data-test>Test connection</button><button class="btn" type="button" data-del>Delete</button>' : ""}<button class="btn primary">Save</button></div>
    <div data-steps style="grid-column:1/-1"></div></form>`;
}

async function brokerConnections(body, st, ctx) {
  body.innerHTML = `${canEdit() ? '<div class="row" style="margin-bottom:8px"><button class="btn" data-new>New broker</button></div>' : ""}<div data-form>${brokerForm(st.t)}</div>${st.t ? credentialsCard(AB, st.t) : ""}`;
  const wire = () => {
    const f = body.querySelector("#bForm");
    f.addEventListener("submit", (ev) => { ev.preventDefault(); guarded(f.querySelector(".primary"), async () => {
      const fd = new FormData(f);
      const payload = { name: fd.get("name"), environment: fd.get("environment"), config: { flavor: fd.get("flavor"), management_url: fd.get("management_url"),
        management_user: fd.get("management_user"), stomp_host: fd.get("stomp_host"), stomp_port: Number(fd.get("stomp_port")) || 61613, stomp_user: fd.get("stomp_user"),
        stomp_tls: !!fd.get("stomp_tls"), verify_tls: !!fd.get("verify_tls"), ca_file: fd.get("ca_file"), dlq: fd.get("dlq"),
        test_destinations: lines(fd.get("test_destinations")), consume_destinations: lines(fd.get("consume_destinations")), allow_publish: !!fd.get("allow_publish"),
        max_messages: Number(fd.get("max_messages")) || 200, rate: Number(fd.get("rate")) || 20, authorization_confirmed: !!fd.get("authorization_confirmed"),
        authorization_statement: fd.get("authorization_statement"), authorization_reference: fd.get("authorization_reference"), production_approval: fd.get("production_approval") || undefined } };
      const r = st.t ? await api(`${AB}/${enc(st.t.id)}`, { method: "PUT", body: payload }) : await api(AB, { method: "POST", body: payload });
      stored("sg.activemq.target", r.broker.id); toast("Saved.", "success"); ctx.reload(); }); });
    f.querySelector("[data-test]")?.addEventListener("click", (e) => guarded(e.target, async () => { f.querySelector("[data-steps]").innerHTML = connectionSteps(await api(`${AB}/${enc(st.t.id)}/test-connection`, { method: "POST" })); }));
    f.querySelector("[data-del]")?.addEventListener("click", (e) => guarded(e.target, async () => { if (!window.confirm(`Delete ${st.t.name}?`)) return; await api(`${AB}/${enc(st.t.id)}`, { method: "DELETE" }); stored("sg.activemq.target", ""); ctx.reload(); }));
  };
  wire();
  body.querySelector("[data-new]")?.addEventListener("click", () => { st.t = null; body.querySelector("[data-form]").innerHTML = brokerForm(null); wire(); });
  if (st.t) wireCredentials(body, AB, st.t, ctx.reload);
}

async function brokerDiscovery(body, st, ctx, { monitoring = false } = {}) {
  const r = await api(`${AB}/${enc(st.t.id)}/monitor`);
  const b = r.broker;
  body.innerHTML = `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap"><b>${esc(b.flavor)} ${esc(b.version)}</b><span class="muted small">broker ${esc(b.name)} · up ${esc(b.uptime)} · memory ${esc(b.memory_pct)}% · store ${esc(b.store_pct ?? "-")}% · ${esc(b.connections)} connections · ${esc(b.consumers)} consumers</span>
    <span class="spacer"></span>${!monitoring && canEdit() ? '<button class="btn primary" data-disc>Run discovery (records results)</button>' : '<button class="btn small" data-ref>Refresh</button>'}</div>
    ${r.alerts.length ? `<ul>${r.alerts.map((a) => `<li>${pill(a.level)} ${esc(a.destination || "broker")}: ${esc(a.text)}</li>`).join("")}</ul>` : '<p class="pill PASS">No alert.</p>'}
    <table class="data compact"><thead><tr><th>Destination</th><th>Type</th><th>Depth</th><th>Enqueued</th><th>Dequeued</th><th>Consumers</th><th>In flight</th><th>Expired</th><th></th></tr></thead><tbody>
    ${r.destinations.map((d) => `<tr><td>${esc(d.name)}${d.address && d.address !== d.name ? `<div class="muted small">address ${esc(d.address)}</div>` : ""}</td><td>${esc(d.type)}</td><td>${esc(d.depth ?? "-")}</td><td>${esc(d.enqueued ?? "-")}</td><td>${esc(d.dequeued ?? "-")}</td>
      <td>${esc(d.consumers ?? "-")}</td><td>${esc(d.in_flight ?? "-")}</td><td>${esc(d.expired ?? "-")}</td><td>${d.type !== "topic" ? `<button class="btn small" data-browse="${esc(d.name)}">Browse</button>` : ""}</td></tr>`).join("")}</tbody></table>
    <p class="muted small">Browsing uses the management API and does not consume anything. Bodies and properties are sanitized.</p></div>`;
  body.querySelector("[data-disc]")?.addEventListener("click", (e) => guarded(e.target, async () => { const x = await api(`${AB}/${enc(st.t.id)}/runs`, { method: "POST", body: { kind: "discovery" } }); st.tab = "results"; st.openRun = x.run.id; ctx.rerender(); }));
  body.querySelector("[data-ref]")?.addEventListener("click", () => brokerDiscovery(body, st, ctx, { monitoring }));
  body.querySelectorAll("[data-browse]").forEach((btn) => btn.addEventListener("click", () => guarded(btn, async () => {
    const m = await api(`${AB}/${enc(st.t.id)}/browse?destination=${enc(btn.dataset.browse)}&limit=20`);
    openModal(`Browse ${btn.dataset.browse}`, `<p class="muted small">${esc(m.note)}</p>${m.messages.map((x) => `<div class="card" style="margin:6px 0"><div class="small">${esc(x.id)} · correlation ${esc(x.correlation_id || "-")} · priority ${esc(x.priority)} · deliveries ${esc(x.delivery_count ?? "-")}${x.dlq_cause ? ` · ${esc(x.dlq_cause)}` : ""}</div>
      <pre class="code" dir="auto">${esc(short(x.body, 2000))}</pre><div class="muted small">${esc(JSON.stringify(x.properties || {}))}</div></div>`).join("") || '<p class="muted">Empty.</p>'}`, { wide: true });
  })));
}

const AMQ_TEMPLATES = {
  publish: (t) => ({ type: "publish_verify", name: "Order message reaches its queue", destination: (t.config.test_destinations[0] || "SKYGUARD.TEST.*").replace("*", "ORDERS"),
    body: JSON.stringify({ orderId: 1, status: "NEW" }), properties: { messageType: "ORDER" }, expect: { jsonpath: { "$.orderId": 1 }, within_seconds: 10 } }),
  consumer: (t) => ({ type: "consumer", name: "Order service consumes the message", destination: (t.config.test_destinations[0] || "SKYGUARD.TEST.*").replace("*", "ORDERS.IN"),
    body: JSON.stringify({ orderId: 2 }), expect: { within_seconds: 20, arrive_at: null } }),
  dlq: (t) => ({ type: "retry_dlq", name: "Invalid message is retried then dead-lettered", destination: (t.config.test_destinations[0] || "SKYGUARD.TEST.*").replace("*", "ORDERS.IN"),
    body: "{not valid json", expect: { within_seconds: 60 } }),
};

export const activemqTesting = makePage({
  key: "activemq", title: "ActiveMQ Testing", targetLabel: "Broker", listUrl: AB, listKey: "brokers",
  describe: (t) => `${t.config.flavor} · ${t.config.stomp_host || ""}:${t.config.stomp_port || ""}`,
  intro: "ActiveMQ Classic and Artemis through separate read-only adapters: discovery, publish & verify on authorized test destinations, consumer verification, retry and dead-letter tests, selectors, TTL, priority, durable subscriptions, performance and broker security.",
  noTarget: ["connections"],
  tabs: [["connections", "Broker Connections"], ["discovery", "Queue & Topic Discovery"], ["designer", "Message Test Designer"], ["publish", "Publish & Verify"],
    ["consumer", "Consumer Verification"], ["dlq", "Retry & Dead-Letter Testing"], ["monitoring", "Broker Monitoring"], ["results", "Results & Defects"]],
  draw: {
    connections: brokerConnections, discovery: (b, st, ctx) => brokerDiscovery(b, st, ctx), monitoring: (b, st, ctx) => brokerDiscovery(b, st, ctx, { monitoring: true }),
    designer: (b, st, ctx) => casesTab(b, st, ctx, { listModule: "activemq", base: AB, template: AMQ_TEMPLATES.publish, describe: (c) => `${c?.destination || ""} ${c?.count > 1 ? `×${c.count}` : ""}` }),
    publish: (b, st, ctx) => casesTab(b, st, ctx, { listModule: "activemq", base: AB, template: AMQ_TEMPLATES.publish, kinds: ["publish_verify", "selector", "priority", "ttl", "durable", "performance", "security"], describe: (c) => `${c?.destination || ""}` }),
    consumer: (b, st, ctx) => casesTab(b, st, ctx, { listModule: "activemq", base: AB, template: AMQ_TEMPLATES.consumer, kinds: ["consumer"], describe: (c) => `${c?.destination} within ${c?.expect?.within_seconds}s` }),
    dlq: (b, st, ctx) => casesTab(b, st, ctx, { listModule: "activemq", base: AB, template: AMQ_TEMPLATES.dlq, kinds: ["retry_dlq"], describe: (c) => `${c?.destination} within ${c?.expect?.within_seconds}s` }),
    results: (b, st, ctx) => resultsTab(b, st, ctx, "activemq"),
  },
});
