# User guide: SkyGuard QA 23.15.0

Press Ctrl+F5 once after the upgrade. Everything here works offline on the office intranet. Earlier guides:
`previous-23.14.1/USER_GUIDE.md` and the other `previous-*` folders.

## 1. Creating a TFS defect from any result

The bar at the top of every result page has **Create TFS Defect**. It now works on all of them: Vulnerability
Scanning, Execution History (a failed run, and a run you stopped), Performance Results (passed or failed), API
Testing, Integration Comparison and the security screens.

1. Press **Create TFS Defect**. The preview shows:
   * the title SkyGuard proposes (change it if you like),
   * **Severity** and **Priority** (1 to 4; the dropdown's numbers are what the API now expects),
   * the **reproduction text**: the numbered steps with their result, Expected result, Actual result, the error, the
     environment (browser, address, script and run) and the evidence list. Edit it freely: what you see is what the
     defect carries.
   * the **attachments**: the run's screenshots (the failed step first), the trace, the video, the report and the
     result files. Untick **Attach the evidence** to create the defect without them.
   * **Assign to**: anyone from your TFS project.
2. Then either:
   * **Create draft** - the defect goes to **Defect Review**. Approve it there and press **Publish** to create the
     bug in TFS, or
   * **Approve and send to TFS now** - one step: the draft is created, approved and published. The answer carries the
     TFS id, which is also a link.
3. If TFS refuses the bug (a mandatory field of your process, for example), the draft is kept with the message, so
   nothing is lost: fix it in Defect Review and publish again.
4. A defect for a problem that is already open is reported as a duplicate, with the existing bug's id, instead of
   creating a second one. "Create anyway" is on the draft.

**Performance results.** A run that passed can still become a defect (an SLA that passed only just, a slow request).
The body carries the samples, error rate, average and 95th percentile, throughput, peak users and every SLA check with
its limit and actual value; `qa-report.pdf` and `metrics.json` are attached.

## 2. The Workflow Operator (the page that was called AI Agent)

Same page, same place in the menu, a name that says what it does: you teach it your application's workflows, it shows
one preview, and it runs them.

### Teaching it once

1. **Connect** the application (its address), or type `open https://...` in the chat.
2. Press **Start learning** and use the application normally: sign in, open the menus, create a record, fill the
   fields, save. SkyGuard watches and writes a draft of what you did.
3. **The password you type while signing in, and an authorization or approval code you type, are kept** for you, in
   the Windows Credential Manager, masked everywhere as `********`. You will not be asked for them again. A one-time
   code (SMS, authenticator) is never kept: a run always asks you for that one.
4. Approve the lesson in **Knowledge**. It is now a workflow you can ask for by name.

### Asking for it

* Type (or say) the workflow's name: "create trip". The preview shows every field with its value and where the value
  came from - "while teaching", "your usual value", or from your request.
* **Say "yes"** - or "go ahead", "run it", "نعم", "نفذ", "تأكيد", "موافق" - and it runs. One preview, one word; no
  second confirmation. (A request that changes records still shows the tick box under the preview: that is the one
  confirmation that stays.)
* Fill in only what changes: "create a trip tomorrow" uses your usual route, driver and bus.
* **Many records in one request:** "create 10 trips with the same data, one per day, drivers: Aslam Khan, Bilal Saeed,
  Dina Yousef" becomes 10 records - a different day for each, the drivers in turn, everything else as usual - in one
  preview.
* Your usual requests are offered as suggestions, with how often you used them. **Forget** removes a value, a request
  or a learned code at any time (Advanced Administrator Settings > what the agent remembers).

## 3. Voice, in the language you speak

* Speak English, the reply is spoken in English. Speak Arabic, the reply is spoken in Arabic, by the offline Arabic
  voice. This works in the Voice Command Center, in the Workflow Operator's chat and in the browser extension.
* In **Preferences > Voice**, "English and Arabic" now really detects Arabic: when the recognizer is unsure, the
  sentence is read again as Arabic.
* Arabic as the recognizer writes it is understood: "إفتاء الإشارات" opens Notifications, "إفتح لوحة التحكم" opens the
  Dashboard. If a word is too far from anything known, the question comes back - in Arabic.
* **Listen always**: press it once in the voice popup (or in the extension's chat). Say a request, pause, and it runs;
  then say the next one. No button in between. For example: "open AI-driven automation", "the script name is online
  store", "generate the script", "create the defect in TFS". Press **Stop listening** when you are done; leaving the
  chat also stops it.
