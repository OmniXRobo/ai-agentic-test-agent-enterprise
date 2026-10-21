// Test Plans and Test Suites from the active TFS / Azure DevOps project.

import { api } from "../api.js";
import { esc } from "../util.js";

function notConnected(view, err) {
  view.innerHTML = `<div class="card empty"><h2>TFS data is not available</h2><p>${esc(err.message)}</p>
    <p><a class="btn primary" href="#/tfs-settings">Open TFS connection settings</a></p></div>`;
}

async function showSuites(view, plan) {
  const panel = view.querySelector("#detail");
  panel.innerHTML = '<p class="muted">Loading suites…</p>';
  try {
    const { suites } = await api(`/api/tfs/test-plans/${plan.id}/suites`);
    panel.innerHTML = `<h2>${esc(plan.name)}</h2>
      <p class="muted">${esc(plan.state || "")} · ${esc(plan.iteration || "")}</p>
      ${suites.length ? `<table class="data"><thead><tr><th>ID</th><th>Suite</th><th>Type</th><th>Cases</th></tr></thead><tbody>
      ${suites.map((s) => `<tr class="clickable" data-suite="${s.id}"><td>${s.id}</td><td>${esc(s.name)}</td><td>${esc(s.suite_type || "")}</td><td>${s.test_case_count ?? ""}</td></tr>`).join("")}
      </tbody></table>` : '<p class="muted">This plan has no suites.</p>'}
      <div id="cases"></div>`;
    panel.querySelectorAll("tr[data-suite]").forEach((tr) => tr.addEventListener("click", async () => {
      const box = panel.querySelector("#cases");
      box.innerHTML = '<p class="muted">Loading test cases…</p>';
      try {
        const { test_cases } = await api(`/api/tfs/test-plans/${plan.id}/suites/${tr.dataset.suite}/test-cases`);
        box.innerHTML = `<h3>Test cases</h3>${test_cases.length ? `<ul>${test_cases.map((t) => `<li><a href="#/test-cases?id=${t.id}">#${t.id}</a> ${esc(t.title || "")}</li>`).join("")}</ul>` : '<p class="muted">No test cases in this suite.</p>'}`;
      } catch (err) {
        box.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
      }
    }));
  } catch (err) {
    panel.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
  }
}

export async function mount(view, entry) {
  view.innerHTML = '<p class="muted">Loading test plans…</p>';
  let plans;
  try {
    ({ plans } = await api("/api/tfs/test-plans"));
  } catch (err) {
    notConnected(view, err);
    return;
  }
  view.innerHTML = `<div class="split">
    <section class="card" id="list">${plans.length ? `<table class="data"><thead><tr><th>ID</th><th>Plan</th><th>State</th><th>Iteration</th></tr></thead><tbody>
      ${plans.map((p) => `<tr class="clickable" data-plan="${p.id}"><td>${p.id}</td><td>${esc(p.name)}</td><td>${esc(p.state || "")}</td><td>${esc((p.iteration || "").split("\\").pop())}</td></tr>`).join("")}
      </tbody></table>` : '<p class="muted">No test plans in this project.</p>'}</section>
    <section class="card detail" id="detail"><p class="muted">Select a test plan to see its ${entry.route === "test-suites" ? "suites" : "suites and test cases"}.</p></section>
  </div>`;
  view.querySelectorAll("tr[data-plan]").forEach((tr) => tr.addEventListener("click", () => {
    view.querySelectorAll("tr[data-plan]").forEach((x) => x.classList.toggle("selected", x === tr));
    showSuites(view, plans.find((p) => String(p.id) === tr.dataset.plan));
  }));
}
