// 23.14.0: the same buttons on every applicable result: Analyze with AI, Create TFS Defect, Generate PDF Report,
// Email Developer, View Evidence and Retest. A passed result gets View Evidence, PDF and Email only.
// subject kinds: result (Security / Fuse / ActiveMQ result), finding (security finding), run (suite summary),
// exec (a run in Execution History).

import { api } from "./api.js";
import { esc, toast, openModal } from "./util.js";
import { canEdit } from "./auth.js";

const B = "/api/test-actions";
const enc = encodeURIComponent;
const json = (v) => JSON.stringify(v ?? null, null, 2);
const PASSED = new Set(["PASSED", "COMPLETED", "fixed"]);

export function actionButtons(kind, id, status, { retest = true, compact = true, defectAlways = false } = {}) {
  const passed = PASSED.has(String(status || ""));
  const cls = compact ? "btn small" : "btn";
  const b = (act, label, extra = "") => `<button type="button" class="${cls}" data-ra="${act}" data-kind="${esc(kind)}" data-id="${esc(id)}" ${extra}>${label}</button>`;
  const out = [];
  if (!passed) out.push(b("analyze", "Analyze with AI"));
  if ((!passed || defectAlways) && canEdit()) out.push(b("defect", "Create TFS Defect"));
  out.push(b("pdf", "Generate PDF Report"));
  if (canEdit()) out.push(b("email", "Email Developer"));
  out.push(b("evidence", "View Evidence"));
  if (retest && !passed && canEdit() && kind !== "run") out.push(b("retest", "Retest"));
  return `<span class="row ra" style="gap:4px;flex-wrap:wrap">${out.join("")}</span>`;
}

export function wireActions(root, { onChange } = {}) {
  root.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-ra]");
    if (!btn || !root.contains(btn)) return;
    const { ra, kind, id } = btn.dataset;
    btn.disabled = true;
    try {
      if (ra === "analyze") await openAnalysis(kind, id);
      else if (ra === "defect") await openDefect(kind, id, onChange);
      else if (ra === "pdf") await makePdf(kind, id);
      else if (ra === "email") await openEmailDraft(kind, id);
      else if (ra === "evidence") await openEvidence(kind, id);
      else if (ra === "retest") {
        const r = await api(`${B}/${kind}/${enc(id)}/retest`, { method: "POST" });
        toast(`Retest started (${r.run?.id || "run"}). The result appears in the results list when it finishes.`, "success", 6000);
        onChange?.();
      }
    } catch (err) {
      toast(err.message, "error", 9000);
    } finally { btn.disabled = false; }
  });
}

// ---- Analyze with AI ----------------------------------------------------------------------------------------------
function list(items) { return (items || []).length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="muted">None.</p>'; }

function analysisHtml(a) {
  if (!a) return '<p class="muted">No AI analysis yet.</p>';
  return `<div class="grid" style="gap:10px">
    <div class="card" style="border-left:4px solid var(--pass,#2e7d32)"><h4 style="margin:0 0 4px">Verified facts (from the evidence, not the AI)</h4>${list(a.verified_facts)}
      ${(a.missing_evidence || []).length ? `<p class="muted small">Missing evidence: ${esc(a.missing_evidence.join(", "))}</p>` : ""}</div>
    <div class="card"><h4 style="margin:0 0 4px">AI analysis <span class="pill WAITING">${esc(a.confidence || "?")} confidence</span></h4>
      <p><b>Summary:</b> ${esc(a.defect_summary)}</p>
      <p><b>Expected:</b> ${esc(a.expected_result)}<br><b>Actual:</b> ${esc(a.actual_result)}</p>
      <p><b>Affected component:</b> ${esc(a.affected_component)} · <b>Severity:</b> ${esc(a.severity_suggestion)} · <b>Priority:</b> ${esc(a.priority_suggestion)}</p>
      <p><b>Supporting evidence:</b></p>${list(a.supporting_evidence)}
      <p><b>Recommended investigation:</b></p>${list(a.recommended_investigation)}
      <p><b>Suggested fix:</b> ${esc(a.suggested_fix)}</p><p><b>Retest:</b> ${esc(a.retest_recommendation)}</p></div>
    <div class="card" style="border-left:4px solid var(--warn,#ed6c02)"><h4 style="margin:0 0 4px">AI hypotheses (not verified)</h4>
      <p><b>Potential root cause:</b> ${esc(a.potential_root_cause)}</p>${list(a.hypotheses)}</div></div>`;
}

async function openAnalysis(kind, id) {
  const { el } = openModal("Analyze with AI", '<div data-a><p class="muted">Loading…</p></div>', { wide: true });
  const box = el.querySelector("[data-a]");
  const show = async () => {
    const h = (await api(`${B}/${kind}/${enc(id)}/analyses`)).history;
    box.innerHTML = `<p class="muted small">The On-Premise AI on this PC reads the sanitized evidence only (no passwords, tokens or cookies). It suggests; it does not decide.</p>
      <div class="row" style="gap:8px"><button class="btn primary" data-run>${h.length ? "Analyze again" : "Analyze now"}</button>
        ${h.length > 1 ? `<select data-hist>${h.map((x, i) => `<option value="${i}">${esc(x.created_at)} · ${esc(x.model || "")}</option>`).join("")}</select>` : ""}</div>
      <div data-out style="margin-top:10px">${analysisHtml(h[0]?.analysis)}</div>`;
    box.querySelector("[data-hist]")?.addEventListener("change", (e) => { box.querySelector("[data-out]").innerHTML = analysisHtml(h[Number(e.target.value)].analysis); });
    box.querySelector("[data-run]").addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Analyzing… (the local AI can take a minute)";
      try { await api(`${B}/${kind}/${enc(id)}/analyze`, { method: "POST" }); await show(); }
      catch (err) {
        toast(err.message, "error", 9000); e.target.disabled = false; e.target.textContent = "Analyze again";
        if (err.details?.verified_facts) box.querySelector("[data-out]").innerHTML = analysisHtml({ verified_facts: err.details.verified_facts });
      }
    });
  };
  await show();
}

// ---- Create TFS Defect ----------------------------------------------------------------------------------------------
// 23.15.0: the body is readable text (steps, expected, actual, evidence as "label: value" lines, not a JSON dump); the
// run's screenshots and result files are attached to the TFS bug; "Approve and send now" sends it at once.
let people = null;
const kb = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
export async function openDefect(kind, id, onChange) {
  const p = (await api(`${B}/${kind}/${enc(id)}/defect`)).preview;
  const files = p.attachments || [];
  const { el, close } = openModal("Create TFS Defect (preview)", `
    <form class="grid" style="gap:10px" data-f>
      <label>Title <input name="title" required minlength="3" maxlength="250" value="${esc(p.title)}"></label>
      <div class="row" style="gap:10px;flex-wrap:wrap">
        <label>Severity <select name="severity">${["1 - Critical", "2 - High", "3 - Medium", "4 - Low"].map((s, i) => `<option value="${i + 1}" ${Number(p.severity) === i + 1 ? "selected" : ""}>${s}</option>`).join("")}</select></label>
        <label>Priority <select name="priority">${[1, 2, 3, 4].map((n) => `<option value="${n}" ${Number(p.priority) === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
        <label>Assign to <select name="assigned_to"><option value="">Not assigned</option></select></label></div>
      <label>Steps to reproduce, expected and actual result <textarea name="repro_text" rows="16" dir="auto" style="font-family:inherit;white-space:pre-wrap">${esc(p.repro_text || "")}</textarea></label>
      ${files.length ? `<div><label><span><input type="checkbox" name="attach" checked> Attach ${files.length} file(s) to the TFS bug</span></label>
        <ul class="small muted" style="margin:4px 0 0 18px">${files.map((f) => `<li>${esc(f.name)} (${esc(f.kind)}, ${kb(f.size)})</li>`).join("")}</ul></div>`
        : '<p class="muted small" style="margin:0">No screenshots or result files were recorded for this item.</p>'}
      <label><span><input type="checkbox" name="send_now"> Approve and send to TFS now (otherwise it waits in Defect Review for approval)</span></label>
      <p class="muted small" style="margin:0" data-note>The defect goes to Defect Review first (the project's mandatory fields, duplicate check and approval) unless you tick "send now". If TFS refuses it, the draft is kept and the reason is shown.</p>
      <div class="row"><span class="spacer"></span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" type="submit">Create defect</button></div></form>`, { wide: true });
  const sel = el.querySelector("[name=assigned_to]");
  api("/api/defects/assignees").then((r) => {
    people = people || r.people || [];
    sel.innerHTML = '<option value="">Not assigned</option>' + people.map((x) => `<option value="${esc(x.unique_name || x.name)}">${esc(x.name)}</option>`).join("");
  }).catch(() => { /* TFS people are optional */ });
  el.querySelector("[data-f]").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const btn = ev.target.querySelector("[type=submit]");
    btn.disabled = true; btn.textContent = fd.get("send_now") ? "Sending to TFS…" : "Creating…";
    try {
      const r = await api(`${B}/${kind}/${enc(id)}/defect`, { method: "POST", body: {
        title: String(fd.get("title") || "").trim(), severity: Number(fd.get("severity")), priority: Number(fd.get("priority")),
        repro_text: String(fd.get("repro_text") || ""), attach: !!fd.get("attach"), send_now: !!fd.get("send_now"),
        assigned_to: fd.get("assigned_to") || null } });
      if (r.note) toast(r.note, "info", 9000);
      else if (r.tfs_id) toast(`Created in TFS as #${r.tfs_id}${r.attachments?.length ? ` with ${r.attachments.length} attachment(s)` : ""}.`, "success", 9000);
      else toast(`Defect draft #${r.id ?? ""} is waiting in Defect Review.${r.duplicates?.length ? " Possible duplicates were found." : ""}`, "success", 8000);
      close(); onChange?.();
      if (!r.tfs_id && r.id) location.hash = `#/${r.source === "performance" ? "perf-defects" : "defect-review"}?id=${r.id}`;
    } catch (err) {
      toast(err.message, "error", 12000);
      btn.disabled = false; btn.textContent = "Create defect";
      if (err.details?.draft_id) el.querySelector("[data-note]").textContent = `The draft #${err.details.draft_id} was kept in Defect Review: ${err.message}`;
    }
  });
}

// ---- PDF -------------------------------------------------------------------------------------------------------------
async function makePdf(kind, id) {
  toast("Making the PDF report…", "info", 3000);
  const r = (await api(`${B}/${kind}/${enc(id)}/pdf`, { method: "POST" })).pdf;
  const { el } = openModal(r.title, `<p>The report is ready (${Math.max(1, Math.round(r.size / 1024))} KB). It contains no passwords, tokens or cookies.</p>
    <div class="row" style="gap:8px"><a class="btn primary" href="${esc(r.url)}" target="_blank" rel="noopener">Open</a>
    <a class="btn" href="${esc(r.url)}?download=true">Download</a><button class="btn" data-mail>Email Developer</button></div>`);
  el.querySelector("[data-mail]").addEventListener("click", () => openEmailDraft(kind, id));
}

// ---- Email Developer ------------------------------------------------------------------------------------------------
async function openEmailDraft(kind, id) {
  const d = (await api(`${B}/${kind}/${enc(id)}/email`, { method: "POST" })).draft;
  openCompose(d);
}

export function openCompose(d) {
  const addr = (a) => (a || []).join("; ");
  const ctx = d.context || {};
  const { el, close } = openModal("Email Developer", `
    <form class="grid" style="gap:8px" data-f>
      ${!d.email_configured ? '<p class="pill FAIL" style="margin:0">No SMTP server is configured yet (Settings &gt; Email / SMTP). You can prepare the draft; it is kept until it can be sent.</p>' : ""}
      ${d.status === "failed" ? `<p class="pill FAIL" style="margin:0">The last send failed: ${esc(d.error || "")}. The draft is kept; correct it and send again.</p>` : ""}
      <div class="row" style="gap:8px;flex-wrap:wrap"><label style="flex:1">To <input name="to" value="${esc(addr(d.to_addrs))}" placeholder="dev@company.local; …"></label>
        ${d.directory?.length ? `<label>Developer directory <select data-dir><option value="">Add from the directory…</option>${d.directory.map((p) => `<option value="${esc(p.email)}">${esc(p.name)}${p.team ? ` (${esc(p.team)})` : ""}</option>`).join("")}</select></label>` : ""}</div>
      <label>CC <input name="cc" value="${esc(addr(d.cc_addrs))}"></label>
      <label>BCC <input name="bcc" value="${esc(addr(d.bcc_addrs))}"></label>
      <label>Subject <input name="subject" required maxlength="250" value="${esc(d.subject)}"></label>
      <div class="row" style="gap:8px;flex-wrap:wrap">${["application", "environment", "execution", "reference"].map((k) => `<label>${k[0].toUpperCase() + k.slice(1)} <input name="ctx_${k}" value="${esc(ctx[k] || "")}" maxlength="200"></label>`).join("")}</div>
      <label>Message <textarea name="body" rows="12" dir="auto">${esc(d.body)}</textarea></label>
      <fieldset><legend>Attachments (limit ${esc(d.limits?.max_attachment_mb)} MB each, ${esc(d.limits?.max_total_attachment_mb)} MB together; sanitized)</legend>
        ${(d.available || []).map((o) => `<label class="row" style="gap:6px"><input type="checkbox" name="att" value="${esc(o.key)}" ${(d.attachments || []).includes(o.key) ? "checked" : ""}> ${esc(o.label)} <span class="muted small">${o.made_on_send ? "made when sent" : `${Math.max(1, Math.round((o.size || 0) / 1024))} KB`}</span></label>`).join("") || '<p class="muted">No attachments available.</p>'}</fieldset>
      <p class="muted small" style="margin:0">Recipients allowed: ${d.limits?.allow_external ? "any domain" : esc((d.limits?.allowed_domains || []).join(", ") || "the sender's domain")}. Nothing is sent until you press Send and confirm. "Accepted" means your SMTP server accepted the email; delivery to the mailbox is not confirmed by SMTP.</p>
      <div class="row" style="gap:8px"><span class="muted small">Draft ${esc(d.id)} · ${esc(d.status)}</span><span class="spacer"></span>
        <button class="btn" type="button" data-close>Close</button><button class="btn" type="button" data-save>Save draft</button>
        <button class="btn primary" type="submit" ${d.status === "accepted" ? "disabled" : ""}>Send…</button></div></form>`, { wide: true });
  const f = el.querySelector("[data-f]");
  el.querySelector("[data-dir]")?.addEventListener("change", (e) => {
    if (!e.target.value) return;
    f.to.value = [f.to.value.trim(), e.target.value].filter(Boolean).join("; "); e.target.value = "";
  });
  const values = () => ({
    to: f.to.value, cc: f.cc.value, bcc: f.bcc.value, subject: f.subject.value, body: f.body.value,
    attachments: [...f.querySelectorAll("[name=att]:checked")].map((x) => x.value),
    context: Object.fromEntries(["application", "environment", "execution", "reference"].map((k) => [k, f[`ctx_${k}`].value])),
  });
  const save = async () => api(`${B}/email/${enc(d.id)}`, { method: "PUT", body: values() });
  el.querySelector("[data-save]").addEventListener("click", async () => { try { await save(); toast("Draft saved.", "success"); } catch (err) { toast(err.message, "error", 9000); } });
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const saved = (await save()).draft;
      const who = [...saved.to_addrs, ...saved.cc_addrs, ...saved.bcc_addrs];
      const files = saved.attachments.length;
      if (!window.confirm(`Send this email to ${who.length} recipient(s)?\n\n${who.join("\n")}\n\nSubject: ${saved.subject}\nAttachments: ${files}`)) return;
      const r = (await api(`${B}/email/${enc(d.id)}/send`, { method: "POST", body: { confirm: true } })).draft;
      toast(`Accepted by the SMTP server (${r.smtp_reply?.accepted_by || "mail server"}). Delivery to the mailboxes is not confirmed by SMTP.`, "success", 9000);
      close();
    } catch (err) {
      if (err.code === "EMAIL_DUPLICATE" && window.confirm(`${err.message}\n\nSend it again anyway?`)) {
        try { await api(`${B}/email/${enc(d.id)}/send`, { method: "POST", body: { confirm: true, allow_resend: true } }); toast("Accepted by the SMTP server.", "success"); close(); }
        catch (e2) { toast(e2.message, "error", 9000); }
        return;
      }
      toast(err.message, "error", 10000);
    }
  });
}

// ---- View Evidence -------------------------------------------------------------------------------------------------
async function openEvidence(kind, id) {
  const r = await api(`${B}/${kind}/${enc(id)}/evidence`);
  const f = r.facts || {};
  const trace = f.evidence?.trace;
  openModal("Evidence", `
    <p class="muted small">Sanitized: passwords, tokens, cookies, keys and authorization headers are removed.</p>
    <table class="data compact"><tbody>${[["Status", f.status], ["Test", f.test_name], ["Module", f.module_name], ["Application", f.application], ["Environment", f.environment],
      ["Execution", f.execution_id], ["Correlation ID", f.correlation_id], ["URL", f.url], ["Severity", f.severity]].filter(([, v]) => v)
      .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("")}</tbody></table>
    ${f.expected ? `<h4>Expected</h4><pre class="code">${esc(typeof f.expected === "string" ? f.expected : json(f.expected))}</pre>` : ""}
    ${f.actual ? `<h4>Actual</h4><pre class="code">${esc(typeof f.actual === "string" ? f.actual : json(f.actual))}</pre>` : ""}
    ${trace ? `<h4>End-to-end trace</h4><ol>${trace.map((t) => `<li>${esc(t.hop)}: ${esc(t.destination || t.url || "")} ${t.found === false ? '<span class="pill FAIL">not found</span>' : t.found ? '<span class="pill PASS">found</span>' : ""} ${esc(t.note || "")}</li>`).join("")}</ol>` : ""}
    ${(r.screenshots || []).map((s) => `<img src="${esc(s.url)}" alt="Screenshot ${s.index + 1}" style="max-width:100%;border:1px solid var(--line,#ccc);margin:6px 0">`).join("")}
    <details><summary>All evidence (JSON)</summary><pre class="code" style="max-height:50vh;overflow:auto">${esc(json(f.evidence))}</pre></details>
    ${(f.missing || []).length ? `<p class="muted small">Not available: ${esc(f.missing.join(", "))}</p>` : ""}`, { wide: true });
}
