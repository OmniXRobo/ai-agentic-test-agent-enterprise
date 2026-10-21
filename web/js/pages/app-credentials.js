// Application Credentials: role-based logins for applications under test.
// Usernames are stored in the local database; passwords only in the Windows Credential Manager.
// Kept separate from the TFS connection credentials.

import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";

async function render(view) {
  const { items, store_available } = await api("/api/automation/credentials");
  view.querySelector("#store").hidden = store_available;
  view.querySelector("#list").innerHTML = items.length ? `
    <table class="data"><thead><tr><th>Application</th><th>Role</th><th>Username</th><th>Password</th><th>Login URL</th><th>Updated</th><th></th></tr></thead><tbody>
    ${items.map((c) => `<tr><td>${esc(c.application)}</td><td>${esc(c.role)}</td><td>${esc(c.username)}</td>
      <td>${c.has_password ? '<span class="pill PASS">Saved</span>' : '<span class="pill WARN">Not set</span>'}</td>
      <td>${esc(c.login_url || "")}</td><td>${esc(dateTimeOf(c.updated_at))}</td>
      <td><button class="link-btn" data-edit="${esc(c.id)}">Edit</button> · <button class="link-btn" data-del="${esc(c.id)}">Delete</button></td></tr>`).join("")}
    </tbody></table>
    <p class="muted">Use <code>{{CRED:role.username}}</code> and <code>{{CRED:role.password}}</code> in scripts. Passwords are resolved only at run time and masked in logs and evidence.</p>`
    : '<p class="muted">No application credentials saved yet.</p>';
  const form = view.querySelector("#f");
  view.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const c = items.find((x) => x.id === b.dataset.edit);
    form.application.value = c.application;
    form.role.value = c.role;
    form.username.value = c.username;
    form.login_url.value = c.login_url || "";
    form.password.value = "";
    form.password.placeholder = c.has_password ? "Saved. Leave blank to keep it" : "Password";
    form.application.focus();
  }));
  view.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Delete this credential? Scripts that use this role will fail until it is saved again.")) return;
    try { await api(`/api/automation/credentials/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); render(view); }
    catch (err) { toast(err.message, "error"); }
  }));
}

export async function mount(view) {
  view.innerHTML = `
    <p class="pill WARN" id="store" hidden>The Windows Credential Manager is not available, so passwords cannot be saved.</p>
    <form class="card settings" id="f" autocomplete="off">
      <h3>Save a credential</h3>
      <label>Application <input name="application" required placeholder="e.g. Booking Portal"></label>
      <label>Role <input name="role" required placeholder="e.g. admin, agent, customer"></label>
      <label>Username <input name="username" required autocomplete="off"></label>
      <label>Password <input name="password" type="password" autocomplete="new-password" placeholder="Password"></label>
      <label>Login URL (optional) <input name="login_url" placeholder="https://your-app/login"></label>
      <div class="row"><button class="btn primary" type="submit">Save</button><button class="btn" type="reset">Clear</button></div>
    </form>
    <div class="card" id="list" style="margin-top:12px"><p class="muted">Loading…</p></div>`;
  const form = view.querySelector("#f");
  form.addEventListener("reset", () => { form.password.placeholder = "Password"; });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(form));
    try {
      await api("/api/automation/credentials", { method: "PUT", body: {
        application: f.application.trim(), role: f.role.trim(), username: f.username.trim(),
        password: f.password || null, login_url: f.login_url.trim() || null } });
      form.reset();
      toast("Credential saved", "success");
      render(view);
    } catch (err) { toast(err.message, "error"); }
  });
  await render(view);
}
