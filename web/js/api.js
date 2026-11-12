// Fetch wrapper that never parses an HTML error page as JSON.
import { signInExpired } from "./auth.js";

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `Request failed (HTTP ${status})`);
    this.status = status;
    this.code = body?.error_code || `HTTP_${status}`;
    this.details = body?.details ?? null;
    this.agent = body?.agent ?? null;
    this.runId = body?.run_id ?? null;
  }
}

const FRIENDLY = {
  401: "Authentication is required or has expired.",
  403: "You do not have permission for this action.",
  404: "The requested item was not found.",
  409: "This action conflicts with something already running.",
  429: "Too many requests. Please wait and try again.",
  500: "The server hit an unexpected error.",
};

// Final patch: a TFS project that needs a value for a mandatory field (e.g. a "Test Result" drop-down) answers
// TFS_FIELDS_REQUIRED with the fields and their allowed values. Every page that creates work items in TFS gets the
// same dialog: the user picks the values, they are saved as this project's defaults (Preferences > TFS Integration)
// and the same request is sent again. Cancel keeps the original error.
async function askTfsFields(details) {
  const { esc, openModal } = await import("./util.js");
  const fields = details?.fields || [];
  if (!fields.length || !details?.type) return false;
  const body = `<p class="muted">This TFS project needs these fields for a <b>${esc(details.type)}</b>. The values are saved as
    this project's defaults (change them in Preferences &gt; TFS Integration), then the work item is created.</p>
    <form id="tfsFieldsForm" class="settings" style="max-width:none">
    ${fields.map((f, i) => `<label>${esc(f.name)} <span class="muted">(${esc(f.ref)})${f.problem === "not_allowed" ? ` · "${esc(f.value)}" is not allowed` : ""}</span>
      ${f.allowed?.length ? `<select name="f${i}" required><option value="">Choose…</option>${f.allowed.map((a) => `<option>${esc(a)}</option>`).join("")}</select>`
        : `<input name="f${i}" required maxlength="400">`}</label>`).join("")}
    <div class="row"><button class="btn primary" type="submit">Save and create</button><button class="btn" type="button" data-close>Cancel</button></div></form>`;
  const { el, close } = openModal("TFS needs a few more fields", body);
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    new MutationObserver((_, obs) => { if (!el.isConnected) { obs.disconnect(); finish(false); } }).observe(document.body, { childList: true });
    el.querySelector("#tfsFieldsForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const values = {};
      fields.forEach((f, i) => { values[f.ref] = e.target.elements[`f${i}`].value; });
      try {
        await api("/api/tfs/preferences/defaults", { method: "PUT", body: { type: details.type, values } });
        finish(true); close();
      } catch (err) {
        el.querySelector(".modal-body").insertAdjacentHTML("afterbegin", `<p class="pill FAIL">${esc(err.message)}</p>`);
      }
    });
  });
}

// rawBody (a Blob, e.g. recorded audio) is sent as-is with contentType instead of JSON.
export async function api(path, opts = {}) {
  try {
    return await rawApi(path, opts);
  } catch (err) {
    if (err instanceof ApiError && err.code === "TFS_FIELDS_REQUIRED" && !opts._retried
        && !path.startsWith("/api/tfs/preferences/") && opts.method && opts.method !== "GET") {
      if (await askTfsFields(err.details)) return api(path, { ...opts, _retried: true });
    }
    throw err;
  }
}

async function rawApi(path, { method = "GET", body, rawBody, contentType, signal } = {}) {
  let resp;
  try {
    resp = await fetch(path, {
      method,
      signal,
      headers: rawBody !== undefined ? { "Content-Type": contentType || "application/octet-stream" }
        : body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: rawBody !== undefined ? rawBody : body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, { error_code: "NETWORK_ERROR", message: "The QA runtime is not reachable." });
  }
  const type = resp.headers.get("content-type") || "";
  let data = null;
  if (type.includes("application/json")) {
    try { data = await resp.json(); } catch { data = null; }
  }
  if (!resp.ok) {
    if (resp.status === 401 && data?.error_code === "AUTH_REQUIRED") signInExpired(); // 23.3.34: sign in again
    throw new ApiError(resp.status, data || { error_code: `HTTP_${resp.status}`, message: FRIENDLY[resp.status] || `HTTP ${resp.status}` });
  }
  if (data === null) {
    throw new ApiError(resp.status, { error_code: "INVALID_RESPONSE", message: "The server returned a non-JSON response." });
  }
  return data;
}

// 23.3.33: the browser chosen under Settings > Automation runs (Microsoft Edge when the installer found it), used as
// the starting choice of every Browser field. Loaded once per page load.
let browserChoice = null;
export function defaultBrowser() {
  browserChoice ??= api("/api/settings").then(({ settings }) => settings?.playback?.browser || "chromium")
    .catch(() => "chromium");
  return browserChoice;
}

export async function preselectBrowser(root) {
  const b = await defaultBrowser();
  root?.querySelectorAll('select[name="browser"]').forEach((s) => {
    if (!s.dataset.keep && [...s.options].some((o) => o.value === b)) s.value = b;
  });
}
