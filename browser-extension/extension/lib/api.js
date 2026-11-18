// Client for the local companion (http://127.0.0.1:<port>). The device secret (from pairing) is kept in
// chrome.storage.local; the short-lived access token only in chrome.storage.session (cleared when the browser
// closes). Nothing here ever logs a request body.
const DEFAULT_PORT = 8790;

export async function settings() {
  const s = await chrome.storage.local.get(["port", "device_id", "device_secret", "trusted_input", "auto_learn", "owner"]);
  return { port: Number(s.port) || DEFAULT_PORT, device_id: s.device_id || "", device_secret: s.device_secret || "", trusted_input: !!s.trusted_input, auto_learn: !!s.auto_learn, owner: s.owner || "" };
}

export async function setPort(port) {
  const p = Number(port);
  if (!Number.isInteger(p) || p < 1024 || p > 65535) throw new Error("Use a port between 1024 and 65535.");
  await chrome.storage.local.set({ port: p });
}

async function token() {
  const s = await chrome.storage.session.get(["token", "token_expires", "user"]);
  return s;
}

export class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

async function raw(method, path, { body, audio, auth = true, device = true } = {}) {
  const s = await settings();
  const headers = {};
  if (device) {
    if (!s.device_id) throw new ApiError(401, "NOT_PAIRED", "Pair the extension with the companion first (Options).");
    headers["X-SG-Device"] = s.device_id;
    headers["X-SG-Secret"] = s.device_secret;
  }
  if (auth) {
    let t = await token();
    if (!t.token && s.owner && path !== "/v1/auth/device") { await deviceLogin().catch(() => null); t = await token(); }
    if (!t.token) throw new ApiError(401, "AUTH_REQUIRED", "Sign in to the companion (Options).");
    headers.Authorization = "Bearer " + t.token;
  }
  let payload;
  if (audio) { headers["Content-Type"] = "audio/wav"; payload = audio; }
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try {
    res = await fetch(`http://127.0.0.1:${s.port}${path}`, { method, headers, body: payload, cache: "no-store", credentials: "omit" });
  } catch (e) {
    throw new ApiError(0, "COMPANION_OFFLINE", "The SkyGuard companion is not running on this PC. Start it from the Start menu (SkyGuard Agent) or run SkyGuardAgent.cmd.");
  }
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok || data.ok === false) throw new ApiError(res.status, data.error_code || "ERROR", data.error || `Request failed (${res.status}).`);
  return data;
}

let refreshing = null;
async function maybeRefresh() {
  const t = await token();
  if (!t.token || !t.token_expires) return;
  if (t.token_expires - Date.now() > 4 * 60 * 1000) return;
  if (!refreshing) {
    refreshing = raw("POST", "/v1/auth/refresh", { body: {} }).then(async (r) => {
      await chrome.storage.session.set({ token: r.token, token_expires: Date.now() + r.expires_in * 1000 });
    }).catch(async (e) => { if (e.status === 401) await chrome.storage.session.remove(["token", "token_expires", "user"]); })
      .finally(() => { refreshing = null; });
  }
  await refreshing;
}

export async function call(method, path, body) {
  await maybeRefresh();
  try {
    return await raw(method, path, { body });
  } catch (e) {
    if (e.status === 401 && (e.code === "AUTH_EXPIRED" || e.code === "AUTH_REQUIRED")) {
      await chrome.storage.session.remove(["token", "token_expires", "user"]);
      // 1.4.0: a browser set up by this PC's installer signs in again by itself, once
      if (await deviceLogin().catch(() => null)) return raw(method, path, { body });
    }
    throw e;
  }
}

// ---- 1.4.0: set up by the installer (no pairing code, no user to create, no sign-in) ------------------------------
// INSTALL.cmd leaves auto-setup.json (port and a one-time ticket) inside this extension's own folder in the person's
// profile. The ticket is used once; the companion then binds this browser to this PC's owner user.
export async function autoSetup() {
  let file;
  try {
    const r = await fetch(chrome.runtime.getURL("auto-setup.json"), { cache: "no-store" });
    if (!r.ok) return { done: false };
    file = await r.json();
  } catch (e) { return { done: false }; }
  if (!file || typeof file.ticket !== "string" || file.ticket.length < 40 || file.ticket.length > 64) return { done: false };
  const seen = (await chrome.storage.local.get("ticket_used")).ticket_used;
  const fp = file.ticket.slice(0, 12);
  if (seen === fp) return { done: true };
  if (Number.isInteger(file.port) && file.port >= 1024 && file.port <= 65535) await chrome.storage.local.set({ port: file.port });
  const brand = (globalThis.navigator?.userAgentData?.brands || []).map((b) => b.brand).find((b) => /Edge|Chrome/.test(b)) || "browser";
  const r = await raw("POST", "/v1/pair", { body: { ticket: file.ticket, name: brand }, auth: false, device: false });
  await chrome.storage.local.set({ device_id: r.device_id, device_secret: r.device_secret, owner: r.owner || "", ticket_used: fp });
  await chrome.storage.session.remove(["token", "token_expires", "user"]);
  if (r.owner) await deviceLogin();
  return { done: true, owner: r.owner || "" };
}

export async function deviceLogin() {
  const s = await settings();
  if (!s.owner || !s.device_id) return null;
  const r = await raw("POST", "/v1/auth/device", { body: {}, auth: false });
  await chrome.storage.session.set({ token: r.token, token_expires: Date.now() + r.expires_in * 1000, user: r.user });
  return r.user;
}

export const get = (p) => call("GET", p);
export const post = (p, b = {}) => call("POST", p, b);
export const put = (p, b = {}) => call("PUT", p, b);
export const patch = (p, b = {}) => call("PATCH", p, b);
export const del = (p) => call("DELETE", p);

export async function transcribe(wavBlob) {
  await maybeRefresh();
  return raw("POST", "/v1/voice/transcribe", { audio: wavBlob });
}

export async function hello() { return raw("GET", "/v1/hello", { auth: false, device: false }); }

export async function pair(code, name) {
  const r = await raw("POST", "/v1/pair", { body: { code, name }, auth: false, device: false });
  await chrome.storage.local.set({ device_id: r.device_id, device_secret: r.device_secret });
  return r;
}

export async function unpair() {
  await chrome.storage.local.remove(["device_id", "device_secret", "owner"]);
  await chrome.storage.session.remove(["token", "token_expires", "user"]);
}

export async function setupState() { return raw("GET", "/v1/setup-state", { auth: false }); }
export async function setupAdmin(username, password) { return raw("POST", "/v1/setup", { body: { username, password }, auth: false }); }

export async function login(username, password) {
  const r = await raw("POST", "/v1/auth/login", { body: { username, password }, auth: false });
  await chrome.storage.session.set({ token: r.token, token_expires: Date.now() + r.expires_in * 1000, user: r.user });
  return r.user;
}

export async function logout() {
  try { await raw("POST", "/v1/auth/logout", { body: {} }); } catch (e) { /* signed out locally anyway */ }
  await chrome.storage.session.remove(["token", "token_expires", "user"]);
}

export async function currentUser() { return (await token()).user || null; }
