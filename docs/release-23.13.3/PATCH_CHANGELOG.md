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

## Browser extension 1.4.3

* The side panel shows the same page buttons under the question. Update: `UPDATE_EXTENSION.cmd`, then Reload.

## Where the SkyGuard QA folder is

`INSTALL_ARABIC_VOICE.cmd` and `GET_ARABIC_VOICE.ps1` are in the SkyGuard QA application folder, by default
`%LOCALAPPDATA%\Programs\AIAgenticTestAgent\app` (paste it into the File Explorer address bar). When Setup could not
write the original folder it is `%LOCALAPPDATA%\SkyGuardQA\app-23.13.3`. README_FIRST.txt says this too.
