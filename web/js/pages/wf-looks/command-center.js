// Agent Workflow, Command Center look (Preferences > Agent Workflow look). Same information as Classic: hero, the
// AIRBORNE / ON STANDBY / DOWN counts, the explanation line, Run system diagnostics, one card per runtime agent with
// Stop / Pause / Resume, and Live Agent Activity. Only the presentation changes. Inline SVG, canvas and CSS
// (css/wf-looks/command-center.css): no images, fonts or scripts from the network. Motion stops under "reduce motion".

import { state } from "../../store.js";
import { setCardDeco } from "../../agent-card.js";
import { counts, feedHtml, gridHtml, wire, EXPLAIN } from "./common.js";
import { esc } from "../../util.js";

export const css = "command-center.css";
let cleanup = null;
const JET = "M50 2 54 30 96 56v8L54 52l-2 26 13 9v5l-15-4-15 4v-5l13-9-2-26L4 64v-8l42-26z";

// Full-page backdrop: star field, nebula glow, perspective grid floor, faint grid. Decorative only.
function backdropHtml() {
  let stars = "";
  let seed = 7;
  const rnd = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
  for (let i = 0; i < 140; i++) {
    const r = rnd() < 0.12 ? 1.4 : 0.7;
    stars += `<circle cx="${(rnd() * 1600).toFixed(0)}" cy="${(rnd() * 900).toFixed(0)}" r="${r}" class="cc-star${i % 7 === 0 ? " tw" : ""}" style="animation-delay:${(rnd() * 4).toFixed(2)}s"/>`;
  }
  return `<div class="cc-backdrop" aria-hidden="true"><div class="cc-stick">
    <svg class="cc-stars" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice">${stars}</svg>
    <div class="cc-nebula"></div>
    <div class="cc-floor"><div class="cc-floor-grid"></div></div>
    <div class="cc-scan"></div>
  </div></div>`;
}

// The advanced android at the heart of the command bay: metal shading, glowing visor and chest core.
function androidSvg() {
  return `<svg class="cc-android" viewBox="0 0 400 420" aria-hidden="true">
    <defs>
      <linearGradient id="ccMetal" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f2f8ff"/><stop offset=".35" stop-color="#a9bdd8"/><stop offset=".7" stop-color="#4b6286"/><stop offset="1" stop-color="#1c2a44"/></linearGradient>
      <linearGradient id="ccMetalDark" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#34496b"/><stop offset="1" stop-color="#0b1426"/></linearGradient>
      <linearGradient id="ccPlate" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#1a2b48"/><stop offset=".5" stop-color="#2d4670"/><stop offset="1" stop-color="#0e1a30"/></linearGradient>
      <linearGradient id="ccVisor" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#00151f"/><stop offset=".5" stop-color="#003a52"/><stop offset="1" stop-color="#00151f"/></linearGradient>
      <radialGradient id="ccCore" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ffffff"/><stop offset=".25" stop-color="#9ff6ff"/><stop offset=".6" stop-color="#18c8ff"/><stop offset="1" stop-color="#18c8ff" stop-opacity="0"/></radialGradient>
      <radialGradient id="ccSheen" cx=".3" cy=".25" r=".6"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
      <filter id="ccGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    </defs>
    <!-- shoulders and chest armour -->
    <path d="M24 420c4-70 30-104 92-118l40-10h88l40 10c62 14 88 48 92 118z" fill="url(#ccMetalDark)"/>
    <path d="M60 420c6-52 28-80 74-92l30-8h72l30 8c46 12 68 40 74 92z" fill="url(#ccPlate)"/>
    <path d="M24 420c4-70 30-104 92-118l18-4-10 40-62 82z" fill="url(#ccMetal)" opacity=".55"/>
    <path d="M376 420c-4-70-30-104-92-118l-18-4 10 40 62 82z" fill="url(#ccMetal)" opacity=".35"/>
    <path d="M134 330 164 320h72l30 10-14 34h-104z" fill="#0b1527" stroke="#2a8fd0" stroke-opacity=".5"/>
    <path d="M96 352l40 8M304 352l-40 8M84 384l52 6M316 384l-52 6" stroke="#38d6ff" stroke-width="2" opacity=".6" class="cc-trace"/>
    <!-- chest core -->
    <circle cx="200" cy="378" r="34" fill="#061224" stroke="#5aa8e0" stroke-width="3"/>
    <circle cx="200" cy="378" r="44" fill="url(#ccCore)" class="cc-core-glow"/>
    <circle cx="200" cy="378" r="22" fill="none" stroke="#bff8ff" stroke-width="2" stroke-dasharray="6 5" class="cc-core-ring"/>
    <circle cx="200" cy="378" r="9" fill="#fff" filter="url(#ccGlow)"/>
    <!-- neck -->
    <rect x="166" y="262" width="68" height="60" rx="10" fill="url(#ccMetalDark)"/>
    <path d="M168 276h64M168 290h64M168 304h64" stroke="#5f7aa3" stroke-width="3" opacity=".7"/>
    <path d="M182 262v60M218 262v60" stroke="#38d6ff" stroke-width="1.5" opacity=".55" class="cc-trace"/>
    <!-- ear pods -->
    <ellipse cx="96" cy="170" rx="22" ry="40" fill="url(#ccMetalDark)" stroke="#4d6a96" stroke-width="2"/>
    <ellipse cx="304" cy="170" rx="22" ry="40" fill="url(#ccMetalDark)" stroke="#4d6a96" stroke-width="2"/>
    <ellipse cx="96" cy="170" rx="9" ry="20" fill="#38d6ff" opacity=".8" filter="url(#ccGlow)" class="cc-ear"/>
    <ellipse cx="304" cy="170" rx="9" ry="20" fill="#38d6ff" opacity=".8" filter="url(#ccGlow)" class="cc-ear"/>
    <path d="M86 132 70 70M314 132l16-62" stroke="#7d97bf" stroke-width="4" stroke-linecap="round"/>
    <circle cx="70" cy="66" r="6" class="cc-beacon"/><circle cx="330" cy="66" r="6" class="cc-beacon b2"/>
    <!-- head -->
    <path d="M200 40c62 0 104 40 104 104v52c0 46-30 80-66 90l-38 8-38-8c-36-10-66-44-66-90v-52C96 80 138 40 200 40z" fill="url(#ccMetal)"/>
    <path d="M200 40c62 0 104 40 104 104v52c0 46-30 80-66 90l-38 8-38-8c-36-10-66-44-66-90v-52C96 80 138 40 200 40z" fill="url(#ccSheen)"/>
    <path d="M200 48v42M150 62l14 34M250 62l-14 34" stroke="#20314f" stroke-width="3" opacity=".55"/>
    <path d="M176 52h48l-6 30h-36z" fill="#0d1a2f"/><path d="M186 60h28" stroke="#38d6ff" stroke-width="3" class="cc-trace"/>
    <!-- face plate -->
    <path d="M124 196c0 40 26 70 58 78l18 4 18-4c32-8 58-38 58-78v-10H124z" fill="url(#ccPlate)"/>
    <path d="M150 230h30l10 14M250 230h-30l-10 14M160 252h80" stroke="#38d6ff" stroke-width="1.6" opacity=".7" class="cc-trace"/>
    <path d="M178 262h44l-6 12h-32z" fill="#08111f" stroke="#2a8fd0" stroke-opacity=".6"/>
    <path d="M186 268h28" stroke="#38d6ff" stroke-width="2" class="cc-mouth"/>
    <!-- visor -->
    <path d="M114 142c26-14 56-20 86-20s60 6 86 20l-6 50c-24 10-52 14-80 14s-56-4-80-14z" fill="url(#ccVisor)" stroke="#52c9ff" stroke-width="2.5"/>
    <path d="M124 150c22-10 48-15 76-15" stroke="#fff" stroke-opacity=".45" stroke-width="3" fill="none" stroke-linecap="round"/>
    <rect x="132" y="162" width="136" height="6" rx="3" fill="#38d6ff" opacity=".35" class="cc-visor-scan"/>
    <g filter="url(#ccGlow)" class="cc-eyes">
      <path d="M140 170l46 4-4 12-40-4z" fill="#7ff6ff"/>
      <path d="M260 170l-46 4 4 12 40-4z" fill="#7ff6ff"/>
      <circle cx="166" cy="178" r="5" fill="#fff"/><circle cx="234" cy="178" r="5" fill="#fff"/>
    </g>
  </svg>`;
}

// Holographic globe with meridians that turn (SMIL, works offline in Edge/Chrome).
function globeSvg() {
  const lats = [-60, -30, 0, 30, 60].map((d) => {
    const y = 120 + Math.sin((d * Math.PI) / 180) * 110, rx = Math.cos((d * Math.PI) / 180) * 110;
    return `<ellipse cx="120" cy="${y.toFixed(1)}" rx="${rx.toFixed(1)}" ry="${(rx * 0.16).toFixed(1)}"/>`;
  }).join("");
  const mer = [0, 1, 2, 3, 4, 5].map((i) => `<ellipse cx="120" cy="120" rx="110" ry="110">
      <animate attributeName="rx" values="110;0;110" dur="9s" begin="${(-i * 1.5).toFixed(1)}s" repeatCount="indefinite"/></ellipse>`).join("");
  return `<svg class="cc-globe" viewBox="0 0 240 240" aria-hidden="true"><circle cx="120" cy="120" r="110" class="cc-globe-edge"/><g class="cc-globe-lines">${lats}${mer}</g></svg>`;
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
  return `<div class="cc-hero">
    <div class="cc-hero-frame" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
    <div class="cc-hero-copy">
      <div class="cc-kicker"><span class="cc-kicker-lamp"></span>&#9650; Air Defense Operations</div>
      <div class="cc-title" data-text="Agent Squadron">Agent Squadron</div>
      <div class="cc-sub">Defense grid online · every card reports real work</div>
      <div class="cc-strip-host" id="wfStrip">${statusStripHtml()}</div>
    </div>
    <div class="cc-core-bay" aria-hidden="true">
      <div class="cc-beam"></div>
      ${globeSvg()}
      <div class="cc-orbit o1"><span></span></div>
      <div class="cc-orbit o2"><span></span></div>
      <div class="cc-orbit o3"><span></span></div>
      ${androidSvg()}
      <div class="cc-pedestal"><i></i><i></i><i></i></div>
      <div class="cc-particles">${Array.from({ length: 14 }, (_, i) => `<b style="--i:${i}"></b>`).join("")}</div>
    </div>
    ${radarHtml()}
  </div>`;
}

function statusStripHtml() {
  const n = counts();
  const counts_ = { RUNNING: n.airborne, READY: n.standby, FAILED: n.down };
  return `<div class="cc-strip">
    <span class="cc-count run ${counts_.RUNNING ? "on" : ""}"><span class="lamp ${counts_.RUNNING ? "RUNNING" : ""}"></span><em>Airborne</em><b>${counts_.RUNNING}</b></span>
    <span class="cc-count ready"><span class="lamp READY"></span><em>On standby</em><b>${counts_.READY}</b></span>
    <span class="cc-count bad ${counts_.FAILED ? "on" : ""}"><span class="lamp ${counts_.FAILED ? "FAILED" : ""}"></span><em>Down</em><b>${counts_.FAILED}</b></span>
  </div>`;
}

export async function mount(view) {
  setCardDeco(false);
  view.innerHTML = `<div class="wf-defense cc look-cmd">
    ${backdropHtml()}
    ${heroHtml()}
    <div class="cc-toolbar">
      <p class="muted">${esc(EXPLAIN)}</p>
      <button class="btn primary cc-launch" id="runDiag"><span class="cc-launch-ring" aria-hidden="true"></span>Run system diagnostics</button>
    </div>
    <div class="workflow">
      <section class="agents-grid" id="agentsGrid">${gridHtml()}</section>
      <section class="card cc-comms">
        <h2><span class="cc-comms-lamps" aria-hidden="true"><i></i><i></i><i></i></span>Live Agent Activity</h2>
        <div class="feed" id="feed">${feedHtml()}</div>
      </section>
    </div></div>`;
  const offs = [];

  const stop = wire(view, { strip: statusStripHtml });
  cleanup = () => { stop(); offs.forEach((f) => f && f()); };
}

export function unmount() {
  if (cleanup) cleanup();
  cleanup = null;
}
