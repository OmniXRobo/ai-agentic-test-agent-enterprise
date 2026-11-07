// 23.3.31: AI analysis report panel shared by Recent Runs (AI-Driven Automation, Guided Browser Automation),
// Performance Results and the Dashboard. The On-Premise AI writes the analysis; the tester edits it or asks again;
// when satisfied, Make PDF, then Open / Download, or Email PDF (subject and body written from the analysis, the PDF
// attached, sent through Settings > Outlook Email only when the tester presses Send).

import { api } from "./api.js";
import { esc, toast, dateTimeOf, openModal } from "./util.js";

const SECTIONS = [
  ["observations", "Observations"],
  ["issues", "Issues and risks"],
  ["suspected_bottlenecks", "Suspected bottlenecks"],
  ["recommendations", "Recommendations"],
  ["undetermined", "Not determinable from this data"],
];

const base = (kind, id) => `/api/ai-reports/${kind}/${encodeURIComponent(id)}`;
const bottleneckText = (b) => `${b.area} (${b.confidence} confidence): ${b.evidence}`;

function listOf(a, key) {
  const items = a[key] || [];
  return key === "suspected_bottlenecks" ? items.map(bottleneckText) : items;
}

export function analysisView(a) {
  const list = (items) => `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  return `<p>${esc(a.summary)}</p>
    ${SECTIONS.map(([k, label]) => (listOf(a, k).length ? `<strong>${esc(label)}</strong>${list(listOf(a, k))}` : "")).join("")}
    <p class="muted">AI analysis by ${esc(a.model || "the On-Premise AI")} on ${esc(dateTimeOf(a.created_at))}${a.edited ? `, edited ${esc(dateTimeOf(a.updated_at))}` : ""}. Treat it as hypotheses to verify.</p>`;
}

function editView(a, kind) {
  const keys = SECTIONS.filter(([k]) => k !== "suspected_bottlenecks" || kind === "perf").filter(([k]) => kind === "perf" ? k !== "issues" : k !== "suspected_bottlenecks");
  return `<label>Summary <textarea data-edit="summary" rows="3">${esc(a.summary || "")}</textarea></label>
    ${keys.map(([k, label]) => `<label>${esc(label)} <span class="muted small">(one point per line)</span>
      <textarea data-edit="${k}" rows="${Math.min(10, Math.max(3, listOf(a, k).length + 1))}">${esc(listOf(a, k).join("\n"))}</textarea></label>`).join("")}`;
}

/**
 * Mounts the panel in `box`.
 *   kind: "run" | "perf" | "dashboard";  id: run id (or "current" for the dashboard)
 *   intro: text shown before the first analysis;  initial: an already loaded state (skips the first GET)
 */
export async function mountAiReport(box, { kind, id, intro = "", analyzeLabel = "Analyze with On-Premise AI", initial = null }) {
  let st = initial;
  let editing = false;
  const busy = (msg) => { box.querySelector("[data-body]").innerHTML = `<p class="muted">${esc(msg)}</p>`; box.querySelectorAll("button").forEach((b) => (b.disabled = true)); };

  const draw = () => {
    const a = st?.analysis;
    const pdfUrl = `${base(kind, id)}/pdf`;
    box.innerHTML = `
      <div class="row ai-report-bar" style="flex-wrap:wrap;gap:6px">
        <button class="btn ${a ? "" : "primary"}" data-act="analyze">${a ? "Analyze again" : esc(analyzeLabel)}</button>
        ${a && !editing ? '<button class="btn" data-act="edit" title="Change the wording before making the PDF">Edit</button>' : ""}
        ${editing ? '<button class="btn primary" data-act="save">Save changes</button><button class="btn" data-act="cancel">Cancel</button>' : ""}
        <span class="spacer"></span>
        ${a && !editing ? `<button class="btn ${st.has_pdf ? "" : "primary"}" data-act="pdf" title="Make the PDF from this analysis">${st.has_pdf ? "Make PDF again" : "Generate PDF"}</button>
          ${st.has_pdf ? `<a class="btn" href="${pdfUrl}" target="_blank" rel="noopener">Open PDF</a><a class="btn" href="${pdfUrl}?download=true">Download PDF</a>` : ""}
          <button class="btn" data-act="email" title="Email the PDF through Outlook; subject and body are written for you">Email PDF</button>` : ""}
      </div>
      <div data-body style="margin-top:8px">${a ? (editing ? editView(a, kind) : analysisView(a)) : `<p class="muted">${esc(intro)}</p>`}</div>
      ${a && !editing && st.pdf_made_at && !st.has_pdf ? '<p class="muted small">The analysis changed after the last PDF; Generate PDF makes it again.</p>' : ""}`;
    box.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => ACTIONS[b.dataset.act]()));
  };

  const call = async (msg, fn) => {
    busy(msg);
    try { st = await fn(); } catch (err) { toast(err.message, "error"); }
    draw();
  };

  const ACTIONS = {
    analyze: () => call("The On-Premise AI is writing the analysis… (this can take a minute on a PC without a GPU)",
      () => api(`${base(kind, id)}/analyze`, { method: "POST", body: {} })),
    edit: () => { editing = true; draw(); },
    cancel: () => { editing = false; draw(); },
    save: () => {
      const body = {};
      box.querySelectorAll("[data-edit]").forEach((t) => {
        body[t.dataset.edit] = t.dataset.edit === "summary" ? t.value : t.value.split("\n").map((x) => x.trim()).filter(Boolean);
      });
      editing = false;
      return call("Saving…", () => api(base(kind, id), { method: "PATCH", body }));
    },
    pdf: async () => {
      await call("Making the PDF…", () => api(`${base(kind, id)}/pdf`, { method: "POST", body: {} }));
      if (st?.has_pdf) toast("PDF ready: Open PDF or Download PDF", "success");
    },
    email: () => openEmail(kind, id, () => { if (!st.has_pdf) refresh(); }),
  };

  const refresh = async () => { try { st = await api(base(kind, id)); } catch (err) { toast(err.message, "error"); } draw(); };
  if (st) draw(); else { box.innerHTML = '<p class="muted">Loading…</p>'; await refresh(); }
}

export async function openEmail(kind, id, onSent = () => {}) {
  let d;
  try { d = await api(`${base(kind, id)}/email`); } catch (err) { toast(err.message, "error"); return; }
  const { el, close } = openModal("Email the PDF report", `
    ${d.email_configured ? "" : '<p class="pill WARN" style="white-space:normal">Outlook email is not set up yet. Set it up under <a href="#/email-settings">Settings &gt; Outlook Email</a>, then send.</p>'}
    <form class="grid" id="mailForm" style="gap:10px">
      <label>To <input name="to" value="${esc(d.to)}" placeholder="name@company.com; other@company.com" required></label>
      <label>Cc <input name="cc" value="${esc(d.cc)}" placeholder="optional"></label>
      <label>Subject <input name="subject" value="${esc(d.subject)}" maxlength="250" required></label>
      <label>Message <textarea name="body" rows="14">${esc(d.body)}</textarea></label>
      <p class="muted" style="margin:0">Attached: <strong>${esc(d.attachment)}</strong> (made now if it is missing or the analysis changed).</p>
      <div class="row"><span class="spacer"></span><button type="button" class="btn" data-close>Cancel</button><button class="btn primary" type="submit">Send</button></div>
    </form>`, { wide: true });
  el.querySelector("#mailForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const btn = ev.target.querySelector("[type=submit]");
    btn.disabled = true; btn.textContent = "Sending…";
    try {
      const r = await api(`${base(kind, id)}/email`, { method: "POST", body: { to: f.get("to"), cc: f.get("cc") || null, subject: f.get("subject"), body: f.get("body") } });
      toast(`Email sent to ${(r.to || []).join(", ")} with ${r.attachment}`, "success");
      close(); onSent();
    } catch (err) { toast(err.message, "error"); btn.disabled = false; btn.textContent = "Send"; }
  });
}

// ---- step screenshots: a large view with previous / next ----------------------------------------------------

export function openShots(shots, start = 0) {
  if (!shots.length) return;
  let i = start;
  const { el } = openModal("Step screenshot", '<div data-shot></div>', { wide: true });
  const draw = () => {
    const s = shots[i];
    el.querySelector("[data-shot]").innerHTML = `
      <div class="row" style="flex-wrap:wrap"><strong>${esc(s.label)}</strong>${s.status ? ` <span class="pill ${esc(s.status)}">${esc(s.status)}</span>` : ""}<span class="spacer"></span>
        <button class="btn" data-nav="-1" ${i === 0 ? "disabled" : ""}>← Previous</button>
        <span class="muted">${i + 1} / ${shots.length}</span>
        <button class="btn" data-nav="1" ${i === shots.length - 1 ? "disabled" : ""}>Next →</button>
        <a class="btn" href="${s.url}" target="_blank" rel="noopener">Open full size</a></div>
      <img class="shot-large" src="${s.url}" alt="${esc(s.label)}">`;
    el.querySelectorAll("[data-nav]").forEach((b) => (b.onclick = () => { i += Number(b.dataset.nav); draw(); }));
  };
  const onKey = (e) => {
    if (!el.isConnected) { document.removeEventListener("keydown", onKey); return; }
    if (e.key === "ArrowRight" && i < shots.length - 1) { i++; draw(); }
    if (e.key === "ArrowLeft" && i > 0) { i--; draw(); }
  };
  document.addEventListener("keydown", onKey);
  draw();
}
