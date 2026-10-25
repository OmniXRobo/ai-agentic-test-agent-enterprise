// TFS Requirement Scope selector: hierarchy-aware, lazy-loaded tree with search, counts and preview.
// Selecting a parent includes its descendants unless they are unticked; a child can be selected alone
// (its parents are kept for traceability by the server). The server normalises and deduplicates.

import { api } from "../api.js";
import { esc, toast } from "../util.js";

const OPTIONS = [
  ["features", "Selected Features"], ["epics", "Selected Epics"], ["current_sprint", "Current Sprint / Iteration"],
  ["entire_project", "Entire Project"], ["all_stories", "All User Stories"], ["stories", "Selected User Stories"],
  ["acceptance_criteria", "Selected Acceptance Criteria"], ["test_cases", "Selected Test Cases"],
  ["custom", "Custom Hierarchy Selection"],
];
// Which level is listed first for each option, and whether items at that level are selectable.
const LIST_LEVEL = { epics: "epic", features: "feature", stories: "story", acceptance_criteria: "story", test_cases: "testcase" };
const LEVEL_NAMES = { epic: "Epics", feature: "Features", story: "User Stories", ac: "Acceptance Criteria", testcase: "Test Cases" };

export async function scopeSelector(box) {
  let cfg;
  try {
    cfg = await api("/api/tfs/hierarchy/config");
  } catch (err) {
    box.innerHTML = `<p class="pill FAILED">TFS is not available: ${esc(err.message)}</p>
      <p class="muted">Configure the active profile in <a href="#/tfs-settings">TFS Connection</a>, or choose Application Only.</p>`;
    return { value: () => null, ready: false };
  }
  const options = OPTIONS.filter(([k]) => (k !== "epics" || cfg.epic_types.length) && (k !== "features" || cfg.feature_types.length));
  box.innerHTML = `
    <h3>TFS Requirement Scope</h3>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      <label>Project <input value="${esc(cfg.project || "")}" disabled></label>
      <label>Sprint / Iteration <select name="iteration"><option value="">All iterations</option>
        ${cfg.iterations.map((i) => `<option>${esc(i)}</option>`).join("")}</select></label>
      <label>Requirement Scope <select name="option">${options.map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
      <label>Search <input name="text" placeholder="Work item ID or title (e.g. F100, US118, login)"></label>
    </div>
    <div class="row" id="storiesIn" hidden>All User Stories in:
      <label class="row"><input type="radio" name="stories_in" value="sprint" checked> Current Sprint</label>
      <label class="row"><input type="radio" name="stories_in" value="project"> Entire Project</label></div>
    <div id="tree" class="tree" style="margin-top:8px;max-height:360px;overflow:auto"></div>
    <div class="row" style="margin-top:8px"><button type="button" class="btn" id="preview">Preview Requirement Scope</button>
      <button type="button" class="btn" id="refresh" title="Read TFS again">Refresh</button><span class="spacer"></span>
      <span class="muted" id="selCount"></span></div>
    <div id="previewBox"></div>
    <p class="muted">Levels follow the project's process (${esc([cfg.epic_types.length ? "Epic" : "", cfg.feature_types.length ? "Feature" : "", ...cfg.requirement_types, ...cfg.test_types].filter(Boolean).join(" → "))});
      relationships are the real TFS Parent/Child and Tested By links.</p>`;
  const $ = (sel) => box.querySelector(sel);
  const selected = new Set();
  const excluded = new Set();
  const parentOf = new Map();
  const cache = new Map();
  let lastScope = null;

  const isOn = (id) => {
    if (excluded.has(id)) return false;
    if (selected.has(id)) return true;
    const p = parentOf.get(id);
    return p ? isOn(p) : false;
  };
  const option = () => $("[name=option]").value;
  const selectable = (level) => {
    const o = option();
    if (o === "custom") return true;
    if (o === "acceptance_criteria") return level === "ac";
    return LIST_LEVEL[o] === level;
  };

  const nodeHtml = (n, depth) => `
    <div class="tree-node" data-id="${esc(n.id)}" style="padding-left:${depth * 18}px">
      ${n.has_children ? `<button type="button" class="link-btn" data-expand="${esc(n.id)}" aria-label="Expand">▶</button>` : '<span style="display:inline-block;width:12px"></span>'}
      ${selectable(n.level) ? `<input type="checkbox" data-check="${esc(n.id)}" ${isOn(String(n.id)) ? "checked" : ""}>` : ""}
      <strong>${esc(n.label)}</strong> ${esc(n.title || "")} <span class="muted">${esc(n.type || "")}${n.state ? ` · ${esc(n.state)}` : ""}</span>
      <div data-kids="${esc(n.id)}"></div></div>`;

  const children = async (id) => {
    if (!cache.has(id)) cache.set(id, (await api(`/api/tfs/hierarchy/children/${encodeURIComponent(id)}`)).items);
    return cache.get(id);
  };
  const renderList = async () => {
    const o = option();
    $("#storiesIn").hidden = o !== "all_stories";
    lastScope = null;
    $("#previewBox").innerHTML = "";
    const tree = $("#tree");
    if (["entire_project", "current_sprint", "all_stories"].includes(o)) {
      tree.innerHTML = `<p class="muted">${o === "entire_project" ? "The whole project's requirement hierarchy." : o === "current_sprint"
        ? "Requirements of the selected Sprint / Iteration, with their Features, Acceptance Criteria and Test Cases." : "User Stories with their Acceptance Criteria and Test Cases."} Use Preview to see the counts.</p>`;
      updateCount();
      return;
    }
    const level = o === "custom" ? (cfg.epic_types.length ? "epic" : cfg.feature_types.length ? "feature" : "story") : LIST_LEVEL[o];
    tree.innerHTML = '<p class="muted">Loading…</p>';
    try {
      const params = new URLSearchParams({ level });
      const it = $("[name=iteration]").value;
      if (it && ["story", "testcase"].includes(level)) params.set("iteration", it);
      if ($("[name=text]").value.trim()) params.set("text", $("[name=text]").value.trim());
      const r = await api(`/api/tfs/hierarchy/items?${params}`);
      tree.innerHTML = r.items.length ? r.items.map((n) => nodeHtml(n, 0)).join("") + (r.truncated ? '<p class="muted">More items exist; refine the search.</p>' : "")
        : `<p class="muted">No ${esc(LEVEL_NAMES[level])} found.</p>`;
    } catch (err) { tree.innerHTML = `<p class="pill FAILED">${esc(err.message)}</p>`; }
    updateCount();
  };
  const refreshChecks = () => box.querySelectorAll("[data-check]").forEach((c) => { c.checked = isOn(c.dataset.check); });
  const updateCount = () => {
    const o = option();
    $("#selCount").textContent = ["entire_project", "current_sprint", "all_stories"].includes(o) ? "" : `${selected.size} selected${excluded.size ? `, ${excluded.size} excluded` : ""}`;
  };

  box.addEventListener("click", async (e) => {
    const exp = e.target.closest("[data-expand]");
    if (!exp) return;
    const id = exp.dataset.expand;
    const kids = box.querySelector(`[data-kids="${CSS.escape(id)}"]`);
    if (kids.innerHTML) { kids.innerHTML = ""; exp.textContent = "▶"; return; }
    exp.textContent = "▼";
    kids.innerHTML = '<span class="muted">Loading…</span>';
    try {
      const items = await children(id);
      items.forEach((n) => parentOf.set(String(n.id), id));
      // Nested inside the parent's node, so one level of indent is relative to it.
      kids.innerHTML = items.length ? items.map((n) => nodeHtml(n, 1)).join("") : '<span class="muted">No child items.</span>';
    } catch (err) { kids.innerHTML = `<span class="pill FAILED">${esc(err.message)}</span>`; }
  });
  box.addEventListener("change", (e) => {
    const c = e.target.closest("[data-check]");
    if (!c) return;
    const id = c.dataset.check;
    const parentOn = parentOf.get(id) && isOn(parentOf.get(id));
    if (c.checked) { excluded.delete(id); if (!parentOn) selected.add(id); }
    else if (parentOn) { excluded.add(id); selected.delete(id); }
    else selected.delete(id);
    lastScope = null;
    refreshChecks();
    updateCount();
  });
  $("[name=option]").addEventListener("change", () => { selected.clear(); excluded.clear(); renderList(); });
  $("[name=iteration]").addEventListener("change", renderList);
  let t = null;
  $("[name=text]").addEventListener("input", () => { clearTimeout(t); t = setTimeout(renderList, 350); });
  $("#refresh").addEventListener("click", async () => {
    await api("/api/tfs/hierarchy/refresh", { method: "POST" });
    cache.clear();
    renderList();
    toast("TFS hierarchy reloaded.");
  });

  const value = () => ({
    option: option(), iteration: $("[name=iteration]").value || null, ids: [...selected], excluded: [...excluded],
    stories_in: box.querySelector("[name=stories_in]:checked")?.value || "sprint",
  });
  const preview = async () => {
    const r = await api("/api/tfs/hierarchy/resolve", { method: "POST", body: value() });
    lastScope = r.scope;
    const s = r.scope;
    const tree = (key, depth) => {
      const n = s.items[key];
      if (!n || depth > 4) return "";
      const active = s.active.includes(key);
      return `<div style="padding-left:${depth * 16}px" class="${active ? "" : "muted"}">${esc(n.label)} ${esc(n.title || "")}${active ? "" : " (context)"}</div>
        ${n.children.filter((c) => ["epic", "feature", "story"].includes(s.items[c]?.level)).map((c) => tree(c, depth + 1)).join("")}`;
    };
    $("#previewBox").innerHTML = `<div class="card" style="margin-top:8px"><h3>Selected Scope</h3>
      ${s.large ? `<p class="pill WARN">Large Scope: this loads ${s.option === "entire_project" ? "the requirement hierarchy for the entire project" : `${s.total} requirement items`}.</p>` : ""}
      <div class="grid kpis">${Object.entries(LEVEL_NAMES).map(([k, v]) => `<div><div class="kpi-label">${esc(v)}</div><div class="kpi-value" style="font-size:20px">${s.counts[k]}</div></div>`).join("")}</div>
      <details><summary>Expand hierarchy</summary>${s.roots.map((k) => tree(k, 0)).join("")}</details></div>`;
    return s;
  };
  $("#preview").addEventListener("click", async () => { try { await preview(); } catch (err) { toast(err.message, "error"); } });
  await renderList();
  return { ready: true, config: cfg, value, preview: async () => lastScope || preview() };
}
