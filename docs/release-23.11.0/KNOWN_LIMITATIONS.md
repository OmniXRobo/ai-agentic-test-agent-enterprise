# Known limitations of 23.11.0

What was not verified, and what the new features do not do. TEST_REPORT.md lists what was run.

## Not verified in this release

| Not verified | Why | What was done instead |
|---|---|---|
| **Windows 11 itself** | No Windows 11 machine was available to the build. | GitHub Actions `windows-latest` runs the full test suite, Setup.exe unattended over real 23.3.35, 23.9.0 and 23.10.0 installations, rollback, `APPLY_PATCH.ps1` under Windows PowerShell 5.1, and the extension companion installer on a fresh profile. The Setup wizard pages were also walked through under Wine (TEST_REPORT.md). |
| **A real TFS / Azure DevOps Server** | No server was reachable from the build. | A stand-in TFS (`tests/fake_ado.py`) that answers like the Azure DevOps REST API. It covers mandatory fields with allowed values, tags with `CONTAINS`, attachments, a validate-only call, a server error, an unreachable server, and a created item whose answer is lost. Real-server behaviour that differs from the documented API (custom process rules, server-side field rules, attachment size limits set by the administrator) was not exercised. **Try one draft against your TFS first.** |
| **Live local Qwen** | No model server in the build environment. | The AI classification path is tested with a stub that returns valid, invalid and unknown-class answers. Without a configured on-premise AI, drafts are classified by the rules only and say so. |
| **SSO, MFA and CAPTCHA sign-ins** | No such application was available. | Unchanged behaviour from earlier releases: the run pauses for the person. |
| Firefox | SkyGuard's browser features target Edge and Chrome (Chromium). | The new pages were checked in Chromium with Playwright. |

## Editable Python

* The validation policy is **not a sandbox** (SECURITY_REPORT.md section 3). Give edit rights only to people you would
  let write test code.
* An edited test is plain Pytest + Playwright. Two runner features do not apply to it: self-healing locators, and the
  pause that lets a person help during a Play run. Its failures still become drafts with a screenshot, a traceback and
  (when no secret was typed) a trace.
* *Run data rows* on an edited script runs **every** row, each as its own test. Choosing rows applies only to scripts
  whose Python is not edited.
* The editor is a text area with syntax colours, line numbers, find / replace and undo / redo. It does not offer
  auto-completion or code hints.
* When the recorded steps change after an edit, the saved Python is not updated by itself. The card says the steps
  changed since the Python was saved, and **Regenerate** (which asks first) or a manual edit brings them together.

## Smart Wait

* It waits for XHR and fetch requests. It does **not** wait for WebSocket messages, Server-Sent Events, or requests
  running longer than 20 seconds (long polling); those are reported, not waited for.
* Loaders are recognised by common markers: `aria-busy`, spinner, loader and loading classes, BlockUI, DataTables
  "processing", and autocomplete loading. A custom loader with another class name is detected only through the DOM
  quiet period.
* A page that never stops changing, such as a live ticker or a constant animation, never becomes quiet. The wait then
  ends at its timeout and is reported as *uncertain*. The step continues, unless the wait is set to strict.
* The recorder adds a wait only when the page started an XHR or fetch request **within 2.5 seconds** of the recorded
  action. Data loaded later, or by WebSocket, is not noticed. Use **+W** or **Insert waits…** for those steps.

## Defect Review and TFS publishing

* **Change in behaviour:** the "automatic" defect mode no longer creates TFS bugs by itself. It prepares the draft,
  and a person approves and sends it. This is what the request asks for, but it is different from earlier releases.
* The classification is rule-based with fixed confidence values. Those values are not calibrated statistics. The
  local AI's opinion is shown beside the rules' view and changes nothing by itself.
* Duplicate checks need TFS. When TFS is not configured or not reachable, the draft says "Duplicate check not done",
  and sending is refused until TFS answers.
* Attachments larger than 20 MB are not sent, and the draft says which ones.
* Rollback to an older release keeps migration 20's new columns and tables. Older versions ignore them, so nothing
  breaks.

## Distribution

* **Setup.exe is not code-signed.** SmartScreen may warn. The manual install does the same without an .exe.
