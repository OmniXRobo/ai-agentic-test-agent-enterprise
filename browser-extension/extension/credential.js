import * as api from "./lib/api.js";

const $ = (s) => document.querySelector(s);
const account = new URLSearchParams(location.search).get("account") || "";

async function load() {
  try {
    const { accounts } = await api.get("/v1/accounts");
    const acc = accounts.find((a) => a.id === account);
    if (!acc) throw new Error("Unknown account.");
    $("#who").textContent = `${acc.label} (${acc.login_id})`;
  } catch (e) { $("#err").textContent = e.message; }
}

$("#f").onsubmit = async (e) => {
  e.preventDefault();
  const pw = $("#pw").value, pw2 = $("#pw2").value;
  if (pw !== pw2) { $("#err").textContent = "The passwords are not the same."; return; }
  if (!$("#consent").checked) { $("#err").textContent = "Saving needs your consent."; return; }
  try {
    await api.put(`/v1/accounts/${encodeURIComponent(account)}/credential`, { password: pw, consent: true, expires_at: $("#exp").value || undefined });
    $("#pw").value = ""; $("#pw2").value = "";
    $("#f").classList.add("hidden");
    $("#err").textContent = "";
    $("#who").textContent += ": credential saved";
  } catch (err) { $("#err").textContent = err.message; }
};
$("#close").onclick = () => window.close();
load();
