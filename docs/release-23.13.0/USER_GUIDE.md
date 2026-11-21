# What is new for you in SkyGuard QA 23.13.0

The full user manual with screenshots is `web\manual\SkyGuard_QA_User_Manual.pdf` in the application folder (it is
also attached to each welcome email).

## Edge

Choose **Microsoft Edge** under Settings > Preferences > Browser. AI-Driven generation, Discovery, Hybrid, Guided
playback and exported scripts then use Edge. If a browser is not installed on the PC, SkyGuard uses the next one
(Preferences, Edge, Chrome) and the run log says so.

## Dashboard

Charts are at the top. Click any slice, bar or day to see the runs or defects behind it. The AI dashboard analysis
is at the bottom: *Generate AI analysis*, then edit, PDF, download or email.

## Report bar on the testing pages

On AI-Driven, Guided, Discovery, Hybrid, API Testing, Scheduled Runs, every Performance page, Integration Comparison
and Analytics there is a bar above the page:

* **Generate AI result**: the On-Premise AI reads that module's results and writes what they show, risks and next
  steps. Edit it, then make the PDF, download or email it.
* **Create TFS defect**: title, severity, priority, *Assign to* (people from TFS), steps. It waits in Defect Review
  for approval, or tick *Approve and send to TFS now* if you may approve. A likely duplicate always stops in Defect
  Review.
* **Send email (PDF)**: emails the module's PDF report through Outlook.

## Defect Review

Every defect has **Assign to**, a list of the people in your TFS project.

## Guided Browser

Tabs above the script: All, View Script, Edit Script, Flow Logic, Test Data, Credentials, Components, History,
Result, Evidence, Logs, and *Copy script*. The Python area is wider and taller; version history is at the bottom.

## Healing

When SkyGuard heals a locator and the step passes, the fix is saved in the script, so the next run does not need it
again. When a run stops and asks for help, click the right element in the browser: SkyGuard remembers it.

## AI Agent

* Write or say commands in **Arabic**; the line *Understood as* shows the English command before it runs, and the
  reply comes in Arabic.
* For a website that needs no sign-in, say "there is no account and no need of account", or tick *This website needs
  no sign-in* in Applications & Accounts.
* Websites whose address changes on every visit (`www.` / `booking.`, session IDs) are recognised as the same
  website.
* The chat keeps one browser window and stays signed in. Ask for a new window when you want one.

## Voice

Your chosen voice keeps its gender (the male voice no longer answers with a female voice). Arabic needs the
multilingual speech model; see KNOWN_LIMITATIONS.md.

Arabic spoken replies: install the offline Arabic voice once (`GET_ARABIC_VOICE.ps1` on a PC with Internet, then
`INSTALL_ARABIC_VOICE.cmd` on this PC). Voice settings > Voice reply shows which voice speaks Arabic; press *Test
Arabic Voice* to hear it. You can call the agent by its name in Arabic too (for example "كابتن افتح صفحة الحجز").

## Users (administrators)

A new user needs an email address, their TFS project and their TFS Personal Access Token. They receive an email with
the address of SkyGuard QA, their user name and first password, and the user manual, and must change the password at
the first sign-in. *TFS project / PAT* on a user's row changes them later; *Send manual* emails the manual again.

## HTTPS

Users > HTTPS: choose a name such as `skyguard-qa`, save, restart, then *Trust on this PC*. Open
`https://skyguard-qa/`. Details: INSTALLATION_GUIDE.md section 3a.
