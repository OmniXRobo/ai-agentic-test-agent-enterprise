// Honest placeholder for sections delivered by later build stages. Shows no data.

import { esc } from "../util.js";

const STAGES = {
  2: "TFS / Azure DevOps integration",
  3: "On-Premise AI and test design agents",
  4: "Playwright automation, recording and self-healing",
  5: "JMeter performance testing",
  6: "Defect Intelligence",
  7: "Voice Command Agent",
  8: "Reports and analytics",
};

export async function mount(view, entry) {
  view.innerHTML = `
    <div class="card empty">
      <h2>${esc(entry.title)}</h2>
      <p>This section is delivered in build stage ${entry.stage}: ${esc(STAGES[entry.stage] || "")}.</p>
      <p>It will show real data from your configured systems only. No sample or demo records are displayed.</p>
    </div>`;
}
