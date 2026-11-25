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

// 23.13.0: HTTPS with a friendly name (offline; the certificate is made on this PC)
async function httpsCard(view) {
  const host = view.querySelector("#httpsCard");
  let h;
  try { h = await api("/api/auth/network/https"); } catch (err) { host.innerHTML = `<p class="muted">${esc(err.message)}</p>`; return; }
  const state = h.running ? `<span class="pill PASS">ON</span> <a href="${esc(h.url)}" target="_blank" rel="noopener">${esc(h.url)}</a>`
    : h.enabled ? '<span class="pill WARN">SET UP</span> Exit SkyGuard QA and open it again to start HTTPS.' : '<span class="pill WAITING">OFF</span>';
  host.innerHTML = `
    <h2>HTTPS and a friendly address</h2>
    <p>${state}</p>
    <p class="muted">Open SkyGuard QA as <b>https://${esc(h.hostname)}/</b> instead of an IP address or PC name. Everything stays offline: SkyGuard QA makes its own certificate authority on this PC (no internet, no outside service). The address <code>http://127.0.0.1:${h.http_port}/</code> keeps working on this PC.</p>
    <form class="row" id="httpsForm" style="flex-wrap:wrap;gap:8px">
      <label>Name <input name="hostname" value="${esc(h.hostname)}" maxlength="63" pattern="[A-Za-z0-9][A-Za-z0-9.\-]*" required></label>
      <label>Port <input name="port" type="number" min="1" max="65535" value="${h.port}" style="width:90px"></label>
      <label><span><input type="checkbox" name="enabled" ${h.enabled ? "checked" : ""}> Use HTTPS</span></label>
      <button class="btn primary" type="submit">Save</button>
    </form>
    <h3>Steps</h3>
    <ol class="muted">
      <li>Tick <b>Use HTTPS</b> and Save (the certificate is made now).</li>
      <li><button class="btn small" id="trustBtn" ${h.ready ? "" : "disabled"}>Trust on this PC</button> runs <code>ENABLE_HTTPS.cmd</code> as administrator: it trusts the SkyGuard QA certificate in Windows (Edge uses it), adds <code>${esc(h.hostname)}</code> to the hosts file and opens the port on office networks.</li>
      <li>Exit SkyGuard QA and open it again.</li>
      <li>Colleagues: ${h.network_enabled ? "" : "turn on Network access below, then "}copy <code>TRUST_SKYGUARD_QA.cmd</code> from the application folder and the certificate <a href="/api/auth/https-ca.crt">SkyGuard_QA_Local_CA.crt</a> to their PC and run <code>TRUST_SKYGUARD_QA.cmd &lt;this PC's IP address&gt; ${esc(h.hostname)}</code> once. If IT adds <b>${esc(h.hostname)}</b> to the office DNS, only the certificate step is needed.</li>
    </ol>
    ${h.ca_sha256 ? `<p class="muted">Certificate fingerprint (SHA-256): <code style="word-break:break-all">${esc(h.ca_sha256)}</code></p>` : ""}`;
  host.querySelector("#httpsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const r = await api("/api/auth/network/https", { method: "PUT", body: { hostname: String(f.get("hostname")).trim(), port: Number(f.get("port") || 443), enabled: !!f.get("enabled") } });
      toast(r.made ? "Certificate made. Now trust it on this PC, then restart SkyGuard QA." : "Saved. Restart SkyGuard QA to apply.", "success");
    } catch (err) { toast(err.message, "error"); }
    httpsCard(view);
  });
  host.querySelector("#trustBtn").addEventListener("click", async () => {
    try { await api("/api/auth/network/https/trust", { method: "POST" }); toast("Windows is asking for administrator permission on this PC's screen.", "success"); }
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
      <label>Email <input name="email" type="email" required maxlength="254" placeholder="name@company.ae"></label>
      <label>TFS project <input name="tfs_project" required maxlength="200" placeholder="This person's own project"></label>
      <label>TFS Personal Access Token <input name="tfs_pat" type="password" required minlength="8" maxlength="512" autocomplete="off" placeholder="Must be valid for that project"></label>
      <label><span><input type="checkbox" name="send_welcome" checked> Email the address, user name, password and the user manual (PDF)</span></label>
      <div class="row"><button class="btn primary" type="submit">Create user</button> <a class="btn" href="/manual/SkyGuard_QA_User_Manual.pdf" target="_blank" rel="noopener">User manual (PDF)</a></div>
    </form>
    <p class="muted">Each person works in their own TFS project with their own PAT: changing it never changes anyone else's project. The PAT is checked against the project before the account is made and is kept in the Windows Credential Manager, never in a file.</p>
    <ul class="muted role-help">${Object.entries(roles).map(([v, l]) => `<li><b>${esc(l)}</b>: ${esc(ROLE_HELP[v] || "")}</li>`).join("")}</ul>
    <div style="overflow-x:auto"><table class="data users-table"><thead><tr><th>User name</th><th>Name</th><th>Email</th><th>TFS project</th><th>Role</th><th>Status</th><th>Last sign-in</th><th></th></tr></thead><tbody>
    ${users.map((u) => {
      const self = u.id === currentUser?.id;
      return `<tr data-id="${u.id}">
        <td><b>${esc(u.username)}</b>${self ? ' <span class="muted">(you)</span>' : ""}</td>
        <td>${esc(u.display_name)}</td>
        <td>${esc(u.email || "")}</td>
        <td>${esc(u.tfs_project || "shared")}</td>
        <td><select data-role ${self ? "disabled" : ""}>${roleOptions(roles, u.role)}</select></td>
        <td>${u.disabled ? '<span class="pill FAIL">Disabled</span>' : u.must_change ? '<span class="pill WARN">Must change password</span>' : '<span class="pill PASS">Active</span>'}</td>
        <td>${esc(dateTimeOf(u.last_login_at) || "never")}</td>
        <td class="row" style="flex-wrap:wrap;gap:4px">
          <button class="btn small" data-reset>Reset password</button>
          <button class="btn small" data-tfs>TFS project / PAT</button>
          <button class="btn small" data-email>Email</button>
          ${u.email ? '<button class="btn small" data-welcome>Send manual</button>' : ""}
          ${self ? "" : `<button class="btn small" data-toggle>${u.disabled ? "Enable" : "Disable"}</button><button class="btn small danger" data-del>Delete</button>`}
        </td></tr>`;
    }).join("")}
    </tbody></table></div>`;
  const reload = () => usersCard(view).catch((err) => toast(err.message, "error"));
  host.querySelector("#newUser").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const r = await api("/api/auth/users", { method: "POST", body: { username: String(f.get("username")).trim(), display_name: String(f.get("display_name") || "").trim(), password: String(f.get("password")), role: f.get("role"),
        email: String(f.get("email")).trim(), tfs_project: String(f.get("tfs_project")).trim(), tfs_pat: String(f.get("tfs_pat")), send_welcome: !!f.get("send_welcome") } });
      const user = r.user;
      const mail = r.welcome?.sent ? ` Welcome email sent to ${r.welcome.to}.` : (f.get("send_welcome") ? ` No email was sent: ${r.welcome?.reason || "unknown reason"}` : "");
      const pat = r.pat_check?.checked ? "" : ` ${r.pat_check?.detail || ""}`;
      toast(`${user.username} can now sign in as ${user.role_name}.${mail}${pat}`, r.welcome?.sent || !f.get("send_welcome") ? "success" : "info");
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
    tr.querySelector("[data-tfs]").addEventListener("click", () => {
      const project = window.prompt(`TFS project of ${name}:`, tr.children[3].textContent === "shared" ? "" : tr.children[3].textContent);
      if (project === null) return;
      const pat = window.prompt(`TFS Personal Access Token of ${name} for "${project}" (leave empty to keep the saved one):`, "");
      if (pat === null) return;
      patch({ tfs_project: project.trim(), ...(pat.trim() ? { tfs_pat: pat.trim() } : {}) }, `TFS settings of ${name} saved.`);
    });
    tr.querySelector("[data-email]").addEventListener("click", () => {
      const email = window.prompt(`Email of ${name}:`, tr.children[2].textContent);
      if (email !== null) patch({ email: email.trim() }, `Email of ${name} saved.`);
    });
    tr.querySelector("[data-welcome]")?.addEventListener("click", async () => {
      try {
        const { welcome } = await api(`/api/auth/users/${id}/welcome`, { method: "POST" });
        toast(welcome.sent ? `Sign-in address and user manual sent to ${welcome.to}.` : `Not sent: ${welcome.reason}`, welcome.sent ? "success" : "error");
      } catch (err) { toast(err.message, "error"); }
    });
    tr.querySelector("[data-del]")?.addEventListener("click", async () => {
      if (!(await confirmDelete({ name, title: "Delete user?", body: "This person can no longer sign in. Their tests, results and reports stay." }))) return;
      try { await api(`/api/auth/users/${id}`, { method: "DELETE" }); toast(`${name} was deleted.`, "success"); } catch (err) { toast(err.message, "error"); }
      reload();
    });
  });
}

export async function mount(view) {
  view.innerHTML = `<div class="card" id="usersCard"><p class="muted">Loading…</p></div><div class="card" id="netCard"><p class="muted">Loading…</p></div><div class="card" id="httpsCard"><p class="muted">Loading…</p></div>`;
  await usersCard(view);
  await networkCard(view);
  await httpsCard(view);
}
