# Changelog: SkyGuard QA 23.13.3 and extension 1.4.3

Cumulative on 23.13.2 (commit `89a6429`, PR #61). One upgrade for every release from 23.3.35 to 23.13.2. Everything
works offline. The database stays at 22 migrations. Earlier changes: `previous-23.13.2/`, `previous-23.13.1/` ...

## Arabic "open page" on the AI Agent page

* Spoken Arabic is translated literally. "افتح صفحة" (open page), also when the recognizer writes it as
  "إفتاحة صفحة", becomes "open page". The local AI no longer turns it into "Open the booking page".
* A short command that the built-in word list understands completely is taken as it is; longer sentences still go
  to the local AI, which is now told never to add a page, place, date or account that was not said.
* More misheard spellings of "افتح" are understood (إفتاحة, افتاحة, افتاحه, افتحه ...), with or without hamza.

## "Which page do you mean?"

* When a page is not named, the question lists the learned pages as a person names them: "Book flights, Manage
  booking". Web addresses (/ae/english/book), the "| Find Hotels ... | Emirates" tail of page titles and the same
  page twice are left out.
* The pages are offered as buttons under the question, with "Open the start page". One click opens the page.
* For an Arabic voice request the question is spoken in Arabic.
* The same clean page names are used in the suggestions under the chat.

## Asked and told in the language you spoke

* An Arabic request is answered in Arabic: the reply also shows the Arabic sentence that is spoken, and "Which page do
  you mean?" is asked in Arabic ("أي صفحة تقصد؟ ..."). An English request is answered in English.
* After a spoken request, the agent also says what happens next, in the same language: the preview is ready, the task
  is done (with its result), it failed (why is on screen), or it waits for you to finish a step.
* A page button pressed under a spoken question is answered in the language of that question.

## Websites whose address changes on every visit

* Some websites change part of the address every time (abc.com/12345 one day, abc.com/by123 the next). Those parts
  (numbers, codes with digits, long tokens, session folders) are now treated as "changes every visit": the page is the
  same page and the website is the same application.
* A learned page or task step is opened with the changing part of the page open now (learned abc.com/12345/booking,
  open now abc.com/by123/home: abc.com/by123/booking). When it cannot be rebuilt, the start page is opened instead of
  an address that no longer exists. The agent no longer asks you to teach it again because the address changed.
* Connect & Learn with the new address (or with / without "www.") finds the application already learned.

## Browser extension 1.4.3

* The side panel shows the same page buttons under the question, shows the Arabic answer to an Arabic request, and
  says what happens to the task (preview ready, done, failed, waiting) in the language spoken. The companion adds
  `POST /v1/voice/line` for the Arabic wording.
* A page it opens on a site with changing addresses uses the address part of the tab open now. Update: `UPDATE_EXTENSION.cmd`, then Reload.

## Where the SkyGuard QA folder is

`INSTALL_ARABIC_VOICE.cmd` and `GET_ARABIC_VOICE.ps1` are in the SkyGuard QA application folder, by default
`%LOCALAPPDATA%\Programs\AIAgenticTestAgent\app` (paste it into the File Explorer address bar). When Setup could not
write the original folder it is `%LOCALAPPDATA%\SkyGuardQA\app-23.13.3`. README_FIRST.txt says this too.
