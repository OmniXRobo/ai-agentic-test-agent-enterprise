// Agent Workflow, Mission Control look (Preferences > Agent Workflow look): the SkyGuard Core page under three tabs.
//   01 Live Agent Network: the orchestrator, every agent and the engines they run on, then the cards and activity.
//   02 AI Knowledge Graph: agents, categories, inputs, outputs, engines and dependencies, from the agent registry
//      (GET /api/agents), with search, zoom, pan, filters and export (PNG / JSON, saved on this PC).
//   03 Workflow Map: the dependencies between agents, laid out in order. View only.
// Every node and line is a stored registry fact or a runtime event: nothing is sampled or invented. What SkyGuard does
// not measure (CPU, memory, tokens) says "Not available". Canvas, SVG and CSS only: no libraries or network.

import { state, subscribe } from "../../store.js";
import { setCardDeco } from "../../agent-card.js";
import { agentState } from "../../agent-state.js";
import { counts } from "./common.js";
import { backdropHtml, heroHtml, bodyHtml, startCore } from "./skyguard-core.js";
import { esc, timeOf } from "../../util.js";

export const css = "mission-control.css";

let cleanup = null;
const short = (n) => String(n || "").replace(/ Agent$/, "");
const live = (s) => s === "RUNNING" || s === "PAUSED";
const stOf = (a) => agentState(a, state.agents).state;
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

// Engines are the runtime components named in each agent's "source" (registry), e.g. "tfs.work_items + ai.gateway".
const ENGINE_NAMES = {
  ai: "Local AI", tfs: "TFS / Azure DevOps", playwright: "Playwright", jmeter: "JMeter", reports: "Reports · PDF",
  voice: "Voice engine", mailer: "Email", scheduler: "Scheduler", orchestrator: "Orchestrator", performance: "Performance engine",
  defects: "Defect store", comparison: "Comparison engine", apitesting: "API test engine", opsagent: "Operations engine",
  aura: "AURA memory", sectest: "Security engine", fusetest: "Fuse adapters", amqtest: "ActiveMQ client", itest: "Result actions",
};
function enginesOf(a) {
  return [...new Set(String(a.source || "").split("+").map((p) => p.trim().split(".")[0]).filter(Boolean))];
}
const engineLabel = (k) => ENGINE_NAMES[k] || k;

function loop(fn, offs) {
  let id = 0, on = true;
  const f = (t) => { if (!on) return; fn(t); id = requestAnimationFrame(f); };
  id = requestAnimationFrame(f);
  offs.push(() => { on = false; cancelAnimationFrame(id); });
}
function fit(canvas) {
  const r = canvas.getBoundingClientRect(), d = devicePixelRatio || 1;
  canvas.width = Math.max(1, r.width * d); canvas.height = Math.max(1, r.height * d);
  const c = canvas.getContext("2d"); c.setTransform(d, 0, 0, d, 0, 0);
  return [c, r.width, r.height];
}
function download(name, blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

function tabsBarHtml() {
  return `<div class="tv-tabs" role="tablist">
    <button role="tab" class="tv-tab on" data-tab="net"><b>01</b>Live Agent Network<i>orchestration</i></button>
    <button role="tab" class="tv-tab" data-tab="kg"><b>02</b>AI Knowledge Graph<i>explore</i></button>
    <button role="tab" class="tv-tab" data-tab="wb"><b>03</b>Workflow Map<i>view only</i></button>
    <span class="tv-tabs-sp"></span>
    <label class="tv-motion"><input type="checkbox" ${reducedMotion() ? "" : "checked"}> Full animation</label>
  </div>`;
}

/* ===================== 01 LIVE AGENT NETWORK ===================== */
function netKpis() {
  const n = counts();
  return `<span><em>Agents</em><b>${n.total}</b></span><span><em>Running</em><b>${n.airborne}</b></span><span><em>Waiting</em><b>${n.WAITING}</b></span><span><em>Completed</em><b>${n.COMPLETED}</b></span><span><em>Failed</em><b class="bad">${n.FAILED}</b></span><span><em>Unavailable</em><b>${n.UNAVAILABLE}</b></span>`;
}
function hqRows() {
  const cats = new Map();
  for (const a of state.agents.values()) {
    const c = a.category || "Other";
    if (!cats.has(c)) cats.set(c, []);
    cats.get(c).push(a);
  }
  return [...cats].map(([c, list]) => {
    const act = list.filter((a) => live(stOf(a))).length;
    return `<div class="tv-row ${act ? "on" : ""}"><i></i>${esc(c)}<b>${act ? `${act} active` : list.length}</b></div>`;
  }).join("");
}
function focusAgent() {
  let best = null;
  for (const a of state.agents.values()) if (live(stOf(a)) && (!best || new Date(a.started_at) > new Date(best.started_at))) best = a;
  return best;
}
function focusHtml() {
  const f = focusAgent();
  return `<dl><dt>Focus</dt><dd>${esc(f ? short(f.name) : "—")}</dd><dt>Run</dt><dd>${esc(f?.run_id || "—")}</dd><dt>Progress</dt><dd>${f ? `${f.progress ?? 0}%` : "—"}</dd><dt>Mode</dt><dd>${f ? (stOf(f) === "PAUSED" ? "paused" : "running") : "standby"}</dd></dl>`;
}
function calloutHtml() {
  const n = counts();
  return `<b>${n.airborne} agent${n.airborne === 1 ? "" : "s"} airborne</b><span>${n.FAILED} failed · ${n.COMPLETED} completed in the last 30 s</span>`;
}

function netHtml() {
  const engines = new Set();
  for (const a of state.agents.values()) enginesOf(a).forEach((e) => engines.add(e));
  return `<section class="tv-net">
    <div class="tv-net-head"><span class="tv-badge">SKYGUARD QA<em>AGENT MODE</em></span>
      <div class="tv-net-title"><b>AGENT SQUADRON · LIVE NETWORK</b><span>orchestrator, ${state.agents.size} agents, ${engines.size} engines from the agent registry</span></div>
      <div class="tv-kpis" id="tvKpis">${netKpis()}</div>
    </div>
    <div class="tv-net-stage">
      <canvas id="tvNet"></canvas>
      <div class="tv-box tl"><div class="tv-box-h"><b>01</b>SQUADRON HQ · agents by category</div><div id="tvHq">${hqRows()}</div>
        <small>every action keeps its run ID and evidence</small></div>
      <div class="tv-box tr tv-clock"><div class="tv-box-h">CLOCK<em id="tvT">T+00:00</em></div><canvas id="tvClock" width="120" height="120"></canvas><b id="tvClockTxt"></b></div>
      <div class="tv-box bl" id="tvFocus">${focusHtml()}</div>
      <div class="tv-callout" id="tvCallout">${calloutHtml()}</div>
    </div>
    <div class="tv-cmd"><span>&gt;</span><b id="tvCmd"></b><i class="tv-caret"></i><em id="tvCmdWho">STANDBY</em></div>
  </section>`;
}

function mountNet(view, offs, motion) {
  const cv = view.querySelector("#tvNet");
  const agents = [...state.agents.values()].filter((a) => a.id !== "orchestration")
    .sort((a, b) => (a.category || "").localeCompare(b.category || "")); // a category sits together on the orbit
  const pos = agents.map((a, i) => ({ a, ang: (i / agents.length) * Math.PI * 2, rr: [0.4, 0.6, 0.8][i % 3] }));
  const engKeys = [...new Set(agents.flatMap(enginesOf))].filter((k) => k !== "orchestrator");
  const sys = engKeys.map((k, i) => ({ k, l: engineLabel(k), ang: (i / engKeys.length) * Math.PI * 2 + 0.2 }));
  const cmd = view.querySelector("#tvCmd"), who = view.querySelector("#tvCmdWho");
  const clk = view.querySelector("#tvClock").getContext("2d"), clkTxt = view.querySelector("#tvClockTxt"), tT = view.querySelector("#tvT");
  const t0 = performance.now();
  let ctx, W, H, typed = 0, full = "";
  const resize = () => { [ctx, W, H] = fit(cv); };
  resize();
  const ro = new ResizeObserver(resize); ro.observe(cv); offs.push(() => ro.disconnect());
  loop((ms) => {
    if (!cv.isConnected || cv.offsetParent === null) return; // hidden tab: draw nothing
    const t = motion() ? ms / 1000 : 0, rot = t * 0.05;
    const cx = W / 2, cy = H / 2 + 6, RX = W * 0.40, RY = H * 0.36;
    ctx.clearRect(0, 0, W, H);
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, RX); g.addColorStop(0, "rgba(56,214,255,.12)"); g.addColorStop(1, "rgba(56,214,255,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "rgba(56,214,255,.55)"; ctx.setLineDash([10, 9]); ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.ellipse(cx, cy, RX, RY, -0.05, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
    ctx.strokeStyle = "rgba(56,214,255,.12)"; ctx.beginPath(); ctx.ellipse(cx, cy, RX * 0.55, RY * 0.55, -0.05, 0, Math.PI * 2); ctx.stroke();
    const P = (ang, rr) => [cx + Math.cos(ang + rot) * RX * rr, cy + Math.sin(ang + rot) * RY * rr];
    const sysPos = Object.fromEntries(sys.map((s) => [s.k, P(s.ang, 1)]));
    const nodes = pos.map((p) => ({ ...p, xy: P(p.ang, p.rr), s: stOf(p.a) }));
    const byId = Object.fromEntries(nodes.map((n) => [n.a.id, n]));
    for (const n of nodes) {
      const hot = live(n.s);
      ctx.strokeStyle = hot ? "rgba(143,248,255,.85)" : n.s === "FAILED" ? "rgba(255,75,92,.5)" : "rgba(56,214,255,.16)"; ctx.lineWidth = hot ? 1.8 : 1;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(...n.xy); ctx.stroke();
      for (const sk of enginesOf(n.a)) {
        if (!sysPos[sk]) continue;
        ctx.strokeStyle = hot ? "rgba(143,248,255,.7)" : "rgba(56,214,255,.09)"; ctx.beginPath(); ctx.moveTo(...n.xy); ctx.lineTo(...sysPos[sk]); ctx.stroke();
        if (hot) { const f = (t * 0.7) % 1, [sx, sy] = sysPos[sk]; ctx.fillStyle = "#e6fdff"; ctx.beginPath(); ctx.arc(n.xy[0] + (sx - n.xy[0]) * f, n.xy[1] + (sy - n.xy[1]) * f, 2.4, 0, 7); ctx.fill(); }
      }
      for (let k = 1; k < 3; k++) { const f = k / 3; ctx.fillStyle = hot ? "#8ff8ff" : "rgba(56,214,255,.4)"; ctx.fillRect(cx + (n.xy[0] - cx) * f - 2, cy + (n.xy[1] - cy) * f - 2, 4, 4); }
      if (hot) for (let k = 0; k < 3; k++) {
        const f = ((t * 0.5 + k / 3) % 1), x = cx + (n.xy[0] - cx) * f, y = cy + (n.xy[1] - cy) * f;
        ctx.fillStyle = "#e6fdff"; ctx.shadowColor = "#38d6ff"; ctx.shadowBlur = 12; ctx.beginPath(); ctx.arc(x, y, 2.6, 0, 7); ctx.fill(); ctx.shadowBlur = 0;
      }
    }
    // recorded dependencies between agents (registry "depends")
    ctx.strokeStyle = "rgba(200,246,255,.35)"; ctx.setLineDash([3, 4]);
    for (const n of nodes) for (const d of n.a.depends || []) if (byId[d]) { ctx.beginPath(); ctx.moveTo(...byId[d].xy); ctx.lineTo(...n.xy); ctx.stroke(); }
    ctx.setLineDash([]);
    ctx.font = "10px Consolas, monospace"; ctx.textAlign = "center";
    for (const s of sys) {
      const [x, y] = sysPos[s.k];
      ctx.strokeStyle = "rgba(200,246,255,.85)"; ctx.fillStyle = "rgba(4,14,28,.92)"; ctx.fillRect(x - 9, y - 9, 18, 18); ctx.strokeRect(x - 9, y - 9, 18, 18);
      ctx.fillStyle = "#38d6ff"; ctx.fillRect(x - 3, y - 3, 6, 6); ctx.fillStyle = "rgba(190,220,245,.8)"; ctx.fillText(s.l.toLowerCase(), x, y + 22);
    }
    for (const n of nodes) {
      const [x, y] = n.xy, hot = live(n.s);
      const col = n.s === "FAILED" ? "#ff4b5c" : n.s === "COMPLETED" ? "#2fe08a" : n.s === "UNAVAILABLE" ? "#5f7fa6" : hot ? "#8ff8ff" : "#d6ecff";
      ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.arc(x, y, hot ? 9 : 6, 0, 7); ctx.stroke();
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, hot ? 4 : 2.6, 0, 7); ctx.fill();
      if (hot) { const p = (t % 1.4) / 1.4; ctx.strokeStyle = `rgba(56,214,255,${1 - p})`; ctx.beginPath(); ctx.arc(x, y, 10 + p * 22, 0, 7); ctx.stroke(); }
      ctx.fillStyle = hot ? "#8ff8ff" : n.s === "FAILED" ? "#ff7884" : "rgba(200,225,245,.8)"; ctx.font = `${hot ? "bold " : ""}10px Consolas, monospace`;
      if (!hot) { const l = short(n.a.name).toUpperCase(); ctx.font = "9px Consolas, monospace"; ctx.fillText(l.length > 22 ? `${l.slice(0, 21)}…` : l, x, y + 17); }
    }
    for (const n of nodes.filter((m) => live(m.s))) {
      const [x, y] = n.xy, bx = x + 22, by = y - 46, w = 220;
      ctx.strokeStyle = "#38d6ff"; ctx.fillStyle = "rgba(2,12,26,.9)"; ctx.fillRect(bx, by, w, 36); ctx.strokeRect(bx, by, w, 36);
      ctx.textAlign = "left"; ctx.fillStyle = "#8ff8ff"; ctx.font = "bold 13px Consolas, monospace"; ctx.fillText(short(n.a.name).toUpperCase().slice(0, 24), bx + 8, by + 15);
      ctx.fillStyle = "rgba(210,230,250,.85)"; ctx.font = "10px Consolas, monospace"; ctx.fillText(`${n.s === "PAUSED" ? "paused" : "running"} · ${n.a.progress ?? 0}% · ${n.a.run_id || ""}`.slice(0, 34), bx + 8, by + 29); ctx.textAlign = "center";
    }
    const pulse = 1 + Math.sin(t * 3) * 0.08;
    ctx.shadowColor = "#38d6ff"; ctx.shadowBlur = 30; ctx.fillStyle = "#38d6ff"; ctx.fillRect(cx - 11 * pulse, cy - 11 * pulse, 22 * pulse, 22 * pulse); ctx.shadowBlur = 0;
    ctx.fillStyle = "#fff"; ctx.font = "bold 11px Consolas, monospace"; ctx.fillText("QA ORCHESTRATION", cx, cy + 30);
    // command line: what the focused agent reports now
    const f = focusAgent(), text = f ? `${short(f.name)}: ${f.activity || ""}` : "All agents ready for tasking.";
    if (text !== full) { full = text; typed = motion() ? 0 : text.length; }
    if (typed < full.length) typed++;
    cmd.textContent = full.slice(0, typed);
    who.textContent = f ? short(f.name).toUpperCase() : "STANDBY";
    const d = new Date(), el = Math.floor((ms - t0) / 1000);
    tT.textContent = `T+${String(Math.floor(el / 60)).padStart(2, "0")}:${String(el % 60).padStart(2, "0")}`;
    clkTxt.textContent = d.toLocaleTimeString([], { hour12: false });
    clk.clearRect(0, 0, 120, 120); clk.strokeStyle = "rgba(200,246,255,.6)"; clk.lineWidth = 1; clk.beginPath(); clk.arc(60, 60, 48, 0, 7); clk.stroke();
    for (let i = 0; i < 60; i++) { const a = (i / 60) * 7 - 1.5708, r1 = i % 5 ? 44 : 40; clk.beginPath(); clk.moveTo(60 + Math.cos(a) * r1, 60 + Math.sin(a) * r1); clk.lineTo(60 + Math.cos(a) * 47, 60 + Math.sin(a) * 47); clk.stroke(); }
    const hand = (v, len, col, w) => { const a = v * Math.PI * 2 - Math.PI / 2; clk.strokeStyle = col; clk.lineWidth = w; clk.beginPath(); clk.moveTo(60, 60); clk.lineTo(60 + Math.cos(a) * len, 60 + Math.sin(a) * len); clk.stroke(); };
    hand(((d.getHours() % 12) + d.getMinutes() / 60) / 12, 24, "#fff", 2.5); hand(d.getMinutes() / 60, 36, "#fff", 2); hand((d.getSeconds() + d.getMilliseconds() / 1000) / 60, 42, "#38d6ff", 1.2);
  }, offs);
}

/* ===================== 02 AI KNOWLEDGE GRAPH ===================== */
const KG_TYPES = [
  ["agent", "Agents", "#8ff8ff"], ["category", "Categories", "#5fb8ff"], ["input", "Inputs", "#7fd0ff"],
  ["output", "Outputs", "#4fe0d0"], ["engine", "Engines", "#c8f6ff"],
];
const KG_EDGE = { category: "in category", consumes: "takes as input", produces: "produces", runs_on: "runs on", depends: "depends on" };

/** Nodes and links from the registry only. An input and an output with the same name are the same node. */
function buildKg() {
  const nodes = [], edges = [], idx = new Map();
  const node = (type, key, label, extra = {}) => {
    const k = `${type}:${key}`;
    if (!idx.has(k)) { idx.set(k, nodes.length); nodes.push({ id: nodes.length, type, key, label, ...extra }); }
    return idx.get(k);
  };
  const artifact = (label, as) => { // one node per name; type says how it is first used
    const k = String(label).toLowerCase();
    for (const t of ["input", "output"]) if (idx.has(`${t}:${k}`)) return idx.get(`${t}:${k}`);
    return node(as, k, label);
  };
  for (const a of state.agents.values()) {
    const me = node("agent", a.id, short(a.name), { agent: a.id });
    edges.push([me, node("category", a.category || "Other", a.category || "Other"), "category"]);
    for (const i of a.inputs || []) edges.push([artifact(i, "input"), me, "consumes"]);
    for (const o of a.outputs || []) edges.push([me, artifact(o, "output"), "produces"]);
    for (const e of enginesOf(a)) edges.push([me, node("engine", e, engineLabel(e)), "runs_on"]);
  }
  for (const a of state.agents.values()) for (const d of a.depends || []) {
    if (idx.has(`agent:${d}`)) edges.push([idx.get(`agent:${a.id}`), idx.get(`agent:${d}`), "depends"]);
  }
  // force layout, deterministic (same picture every time)
  let seed = 11; const rnd = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
  const ring = { agent: 0, category: 1, engine: 2, input: 3, output: 3 };
  nodes.forEach((n) => { const a = rnd() * Math.PI * 2, r = 80 + ring[n.type] * 140 + rnd() * 60; n.x = Math.cos(a) * r; n.y = Math.sin(a) * r * 0.7; n.vx = 0; n.vy = 0; });
  for (let it = 0; it < 260; it++) {
    const cool = 1 - it / 260;
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const A = nodes[i], B = nodes[j]; let dx = A.x - B.x, dy = A.y - B.y; const d2 = dx * dx + dy * dy + 0.01;
      if (d2 > 160000) continue;
      const f = 1800 / d2; dx *= f; dy *= f; A.vx += dx; A.vy += dy; B.vx -= dx; B.vy -= dy;
    }
    for (const [a, b] of edges) {
      const A = nodes[a], B = nodes[b], dx = B.x - A.x, dy = B.y - A.y, d = Math.sqrt(dx * dx + dy * dy) || 1, f = (d - 70) * 0.012;
      A.vx += (dx / d) * f * d * 0.05; A.vy += (dy / d) * f * d * 0.05; B.vx -= (dx / d) * f * d * 0.05; B.vy -= (dy / d) * f * d * 0.05;
    }
    for (const n of nodes) { n.vx -= n.x * 0.004; n.vy -= n.y * 0.006; n.x += Math.max(-20, Math.min(20, n.vx)) * cool; n.y += Math.max(-20, Math.min(20, n.vy)) * cool; n.vx *= 0.5; n.vy *= 0.5; }
  }
  // scale to a 1200 x 560 world so "fit" shows it at about 1:1 (labels stay readable)
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const mx = (Math.max(...xs) + Math.min(...xs)) / 2, my = (Math.max(...ys) + Math.min(...ys)) / 2;
  const k = Math.min(1200 / (Math.max(...xs) - Math.min(...xs) || 1), 560 / (Math.max(...ys) - Math.min(...ys) || 1));
  nodes.forEach((n) => { n.x = (n.x - mx) * k; n.y = (n.y - my) * k; });
  const adj = new Map(nodes.map((n) => [n.id, []]));
  for (const [a, b, k] of edges) { adj.get(a).push([b, k, "out"]); adj.get(b).push([a, k, "in"]); }
  return { nodes, edges, adj };
}

function kgHtml(g) {
  return `<section class="tv-kg">
    <div class="tv-kg-head">
      <div><b>AI KNOWLEDGE GRAPH / SKYGUARD QA</b><span>Source: agent registry (categories, inputs, outputs, engines, dependencies) and the live agent states</span></div>
      <div class="tv-kg-live"><i></i>LIVE</div>
    </div>
    <div class="tv-kg-filters">${KG_TYPES.map(([k, l, c]) => `<button type="button" class="tv-chip on" data-type="${k}" style="--c:${c}">${l} <b>${g.nodes.filter((n) => n.type === k).length}</b></button>`).join("")}</div>
    <div class="tv-kg-search"><span>&gt;</span><input id="tvKgQ" placeholder="Search: an agent, input, output, engine or category (for example: API, evidence, TFS)" aria-label="Search the knowledge graph"><em id="tvKgHits">read-only search</em></div>
    <div class="tv-kg-stage" id="tvKgStage">
      <canvas id="tvKg"></canvas>
      <div class="tv-kg-meta">GRAPH / <b>${g.nodes.length}</b> NODES · <b>${g.edges.length}</b> LINKS · from the agent registry</div>
      <div class="tv-kg-az">ZOOM <b id="tvZoom">1.00</b></div>
      <div class="tv-kg-legend">${Object.entries(KG_EDGE).map(([k, l]) => `<div><i class="l-${k === "depends" ? "ai" : "wf"}"></i>${l}</div>`).join("")}</div>
      <div class="tv-kg-tools"><button type="button" data-k="in" title="Zoom in">+</button><button type="button" data-k="out" title="Zoom out">−</button><button type="button" data-k="fit" title="Fit">⤢</button><button type="button" data-k="focus" title="Focus the selected node">◎</button><button type="button" data-k="png" title="Export PNG">⇩</button><button type="button" data-k="json" title="Export JSON">{ }</button></div>
      <div class="tv-kg-sel"><b></b></div>
    </div>
    <div class="tv-grid" id="tvKgPanels"></div>
  </section>`;
}

function kgPanels(g, sel) {
  const n = g.nodes[sel];
  const nb = g.adj.get(sel) || [];
  const a = n?.agent ? state.agents.get(n.agent) : null;
  const s = a ? stOf(a) : null;
  const list = (kind, dir) => nb.filter(([, k, d]) => k === kind && (!dir || d === dir)).map(([b]) => g.nodes[b].label);
  const join = (xs) => xs.length ? xs.map(esc).join(" · ") : '<span class="tv-empty">none recorded</span>';
  const insp = a ? `<div class="tv-insp"><div><small>agent /</small><strong>${esc(a.name)}</strong><p>${esc(a.role || "")}</p>
      <dl><dt>Category</dt><dd>${esc(a.category || "—")}</dd><dt>Source</dt><dd>${esc(a.source || "—")}</dd><dt>Inputs</dt><dd>${join(a.inputs || [])}</dd><dt>Outputs</dt><dd>${join(a.outputs || [])}</dd>
      <dt>Depends on</dt><dd>${join(list("depends", "out"))}</dd><dt>Needed by</dt><dd>${join(list("depends", "in"))}</dd></dl></div>
      <div><dl><dt>State</dt><dd>${esc(s)}</dd><dt>Run</dt><dd>${esc(a.run_id || "—")}</dd><dt>Started</dt><dd>${esc(a.started_at ? timeOf(a.started_at) : "—")}</dd><dt>Progress</dt><dd>${live(s) ? `${a.progress ?? 0}%` : "—"}</dd>
      <dt>Activity</dt><dd>${esc(a.activity || "—")}</dd><dt>Availability</dt><dd>${a.available === false ? `<span class="bad">${esc(a.unavailable_reason || "Unavailable on this PC")}</span>` : '<span class="ok">available</span>'}</dd></dl></div></div>`
    : `<div class="tv-insp"><div><small>${esc(n?.type || "")} /</small><strong>${esc(n?.label || "")}</strong>
      <dl>${Object.entries(KG_EDGE).map(([k, l]) => { const xs = nb.filter(([, kk]) => kk === k).map(([b]) => g.nodes[b].label); return xs.length ? `<dt>${esc(l)}</dt><dd>${join(xs)}</dd>` : ""; }).join("")}</dl></div></div>`;
  const cats = new Map();
  for (const ag of state.agents.values()) cats.set(ag.category || "Other", (cats.get(ag.category || "Other") || 0) + 1);
  const log = state.events.slice(-8).map((e) => `<div><em>${esc(timeOf(e.timestamp))}</em> ${e.name === "agent.failed" ? "<s>×</s>" : e.name === "agent.completed" ? "<u>✓</u>" : "+"} ${esc(short(e.agent_name || ""))} <span>${esc((e.activity || e.name || "").slice(0, 60))}</span></div>`).join("") || '<div class="tv-empty">No agent activity yet.</div>';
  const fails = new Map(); const lastOk = new Map();
  for (const e of state.events) {
    if (e.name === "agent.failed") fails.set(e.agent_id, (fails.get(e.agent_id) || 0) + 1);
    if (e.name === "agent.completed") lastOk.set(e.agent_id, e.timestamp);
  }
  const health = [...state.agents.values()].map((ag) => {
    const st = stOf(ag);
    return `<tr><td>${esc(short(ag.name))}</td><td class="${st === "FAILED" ? "bad" : st === "UNAVAILABLE" ? "na" : "ok"}">${esc(st)}</td><td>${fails.get(ag.id) || 0}</td><td>${lastOk.get(ag.id) ? esc(timeOf(lastOk.get(ag.id))) : "—"}</td></tr>`;
  }).join("");
  return `
    <div class="tv-p w2"><h4><b>01</b> INSPECTOR <em>${esc(s || n?.type || "")}</em></h4>${insp}</div>
    <div class="tv-p"><h4><b>02</b> CONNECTIONS <em>${nb.length} links</em></h4><div class="tv-kv">${Object.entries(KG_EDGE).map(([k, l]) => `<span>${esc(l)}</span><b>${nb.filter(([, kk]) => kk === k).length}</b>`).join("")}</div></div>
    <div class="tv-p"><h4><b>03</b> REGISTRY <em>${state.agents.size} agents</em></h4><div class="tv-kv">${[...cats].map(([c, k]) => `<span>${esc(c)}</span><b>${k}</b>`).join("")}</div></div>
    <div class="tv-p w2"><h4><b>04</b> EXECUTION LOG <em>live</em></h4><div class="tv-log">${log}</div></div>
    <div class="tv-p"><h4><b>05</b> RESOURCES <em>this PC</em></h4><div class="tv-kv"><span>CPU</span><b class="na">Not available</b><span>Memory</span><b class="na">Not available</b><span>AI tokens</span><b class="na">Not available</b></div><small class="tv-note">SkyGuard QA does not measure these yet.</small></div>
    <div class="tv-p"><h4><b>06</b> SEARCH TIPS <em>read-only</em></h4><div class="tv-note">Type a word to highlight matching nodes; Enter selects the first match. Search never changes anything. Click a node to inspect it; drag to pan; wheel to zoom.</div></div>
    <div class="tv-p w2" style="grid-column:1/-1"><h4><b>07</b> AGENT HEALTH <em>state · failures and last success in the events loaded on this page</em></h4><div style="max-height:300px;overflow:auto"><table class="tv-tbl"><tr><th>Agent</th><th>State</th><th>Failures</th><th>Last success</th></tr>${health}</table></div></div>`;
}

function mountKg(view, offs, motion, g) {
  const cv = view.querySelector("#tvKg"), stage = view.querySelector("#tvKgStage");
  let ctx, W, H;
  const resize = () => { [ctx, W, H] = fit(cv); };
  resize();
  const ro = new ResizeObserver(resize); ro.observe(cv); offs.push(() => ro.disconnect());
  const hidden = new Set();
  let zoom = 1, px = 0, py = 0, drag = null, hits = new Set();
  let sel = g.nodes.findIndex((n) => n.agent && live(stOf(state.agents.get(n.agent))));
  if (sel < 0) sel = g.nodes.findIndex((n) => n.type === "agent");
  const panels = view.querySelector("#tvKgPanels");
  const drawPanels = () => { panels.innerHTML = kgPanels(g, sel); };
  drawPanels();
  const visible = (n) => !hidden.has(n.type);
  const X = (n) => W / 2 + px + n.x * zoom, Y = (n) => H / 2 + py + n.y * zoom;
  const fitAll = () => {
    const vs = g.nodes.filter(visible); if (!vs.length) return;
    const xs = vs.map((n) => n.x), ys = vs.map((n) => n.y);
    const w = Math.max(...xs) - Math.min(...xs) + 120, h = Math.max(...ys) - Math.min(...ys) + 120;
    zoom = Math.max(0.3, Math.min(3, Math.min(W / w, H / h)));
    px = -((Math.max(...xs) + Math.min(...xs)) / 2) * zoom; py = -((Math.max(...ys) + Math.min(...ys)) / 2) * zoom;
    view.querySelector("#tvZoom").textContent = zoom.toFixed(2);
  };
  fitAll();
  let fitted = W > 50;
  const ro2 = new ResizeObserver(() => { if (!fitted && W > 50) { fitted = true; fitAll(); } });
  ro2.observe(cv); offs.push(() => ro2.disconnect());
  const setZoom = (z) => { zoom = Math.min(4, Math.max(0.3, z)); view.querySelector("#tvZoom").textContent = zoom.toFixed(2); };
  const onWheel = (e) => { e.preventDefault(); setZoom(zoom * (e.deltaY < 0 ? 1.1 : 0.9)); };
  const onDown = (e) => { drag = [e.clientX, e.clientY, px, py, false]; };
  const onMove = (e) => { if (!drag) return; px = drag[2] + e.clientX - drag[0]; py = drag[3] + e.clientY - drag[1]; if (Math.abs(e.clientX - drag[0]) + Math.abs(e.clientY - drag[1]) > 4) drag[4] = true; };
  const onUp = (e) => {
    if (drag && !drag[4] && e.target === cv) {
      const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
      let best = -1, bd = 300;
      g.nodes.forEach((n, i) => { if (!visible(n)) return; const d = (X(n) - x) ** 2 + (Y(n) - y) ** 2; if (d < bd) { bd = d; best = i; } });
      if (best >= 0) { sel = best; drawPanels(); }
    }
    drag = null;
  };
  cv.addEventListener("wheel", onWheel, { passive: false });
  cv.addEventListener("pointerdown", onDown);
  addEventListener("pointermove", onMove);
  addEventListener("pointerup", onUp);
  offs.push(() => { removeEventListener("pointermove", onMove); removeEventListener("pointerup", onUp); });
  view.querySelectorAll(".tv-chip").forEach((b) => b.addEventListener("click", () => {
    b.classList.toggle("on");
    if (b.classList.contains("on")) hidden.delete(b.dataset.type); else hidden.add(b.dataset.type);
  }));
  const q = view.querySelector("#tvKgQ"), hitsEl = view.querySelector("#tvKgHits");
  const search = () => {
    const words = q.value.toLowerCase().split(/\s+/).filter((w) => w.length > 1 && !["which", "what", "show", "the", "and", "for", "with", "agents", "agent"].includes(w));
    hits = new Set();
    if (words.length) g.nodes.forEach((n, i) => {
      const a = n.agent ? state.agents.get(n.agent) : null;
      const text = `${n.label} ${n.type} ${a ? `${a.name} ${a.role} ${a.category} ${a.source}` : ""}`.toLowerCase();
      if (words.some((w) => text.includes(w))) hits.add(i);
    });
    hitsEl.innerHTML = words.length ? `read-only search · <b>${hits.size}</b> match${hits.size === 1 ? "" : "es"}` : "read-only search";
  };
  q.addEventListener("input", search);
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && hits.size) { sel = [...hits][0]; drawPanels(); const n = g.nodes[sel]; px = -n.x * zoom; py = -n.y * zoom; } });
  view.querySelector(".tv-kg-tools").addEventListener("click", (e) => {
    const k = e.target.closest("button")?.dataset.k;
    if (k === "in") setZoom(zoom * 1.2);
    else if (k === "out") setZoom(zoom / 1.2);
    else if (k === "fit") fitAll();
    else if (k === "focus") { const n = g.nodes[sel]; px = -n.x * zoom; py = -n.y * zoom; }
    else if (k === "png") cv.toBlob((b) => b && download("skyguard-knowledge-graph.png", b));
    else if (k === "json") {
      const data = { source: "SkyGuard QA agent registry", exported_at: new Date().toISOString(),
        nodes: g.nodes.map(({ id, type, label, agent }) => ({ id, type, label, ...(agent ? { agent_id: agent } : {}) })),
        links: g.edges.map(([a, b, k]) => ({ from: a, to: b, relation: KG_EDGE[k] })) };
      download("skyguard-knowledge-graph.json", new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    }
  });
  const col = Object.fromEntries(KG_TYPES.map(([k, , c]) => [k, c]));
  const popSel = view.querySelector(".tv-kg-sel");
  loop((ms) => {
    if (stage.offsetParent === null) return;
    const t = motion() ? ms / 1000 : 0;
    ctx.clearRect(0, 0, W, H);
    const bg = ctx.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, W * 0.6); bg.addColorStop(0, "rgba(20,60,110,.35)"); bg.addColorStop(1, "rgba(2,8,18,0)"); ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
    const nbSet = new Set((g.adj.get(sel) || []).map(([b]) => b));
    ctx.lineWidth = 0.8;
    for (const [a, b, k] of g.edges) {
      const A = g.nodes[a], B = g.nodes[b]; if (!visible(A) || !visible(B)) continue;
      const on = a === sel || b === sel;
      ctx.strokeStyle = on ? "rgba(220,250,255,.9)" : k === "depends" ? "rgba(143,248,255,.35)" : "rgba(56,214,255,.14)";
      ctx.lineWidth = on ? 1.3 : 0.8; ctx.setLineDash(k === "depends" ? [4, 4] : []);
      ctx.beginPath(); ctx.moveTo(X(A), Y(A)); ctx.lineTo(X(B), Y(B)); ctx.stroke();
      if (on) { const f = (t * 0.6 + b * 0.13) % 1; ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(X(A) + (X(B) - X(A)) * f, Y(A) + (Y(B) - Y(A)) * f, 1.8, 0, 7); ctx.fill(); }
    }
    ctx.setLineDash([]);
    ctx.font = "10px Consolas, monospace";
    for (const n of g.nodes) {
      if (!visible(n)) continue;
      const x = X(n), y = Y(n); if (x < -40 || y < -20 || x > W + 40 || y > H + 20) continue;
      const a = n.agent ? state.agents.get(n.agent) : null, st = a ? stOf(a) : null;
      const c = st === "FAILED" ? "#ff4b5c" : st === "COMPLETED" ? "#2fe08a" : col[n.type];
      const hot = live(st), dim = hits.size && !hits.has(n.id);
      const r = (n.type === "agent" ? 4 : n.type === "category" || n.type === "engine" ? 3.4 : 2.4) * Math.min(1.6, Math.max(0.8, zoom));
      ctx.globalAlpha = dim ? 0.2 : 1; ctx.fillStyle = c;
      if (hot || hits.has(n.id)) { ctx.shadowColor = c; ctx.shadowBlur = 14; }
      ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill(); ctx.shadowBlur = 0;
      if (hot) { const p = (t % 1.4) / 1.4; ctx.strokeStyle = `rgba(143,248,255,${1 - p})`; ctx.beginPath(); ctx.arc(x, y, r + 3 + p * 14, 0, 7); ctx.stroke(); }
      if (n.type === "agent" || n.type === "category" || n.type === "engine" || zoom > 1.3 || nbSet.has(n.id) || hits.has(n.id)) {
        ctx.fillStyle = n.type === "agent" ? "rgba(220,240,255,.85)" : "rgba(150,195,235,.7)"; ctx.fillText(n.label, x + 6, y - 5);
      }
      ctx.globalAlpha = 1;
    }
    const S = g.nodes[sel];
    if (S && visible(S)) {
      const sx = X(S), sy = Y(S);
      ctx.strokeStyle = "rgba(200,240,255,.35)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(W, sy); ctx.moveTo(sx, 0); ctx.lineTo(sx, H); ctx.stroke();
      ctx.strokeStyle = "rgba(143,248,255,.3)"; ctx.beginPath(); ctx.arc(sx, sy, 26 + Math.sin(t * 2) * 3, 0, 7); ctx.stroke();
      ctx.shadowColor = "#8ff8ff"; ctx.shadowBlur = 24; ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(sx, sy, 5, 0, 7); ctx.fill(); ctx.shadowBlur = 0;
      popSel.hidden = false; popSel.style.left = `${Math.min(W - 160, sx + 20)}px`; popSel.style.top = `${Math.max(4, sy - 34)}px`;
      popSel.querySelector("b").textContent = `${S.label.toUpperCase()} / ${S.type.toUpperCase()}`;
    } else popSel.hidden = true;
  }, offs);
  return drawPanels;
}

/* ===================== 03 WORKFLOW MAP (view only) ===================== */
function wbLayout() {
  const agents = [...state.agents.values()];
  const byId = new Map(agents.map((a) => [a.id, a]));
  const linked = new Set();
  for (const a of agents) for (const d of a.depends || []) if (byId.has(d)) { linked.add(a.id); linked.add(d); }
  const depth = new Map();
  const dep = (id, seen = new Set()) => {
    if (depth.has(id)) return depth.get(id);
    if (seen.has(id)) return 0;
    seen.add(id);
    const ds = (byId.get(id).depends || []).filter((d) => byId.has(d));
    const v = ds.length ? 1 + Math.max(...ds.map((d) => dep(d, seen))) : 0;
    depth.set(id, v); return v;
  };
  const cols = [];
  for (const id of linked) { const d = dep(id); (cols[d] = cols[d] || []).push(byId.get(id)); }
  const pos = new Map();
  cols.forEach((list, ci) => {
    list.sort((a, b) => (a.category || "").localeCompare(b.category || "") || a.name.localeCompare(b.name));
    list.forEach((a, ri) => pos.set(a.id, [24 + ci * 200, 24 + ri * 74]));
  });
  const edges = [];
  for (const a of agents) for (const d of a.depends || []) if (pos.has(d) && pos.has(a.id)) edges.push([d, a.id]);
  const solo = agents.filter((a) => !linked.has(a.id));
  return { pos, edges, solo, rows: Math.max(1, ...cols.map((c) => c?.length || 0)), ncol: cols.length };
}

function wbHtml(L) {
  const node = (a) => {
    const s = stOf(a), [x, y] = L.pos.get(a.id);
    return `<div class="tv-node agent ${live(s) ? "run" : s === "FAILED" ? "fail" : s === "UNAVAILABLE" ? "unav" : ""}" data-id="${esc(a.id)}" style="left:${x}px;top:${y}px;width:160px"><b>${esc(short(a.name))}</b><small data-f="st">${esc(a.category || "")} · ${esc(s)}</small><i class="in"></i><i class="out"></i></div>`;
  };
  return `<section class="tv-wb">
    <div class="tv-wb-bar"><b>WORKFLOW MAP</b><span class="tv-wb-name">How the agents depend on each other <em>view only</em></span><span class="tv-sp"></span></div>
    <div class="tv-view-only"><b>View only.</b> Each arrow is a dependency recorded for an agent in the agent registry; a glowing step is running now. Building and running your own workflows is not part of SkyGuard QA yet, so there is nothing to save or run here. You can drag the steps to look at them.</div>
    <div class="tv-wb-body">
      <aside class="tv-wb-pal"><h5>Agents without a recorded dependency</h5>${L.solo.map((a) => `<div class="tv-pal agent" data-id="${esc(a.id)}"><i></i>${esc(short(a.name))}</div>`).join("") || '<div class="tv-empty">none</div>'}</aside>
      <div class="tv-wb-canvas" id="tvWb"><div style="position:relative;width:${Math.max(600, L.ncol * 200 + 40)}px;height:${L.rows * 74 + 40}px"><svg class="tv-wb-svg" id="tvWbSvg"></svg>
        ${[...L.pos.keys()].map((id) => node(state.agents.get(id))).join("")}</div>
      </div>
      <aside class="tv-wb-props" id="tvWbProps"><h5>Step</h5><p class="tv-empty">Click a step to see its details.</p></aside>
    </div>
  </section>`;
}

function wbProps(a) {
  if (!a) return '<h5>Step</h5><p class="tv-empty">Click a step to see its details.</p>';
  const s = stOf(a);
  const needed = [...state.agents.values()].filter((b) => (b.depends || []).includes(a.id)).map((b) => short(b.name));
  return `<h5>Step · ${esc(short(a.name))}</h5><dl>
    <dt>State</dt><dd>${esc(s)}</dd><dt>Category</dt><dd>${esc(a.category || "—")}</dd><dt>Role</dt><dd>${esc(a.role || "—")}</dd>
    <dt>Inputs</dt><dd>${esc((a.inputs || []).join(", ") || "—")}</dd><dt>Outputs</dt><dd>${esc((a.outputs || []).join(", ") || "—")}</dd>
    <dt>Depends on</dt><dd>${esc((a.depends || []).map((d) => short(state.agents.get(d)?.name || d)).join(", ") || "—")}</dd>
    <dt>Needed by</dt><dd>${esc(needed.join(", ") || "—")}</dd><dt>Run</dt><dd>${esc(a.run_id || "—")}</dd></dl>`;
}

function mountWb(view, offs) {
  const box = view.querySelector("#tvWb"), svg = view.querySelector("#tvWbSvg"), props = view.querySelector("#tvWbProps");
  const inner = svg.parentElement;
  const L = mountWb.L;
  const draw = () => {
    const r = inner.getBoundingClientRect();
    const pt = (id, side) => { const el = inner.querySelector(`[data-id="${CSS.escape(id)}"]`), b = el.getBoundingClientRect(); return [side ? b.right - r.left : b.left - r.left, b.top - r.top + b.height / 2]; };
    svg.innerHTML = L.edges.map(([a, b]) => {
      const [x1, y1] = pt(a, 1), [x2, y2] = pt(b, 0), dx = Math.max(40, (x2 - x1) / 2);
      const d = x2 < x1 - 20 ? `M${x1} ${y1}H${x1 + 18}V${(y1 + y2) / 2 + 30}H${x2 - 18}V${y2}H${x2}` : `M${x1} ${y1}C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`;
      const hot = live(stOf(state.agents.get(a))) || live(stOf(state.agents.get(b)));
      return `<path d="${d}" class="e ${hot ? "hot" : ""}"/>${hot ? `<path d="${d}" class="flow"/>` : ""}`;
    }).join("");
  };
  draw();
  let d = null;
  box.addEventListener("pointerdown", (e) => {
    const n = e.target.closest(".tv-node, .tv-pal");
    if (!n) return;
    props.innerHTML = wbProps(state.agents.get(n.dataset.id));
    if (!n.classList.contains("tv-node")) return;
    d = [n, e.clientX, e.clientY, n.offsetLeft, n.offsetTop]; n.setPointerCapture(e.pointerId);
  });
  view.querySelector(".tv-wb-pal").addEventListener("click", (e) => { const n = e.target.closest(".tv-pal"); if (n) props.innerHTML = wbProps(state.agents.get(n.dataset.id)); });
  box.addEventListener("pointermove", (e) => { if (!d) return; d[0].style.left = `${Math.max(0, d[3] + e.clientX - d[1])}px`; d[0].style.top = `${Math.max(0, d[4] + e.clientY - d[2])}px`; draw(); });
  box.addEventListener("pointerup", () => { d = null; });
  return () => { // live states on the steps
    for (const el of inner.querySelectorAll(".tv-node")) {
      const a = state.agents.get(el.dataset.id); if (!a) continue;
      const s = stOf(a);
      el.classList.toggle("run", live(s)); el.classList.toggle("fail", s === "FAILED"); el.classList.toggle("unav", s === "UNAVAILABLE");
      el.querySelector('[data-f="st"]').textContent = `${a.category || ""} · ${s}`;
    }
    draw();
  };
}

/* ===================== page ===================== */
export async function mount(view) {
  setCardDeco(true);
  const g = buildKg();
  const L = wbLayout();
  mountWb.L = L;
  view.innerHTML = `<div class="wf-defense cc look-core look-mission">
    ${backdropHtml()}
    ${tabsBarHtml()}
    <div class="tv-pane" data-pane="kg" hidden>${kgHtml(g)}</div>
    <div class="tv-pane" data-pane="wb" hidden>${wbHtml(L)}</div>
    <div class="tv-pane" data-pane="net">${netHtml()}${heroHtml()}${bodyHtml()}</div>
  </div>`;
  const root = view.querySelector(".wf-defense");
  const offs = [];
  let motionOn = !reducedMotion();
  const motion = () => motionOn;
  const refreshers = { kg: null, wb: null };
  mountNet(view, offs, motion);
  const mounted = new Set(["net"]);
  const show = (tab) => {
    view.querySelectorAll(".tv-tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    view.querySelectorAll(".tv-pane").forEach((p) => { p.hidden = p.dataset.pane !== tab; });
    if (!mounted.has(tab)) {
      mounted.add(tab);
      if (tab === "kg") refreshers.kg = mountKg(view, offs, motion, g);
      else refreshers.wb = mountWb(view, offs);
    }
  };
  view.querySelectorAll(".tv-tab").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  const motionBox = view.querySelector(".tv-motion input");
  root.classList.toggle("reduced", !motionOn);
  motionBox.addEventListener("change", () => { motionOn = motionBox.checked; root.classList.toggle("reduced", !motionOn); });
  const drawNetText = () => {
    view.querySelector("#tvKpis").innerHTML = netKpis();
    view.querySelector("#tvHq").innerHTML = hqRows();
    view.querySelector("#tvFocus").innerHTML = focusHtml();
    view.querySelector("#tvCallout").innerHTML = calloutHtml();
  };
  const stopCore = startCore(view, {
    onAgentEvent: () => { drawNetText(); if (refreshers.wb) refreshers.wb(); },
    onTickExtra: (tick) => { if (tick % 5 === 0) { drawNetText(); if (refreshers.kg && !view.querySelector('[data-pane="kg"]').hidden) refreshers.kg(); } },
  });
  const unsub = subscribe((c) => { if (c.type === "snapshot") drawNetText(); });
  cleanup = () => { stopCore(); unsub(); offs.forEach((f) => f()); };
}

export function unmount() {
  if (cleanup) cleanup();
  cleanup = null;
}
