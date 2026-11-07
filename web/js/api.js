// Fetch wrapper that never parses an HTML error page as JSON.

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

// rawBody (a Blob, e.g. recorded audio) is sent as-is with contentType instead of JSON.
export async function api(path, { method = "GET", body, rawBody, contentType, signal } = {}) {
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
