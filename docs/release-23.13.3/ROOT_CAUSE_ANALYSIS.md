# Root cause analysis: what 23.13.3 changes and why

| Reported | What was found in 23.13.2 | Cause | Change in 23.13.3 |
|---|---|---|---|
| Spoken "افتح صفحة" (heard "إفتاحة صفحة") was understood as "Open the booking page" | The local AI added "booking", which was never said. | The translation prompt gave "open the booking page" as its example, and "إفتاحة" was not in the misheard word list, so the sentence always went to the model. | Literal prompt that forbids adding details; misheard spellings with and without hamza; a short command the word list understands fully is not sent to the model. |
| "Which page of Emirates do you mean? I know: Book flights, Book Flights \| Find Hotels and Car Rental \| Emirates, /ae/english/book, /ae/english." | Duplicates, full browser titles and raw addresses in the question and in the suggestion chips. | Learned screens without a heading are named by their address, and the page title was used as it is. | Page names: first part of the title, no addresses, no site name, no duplicates; offered as buttons (page and extension 1.4.3). |

Earlier: `previous-23.13.2/ROOT_CAUSE_ANALYSIS.md`.
