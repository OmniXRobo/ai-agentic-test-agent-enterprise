// 23.3.34: Settings > Users & Network Access (admin only).
import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";
import { confirmDelete } from "../confirm-delete.js";
import { currentUser } from "../auth.js";

const ROLE_HELP = {
  admin: "Everything, plus Users, Network access and Exit.",
  tester: "Full access: create, edit, delete, run, record, schedules and settings.",
  developer: "View only: sees everything, runs existing tests, views and exports reports.",
  manager: "View only: sees everything, runs existing tests, views and exports reports.",
};

function roleOptions(roles, selected) {
  return Object.entries(roles).map(([v, l]) => `<option value="${v}" ${v === selected ? "selected" : ""}>${esc(l)}</option>`).join("");
}

async function networkCard(view) {
  const host = view.querySelector("#netCard");
  let n;
  try { n = await api("/api/auth/network"); } catch (err) { host.innerHTML = `<p class="muted">${esc(err.message)}</p>`; return; }
  const status = n.active ? '<span class="pill PASS">ON</span> Colleagues can open SkyGuard QA from their PCs.'
    : '<span class="pill WAITING">OFF</span> Only this PC can open SkyGuard QA.';
  host.innerHTML = `
    <h2>Network access</h2>
    <p>${status}</p>
    ${n.restart_needed ? `<p class="pill WARN">Exit SkyGuard QA and open it again to ${n.enabled ? "turn Network access on" : "turn Network access off"}.</p>` : ""}
    <label><span><input type="checkbox" id="netOn" ${n.enabled ? "checked" : ""}> Let colleagues on the office network open SkyGuard QA in their browser</span></label>
    <p class="muted">They need no installation, only the link below and an account from this page. While Network access is on, closing the SkyGuard QA window keeps it running for them; Exit (top right) stops it. This PC must stay on and signed in.</p>
    <h3>Link for your colleagues</h3>
    <ul class="net-links">${n.links.map((l) => `<li><code>${esc(l)}</code> <button class="btn small" data-copy="${esc(l)}">Copy</button></li>`).join("")}</ul>
    <p class="muted">The first link uses this PC's name (<b>${esc(n.pc_name)}</b>), the others its address. If the name does not open on another PC, use an address.</p>
    <h3>Windows Firewall</h3>
    <p class="muted">Windows blocks other PCs until port <b>${n.port}</b> is allowed once on this PC (office networks only, not public Wi-Fi). Allow it here (Windows asks for administrator permission on this PC's screen), run <code>${esc(n.firewall_script)}</code> as administrator, or ask your IT team to allow incoming TCP ${n.port} on this PC. Nothing changes on your colleagues' PCs.</p>
    <div class="row"><button class="btn" id="fwBtn">Allow through Windows Firewall</button></div>
    <h3>Voice for colleagues</h3>
    <p class="muted">Typed AI commands and spoken replies work from every browser. Chrome and Edge allow the microphone only on this PC itself, so colleagues type their commands; voice by microphone works here and on PCs with their own installation.</p>`;
  host.querySelector("#netOn").addEventListener("change", async (e) => {
    try { await api("/api/auth/network", { method: "PUT", body: { enabled: e.target.checked } }); toast("Saved. Exit SkyGuard QA and open it again to apply.", "success"); }
    catch (err) { toast(err.message, "error"); }
    networkCard(view);
  });
  host.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Link copied.", "success"); } catch { toast(b.dataset.copy); }
  }));
  host.querySelector("#fwBtn").addEventListener("click", async () => {
    try { await api("/api/auth/network/firewall", { method: "POST" }); toast("Windows is asking for administrator permission on this PC's screen.", "success"); }
    catch (err) { toast(err.message, "error"); }
  });
}

async function usersCard(view) {
  const host = view.querySelector("#usersCard");
  const { users, roles } = await api("/api/auth/users");
  host.innerHTML = `
    <h2>Users</h2>
    <p class="card-sub">Create an account for each colleague. They sign in with the user name and password you set; they can change the password later from their name at the top right.</p>
    <form class="grid user-new" id="newUser" autocomplete="off">
      <label>User name <input name="username" required maxlength="32" pattern="[A-Za-z0-9][A-Za-z0-9._\\-]{1,31}" placeholder="e.g. ahmed.ali"></label>
      <label>Full name <input name="display_name" maxlength="80" placeholder="Shown at the top right"></label>
      <label>Password <input name="password" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>
      <label>Role <select name="role">${roleOptions(roles, "tester")}</select></label>
      <div class="row"><button class="btn primary" type="submit">Create user</button></div>
    </form>
    <ul class="muted role-help">${Object.entries(roles).map(([v, l]) => `<li><b>${esc(l)}</b>: ${esc(ROLE_HELP[v] || "")}</li>`).join("")}</ul>
    <div style="overflow-x:auto"><table class="data users-table"><thead><tr><th>User name</th><th>Name</th><th>Role</th><th>Status</th><th>Last sign-in</th><th></th></tr></thead><tbody>
    ${users.map((u) => {
      const self = u.id === currentUser?.id;
      return `<tr data-id="${u.id}">
        <td><b>${esc(u.username)}</b>${self ? ' <span class="muted">(you)</span>' : ""}</td>
        <td>${esc(u.display_name)}</td>
        <td><select data-role ${self ? "disabled" : ""}>${roleOptions(roles, u.role)}</select></td>
        <td>${u.disabled ? '<span class="pill FAIL">Disabled</span>' : u.must_change ? '<span class="pill WARN">Must change password</span>' : '<span class="pill PASS">Active</span>'}</td>
        <td>${esc(dateTimeOf(u.last_login_at) || "never")}</td>
        <td class="row" style="flex-wrap:wrap;gap:4px">
          <button class="btn small" data-reset>Reset password</button>
          ${self ? "" : `<button class="btn small" data-toggle>${u.disabled ? "Enable" : "Disable"}</button><button class="btn small danger" data-del>Delete</button>`}
        </td></tr>`;
    }).join("")}
    </tbody></table></div>`;
  const reload = () => usersCard(view).catch((err) => toast(err.message, "error"));
  host.querySelector("#newUser").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const { user } = await api("/api/auth/users", { method: "POST", body: { username: String(f.get("username")).trim(), display_name: String(f.get("display_name") || "").trim(), password: String(f.get("password")), role: f.get("role") } });
      toast(`${user.username} can now sign in as ${user.role_name}.`, "success");
      reload();
    } catch (err) { toast(err.message, "error"); }
  });
  host.querySelectorAll("tr[data-id]").forEach((tr) => {
    const id = Number(tr.dataset.id), name = tr.querySelector("b").textContent;
    const patch = async (body, done) => {
      try { await api(`/api/auth/users/${id}`, { method: "PATCH", body }); toast(done, "success"); } catch (err) { toast(err.message, "error"); }
      reload();
    };
    tr.querySelector("[data-role]")?.addEventListener("change", (e) => patch({ role: e.target.value }, `${name} is now ${e.target.selectedOptions[0].textContent}.`));
    tr.querySelector("[data-toggle]")?.addEventListener("click", (e) => {
      const disable = e.target.textContent === "Disable";
      patch({ disabled: disable }, disable ? `${name} can no longer sign in.` : `${name} can sign in again.`);
    });
    tr.querySelector("[data-reset]").addEventListener("click", () => {
      const pw = window.prompt(`New password for ${name} (at least 8 characters). Tell it to them; they can change it after signing in.`);
      if (pw) patch({ password: pw }, `The password of ${name} was changed.`);
    });
    tr.querySelector("[data-del]")?.addEventListener("click", async () => {
      if (!(await confirmDelete({ name, title: "Delete user?", body: "This person can no longer sign in. Their tests, results and reports stay." }))) return;
      try { await api(`/api/auth/users/${id}`, { method: "DELETE" }); toast(`${name} was deleted.`, "success"); } catch (err) { toast(err.message, "error"); }
      reload();
    });
  });
}

export async function mount(view) {
  view.innerHTML = `<div class="card" id="usersCard"><p class="muted">Loading…</p></div><div class="card" id="netCard"><p class="muted">Loading…</p></div>`;
  await usersCard(view);
  await networkCard(view);
}
