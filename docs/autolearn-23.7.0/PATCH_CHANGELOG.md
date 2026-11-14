# SkyGuard QA 23.7.0: Autonomous Application Learning (changelog)

Cumulative: includes 23.4.0, 23.5.0 and 23.6.0. Agent core 1.1.0. Browser extension and companion 1.1.0.

## Fixed: "the AI Application Learning & Operations Agent is not mature"

Until 23.6.0 a person had to teach the agent each task (guided learning), name it, and type rules in chat. It never
connected tasks, statuses and roles, so it could not answer questions such as *"what happens after a trip is
approved?"*. See *DESIGN_AND_LLM_DECISION.md* for the root cause and the framework/LLM decision.

## New

- **Learn while I browse** (SkyGuard: Operations Agent > Learning; extension: Options > Learning). The agent watches
  you use the application and works out by itself:
  - screens (with `{id}` path templates) and their kind;
  - tasks and their fields in order, steps, defaults, optional fields and what success looks like;
  - statuses and lifecycles, and which task and role moves a record from one status to the next;
  - business rules from the application's own messages: unique, no overlap (with an estimated booking length),
    date today or later, required, ranges, formats; plus the screen's constraints and options;
  - roles: who did which task, which actions each role was offered, what each role was refused;
  - the sign-in page and the identity shown after sign-in;
  - navigation and the last status of each record seen in lists.
- **Understanding** tab (SkyGuard and the extension side panel): flows, lifecycles, rules, roles and screens, with
  *Analyse now* and *Forget observations* (admin).
- **Chat that understands the application.** Questions about how, who, what next, statuses, rules, fields, screens,
  record status and counts are answered from what was observed. Other questions use retrieval over the learned facts,
  phrased by the local AI model when it runs. Synonyms and word forms are handled (*sign off*, *okay* →
  approve; *authorise*, *authorised* → authorize; *add*, *new*, *register* → create).
- **A learned task runs after one approval.** Asking for a task the agent learned by watching offers *Approve and
  run*. Then the usual safeguards apply: read-only inspection, rule checks, preview, confirmation, verification in the
  application.
- The rule checks use what was learned: for example a bus double booking that the application refused while you
  browsed is caught in the preview before anything is submitted.
- The request parser understands `<entity> <code>` (`trip T300`) and option values (`bus BUS-102`) for learned tasks.

## Changed

- Knowledge database schema 2: tables `observations` (the journal) and `app_model` (the mined model). New knowledge
  kind `lifecycle`. Nothing existing changes. The main database is unchanged (18 migrations).
- The shared page reader (`agent_dom.js` 1.1.0) marks layout tables and reports field constraints. It ignores
  synthetic clicks but keeps change events from date pickers and other widgets.
- Playwright driver: `watch()` observes a person in a SkyGuard-opened browser without acting.
- New endpoints: SkyGuard `POST /api/ops/watch`, `POST /api/ops/watch/{sid}/stop`, `GET /api/ops/watch`,
  `GET /api/ops/understanding`, `POST /api/ops/understanding/{app_id}/digest`,
  `POST /api/ops/admin/journal-clear/{app_id}` (admin). Companion `POST /v1/observe`, `GET /v1/understanding`,
  `POST /v1/understanding/{app_id}/digest`, `POST /v1/journal-clear/{app_id}` (admin).

## Unchanged

- Local LLM: `llama.cpp` `llama-server` with a Qwen GGUF model. No new Python packages, no new runtimes.
- Approval before execution, preview and confirmation, credential vault, audit log, roles.

## Packaging

- `APPLY_PATCH.cmd` / `ROLLBACK_PATCH.cmd` / `VERIFY_PATCH.cmd` as before, now for 23.3.35 to 23.6.0.
- New `UPDATE_EXTENSION.cmd` updates the extension companion in `%LOCALAPPDATA%\SkyGuardAgent` to 1.1.0, with backup
  and `-Rollback`.
