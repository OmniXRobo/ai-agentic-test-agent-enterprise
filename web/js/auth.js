// 23.3.34: sign-in screen, first-time password change, and the account menu in the top bar.
// The server checks every request; this only shows the right screen and hides what an account cannot use.
import { esc } from "./util.js";

export let currentUser = null;

async function call(path, method = "GET", body) {
  const resp = await fetch(path, {
    method, credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await resp.json(); } catch { data = null; }
  return { status: resp.status, data };
}

function brandName() {
  return document.getElementById("brandTitle")?.textContent || "SkyGuard QA";
}

function screen(inner) {
  let el = document.getElementById("authScreen");
  if (!el) {
    el = document.createElement("div");
    el.id = "authScreen";
    el.className = "auth-screen";
    document.body.appendChild(el);
  }
  el.innerHTML = `<form class="auth-card card" autocomplete="on" novalidate>
      <div class="auth-brand"><svg viewBox="0 0 40 40" width="34" height="34" aria-hidden="true"><path d="M20 3 37 34 20 26 3 34Z" fill="none" stroke="#38d6ff" stroke-width="2.4" stroke-linejoin="round"/><path d="M20 11 29 28 20 23 11 28Z" fill="#38d6ff" opacity=".85"/></svg>
      <b>${esc(brandName())}</b></div>${inner}<p class="auth-error" role="alert" hidden></p></form>`;
  document.querySelector(".shell")?.setAttribute("hidden", "");
  return el.querySelector("form");
}

function closeScreen() {
  document.getElementById("authScreen")?.remove();
  document.querySelector(".shell")?.removeAttribute("hidden");
}

function showError(form, message) {
  const p = form.querySelector(".auth-error");
  p.textContent = message;
  p.hidden = !message;
}

function signInForm() {
  return new Promise((resolve) => {
    const form = screen(`
      <h2>Sign in</h2>
      <label>User name <input name="username" autocomplete="username" required maxlength="64" autofocus></label>
      <label>Password <input name="password" type="password" autocomplete="current-password" required maxlength="256"></label>
      <button class="btn primary" type="submit">Sign in</button>
      <p class="muted">Ask your SkyGuard QA admin for an account.</p>`);
    form.username.focus();
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button");
      btn.disabled = true;
      showError(form, "");
      const { status, data } = await call("/api/auth/login", "POST", { username: form.username.value.trim(), password: form.password.value });
      btn.disabled = false;
      if (status === 200) { resolve(data.user); return; }
      form.password.value = "";
      showError(form, data?.message || "Sign-in failed.");
    });
  });
}

function passwordForm(user, { forced }) {
  return new Promise((resolve) => {
    const form = screen(`
      <h2>${forced ? "Choose your own password" : "Change password"}</h2>
      <p class="muted">${forced ? `Signed in as <b>${esc(user.username)}</b>. Replace the starting password before you continue.` : ""}
        At least 8 characters, and not the same as the user name.</p>
      <label>Current password <input name="current" type="password" autocomplete="current-password" required maxlength="256"></label>
      <label>New password <input name="new" type="password" autocomplete="new-password" required minlength="8" maxlength="256"></label>
      <label>New password again <input name="again" type="password" autocomplete="new-password" required minlength="8" maxlength="256"></label>
      <div class="row"><button class="btn primary" type="submit">Save password</button>${forced ? "" : '<button class="btn" type="button" data-cancel>Cancel</button>'}</div>`);
    form.current.focus();
    form.querySelector("[data-cancel]")?.addEventListener("click", () => { closeScreen(); resolve(false); });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (form.new.value !== form.again.value) { showError(form, "The two new passwords are not the same."); return; }
      const { status, data } = await call("/api/auth/password", "POST", { current: form.current.value, new: form.new.value });
      if (status === 200) { closeScreen(); resolve(true); return; }
      showError(form, data?.message || "The password was not changed.");
    });
  });
}

/** Resolves with the signed-in account, after the sign-in (and a required password change) when needed. */
export async function ensureSignedIn() {
  let { status, data } = await call("/api/auth/me");
  let user = status === 200 ? data.user : await signInForm();
  if (user.must_change) {
    await passwordForm(user, { forced: true });
    ({ status, data } = await call("/api/auth/me"));
    user = data.user;
  }
  closeScreen();
  currentUser = user;
  document.body.dataset.role = user.role;
  document.body.classList.toggle("view-only", !!user.view_only);
  return user;
}

export function isAdmin() { return currentUser?.role === "admin"; }
export function canEdit() { return !!currentUser && !currentUser.view_only; }

export async function signOut() {
  await call("/api/auth/logout", "POST");
  location.reload();
}

/** The account chip in the top bar: name and role, Change password, Sign out. */
export function initUserMenu(host) {
  if (!host || !currentUser || currentUser.local) return;
  host.innerHTML = `<button class="user-chip" type="button" aria-haspopup="true" aria-expanded="false" title="Your account">
      <span class="user-dot"></span><span>${esc(currentUser.display_name)}</span><b>${esc(currentUser.role_name)}</b></button>
    <div class="popover user-pop" hidden>
      <div class="muted">Signed in as <b>${esc(currentUser.username)}</b> (${esc(currentUser.role_name)}${currentUser.view_only ? ", view only" : ""})</div>
      <button class="btn small" type="button" data-pw>Change password</button>
      <button class="btn small" type="button" data-out>Sign out</button>
    </div>`;
  const btn = host.querySelector(".user-chip"), pop = host.querySelector(".user-pop");
  btn.addEventListener("click", (e) => { e.stopPropagation(); pop.hidden = !pop.hidden; btn.setAttribute("aria-expanded", String(!pop.hidden)); });
  document.addEventListener("click", (e) => { if (!pop.hidden && !host.contains(e.target)) pop.hidden = true; });
  host.querySelector("[data-out]").addEventListener("click", signOut);
  host.querySelector("[data-pw]").addEventListener("click", () => { pop.hidden = true; passwordForm(currentUser, { forced: false }); });
}

// A sign-in that ended (8 hours idle, restart, password reset by the admin) shows the sign-in screen again.
let reloading = false;
export function signInExpired() {
  if (reloading || !currentUser) return; // before signing in, the sign-in screen is already on its way
  if (currentUser.local) return;
  reloading = true;
  location.reload();
}
