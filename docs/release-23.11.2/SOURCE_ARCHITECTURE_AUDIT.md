# Source and architecture audit (23.11.1 → 23.11.2)

## Which release this patch is built on

* The request was a follow-up to the delivered 23.11.1 (commit `8e8df0d`, pull request #55): fix the
  missing-information follow-up and deliver a 23.11.x patch the same way as before.
* The next number after 23.11.1 is **23.11.2**. It is built on 23.11.1 in the same pull request and contains all of
  it. It upgrades any release from 23.3.35 to 23.11.1.
* Nothing of 23.11.1 was overwritten. All of 23.11.1's own tests still run (TEST_REPORT.md).
* The zips are named after the version they install: `SkyGuard_SGQA-23.11.2_AI_Agent_Patch.zip` and
  `SkyGuard_SGQA-23.11.2_Edge_Chrome_Extension.zip`, so they cannot be confused with the 23.11.1 zips, which carried
  the names of the "23.10.0" request.

## Source available

* The full history from before 23.3.35 to 23.11.1 is in the repository. Every release in
  RELEASE_INCLUSION_MANIFEST.md is extracted with `git archive` from its own commit and installed as a baseline in the
  installer tests, now 13 baselines including 23.11.1.
* No source was missing. No source was reconstructed.

## What existed in 23.11.1, and what changed

| Area | Found in 23.11.1 | Decision in 23.11.2 |
|---|---|---|
| Answer to a pending question | Glued to the original request and parsed again by `planner.parse_structured` (`Label: value` only). Nothing understood was kept. | `autopilot.parse_answer` reads the answer against the asked fields (and the task's other fields). The values are kept with the pending question and added to the request; only missing fields are asked. The task chosen for the question is kept. |
| Secret check | `code: <digits>` was always a secret. | Not when an ordinary word precedes "code" (postal, zip, product, ...). Same rule in Python and in the JavaScript used by SkyGuard's page and the extension (the two JavaScript files stay identical). |
| Identity before a change | Verified only from the application's display. An application that never shows the user could run nothing. | A sign-in the agent submitted with the saved account, and that the application accepted, is recorded as `sign_in_confirmed`. `authz.check` accepts it only for operations without a role restriction, outside production mode, and when no identity rule was learned. |
| Suggestions | Recent commands included answers ("First Name; muhammad"). | Answers and unclassified replies are not offered as suggestions. |

## Components touched

```
skyguard_agent/autopilot.py              parse_answer, field_aliases, kept answers, _pending_fields, suggestions
skyguard_agent/secrets_guard.py          ordinary codes are not secrets
skyguard_agent/sessions.py               SessionState.sign_in_confirmed
skyguard_agent/executor.py               sign-in submitted by the agent and accepted -> sign_in_confirmed
skyguard_agent/authz.py                  identity check accepts sign_in_confirmed for unrestricted operations
web/js/secret-check.js                   ordinary codes (same file in the extension)
browser-extension/extension/lib/secret-check.js, manifest.json   1.3.1
browser-extension/companion/skyguard_companion/__init__.py       1.3.1
skyguard_agent/__init__.py               1.3.1 (agent core shared with the companion)
qa_platform/__init__.py                  23.11.2
packaging/...                            installer 23.11.2, new check, Setup texts, CI baselines
```

## Rules kept

* No credentials in prompts, learning memory, scripts, screenshots or logs. Answers pass the same secret check as any
  message before they are kept.
* MFA, CAPTCHA and application permissions are never bypassed. A role-restricted operation still needs the role shown
  by the application. A one-time code at sign-in is still typed by the person; a manual sign-in is never recorded
  as confirmed by the agent.
* The model proposes and never executes. Answers are read without the model.
* No machine-wide changes: no registry keys, system PATH, services or protected folders.
