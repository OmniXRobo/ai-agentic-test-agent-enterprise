import * as api from "./lib/api.js";

const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const msg = (id, text) => { $(id).textContent = text || ""; };

// A slower, older refresh must not overwrite a newer one: on Windows a connection to a closed port takes about two
// seconds to fail, so the check made at page load (old port) could finish after the user saved the right port.
let refreshGen = 0;
let portEdited = false;
$("#port").addEventListener("input", () => { portEdited = true; });

async function refresh() {
  const gen = ++refreshGen;
  const s = await api.settings();
  if (gen !== refreshGen) return;
  if (!portEdited) $("#port").value = s.port;  // never replace a port the person is typing
  $("#trusted").checked = s.trusted_input;
  $("#autoLearn").checked = s.auto_learn;
  const pill = $("#conn");
  let online = true, err = "";
  try { await api.hello(); } catch (e) { online = false; err = e.message; }
  const user = await api.currentUser();
  if (gen !== refreshGen) return;
  if (!online) msg("#connErr", err);
  pill.textContent = !online ? "companion offline" : !s.device_id ? "not paired" : user ? `${user.username} (${user.role})` : "signed out";
  pill.className = "pill " + (!online ? "fail" : user ? "ok" : "warn");
  $("#pairCard").classList.toggle("hidden", !online);
  let usersExist = true;
  if (online && s.device_id) {
    try { usersExist = (await api.setupState()).users_exist; } catch (e) { msg("#pairErr", e.message); }
    if (gen !== refreshGen) return;
  }
  $("#setupCard").classList.toggle("hidden", !online || !s.device_id || usersExist);
  $("#loginCard").classList.toggle("hidden", !online || !s.device_id || !usersExist);
  $("#adminCard").classList.toggle("hidden", !(user && user.role === "admin"));
  if (user && user.role === "admin") await admin();
}

async function admin() {
  try {
    const [{ users }, info] = await Promise.all([api.get("/v1/users"), api.get("/v1/info")]);
    $("#users").innerHTML = users.map((u) => `<div>${esc(u.username)} <span class="pill">${esc(u.role)}</span> <button class="btn" data-del="${esc(u.username)}">Remove</button></div>`).join("");
    document.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => { try { await api.del(`/v1/users/${encodeURIComponent(b.dataset.del)}`); admin(); } catch (e) { msg("#userErr", e.message); } });
    $("#llmMode").value = info.config.llm.mode; $("#llmUrl").value = info.config.llm.url || "";
    $("#fwMode").value = info.config.whisper.mode; $("#fwUrl").value = info.config.whisper.url || "";
    $("#modelState").textContent = `AI model: ${info.llm.state}${info.llm.detail ? " (" + info.llm.detail + ")" : ""}. Speech: ${info.whisper.state}${info.whisper.detail ? " (" + info.whisper.detail + ")" : ""}. Vault: ${info.agent.vault}.`;
  } catch (e) { msg("#userErr", e.message); }
}

$("#savePort").onclick = async () => { try { await api.setPort($("#port").value); portEdited = false; msg("#connErr", "Saved."); refresh(); } catch (e) { msg("#connErr", e.message); } };
$("#test").onclick = async () => { try { const h = await api.hello(); msg("#connErr", `Connected: ${h.name} ${h.version}.`); } catch (e) { msg("#connErr", e.message); } };
$("#pairBtn").onclick = async () => { try { await api.pair($("#code").value.trim(), navigator.userAgentData?.brands?.map((b) => b.brand).find((b) => /Edge|Chrome/.test(b)) || "browser"); $("#code").value = ""; msg("#pairErr", "Paired."); refresh(); } catch (e) { msg("#pairErr", e.message); } };
$("#unpairBtn").onclick = async () => { await api.unpair(); msg("#pairErr", "This browser is no longer paired."); refresh(); };
$("#setupBtn").onclick = async () => { try { await api.setupAdmin($("#suUser").value.trim(), $("#suPass").value); await api.login($("#suUser").value.trim(), $("#suPass").value); $("#suPass").value = ""; refresh(); } catch (e) { msg("#setupErr", e.message); } };
$("#loginBtn").onclick = async () => { try { await api.login($("#liUser").value.trim(), $("#liPass").value); $("#liPass").value = ""; msg("#loginErr", ""); refresh(); } catch (e) { msg("#loginErr", e.message); } };
$("#logoutBtn").onclick = async () => { await api.logout(); refresh(); };
$("#trusted").onchange = async (e) => {
  if (e.target.checked) {
    const ok = await chrome.permissions.request({ permissions: ["debugger"] });
    if (!ok) { e.target.checked = false; return; }
  } else {
    await chrome.permissions.remove({ permissions: ["debugger"] }).catch(() => {});
  }
  await chrome.storage.local.set({ trusted_input: e.target.checked });
};
$("#autoLearn").onchange = async (e) => {
  await chrome.storage.local.set({ auto_learn: e.target.checked });
  chrome.runtime.sendMessage({ type: "auto-learn", on: e.target.checked }).catch(() => {});
};
$("#micBtn").onclick = () => chrome.tabs.create({ url: chrome.runtime.getURL("mic.html") });
$("#addUser").onclick = async () => { try { await api.post("/v1/users", { username: $("#nuUser").value.trim(), password: $("#nuPass").value, role: $("#nuRole").value }); $("#nuPass").value = ""; admin(); } catch (e) { msg("#userErr", e.message); } };
$("#saveCfg").onclick = async () => { try { await api.put("/v1/config", { llm: { mode: $("#llmMode").value, url: $("#llmUrl").value.trim() || undefined }, whisper: { mode: $("#fwMode").value, url: $("#fwUrl").value.trim() || undefined } }); msg("#cfgErr", "Saved; the companion is reconnecting."); setTimeout(admin, 1500); } catch (e) { msg("#cfgErr", e.message); } };
refresh();
