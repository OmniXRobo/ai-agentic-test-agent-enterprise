// Final patch: the application sign-in fields shown next to Generate Automation, Start Intelligent Discovery and
// Hybrid recording (defects 4, 8 and 9). The login is saved as an Application Credential: the username, login URL and
// sign-in method in the local database, the password only in the Windows Credential Manager. The password field is
// never filled back in, and the value typed is cleared as soon as it is saved.

import { api } from "./api.js";
import { esc, toast } from "./util.js";

const METHODS = [["form", "Login form (username and password)"], ["windows", "Windows / NTLM"], ["basic", "Basic authentication"],
  ["manual", "Manual (SSO or MFA: you sign in in the browser)"], ["none", "No sign-in"]];

export function loginBlock(title = "Application sign-in") {
  return `<fieldset class="login-box" data-login style="grid-column:1/-1">
    <legend>${esc(title)}</legend>
    <label>Login URL <input name="lf_login_url" placeholder="https://your-app/login (blank = application URL)" autocomplete="off"></label>
    <label>User ID <input name="lf_username" autocomplete="off" placeholder="e.g. qa.tester"></label>
    <label>Password <input name="lf_password" type="password" autocomplete="new-password" placeholder="kept in Windows Credential Manager"></label>
    <label>Sign-in method <select name="lf_auth">${METHODS.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("")}</select></label>
    <div class="row login-actions"><button class="btn" type="button" data-login-save>Save login</button><span class="muted" data-login-state></span></div>
  </fieldset>`;
}

/** The form's own fields: application, environment, credential_role and base_url (or start_url). */
export function formCtx(form) {
  const v = (n) => form.querySelector(`[name=${n}]`)?.value || "";
  return {
    application: () => v("application"), environment: () => v("environment"), role: () => v("credential_role"),
    baseUrl: () => v("base_url") || v("start_url"),
    setApplication: (a) => { const i = form.querySelector("[name=application]"); if (i && !i.value) i.value = a; },
    setRole: (r) => {
      const s = form.querySelector("[name=credential_role]");
      if (!s) return;
      if (s.tagName === "SELECT" && ![...s.options].some((o) => o.value === r)) s.add(new Option(r, r));
      s.value = r;
    },
  };
}

/** ctx: { application(), environment(), role(), setRole(role), baseUrl(), setApplication(name) } */
export function bindLogin(form, ctx = formCtx(form)) {
  const box = form.querySelector("[data-login]");
  if (!box) return;
  const el = (n) => box.querySelector(`[name=${n}]`);
  const state = box.querySelector("[data-login-state]");
  const appName = () => {
    let a = (ctx.application() || "").trim();
    if (!a) {
      try { a = new URL(ctx.baseUrl()).hostname; } catch { a = ""; }
      if (a) ctx.setApplication?.(a);
    }
    return a;
  };
  const show = async () => {
    const a = appName();
    if (!a) { state.textContent = ""; return; }
    let items = [];
    try { ({ items } = await api("/api/automation/credentials")); } catch { return; }
    const role = (ctx.role() || "").trim().toLowerCase();
    const env = (ctx.environment?.() || "").trim().toLowerCase();
    const mine = items.filter((c) => c.application.toLowerCase() === a.toLowerCase());
    const c = mine.find((x) => x.role.toLowerCase() === role && (x.environment || "").toLowerCase() === env)
      || mine.find((x) => x.role.toLowerCase() === role) || (!role && mine.length === 1 ? mine[0] : null);
    if (!c) { state.textContent = mine.length ? `Saved roles for ${a}: ${mine.map((x) => x.role).join(", ")}` : `No login saved for ${a} yet.`; return; }
    if (!el("lf_username").value) el("lf_username").value = c.username;
    if (!el("lf_login_url").value && c.login_url) el("lf_login_url").value = c.login_url;
    el("lf_auth").value = c.auth_method || "form";
    el("lf_password").placeholder = c.has_password ? "Saved. Leave blank to keep it" : "kept in Windows Credential Manager";
    state.textContent = `Using saved login "${c.role}" for ${a}${c.has_password ? " (password saved)" : " (no password saved)"}.`;
    if (!role) ctx.setRole(c.role);
  };
  const save = async () => {
    const username = el("lf_username").value.trim();
    if (!username) return true;
    const application = appName();
    if (!application) { toast("Enter the application URL or name first.", "warning"); return false; }
    const role = (ctx.role() || "").trim() || "default";
    const login = el("lf_login_url").value.trim();
    try {
      await api("/api/automation/credentials", { method: "PUT", body: { application, role, username,
        password: el("lf_password").value || null, login_url: login || null, environment: (ctx.environment?.() || "").trim() || null,
        auth_method: el("lf_auth").value } });
      el("lf_password").value = "";
      ctx.setRole(role);
      await show();
      return true;
    } catch (err) { toast(err.message, "error"); return false; }
  };
  box.querySelector("[data-login-save]").addEventListener("click", async () => { if (await save()) toast("Login saved", "success"); });
  // Saved before the page's own submit handler runs, so the run that starts uses this login.
  let saving = false;
  form.addEventListener("submit", async (e) => {
    if (saving || !el("lf_username").value.trim()) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    saving = true;
    const ok = await save();
    if (ok) form.requestSubmit();
    saving = false;
  }, true);
  for (const name of ["application", "credential_role", "environment", "base_url", "start_url"]) {
    form.querySelector(`[name=${name}]`)?.addEventListener("change", show);
  }
  show();
}
