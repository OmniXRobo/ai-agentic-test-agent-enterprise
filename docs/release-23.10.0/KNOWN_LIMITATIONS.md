# Known limitations and what was not verified

This list covers only what was actually run. Anything not checked is stated as not checked. TEST_REPORT.md has the
numbers.

## Environments that were not available

* **Windows 10 1909 (build 18363), the PC in your install log.** Not available to the build. Windows was tested on
  GitHub's `windows-latest` runner, which is Windows Server with Windows PowerShell 5.1. On that runner, Setup.exe
  upgraded real 23.3.35 and 23.9.0 installations unattended, verified, rolled back, refused an unsupported version,
  and `APPLY_PATCH.ps1` ran under Windows PowerShell 5.1 (CI job `setup-exe-windows`, see TEST_REPORT.md). Setup is a
  64-bit NSIS 3.09 program, and NSIS supports every Windows 10 release. Its only Windows tools are `powershell.exe`
  (5.1) and the application's own Python, both already used on your PC by 23.9.0.
* **The wizard's pages on real Windows.** On the Windows runner, Setup ran unattended (`/S`). The pages (Welcome,
  Detect, Backup, Install, Verify, Finish) were run page by page under Wine 9.0 on Linux, with a real 23.9.0
  installation, its own Windows Python 3.13 and all its packages. The screenshots are in `evidence/`. Wine is not
  Windows: fonts and spacing differ slightly.
* **Microsoft Edge and Google Chrome.** The extension was tested end to end in Playwright's Chromium with the
  unpacked extension. Real Edge and Chrome builds were not available. MV3 behaves the same in Chromium-based
  browsers, but enterprise policies on your PCs were not tested.
* **Qwen (local AI) and Faster-Whisper on your PC.** No local Qwen server ran here. The simple agent was tested on its
  deterministic path (plain-language rewriting, dates, numbers, task choice), which is what it uses without the model.
  The paths that call the model are unchanged from 23.9.0. Voice was tested with a stand-in transcriber; real speech
  recognition is the unchanged Faster-Whisper server of 23.9.0.
* **Real enterprise applications.** The agent was tested against the local transport stand-in (trips, roles,
  approval and authorization, rules), and the guided recording against a local reporting stand-in (sign-in, menu,
  report with a filter, 15 rows per page, order detail pages, a Delete button). Applications with SSO, MFA, Citrix or
  unusual widgets were not available. MFA and CAPTCHA pause for the person by design; that path was not exercised
  against a real MFA provider.

## Behaviour to know

* **Automatic trust.** A task seen to succeed once is usable and marked *uncertain*; seen twice, *verified*. For an
  uncertain task, the preview and the confirmation box are your control. An administrator can switch automatic trust
  off (*Advanced Administrator Settings*), and approvals then work as in 23.9.0.
* **"Show pending trips"** lists records as the agent last saw them while you browsed. Before acting on a record, the
  agent checks its real status in the application, so a stale list cannot cause a wrong change. The list itself can
  be out of date until you open the list in the application again.
* **The guided recording leaves out the sign-in.** At replay, the saved login (Windows Credential Manager) is used, or
  you sign in yourself when the sign-in method is *I sign in myself*.
* **Master-detail** works when the detail page's address contains the record's key (for example `/orders/SO-1020`). A
  detail that opens without changing the address (a pop-up on the same page) is not turned into a parameterized step;
  the recording says so in *Left out of the recording*.
* **Record for Integration Comparison in the extension** keeps its state in the extension's service worker. If the
  browser suspends the service worker in the middle of a long, idle recording, start the recording again.
* **Setup.exe is not code-signed.** See INSTALLATION_GUIDE.md section 2 and SECURITY_REPORT.md.
* **OCR runtime.** Unchanged from 23.9.0, including its own limitations (`previous-23.9.0/KNOWN_LIMITATIONS.md`).
