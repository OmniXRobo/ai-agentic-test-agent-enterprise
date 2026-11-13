# Changelog: SkyGuard QA 23.6.0 (cumulative patch: 23.4.0 + 23.5.0 + 23.6.0)

This one package installs on **23.3.35, 23.4.0 or 23.5.0** and brings the installation to 23.6.0. It contains every
application file changed since 23.3.35. That includes the 23.4.0 final patch and the 23.5.0 Operations Agent, so you
do not need to apply them first.

## 23.6.0

### Fixed
- **AI Test Design Generate:**
  - uses the 600 s generation timeout (was 120 s);
  - generates long stories in parts;
  - names the failing stage and what to do;
  - checks its input (`STORY_EMPTY`, user story id > 0).
- **Open script:**
  - framework scripts (`FWS-…`) open in AI-Driven Automation;
  - missing, wrong-type, locked or binary script files are reported with path and reason;
  - opening never writes;
  - BOM and Windows-1252 files and Unicode or space paths are handled.
- **Guided playback:**
  - calendar days, dropdown options, autocomplete suggestions and pop-up dismissal are replayed by meaning;
  - hovers on changed labels no longer fail a run;
  - clicks on whole form containers are skipped;
  - prices in labels no longer lower self-healing scores.
- **Operations Agent:**
  - an exact unique `data-test` / `id` / `name` decides between look-alike controls (Swag Labs "Add to cart");
  - true duplicates are offered as choices in chat.

### Added
- **Universal component engine** (`automation/js/components.js`, `automation/components.py`): native and custom
  dropdowns (React Select / MUI / Ant / Bootstrap / Select2 / portal lists), autocomplete, single and range
  calendars, month navigation, and disabled or past dates. It works in iframes, open shadow roots and modals.
- New script actions `choose_option` and `dismiss`, and semantic data on recorded steps.
- **Semantic upgrade** for older recordings (preview, then save as a new version):
  `POST /api/automation/scripts/{id}/semantic-upgrade`.
- **Help during a visible run:**
  - `GET` / `POST /api/automation/runs/{run_id}/intervention`;
  - a banner in the script editor with the choices, Continue, Retry, Skip and Stop;
  - the browser and checkpoint are kept while it waits.
- **Payment and booking guard:** a visible run never clicks "Pay now", "Book now", "Complete booking", "Place order"
  and similar without a separate "Allow this once". Unattended runs follow `playback.guard_commits_unattended`.
- Operations Agent `POST /api/ops/runs/{id}/resume` accepts `choice`.

### Changed
- Simpler AI Operations Agent chat page: chat, input, Send, microphone, progress, Pause / Resume / Stop.
- Version 23.6.0. Main database: unchanged at 18 migrations.

### Not changed
- No new Python packages, so the existing offline wheel set is enough.
- The browser extension package is unchanged (its shared `agent_dom.js` was not modified).

## 23.5.0 (included)
Adaptive AI Application Learning & Operations Agent. See `docs/ops-agent/PATCH_CHANGELOG.md` in the repository.

## 23.4.0 (included)
Final patch over 23.3.35, with the 23.4.0 database migrations (18 in total). See `docs/final-patch/` in the repository.
