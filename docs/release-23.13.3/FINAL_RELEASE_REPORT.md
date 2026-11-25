# Final release report: SkyGuard QA 23.13.3 (cumulative from 23.3.35)

## What you get

| File | What it is |
|---|---|
| `SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch.zip` | One cumulative, offline upgrade to 23.13.3 for any release from 23.3.35 to 23.13.2. It holds `SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe`, `Manual_Install` (`APPLY_PATCH.cmd`), `README_FIRST.txt`, the documents, the SBOM, test results and `SHA256SUMS.txt`. |
| `SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip` | Extension 1.4.3 with its companion: the page buttons in the side panel. |

## What was asked and what is in this release

| # | Asked | Status |
|---|---|---|
| 1 | AI Agent: spoken Arabic "افتح صفحة" (heard "إفتاحة صفحة") became "Open the booking page" | **Done.** Translated literally as "open page"; nothing that was not said is added. |
| 2 | The answer listed duplicates and raw addresses | **Done.** "Which page do you mean?" lists page names only, as buttons; spoken in Arabic for an Arabic request. |
| 3 | Ask in Arabic for Arabic and English for English; say the result after the action in the same language; also in the extension | **Done.** The question and the reply are shown and spoken in the request's language; the preview, result and pauses of the task are spoken in that language too (page and extension 1.4.3). |
| 4 | Address changes on every visit (abc.com/12345, then abc.com/by123): do not ask to be taught again | **Done.** Treated as the same page and application; learned pages and task steps open with the address of today, otherwise the start page (page, tasks, extension). |
| 5 | Where is the SkyGuard QA folder? | In README_FIRST.txt: `%LOCALAPPDATA%\Programs\AIAgenticTestAgent\app`. |

## Tests

18 new tests, 302 regression tests (11 skipped), 34 installer tests (18 earlier releases), all passed.

## After installing

Run Setup (or `Manual_Install\APPLY_PATCH.cmd`), press Ctrl+F5 in the browser. Extension: `UPDATE_EXTENSION.cmd`, then
Reload on the extensions page.
