# Security audit: SkyGuard QA 23.5.0 Operations Agent (and the shared core used by the browser extension)

Each requirement, where it is enforced in code, and the automated test that exercises it. Tests listed here were
executed in this build environment (Linux, Python 3.13, Playwright Chromium); see REGRESSION_TEST_REPORT.md for the
run. Items that need Windows or a real browser build are listed at the end as **not verified here**.

## Secrets

| Requirement | Enforced by | Test |
| --- | --- | --- |
| Never send plaintext passwords to the LLM | Credentials go from the vault straight to the driver as a `secret` fill action; the planner and model only see account labels (`planner.py`, `executor.py`). Chat text is checked first (`secrets_guard.inspect`) | `test_chat_with_secret_reaches_no_model_and_no_storage`, `test_password_typed_in_chat_is_intercepted` |
| A password pasted into chat is intercepted before Qwen, chat history, memory, backend logs, audit, screenshots, vector indexes | Page-side check (`web/js/secret-check.js`, the same file in the extension) refuses to send; the server checks again before anything is stored or forwarded and stores only *message withheld*. The agent keeps no screenshots of typed values and has no vector index | `test_secrets_are_detected`, `test_password_in_chat_never_stored_or_logged` (companion log and audit read back), `test_pair_sign_in_credential_page_and_operation` (extension, real Chromium) |
| Known vault values are caught even without a label | `secrets_guard.inspect` / `redact` compare against the values held in the vault (exact match), in memory only | `test_known_vault_values_are_caught_and_redacted` |
| Never store OTPs, MFA secrets, driver codes, recovery codes, or secrets in learning memory | Fields of type password / one-time-code are recorded as *secret entered* in guided learning; fingerprints keep no values (`locator.fingerprint_of`); executor pauses for codes and never reads them | `test_fingerprint_never_keeps_values_or_coordinates`, `test_one_time_code_pauses_for_the_person` |
| Save credentials only after explicit consent | `Vault.save(..., consent=True)` required; API refuses without it; the form needs the tick | `test_vault_needs_consent_and_unavailable_vault_refuses`, `test_credentials_need_consent_and_never_come_back`, `test_credential_form_requires_consent_and_is_never_returned` |
| Credentials never returned, exported or logged | No API returns a secret; export omits `credential_ref`; audit details are redacted | `test_credentials_need_consent_and_never_come_back`, `test_export_import_and_backup_restore` |

## Accounts and sessions

| Requirement | Enforced by | Test |
| --- | --- | --- |
| Never mix cookies or sessions | SkyGuard: one isolated Playwright context per account. Extension: verified logout before the next login (`sessions.py`) | `test_two_accounts_create_then_approve` |
| Never impersonate another user | Identity and role read from the application after sign-in and compared with the account (`authz.py` checks 1 to 3) | `test_wrong_role_is_refused_before_anything_is_submitted` |
| Never bypass MFA, OTP, CAPTCHA | Detection pauses the run with *complete it yourself*; no code path types a code | `test_one_time_code_pauses_for_the_person` |

## Application integrity

| Requirement | Enforced by | Test |
| --- | --- | --- |
| No direct database writes, no database access | The agent only drives the browser (driver actions: navigate, fill, select, check, click, snapshot). No database connector exists for target applications | design; `test_batch_with_rules_rejections_and_verification` checks records exist only through the site's own POSTs |
| Never bypass browser validation by script value assignment | `agent_dom.js` types with `execCommand('insertText')` (or, in the extension, the optional debugger `Input.insertText`); no `.value =` setter fallback; a field that refuses pauses with `typing_not_accepted` | `test_no_remote_code_or_eval` (static scan), e2e tests submit through the site's own validation |
| Nothing runs without confirmation; confirmation bound to the preview | Preview hash; `authz` check 6 | `test_nothing_runs_without_confirmation`, `test_confirmation_is_bound_to_the_preview` |
| Only approved workflows and rules are used | `KnowledgeBase.approved()`; executor refuses others | `test_unapproved_workflow_cannot_run`, `test_knowledge_states_versions_and_rollback` |
| No duplicate records on repeat | Inspection of existing records + unique rules | `test_repeated_request_does_not_create_duplicates` |
| Discovery is read only | Discovery never submits forms | `test_discovery_is_read_only` |
| Never invent IDs, statuses or evidence | Results only from what the page showed; otherwise *Unknown / unverified* | `test_batch_with_rules_rejections_and_verification` |

## Untrusted content

| Requirement | Enforced by | Test |
| --- | --- | --- |
| Web pages, DOM, screenshots and files are data, never instructions | `guard.py` fences and neutralises page text; model output parsed into a fixed schema; values must appear in the user's own request | `test_page_text_is_wrapped_and_neutralised`, `test_model_values_must_be_grounded_in_the_request` |
| No unrestricted file, shell, database or browser access | Only registered application origins; `javascript:`, `file:` and other schemes refused; no shell or file tool exists in the agent | `test_unsafe_urls_refused`, `test_unsafe_application_urls_are_refused`, `test_other_web_pages_are_refused` |
| No mandatory cloud service | The model URL must be loopback unless an admin explicitly allows a network address (`llm.py`) | `test_llm_must_be_local` |

## Access control (SkyGuard)

| Requirement | Enforced by | Test |
| --- | --- | --- |
| Existing sign-in, view-only roles cannot change anything | `qa_platform/auth.py` | `test_view_only_roles_cannot_change_anything` |
| Restore, import and configuration are admin only | `ADMIN_ONLY` patterns `^/api/ops/admin/.*$`, `^/api/ops/config$` | `test_configuration_and_restore_are_admin_only` |
| Tamper-evident audit | Hash chain (`audit.py`) | `test_audit_chain_detects_tampering`, `test_knowledge_approval_and_audit_chain` |

## Companion (browser extension package)

| Requirement | Enforced by | Test |
| --- | --- | --- |
| Local only | Binds 127.0.0.1; Host header must be 127.0.0.1/localhost with the port (DNS rebinding) | `test_listens_on_loopback_only`, `test_dns_rebinding_host_is_refused` |
| Only the extension may call it | `Origin` must be `chrome-extension://<allowed id>`; device pairing (8-digit code, 10 min, 5 attempts, single use); device secret stored as a hash | `test_requests_need_pairing_and_a_token`, `test_pairing_code_single_use_and_attempt_limit`, `test_health_is_the_only_unauthenticated_request` |
| Users and roles | PBKDF2-SHA256 (310 000 iterations); admin, operator, viewer; lockout | `test_roles`, `test_login_lockout` |
| Short-lived tokens | 15-minute bearer tokens, rotation on refresh, 8-hour limit | `test_token_expires_and_refresh` |
| Input limits | JSON only, size limits, unknown fields refused, rate limits | `test_json_only_size_limits_and_schema`, `test_voice_accepts_only_wav` |
| Least privilege extension | MV3, no host permissions at install (asked per application), `debugger` optional, CSP `script-src 'self'`, no eval or remote code | `test_manifest_v3_least_privilege`, `test_no_remote_code_or_eval`, `test_fixed_extension_id_matches_companion` |

## Not verified here

* Windows Credential Manager (SkyGuard) and Windows DPAPI (companion): the code paths call the Windows APIs; they were
  exercised only through the in-memory test backend because no Windows PC was available.
* Behaviour in real Google Chrome and Microsoft Edge builds, enterprise policies and SmartScreen: only Playwright's
  Chromium was used.
* The real transport application: all execution tests use the stand-in site `tests/transport_site.py`.
* Qwen and Faster-Whisper runtimes: tests use stand-ins; the model files were not loaded.
