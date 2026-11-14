# SkyGuard QA 23.7.0: security review

**Scope:** the passive learning engine (`skyguard_agent/journal.py`, `understanding.py`, `assistant.py`), the changes to
`agent.py`, `planner.py`, `store.py`, the Playwright `watch()` driver, the page reader `agent_dom.js`, the new SkyGuard
and companion endpoints, the extension's automatic observation, and the 23.7.0 installer and extension updater.

**Tools:** manual review; `bandit 1.9.4` (medium and high) on every changed Python file and the installer.

## Results

| Area | Check | Result |
|---|---|---|
| Dependencies | New packages | None. The agent core stays standard library only; no new runtime, model or wheel. |
| Static analysis | bandit, high severity | 0 |
| Static analysis | bandit, medium (2) | Both are existing, reviewed patterns: the installer's row count over table names read from `sqlite_master`, and the store's migration that inserts an integer index. Neither takes user input. |
| Passwords and codes | Capture | Password, one-time-code and secret-marked fields are journaled as `secret: true` with `value: null`. Every value that is kept goes through `redact_text`. Tested: no test password appears anywhere in the observations table, and every secret field has no value. |
| Observation scope | Which pages | Only the registered application's origins are recorded; anything else is dropped by the journal (origin filter) and, in the extension, never sent (permission granted per site, registered applications only). |
| Agent's own actions | Self-learning | The extension does not observe tabs it is driving. The page reader ignores synthetic clicks (`isTrusted` false). It keeps `change` events, so a date picker the person uses is still learned. |
| Prompt injection | Page text reaching the LLM | Page text and application messages are passed to the local LLM only inside `wrap_untrusted` blocks, and only to word flow purposes and answers. The LLM never decides what runs. Answers about flows, rules, roles and statuses come from deterministic code. |
| Execution safety | Learned tasks | A workflow learned by watching is created as *Discovered* or *Awaiting approval* and cannot run until a person approves it. Rules from application messages block a record only after approval. Preview, confirmation, commit authorization and verification are unchanged. |
| New SkyGuard endpoints | Authentication and roles | `/api/ops/watch*`, `/api/ops/understanding*` require sign-in; starting/stopping watch and *Analyse now* are write paths (view-only refused by the existing rules); `/api/ops/admin/journal-clear/{app_id}` is admin only through the existing `ADMIN_ONLY` rule `^/api/ops/admin/.*$`. |
| New companion endpoints | Authentication and roles | `/v1/observe` and `/v1/understanding/{app_id}/digest` need a signed-in *operator* (write); digest also uses the `llm` rate bucket. `/v1/journal-clear/{app_id}` needs *admin*. The companion still listens on 127.0.0.1 only and checks the extension origin and session token. |
| Input size | Journal growth | Pages are compacted (60 table rows, 1 500 characters of text); identical pages are deduplicated; at most 20 000 entries per application, oldest pruned. Session ids from the extension are validated against `[\w.-]{4,80}`. |
| Web pages | XSS | The Understanding tabs build HTML with `esc()`; chat replies are set as text. Learned names (screen titles, messages) are always escaped. |
| Data at rest | Location | Everything stays in the local knowledge database. *Forget observations* deletes the journal and the mined model for one application and is audited. |
| Installer | Privileges | No elevation, services, `HKLM` or machine-wide paths. It writes only inside the installation folder (and, for the extension updater, `%LOCALAPPDATA%\SkyGuardAgent`). Tested by a check over every file in the patch folder. |
| Installer | Offline / integrity | Standard library only, no network imports. Every payload file is checked against `PATCH_MANIFEST.json` (SHA-256) before anything changes; a changed file aborts (tested). The extension updater checks its files against `SHA256_CHECKSUM.txt`. |
| Installer | Failure safety | Backup before copy; atomic replace; automatic restore on any failure; the half-migrated database is kept (tested on 23.3.35, 23.4.0, 23.5.0 and 23.6.0). The extension updater restores `app\` and `extension\` if the new companion does not load. |

## Residual risks

- What a person submits while the agent watches is real. The guide tells users to learn on test environments or with
  test data.
- The booking length behind a learned no-overlap rule is an estimate from what the application refused and accepted
  (at least 60 minutes). The preview shows it, and the application stays the final check.
