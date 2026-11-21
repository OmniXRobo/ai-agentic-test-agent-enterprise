# Source and architecture audit: 23.13.2

## Release number

23.13.2. 23.13.1 (merge commit `0a7ed57`, PR #60) was the latest release. 23.13.2 adds spoken replies to the AI Agent page
(`web/js/pages/ai-agent.js`, `qa_platform/api/agent_simple.py`) and the extension (`sidepanel.js`, the companion's
`/v1/voice/speak`, `skyguard_agent/speech.py`, `skyguard_agent/translate.py spoken_reply`); the extension goes to 1.4.2.

From 23.13.1: 23.13.1. 23.13.0 (merge commit `e0f9ac4`, PR #59) was the latest release. 23.13.1 adds no database migration
and no extension change; it adds the offline Arabic voice (`qa_platform/voice/piper.py`, `tts.py`), the Arabic wake
name (`skyguard_agent/translate.py`, `web/js/voice.js`) and the install helpers at the top of the application folder, so
the patch number goes up (23.13.0 → 23.13.1).

From 23.13.0: 23.13.0. 23.12.0 (merge commit `fd5a117`, PR #58) was the latest release when this work started. This release adds a
database migration (22) and new features, so the minor number goes up (23.12 → 23.13). The browser extension and its
companion go to 1.4.1 because the shared agent core and the side panel changed.

## Where each part is

| Part | Where |
|---|---|
| Browser choice and fallback (Preferences, Edge, Chrome, Chromium) | `qa_platform/automation/browser.py` (`preferred`, `fallback_order`, `launch`); `pytest_export.py`, `framework/template/fixtures/browser.py`, `discovery.py` |
| Healing that is kept, the person's click as locator, dynamic URLs | `qa_platform/automation/runner.py` (`_keep_healed`, `_watch_person`, `_learn_from_person`, `url_pattern`, `url_matches`) |
| AI Agent: public sites, same site, one chat browser, Arabic | `skyguard_agent/knowledge.py` (`site_of`, `same_site`), `autopilot.py`, `executor.py`, `sessions.py`, `translate.py`; `qa_platform/opsagent/service.py`, `qa_platform/api/agent_simple.py`, `api/opsagent.py` (Kind `lifecycle`); `web/js/pages/ai-agent.js`, `ops-agent.js` |
| Voice: Arabic recognition, translation, voice gender | `qa_platform/voice/service.py`, `tts.py`, `fw_service.py`, `whisper_server.py`; `web/js/voice.js` |
| API Testing TLS trust and Open | `qa_platform/apitesting/client.py` (`trust_context`), `service.py`; `web/js/pages/api-testing.js` |
| Users: email, personal TFS project and PAT, welcome email | `qa_platform/auth.py`, `qa_platform/api/auth.py`, `qa_platform/welcome.py`, `qa_platform/tfs/service.py` (`personal`, `save_personal`, `check_pat`, scoped `TTLCache`), `web/js/pages/users.js` |
| TFS people for "Assign to" | `qa_platform/tfs/client.py` (`team_members`), `tfs/service.py` (`assignees`), `qa_platform/api/defects.py`, `qa_platform/defects/service.py`, migration 22 in `qa_platform/storage/db.py`, `web/js/pages/defect-review.js` |
| HTTPS | `qa_platform/https_setup.py` (certificates), `qa_platform/launcher.py` (`https_listener`), `qa_platform/api/guard.py` (`https_hosts`), `qa_platform/api/auth.py` (`/network/https`), `ENABLE_HTTPS.cmd`, `TRUST_SKYGUARD_QA.cmd` |
| Report buttons and module reports | `web/js/module-actions.js`, `web/index.html` (`#moduleBar`), `web/js/app.js`; `qa_platform/reports/ai_reports.py` (kind `module`, `MODULES`, `module_facts`); `POST /api/defects/manual` |
| Dashboard and drill-down | `web/js/pages/dashboard.js`, `web/js/dash-charts.js`, `web/js/pages/history.js`, `qa_platform/reports/analytics.py` |
| Analytics AI | `web/js/pages/analytics.js` |
| Guided Browser tabs | `web/js/pages/automation.js` (`GUIDED_TABS`, `wireGuidedTabs`), `web/js/python-editor.js` |
| User manual | `packaging/manual/build_user_manual.py` → `web/manual/SkyGuard_QA_User_Manual.pdf` |
| Extension 1.4.1 | `browser-extension/companion/skyguard_companion/server.py`, `browser-extension/extension/sidepanel.js`, manifest |
| Installer | `packaging/cumulative-patch/install_patch.py` (baselines up to 23.12.0, migration count 22, check *Edge, healing, AI Agent, HTTPS (23.13.0)*), `build_patch.py` (`ROOT_FILES`), `packaging/setup/build_release.py`, `SkyGuardSetup.nsi` |

## Design choices

* **No new third-party dependency.** The certificates are made with the standard library because the `cryptography`
  wheel is not in the offline bundle; TLS itself is Python's OpenSSL through uvicorn.
* **HTTPS beside HTTP.** A second uvicorn server serves the same application on the HTTPS port; the HTTP address
  keeps working, so nothing that already points to it breaks.
* **Personal TFS access reuses the TFS service.** The personal PAT and project overlay the shared profile for the
  signed-in person; administrators still edit the shared profile. Caches are scoped per person and project.
* **One report bar, the same on every testing page,** so each page keeps its own layout; the module report reuses
  the dashboard's AI report service and the existing Defect Review.
* **Healing writes only proven fixes** (the step passed) and keeps the old selector as an alternate; every change is a
  script version.
