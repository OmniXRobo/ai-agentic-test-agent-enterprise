// Agent Workflow, Holographic Command look (Preferences > Agent Workflow look). Same information as Classic: hero, the
// AIRBORNE / ON STANDBY / DOWN counts, the explanation line, Run system diagnostics, one card per runtime agent with
// Stop / Pause / Resume, and Live Agent Activity. Only the presentation changes. Inline SVG, canvas and CSS
// (css/wf-looks/holographic.css): no images, fonts or scripts from the network. Motion stops under "reduce motion".

import { state } from "../../store.js";
import { setCardDeco } from "../../agent-card.js";
import { counts, feedHtml, gridHtml, wire, EXPLAIN } from "./common.js";
import { esc } from "../../util.js";

export const css = "holographic.css";
let cleanup = null;
const JET = "M50 2 54 30 96 56v8L54 52l-2 26 13 9v5l-15-4-15 4v-5l13-9-2-26L4 64v-8l42-26z";

function rng(seed) { return () => ((seed = (seed * 9301 + 49297) % 233280) / 233280); }

// Full-page backdrop: star field, aurora, hex lattice, perspective grid floor, warp streaks. Decorative only.
function backdropHtml() {
  const rnd = rng(7);
  let stars = "";
  for (let i = 0; i < 180; i++) {
    const r = rnd() < 0.1 ? 1.5 : 0.7;
    stars += `<circle cx="${(rnd() * 1600).toFixed(0)}" cy="${(rnd() * 900).toFixed(0)}" r="${r}" class="cc-star${i % 6 === 0 ? " tw" : ""}" style="animation-delay:${(rnd() * 4).toFixed(2)}s"/>`;
  }
  const streaks = Array.from({ length: 7 }, (_, i) => `<i style="--i:${i};top:${(8 + rnd() * 80).toFixed(0)}%"></i>`).join("");
  return `<div class="cc-backdrop" aria-hidden="true"><div class="cc-stick">
    <svg class="cc-stars" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice">${stars}</svg>
    <div class="cc-aurora"></div>
    <svg class="cc-hexes" width="100%" height="100%"><defs><pattern id="ccHex" width="56" height="97" patternUnits="userSpaceOnUse" patternTransform="scale(.7)">
      <path d="M28 0 56 16v32L28 64 0 48V16zM28 64v33M0 97V80M56 97V80" /></pattern></defs><rect width="100%" height="100%" fill="url(#ccHex)"/></svg>
    <div class="cc-warp">${streaks}</div>
    <div class="cc-floor"><div class="cc-floor-grid"></div></div>
    <div class="cc-scan"></div>
  </div></div>`;
}

// Neural network mesh behind the core: nodes with pulses travelling along the links.
function neuralMeshSvg() {
  const rnd = rng(31);
  const nodes = Array.from({ length: 34 }, () => [40 + rnd() * 1120, 20 + rnd() * 300]);
  let links = "", pulses = "", dots = "";
  nodes.forEach(([x, y], i) => {
    const near = nodes.map((n, j) => [j, Math.hypot(n[0] - x, n[1] - y)]).filter(([j]) => j > i).sort((a, b) => a[1] - b[1]).slice(0, 2);
    for (const [j, d] of near) {
      if (d > 260) continue;
      const [x2, y2] = nodes[j];
      links += `<path d="M${x.toFixed(0)} ${y.toFixed(0)}L${x2.toFixed(0)} ${y2.toFixed(0)}"/>`;
      if ((i + j) % 3 === 0) pulses += `<path d="M${x.toFixed(0)} ${y.toFixed(0)}L${x2.toFixed(0)} ${y2.toFixed(0)}" pathLength="100" style="animation-delay:${(-rnd() * 3).toFixed(2)}s"/>`;
    }
    dots += `<circle cx="${x.toFixed(0)}" cy="${y.toFixed(0)}" r="${i % 5 === 0 ? 3 : 1.8}" style="animation-delay:${(-rnd() * 3).toFixed(2)}s"/>`;
  });
  return `<svg class="cc-mesh" viewBox="0 0 1200 340" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
    <g class="cc-mesh-links">${links}</g><g class="cc-mesh-pulses">${pulses}</g><g class="cc-mesh-nodes">${dots}</g></svg>`;
}

// Segmented HUD arcs and tick rings around the android (rotating in opposite directions).
function hudArcsSvg() {
  const ticks = Array.from({ length: 72 }, (_, i) => {
    const a = (i * 5 * Math.PI) / 180, r1 = 168, r2 = i % 6 === 0 ? 156 : 162;
    return `<path d="M${(180 + r1 * Math.cos(a)).toFixed(1)} ${(180 + r1 * Math.sin(a)).toFixed(1)}L${(180 + r2 * Math.cos(a)).toFixed(1)} ${(180 + r2 * Math.sin(a)).toFixed(1)}"/>`;
  }).join("");
  return `<svg class="cc-hudarcs" viewBox="0 0 360 360" aria-hidden="true">
    <defs><path id="ccArcTxt" d="M180 180m-140 0a140 140 0 1 1 280 0a140 140 0 1 1 -280 0"/></defs>
    <g class="a-ticks">${ticks}</g>
    <circle cx="180" cy="180" r="150" class="a-seg1"/>
    <circle cx="180" cy="180" r="140" class="a-seg2"/>
    <circle cx="180" cy="180" r="128" class="a-seg3"/>
    <g class="a-text"><text><textPath href="#ccArcTxt">SKYGUARD AI CORE · AGENT SQUADRON · SKYGUARD AI CORE · AGENT SQUADRON ·</textPath></text></g>
  </svg>`;
}

// The advanced android: layered armour, translucent cranium with a glowing neural brain, visor eyes and chest core.
function androidSvg() {
  const rnd = rng(11);
  let brain = "", synapses = "";
  for (let i = 0; i < 16; i++) {
    const x1 = 140 + rnd() * 120, y1 = 60 + rnd() * 70, x2 = x1 + (rnd() - 0.5) * 70, y2 = y1 + (rnd() - 0.5) * 40;
    brain += `<path d="M${x1.toFixed(0)} ${y1.toFixed(0)}Q${((x1 + x2) / 2 + 12).toFixed(0)} ${((y1 + y2) / 2 - 14).toFixed(0)} ${x2.toFixed(0)} ${y2.toFixed(0)}"/>`;
    synapses += `<circle cx="${x1.toFixed(0)}" cy="${y1.toFixed(0)}" r="2.6" style="animation-delay:${(-rnd() * 2).toFixed(2)}s"/>`;
  }
  return `<svg class="cc-android" viewBox="0 0 400 440" aria-hidden="true">
    <defs>
      <linearGradient id="ccMetal" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f4f9ff"/><stop offset=".3" stop-color="#b9cbe4"/><stop offset=".65" stop-color="#536b92"/><stop offset="1" stop-color="#18253d"/></linearGradient>
      <linearGradient id="ccMetalR" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8fb0d8"/><stop offset=".5" stop-color="#3d5478"/><stop offset="1" stop-color="#101b30"/></linearGradient>
      <linearGradient id="ccDark" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2c4166"/><stop offset="1" stop-color="#07101f"/></linearGradient>
      <linearGradient id="ccFace" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#0e1a2f"/><stop offset=".5" stop-color="#253c63"/><stop offset="1" stop-color="#0a1426"/></linearGradient>
      <linearGradient id="ccDome" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9ff6ff" stop-opacity=".45"/><stop offset="1" stop-color="#18c8ff" stop-opacity=".08"/></linearGradient>
      <linearGradient id="ccVisor" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#001019"/><stop offset=".5" stop-color="#003e58"/><stop offset="1" stop-color="#001019"/></linearGradient>
      <linearGradient id="ccRim" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#38d6ff" stop-opacity=".9"/><stop offset=".25" stop-color="#38d6ff" stop-opacity="0"/><stop offset=".75" stop-color="#8ff8ff" stop-opacity="0"/><stop offset="1" stop-color="#8ff8ff" stop-opacity=".7"/></linearGradient>
      <radialGradient id="ccCore" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff"/><stop offset=".25" stop-color="#a6f8ff"/><stop offset=".6" stop-color="#18c8ff"/><stop offset="1" stop-color="#18c8ff" stop-opacity="0"/></radialGradient>
      <radialGradient id="ccSheen" cx=".3" cy=".2" r=".6"><stop offset="0" stop-color="#fff" stop-opacity=".6"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
      <filter id="ccGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3.5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      <clipPath id="ccDomeClip"><path d="M118 140c0-62 38-100 82-100s82 38 82 100z"/></clipPath>
    </defs>
    <!-- shoulders / torso armour -->
    <path d="M10 440c4-78 34-118 100-132l46-12h88l46 12c66 14 96 54 100 132z" fill="url(#ccDark)"/>
    <path d="M10 440c4-78 34-118 100-132l30-8-16 56-70 84z" fill="url(#ccMetal)"/>
    <path d="M390 440c-4-78-34-118-100-132l-30-8 16 56 70 84z" fill="url(#ccMetalR)"/>
    <path d="M48 440c8-40 22-64 52-80M352 440c-8-40-22-64-52-80" stroke="#0a1426" stroke-width="5" fill="none"/>
    <path d="M60 372l52 10M340 372l-52 10M44 404l66 8M356 404l-66 8" stroke="#38d6ff" stroke-width="2.2" class="cc-trace" filter="url(#ccGlow)"/>
    <path d="M130 318 166 306h68l36 12-12 44H142z" fill="url(#ccFace)" stroke="#3aa6e6" stroke-opacity=".6"/>
    <path d="M150 440l20-70h60l20 70z" fill="#0b1527" stroke="#2f7fc0" stroke-opacity=".5"/>
    <!-- chest core -->
    <g class="cc-core">
      <circle cx="200" cy="392" r="50" fill="url(#ccCore)" class="cc-core-glow"/>
      <circle cx="200" cy="392" r="32" fill="#04101f" stroke="#7fd8ff" stroke-width="3"/>
      <circle cx="200" cy="392" r="24" fill="none" stroke="#bff8ff" stroke-width="2.5" stroke-dasharray="5 4" class="cc-core-ring"/>
      <path d="M200 374l16 28h-32z" fill="none" stroke="#9ff6ff" stroke-width="2" class="cc-core-ring2"/>
      <circle cx="200" cy="392" r="8" fill="#fff" filter="url(#ccGlow)"/>
    </g>
    <!-- neck with cables -->
    <rect x="164" y="262" width="72" height="56" rx="10" fill="url(#ccDark)"/>
    <path d="M166 274h68M166 287h68M166 300h68" stroke="#6e8bb6" stroke-width="3" opacity=".75"/>
    <path d="M176 262c-8 20-8 40 0 56M224 262c8 20 8 40 0 56" stroke="#38d6ff" stroke-width="2" fill="none" class="cc-trace" filter="url(#ccGlow)"/>
    <!-- ear modules -->
    <g class="cc-ears">
      <path d="M78 140h26l8 30-8 50H78l-10-40z" fill="url(#ccDark)" stroke="#4f6c99" stroke-width="2"/>
      <path d="M322 140h-26l-8 30 8 50h26l10-40z" fill="url(#ccDark)" stroke="#4f6c99" stroke-width="2"/>
      <path d="M84 156v52M316 156v52" stroke="#38d6ff" stroke-width="5" stroke-linecap="round" filter="url(#ccGlow)" class="cc-ear"/>
      <path d="M90 138 74 68M310 138l16-70" stroke="#8aa4ca" stroke-width="4" stroke-linecap="round"/>
      <circle cx="73" cy="64" r="6" class="cc-beacon"/><circle cx="327" cy="64" r="6" class="cc-beacon b2"/>
    </g>
    <!-- head shell -->
    <path d="M200 38c64 0 106 42 106 106v54c0 48-30 82-68 92l-38 8-38-8c-38-10-68-44-68-92v-54C94 80 136 38 200 38z" fill="url(#ccMetal)"/>
    <path d="M200 38c64 0 106 42 106 106v54c0 48-30 82-68 92l-38 8-38-8c-38-10-68-44-68-92v-54C94 80 136 38 200 38z" fill="url(#ccSheen)"/>
    <path d="M200 38c64 0 106 42 106 106v54c0 48-30 82-68 92l-38 8-38-8c-38-10-68-44-68-92v-54C94 80 136 38 200 38z" fill="none" stroke="url(#ccRim)" stroke-width="3"/>
    <!-- translucent cranium with neural brain -->
    <path d="M118 140c0-62 38-100 82-100s82 38 82 100z" fill="#031426" opacity=".85"/>
    <g clip-path="url(#ccDomeClip)">
      <g class="cc-brain" filter="url(#ccGlow)">${brain}</g>
      <g class="cc-synapse">${synapses}</g>
      <rect x="110" y="40" width="180" height="16" class="cc-dome-scan"/>
    </g>
    <path d="M118 140c0-62 38-100 82-100s82 38 82 100z" fill="url(#ccDome)" stroke="#7fe9ff" stroke-width="2" stroke-opacity=".8"/>
    <path d="M140 84c14-24 34-36 60-38" stroke="#fff" stroke-opacity=".55" stroke-width="3" fill="none" stroke-linecap="round"/>
    <!-- face plate -->
    <path d="M122 198c0 42 28 72 60 80l18 4 18-4c32-8 60-38 60-80v-12H122z" fill="url(#ccFace)"/>
    <path d="M136 216l26 6 8 18M264 216l-26 6-8 18M154 250h92" stroke="#38d6ff" stroke-width="1.8" opacity=".75" class="cc-trace"/>
    <path d="M140 232h10v20h-10zM250 232h10v20h-10z" fill="#08111f" stroke="#3aa6e6" stroke-opacity=".6"/>
    <path d="M176 262h48l-6 14h-36z" fill="#060d19" stroke="#3aa6e6" stroke-opacity=".7"/>
    <g class="cc-mouth"><path d="M184 269h4M192 269h4M200 269h4M208 269h4" stroke="#7ff6ff" stroke-width="3"/></g>
    <!-- visor -->
    <path d="M108 146c28-14 60-20 92-20s64 6 92 20l-6 50c-26 10-56 14-86 14s-60-4-86-14z" fill="url(#ccVisor)" stroke="#5fd0ff" stroke-width="2.5"/>
    <rect x="124" y="160" width="152" height="5" rx="2.5" fill="#38d6ff" opacity=".35" class="cc-visor-scan"/>
    <g filter="url(#ccGlow)" class="cc-eyes">
      <path d="M134 172l52 4-4 13-46-4z" fill="#8ff8ff"/>
      <path d="M266 172l-52 4 4 13 46-4z" fill="#8ff8ff"/>
      <circle cx="164" cy="180" r="5.5" fill="#fff"/><circle cx="236" cy="180" r="5.5" fill="#fff"/>
    </g>
    <path d="M118 152c24-10 52-15 82-15" stroke="#fff" stroke-opacity=".4" stroke-width="2.5" fill="none" stroke-linecap="round"/>
  </svg>`;
}

function radarHtml() {
  const rings = [22, 44, 66, 88].map((r) => `<circle cx="100" cy="100" r="${r}"/>`).join("");
  return `<div class="cc-radar" aria-hidden="true">
    <svg viewBox="0 0 200 200"><g class="cc-radar-rings">${rings}<path d="M100 8v184M8 100h184"/></g>
      <circle cx="132" cy="62" r="3.2" class="cc-blip"/><circle cx="70" cy="138" r="3.2" class="cc-blip b2"/><circle cx="150" cy="128" r="3.2" class="cc-blip b3"/>
      <g transform="translate(46 54) rotate(40) scale(.16)"><path d="${JET}" class="cc-radar-jet"/></g>
    </svg>
    <div class="cc-radar-sweep"></div>
  </div>`;
}

function heroHtml() {
  return `<div class="cc-hero" id="ccHero">
    ${neuralMeshSvg()}
    <div class="cc-hero-frame" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
    <div class="cc-hero-copy" data-depth="10">
      <div class="cc-kicker"><span class="cc-kicker-lamp"></span>&#9650; Air Defense Operations</div>
      <div class="cc-title" data-text="Agent Squadron">Agent Squadron</div>
      <div class="cc-sub"><span class="cc-type">Defense grid online · every card reports real work</span></div>
      <div class="cc-strip-host" id="wfStrip">${statusStripHtml()}</div>
    </div>
    <div class="cc-core-bay" aria-hidden="true">
      <div class="cc-layer" data-depth="-14">${hudArcsSvg()}</div>
      <div class="cc-beam"></div>
      <div class="cc-orbit o1"><span></span></div>
      <div class="cc-orbit o2"><span></span></div>
      <div class="cc-orbit o3"><span></span></div>
      <div class="cc-layer" data-depth="22">${androidSvg()}</div>
      <div class="cc-pedestal"><i></i><i></i><i></i><i></i></div>
      <div class="cc-particles">${Array.from({ length: 18 }, (_, i) => `<b style="--i:${i}"></b>`).join("")}</div>
    </div>
    <div class="cc-layer cc-radar-wrap" data-depth="-8">${radarHtml()}</div>
  </div>`;
}

// Each count sits in a ring whose arc is its share of the squadron (derived only from the real counts).
function gauge(cls, label, n, total, lampCls) {
  const c = 2 * Math.PI * 30, frac = total ? n / total : 0;
  return `<span class="cc-gauge ${cls} ${n ? "on" : ""}">
    <svg viewBox="0 0 72 72" aria-hidden="true"><circle cx="36" cy="36" r="30" class="g-bg"/><circle cx="36" cy="36" r="30" class="g-arc" stroke-dasharray="${(c * frac).toFixed(1)} ${c.toFixed(1)}"/><circle cx="36" cy="36" r="24" class="g-in"/></svg>
    <b>${n}</b><em><span class="lamp ${lampCls}"></span>${label}</em></span>`;
}

function statusStripHtml() {
  const n = counts();
  const counts_ = { RUNNING: n.airborne, READY: n.standby, FAILED: n.down };
  const total = counts_.RUNNING + counts_.READY + counts_.FAILED;
  return `<div class="cc-strip">
    ${gauge("run", "Airborne", counts_.RUNNING, total, counts_.RUNNING ? "RUNNING" : "")}
    ${gauge("ready", "On standby", counts_.READY, total, "READY")}
    ${gauge("bad", "Down", counts_.FAILED, total, counts_.FAILED ? "FAILED" : "")}
  </div>`;
}

function stagger(grid) { [...grid.children].forEach((el, i) => el.style.setProperty("--n", i)); }

// 3D depth: the hero layers follow the pointer, and a card tilts toward it. Skipped under "reduce motion".
function bindDepth(view) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return () => {};
  const hero = view.querySelector("#ccHero");
  const grid = view.querySelector("#agentsGrid");
  const onHero = (e) => {
    const r = hero.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    for (const l of hero.querySelectorAll("[data-depth]")) {
      const d = Number(l.dataset.depth);
      l.style.transform = `translate3d(${(x * d).toFixed(1)}px, ${(y * d).toFixed(1)}px, 0)`;
    }
  };
  const onCard = (e) => {
    const card = e.target.closest(".agent-card");
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty("--ry", `${(((e.clientX - r.left) / r.width - 0.5) * 14).toFixed(1)}deg`);
    card.style.setProperty("--rx", `${((0.5 - (e.clientY - r.top) / r.height) * 12).toFixed(1)}deg`);
    card.style.setProperty("--mx", `${(((e.clientX - r.left) / r.width) * 100).toFixed(0)}%`);
    card.style.setProperty("--my", `${(((e.clientY - r.top) / r.height) * 100).toFixed(0)}%`);
  };
  const offCard = (e) => { const card = e.target.closest?.(".agent-card"); if (card && !card.contains(e.relatedTarget)) { card.style.removeProperty("--rx"); card.style.removeProperty("--ry"); } };
  hero.addEventListener("pointermove", onHero);
  grid.addEventListener("pointermove", onCard);
  grid.addEventListener("pointerout", offCard);
  return () => { hero.removeEventListener("pointermove", onHero); grid.removeEventListener("pointermove", onCard); grid.removeEventListener("pointerout", offCard); };
}

export async function mount(view) {
  setCardDeco(true);
  view.innerHTML = `<div class="wf-defense cc look-holo">
    ${backdropHtml()}
    ${heroHtml()}
    <div class="cc-toolbar">
      <span class="cc-toolbar-icon" aria-hidden="true"></span>
      <p class="muted">${esc(EXPLAIN)}</p>
      <button class="btn primary cc-launch" id="runDiag"><span class="cc-launch-ring" aria-hidden="true"></span><span class="cc-launch-ico" aria-hidden="true">&#9654;</span>Run system diagnostics</button>
    </div>
    <div class="workflow">
      <section class="agents-grid" id="agentsGrid">${gridHtml()}</section>
      <section class="card cc-comms">
        <h2><span class="cc-comms-lamps" aria-hidden="true"><i></i><i></i><i></i></span>Live Agent Activity<span class="cc-eq" aria-hidden="true">${"<i></i>".repeat(12)}</span></h2>
        <div class="feed" id="feed">${feedHtml()}</div>
      </section>
    </div></div>`;
  const offs = [];
  offs.push(bindDepth(view));

  const stop = wire(view, { strip: statusStripHtml, onGrid: stagger });
  cleanup = () => { stop(); offs.forEach((f) => f && f()); };
}

export function unmount() {
  if (cleanup) cleanup();
  cleanup = null;
}
