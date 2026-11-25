# Source and architecture audit: 23.15.0

## Release number

23.15.0. 23.14.1 (commit `cd0a585`) is the latest release installed on the user's PCs. This release fixes a reported
defect (Create TFS Defect refused on every result page) **and** adds features to the agent and to voice, so the minor
number goes up (23.14.1 → 23.15.0). The main database is unchanged (23 migrations); the agent's knowledge database
goes to schema 3. The browser extension goes to 1.5.0 with the same extension ID.

## What 23.15.0 changes

| Part | Files |
|---|---|
| Severity / priority accepted as numbers or labels; the preview no longer returns local paths | `qa_platform/api/test_actions.py` |
| A validation error names the field and says what to do | `qa_platform/errors.py` |
| Run facts for functional (including stopped) and performance runs; readable defect text and its HTML; evidence list and attachments; send-now (approve + publish) | `qa_platform/itest/actions.py` |
| The draft carries its evidence files; an existing open draft is updated and its evidence backfilled | `qa_platform/defects/service.py` |
| The defect dialog: readable text, attachment list, assignee, "Approve and send to TFS now"; a defect from a performance run; the run-based defect from the module bar | `web/js/result-actions.js`, `web/js/pages/performance.js`, `web/js/pages/history.js`, `web/js/module-actions.js` |
| Confirmation by word (English and Arabic); remembered values and prompts (knowledge migration 3); unsaid required fields filled from the person's usual and taught values; one request expanded into records, one per day | `skyguard_agent/autopilot.py` |
| Secrets typed while teaching captured and vaulted at ingest; learned codes filled by the run | `skyguard_agent/agent.py`, `skyguard_agent/knowledge.py`, `skyguard_agent/store.py`, `skyguard_agent/executor.py`, `skyguard_agent/learning.py` |
| The page script hands over lasting secrets only, never a one-time code | `skyguard_agent/js/agent_dom.js`, `skyguard_agent/drivers/playwright_driver.py` |
| A waiting preview releases the chat window and the confirmed run picks it up; the agent's settings (`reuse_values`, `remember_secrets`); what the agent remembers and the learned codes over the API | `qa_platform/opsagent/service.py`, `qa_platform/api/agent_simple.py` |
| Fuzzy Arabic word matching and more page words | `skyguard_agent/translate.py` |
| Near-match page names; the Workflow Operator page openable by voice | `qa_platform/voice/intents.py` |
| The word list retried when the model's English leads nowhere | `qa_platform/voice/service.py` |
| A second Arabic transcription pass when the language is unconfident | `qa_platform/voice/whisper_server.py` |
| Hands-free listening ("Listen always") in the page's voice popup | `web/js/voice.js`, `web/js/voice-agent.js` |
| Hands-free listening in the extension; a recording that ends on the pause after a sentence | `browser-extension/extension/sidepanel.js`, `browser-extension/extension/lib/wav-recorder.js` = `web/js/wav-recorder.js` |
| The name: Workflow Operator (page, menu, extension, manual, installer text) | `web/js/app.js`, `web/js/pages/ai-agent.js`, `browser-extension/extension/manifest.json` + `sidepanel.html` + `options.html` + `background.js`, `browser-extension/docs/*`, `packaging/manual/build_user_manual.py` |
| Version, installer check and knowledge schema 3, patch builder | `qa_platform/__init__.py`, `packaging/cumulative-patch/install_patch.py`, `packaging/cumulative-patch/build_patch.py`, `packaging/setup/build_release.py` |
| Tests | `tests/test_tfs_defect_fix_231500.py`, `tests/test_ai_agent_memory_231500.py`, `tests/test_arabic_voice_231500.py`, `tests/test_handsfree_and_name_231500.py`, `tests/test_cumulative_patch_231500.py` |

## Design decisions

* **The route and the API paths keep their old names** (`#ai-agent`, `/api/simple/*`, `/v1/chat`) although the page is
  renamed, so saved links, scripts and the installed extension keep working. Only labels changed.
* **Secrets are removed at ingest**, before the learning pipeline, the recording, the chat, the audit log or the local
  AI can see them, so no later component has to be trusted to mask them.
* **The library default is unchanged**: `Autopilot(reuse_values=False)`. Only the hosted service turns remembering on
  (its default), so an embedding of the library behaves exactly as in 23.14.x.
* **Confirmation by word is bound to the preview's hash**, so a word can never confirm a stale or altered preview.
* **Nothing in the voice path became AI-dependent**: the fuzzy matching, the page near-match and the Arabic retry are
  deterministic text handling that works with the local model off.
