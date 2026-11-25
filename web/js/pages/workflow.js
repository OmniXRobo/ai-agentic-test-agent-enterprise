// Agent Workflow (23.14.0): the page is drawn in the look chosen on Preferences > Agent Workflow look. Every look
// shows the same information (the agent cards with Stop / Pause / Resume, the status counts, Live Agent Activity and
// Run system diagnostics), driven only by runtime events; only the presentation changes. Each look is its own module
// (pages/wf-looks/) with its own stylesheet (css/wf-looks/), loaded only while that look is shown. All offline.

import { api } from "../api.js";

export const WORKFLOW_LOOKS = [
  ["classic", "Classic Squadron", "The original Air Force squadron page: fighter formation, radar and squadron cards."],
  ["command_center", "Command Center", "Deep-space command bay with the AI android, holographic globe, radar and glowing cards."],
  ["holographic", "Holographic Command", "3D holographic bay: neural mesh, HUD arcs, gauges and cards that tilt toward the pointer."],
  ["skyguard_core", "SkyGuard Core", "SkyGuard navy and cyan with the AI energy sphere, data bus and 3D cards."],
  ["mission_control", "Mission Control", "SkyGuard Core with three tabs: Live Agent Network, AI Knowledge Graph and Workflow Map."],
];
const MODULES = {
  classic: () => import("./wf-looks/classic.js"),
  command_center: () => import("./wf-looks/command-center.js"),
  holographic: () => import("./wf-looks/holographic.js"),
  skyguard_core: () => import("./wf-looks/skyguard-core.js"),
  mission_control: () => import("./wf-looks/mission-control.js"),
};
const KEY = "qa.workflowLook";

let current = null;
let link = null;
let token = 0;

/** The chosen look: Preferences (ui.workflow_look), remembered in this browser for a quick start. */
async function chosenLook() {
  let look = null;
  try { look = localStorage.getItem(KEY); } catch { /* ignore */ }
  try {
    const { settings } = await api("/api/settings");
    look = settings?.ui?.workflow_look || look;
    try { localStorage.setItem(KEY, look || "classic"); } catch { /* ignore */ }
  } catch { /* keep the remembered look */ }
  return MODULES[look] ? look : "classic";
}

function loadCss(file) {
  return new Promise((resolve) => {
    if (link) { link.remove(); link = null; }
    if (!file) { resolve(); return; }
    link = document.createElement("link");
    link.rel = "stylesheet";
    link.dataset.wfLook = file;
    link.href = new URL(`../../css/wf-looks/${file}`, import.meta.url).href; // same versioned folder as this script
    link.onload = link.onerror = () => resolve();
    document.head.appendChild(link);
    setTimeout(resolve, 1500); // never hold the page for a slow stylesheet
  });
}

export async function mount(view) {
  const mine = ++token;
  const look = await chosenLook();
  let mod;
  try {
    mod = await MODULES[look]();
  } catch (err) {
    console.error(`Agent Workflow look ${look} failed to load`, err);
    mod = await MODULES.classic();
  }
  if (mine !== token) return; // the person left the page meanwhile
  await loadCss(mod.css);
  if (mine !== token) return;
  current = mod;
  view.dataset.workflowLook = look;
  await mod.mount(view);
}

export function unmount() {
  token++;
  if (current?.unmount) current.unmount();
  current = null;
  if (link) { link.remove(); link = null; }
}
