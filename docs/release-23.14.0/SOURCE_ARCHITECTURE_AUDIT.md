# Source and architecture audit: 23.14.0

## Release number

23.14.0. 23.13.3 (commit `9b42d1b`) is the latest release installed on the user's PCs. This release adds a database
migration (23) and three new modules, so the minor number goes up (23.13 → 23.14). The browser extension is unchanged
(1.4.3).

## Where each part is

| Part | Files |
|---|---|
| Shared store, scope, throttle, run control, sanitizer | `qa_platform/itest/common.py` |
| Result actions (facts, evidence, AI analysis, TFS defect, PDF, email, retest) | `qa_platform/itest/actions.py`, `qa_platform/api/test_actions.py`, `web/js/result-actions.js` |
| Security Testing | `qa_platform/sectest/` (`service.py`, `crawler.py`, `checks.py`, `active.py`, `fuzz.py`, `zap.py`, `scenarios.py`, `client.py`), `qa_platform/api/security.py`, `web/js/pages/security.js` |
| Red Hat Fuse testing | `qa_platform/fusetest/` (`adapters.py`: Camel Jolokia, actuator, black box; `routes.py`; `compare.py`; `service.py`), `/api/fuse/*` in `qa_platform/api/integration.py`, `web/js/pages/integration-testing.js` |
| ActiveMQ testing | `qa_platform/amqtest/` (`jolokia.py`: Classic and Artemis adapters, read-only whitelist; `stomp.py`; `service.py`), `/api/activemq/*` in `qa_platform/api/integration.py`, `web/js/pages/integration-testing.js` |
| Shared screen pieces | `web/js/pages/itest-ui.js` |
| Email / SMTP | `qa_platform/mailer.py`, `qa_platform/api/schedules.py` (`/api/email/*`), `web/js/pages/schedules.js` |
| Dashboard | `/api/itest/summary`, `web/js/pages/dashboard.js` |
| Agents | `qa_platform/agents/registry.py` (43 agents with category, inputs, outputs, dependencies), `qa_platform/api/core.py` (availability), `qa_platform/api/agent_control.py` (Stop / Pause / Resume), `web/js/pages/workflow.js` + `web/js/pages/wf-looks/*`, `web/js/robots.js` |
| Voice | `qa_platform/voice/intents_itest.py`, `intents.py` (pages), `service.py` (`interpret`), `api/voice.py` (`/api/voice/interpret`), `skyguard_agent/translate.py` (Arabic phrases), `web/js/pages/voice.js` (voice tests) |
| Database | migration 23 in `qa_platform/storage/db.py` |
| Wiring | `qa_platform/runtime.py` (`_integration_patch`), `qa_platform/main.py`, `qa_platform/auth.py`, `web/js/app.js` |
| Installer | `packaging/cumulative-patch/install_patch.py` (23.14.0 check, new tables, voice inventory / kept check), `build_patch.py`, `packaging/setup/*` |
| Tests | `tests/test_security_integration_231400.py`, `tests/test_ui_231400.py`, `tests/test_agent_workflow_looks_231400.py`, `tests/test_cumulative_patch_231400.py` |
