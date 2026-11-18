# Final release report: SkyGuard QA 23.11.2 (missing-information follow-up) and extension 1.3.1

## What was reported

On Swag Labs, the learned task *Finish lab* needs First Name, Last Name and Zip/Postal Code. The answer
**"First Name; muhammad"**, and then **"pre pare it"**, made the agent ask for all three fields again.

## What you get

| File | What it is |
|---|---|
| `SkyGuard_SGQA-23.11.2_AI_Agent_Patch.zip` | The upgrade to 23.11.2, for any release from 23.3.35 to 23.11.1. It holds the same parts as before: `SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe` (Welcome → Detect → Backup → Install → Verify → Finish), `Manual_Install` (`APPLY_PATCH.cmd`, `VERIFY_PATCH.cmd`, `ROLLBACK_PATCH.cmd`), `README_FIRST.txt`, the documents, the SBOM, test results, evidence screenshots and `SHA256SUMS.txt`. |
| `SkyGuard_SGQA-23.11.2_Edge_Chrome_Extension.zip` | Extension and companion 1.3.1. `UPDATE_EXTENSION.cmd` updates an installed one (1.0 to 1.3.0); `INSTALL.cmd` is for new installs. |
| `.sha256` files | The SHA-256 of each zip. |

The zips are named after the version they install. That way they cannot be confused with the 23.11.1 zips, which
used the names of the "23.10.0" request. No administrator rights or internet are needed. Your data, and what the agent
learned, are backed up first and kept. If a step fails, the previous version is restored automatically.

## The fault and two more found behind it

| Fault | Root cause | Fix |
|---|---|---|
| "First Name; muhammad" ignored; all fields asked again | An answer was only read in the form `Label: value`, and nothing understood was kept between messages. | The answer is read field by field, in the person's own form. It is kept, confirmed ("Got it: First Name = muhammad."), and only the missing fields are asked. "pre pare it" asks again for those only. |
| "Zip/Postal Code: 54000" stopped as a secret | The secret check treated any `code: <digits>` as a one-time code. | A code after an ordinary word (postal, zip, product, order ...) is not a secret. OTP, driver codes and PINs are still stopped. |
| The order then stopped: "The signed-in identity has not been verified from the application" | Identity was verified only from the user name the application shows, and Swag Labs never shows it. | The agent submits the saved account's sign-in, and the application accepts it. That is now enough, but only for operations no role restricts, and never in Production mode. Role-restricted operations still need the role the application shows. |

The details are in ROOT_CAUSE_ANALYSIS.md.

## Test results

* **The reported conversation on the stand-in shop.** The shop has the same checkout as Swag Labs and, like it, never
  shows the user. The conversation was "checkout an order", "First Name; muhammad", "pre pare it", "last name Ali",
  "54000", then Confirm & Execute. The order was placed and the application gave it the number ORD-1002. This passed
  through the agent, and passed again typed into SkyGuard's AI Agent page in Chromium (screenshots in `evidence/`).
* **Full suite on Linux: 997 passed, 0 failed**, 120 skipped.
* **Installer:** apply, verify, rollback and apply again on 13 of 13 earlier releases, 23.11.1 included (25 passed).
* **Setup under Wine** over a real 23.11.1 installation: "Patched 23.11.1 -> 23.11.2", and every check passed. The OCR
  check gives a WARN under Wine, as before.
* **Windows CI on pull request #56:** the last two runs passed all 14 of 14 jobs. That includes Setup.exe on real 23.3.35, 23.11.0 and 23.11.1 installations, and the full suite on Ubuntu (3.11 to 3.13) and Windows (3.11 to 3.13). In the first two runs the Windows 3.13 full-suite job failed in 23.11.1's tests that use the stand-in transport application; the tests of this fix passed there. That failure is intermittent and its cause is not known yet (TEST_REPORT.md, section 5).
* See TEST_REPORT.md.

## Not verified

* **Swag Labs itself.** Run *Finish lab* once after installing, and answer the questions as you did.
* Microsoft Edge and Google Chrome on Windows, a live local Qwen, and Windows 11 itself, as in 23.11.1.

## Behaviour changes to know about

* On an application that never shows the signed-in user, an operation that no role restricts now runs after the
  application accepts the agent's sign-in. The run log says *"identity confirmed by the application accepting the
  sign-in"*.
* A field's name must be one the page uses ("Last Name"), or one of its parts ("Zip", "Postal Code"). Nicknames such
  as "surname" are not understood (KNOWN_LIMITATIONS.md).
