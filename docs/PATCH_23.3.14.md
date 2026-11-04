# Patch 23.3.14: Dashboard statistics, Air Force squadron agents, Agent Workflow defense theme

Cumulative: includes 23.3.13 and everything before it. Front end plus one read-only analytics addition; no
database change, no setting change.

## Dashboard
* Removed: the Agent Squadron cards, the Recent Runs table and the top tile row (Agents Running, Notifications,
  Passed, Failed, Pass Rate, Defect Drafts). Pass/fail and defect drafts are now in the statistics below.
* Kept: the Azure DevOps tiles and the Performance tiles.
* New **QA progress** bars: user stories with acceptance criteria, test cases with test data, test cases
  automated, automated tests executed, executed tests passing, defects closed ("x of y" and %).
* New **Statistics** cards with pie (donut) and bar charts: Pass / Fail, Executions in the last 7 days,
  User Stories & Acceptance Criteria, Test Cases (automated vs not), Test Data (valid vs invalid rows, rows per
  test case), Defects (open vs closed in TFS, defect drafts by status), Agent runs by type, Agents right now.
  Performance SLA pie when SLA results exist.
* Every figure comes from TFS or recorded results; a figure that is not available shows "—".
  `/api/analytics/summary` has a new `insights` block (test data, defects total, executions, runs by type).

## AI Voice Command Center
* Only the Agent Squadron panel is removed. Mic and radar, Comms Log, Command Handoff, Radio Configuration and
  Orders You Can Give are unchanged.

## Agents (everywhere)
* New Air Force units: pilots (flight helmet, visor, oxygen mask) and combat drones, each with its own squadron
  colour, rank chevrons, wings and a shield badge with the role emblem. Inline SVG, works offline.

## Agent Workflow
* Card details unchanged (activity, Run, Started, Elapsed, Last result) plus **Remaining** (estimated from the
  agent's progress: elapsed / progress x what is left) and **Total time** (elapsed + remaining while it runs, the
  real duration after it finished).
* The running agent blinks: pulsing card frame, flashing beacon, flickering HUD, radar sweep, blinking RUNNING.
  A failed agent shows a steady red alert.
* Defense backdrop: radar grid and rings, a sweeping radar, fighter silhouettes; a status strip with Airborne /
  On standby / Down counts. Animations stop under Windows "reduce motion".

Files: `web/js/pages/dashboard.js`, `web/js/dash-charts.js` (new), `web/js/pages/workflow.js`,
`web/js/pages/command-center.js`, `web/js/agent-card.js`, `web/js/robots.js`, `web/css/defense.css` (new),
`web/index.html`, `qa_platform/reports/analytics.py`, `__init__.py`.
