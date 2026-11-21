# Changelog: SkyGuard QA 23.13.0 and extension 1.4.1

Cumulative on 23.12.0 (commit `fd5a117`). One upgrade for every release from 23.3.35 to 23.12.0. Everything works
offline; nothing new is downloaded on the user's PC.

The numbers follow the 13 items of the request.

## 1. Microsoft Edge for AI-Driven, Discovery and Hybrid

* When Preferences say Microsoft Edge (or Chrome), every part that used to ask for the built-in "chromium" (script
  generation, playback, Discovery, Hybrid, recordings, exported pytest scripts, the framework template) now uses the
  browser chosen in Preferences.
* On a PC where the included Chromium is not installed, SkyGuard tries Preferences, then Edge, then Chrome. Only a
  browser that is *not installed* is replaced, and the replacement is logged. A browser that is installed but blocked
  or crashed is reported as it is, with every browser that was tried.

## 2. Dashboard

* The charts come first, then the key figures, QA progress and performance; the AI dashboard analysis is at the
  bottom.
* New charts: defects by module, API test results, self-healing results. The SLA chart joined the chart grid.
* Every donut slice, bar and trend day is a link. A trend day opens History for that day (`#/history?date=`); a run
  type opens History filtered to that type (`?kind=`).

## 3. Voice

* Arabic and UAE Arabic: a command spoken or typed in Arabic is translated to English by the local AI (a built-in
  word list when the model is not running), runs as the English command, and the reply is given in Arabic. Speech
  recognition runs with English and Arabic ("auto").
* Arabic replies are spoken with an installed Windows Arabic voice of the same gender as the chosen voice.
* Next patch: offline Arabic voice for spoken replies (`GET_ARABIC_VOICE.ps1` on an internet PC,
  `INSTALL_ARABIC_VOICE.cmd` on the office PC; Piper with the male voice `ar_JO-kareem-medium`). Arabic replies pick
  it automatically in both reply styles; an English voice never reads Arabic. Voice settings show which voice speaks
  Arabic and have a *Test Arabic Voice* button.
* Next patch: the wake name said in Arabic is recognised in any spelling the recognizer writes ("كابتن", "كابدان",
  "كبتن" for Captain), and common misheard command words are understood ("كابدان افتا صفا" works like
  "كابتن افتح صفحة"). An installed multilingual speech model with another name (for example `small.ar`) is used for
  Arabic automatically.
* **Bug fixed:** a male voice was chosen but replies came in a female voice. The browser fallback used its own list
  (often Microsoft Zira, female) whenever the Windows voice could not be used. It now keeps the chosen gender (male
  by default).

## 4. Defect Review: "Assign to"

* "Assign to" is a dropdown of the people in the TFS project (team members, plus everyone who already has work items
  assigned in it), for every defect type. It is saved with the draft (migration 22) and sent as
  `System.AssignedTo` when the defect is published.

## 5. Guided Browser

* Script tabs as in AI-Driven: All, View Script, Edit Script, Flow Logic, Test Data, Credentials, Components, History,
  Result, Evidence, Logs, and a *Copy script* button. The chosen tab is remembered.
* The Python area is full width and taller (720 px by default).
* Version history is at the bottom of the page.

## 6. API Testing

* **Open** now shows the run's results at once: the details card is drawn, scrolled into view and the row is marked.
* **Offline TLS false FAIL fixed:** Test Connection and runs trusted only the public certificate bundle, so an intranet
  API with a company CA (trusted by Windows and Edge) failed with a TLS error. The Windows certificate store, the
  public bundle and the application's own CA file are now all trusted. Verification stays on.

## 7. HTTPS with a friendly name

* Users > HTTPS: choose a name (for example `skyguard-qa`) and a port (443). The certificate authority and the server
  certificate are made on this PC with the Python standard library (RSA-2048, SHA-256); nothing is downloaded.
* SkyGuard serves HTTPS on that port beside the normal HTTP address. The name, `localhost`, the PC name, `127.0.0.1`
  and the PC's network addresses are in the certificate.
* *Trust on this PC* runs `ENABLE_HTTPS.cmd` as administrator (trust the certificate, hosts-file entry, firewall
  rule). Colleagues run `TRUST_SKYGUARD_QA.cmd` once.

## 8. AI Agent and the browser extension

* **Bug fixed:** choosing *lifecycle* in the Knowledge tab's *Kind* list made the server refuse the request (the kind
  was missing from its list), and the page replaced its controls with an error, so the dropdowns disappeared. The
  kind is accepted now, and a filter that cannot be loaded is reset so the controls come back.
* Public websites are no longer asked for an account: "there is no account and no need of account" is remembered for
  the application (and can be undone under Applications & Accounts), and the waiting request continues without one.
* Dynamic addresses: other host names of the same site (`www.`, `booking.`, ...) and paths that change on every visit
  are treated as the same application. A different domain is never the same application.
* One browser window is kept across chat commands and stays signed in; a new window opens only when asked.
* Arabic commands are translated to English (shown as *Understood as:*), in the AI Agent page and in the extension.
* Extension and companion 1.4.1 carry the same agent core.

## 9. Users

* Creating a user requires an email address, the person's own TFS project and their TFS Personal Access Token. The
  PAT is checked against the project when the server can be reached; an invalid PAT stops the creation. It is stored
  in Windows Credential Manager for that person only.
* Each person's TFS data (dashboard, defects, assignees) comes from their own project with their own PAT; caches are
  kept apart per person and project.
* Welcome email (through the Outlook email set up in Settings): the address of SkyGuard QA, the user name, the first
  password and the user manual (PDF, with screenshots) attached. When it is sent, the person must change the password
  at the first sign-in. *Send manual* sends the address and the manual again (without a password).
* New: `web/manual/SkyGuard_QA_User_Manual.pdf` (15 screenshots, every main page).

## 10. Report buttons on the testing pages

* AI-Driven, Guided, Discovery, Hybrid, API Testing, Scheduled Runs, Load, Stress, Spike, Soak, Live, Performance
  Schedules, Performance Results, Integration Comparison and Analytics have one bar above the page:
  **Generate AI result**, **Create TFS defect** and **Send email (PDF)**.
* The AI result and the PDF use the module's recorded data (runs, failures, defects, self-healing, timings). The
  defect goes through Defect Review (approval and duplicate check), or straight to TFS when the person ticks
  *Approve and send now* and may approve.

## 11. Analytics

* An AI analysis card on the page, with PDF and email, using the same report service.

## 12. Healing that is kept

* A healed locator whose step passed is written into the script (the old selector stays as an alternate), so the next
  run does not heal again. This is now the default (`playback.auto_apply_healing` in `settings.json`), in Guided, Discovery, Hybrid and AI-Driven
  scripts and in the AI Agent.
* When a run pauses for help and the person clicks the element, that element becomes the step's locator and is saved
  as an approved correction.
* Dynamic addresses: `goto` and `expect_url` steps match addresses whose IDs, dates or tokens change on every visit.

## 13. Version, security, offline

* Version 23.13.0, extension 1.4.1. Security review: SECURITY_REPORT.md. Tests: TEST_REPORT.md.
