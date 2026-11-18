# Root cause analysis: 23.11.2 (missing-information follow-up)

## The report

On Swag Labs, the learned task *Finish lab* (the checkout) needs **First Name**, **Last Name** and **Zip/Postal
Code**. The agent answered *"I still need: First Name, Last Name, Zip/Postal Code"*. The person answered
**"First Name; muhammad"**, then **"pre pare it"**. Each time the agent asked for all three fields again.

The same conversation was replayed on the stand-in online store (`tests/shop_site.py`, which has the same checkout
pages as Swag Labs: *Checkout: Your Information* with First Name, Last Name and Zip/Postal Code, then *Overview*, then
*Finish*). It failed in the same way, and fixing it showed two more faults further along the same path.

## Fault 1: the answer was not understood

**Cause.** In 23.11.1 an answer to a pending question was glued to the original request ("checkout an order" +
"First Name; muhammad") and the whole text was prepared again. Values were then read only by the structured parser
(`planner.parse_structured`), which understands `Label: value`, `Label = value` and `Label is value`. A semicolon, a
space ("first name muhammad"), or the value alone ("muhammad") gave no value. Nothing that was understood was kept
either: the next answer was glued to the original request again, without the earlier one. So the agent asked for
every field each time.

"pre pare it" holds no value at all. It only asks the agent to go on. That is correct to ask again, but only for the
fields that are still missing.

**Fix (`skyguard_agent/autopilot.py`).**

* `parse_answer` reads the answer field by field against the fields that were asked:
  * the field's name in the person's own form: the label ("First Name"), its parts ("Zip", "Postal Code", "zip code")
    and the page's field name split into words ("postalCode" → "postal code");
  * any separator or none: `;`, `:`, `=`, `-`, `,`, "is", "should be", or a space;
  * several fields in one answer ("First name: Muhammad, last name Ali, zip 12345", "my first name is Sara and my
    last name is Ahmed");
  * the value alone when one field is asked ("54000");
  * values in the order asked when every field gets one ("Muhammad; Ali; 12345");
  * a list field (for example a vehicle) takes only one of its options; anything else is reported, never guessed;
  * a value named for a field that was not asked ("last name Ali" while only First Name is asked) goes to that field,
    never to the asked one.
* An answer that only pushes the request on ("pre pare it", "prepare it please", "go ahead") gives no value.
* The values received are kept with the pending question (30 minutes, same application, as before) and added to the
  request each time. The reply says what was received ("Got it: First Name = muhammad.") and asks only for what is
  still missing.
* The task chosen for the question is kept, so an answer cannot switch to another task.
* A new request (anything that is not an answer, such as "navigate to online store") still replaces the question.
  An answer is not offered later as a suggestion.

## Fault 2: a postal code was stopped as a secret

**Cause.** With the answers understood, the prepared request contained "Zip/Postal Code: 54000". The secret guard
stops a message that contains `code: <digits>`, because that is how one-time codes and driver codes are written. A
postal code matched the same pattern, so the request was withheld with *"it looks like it contains a code"*.

**Fix (`skyguard_agent/secrets_guard.py`, `web/js/secret-check.js`, and the identical extension copy).** A "code"
preceded by an ordinary word (postal, zip, post, area, country, dialling, phone, product, item, SKU, airport, city,
currency, language, HS, tax, cost, branch, station, route, order, invoice, reference, booking) is not a secret. "the
code is 482913", "OTP code: 482913", "driver code: 7788", "pin: 1234" and "Code: 5555" are still stopped.

## Fault 3: the order stopped at "not authorized"

**Cause.** With the values accepted, the run signed in and then stopped before the checkout with *"not authorized:
The signed-in identity has not been verified from the application."* Before any step that changes data, the agent
checks that the signed-in user is the account the plan uses. It verified that only from what the application shows
(a "Logged in as ..." line, or the login name on the page). Swag Labs, like the stand-in shop, never shows who is
signed in. So no task could run there, whatever the answers. This was not visible in 23.11.1 because the stand-in shop
was only learned there, never checked out.

**Fix (`skyguard_agent/executor.py`, `sessions.py`, `authz.py`).** When the agent itself typed the account's own user
name and password into the sign-in form, and the application then left the sign-in page with no error, the sign-in
is recorded as **confirmed by the application**. (A one-time code or CAPTCHA at sign-in is still completed by the
person, as before.) This is accepted in place of a
displayed identity only when all of these hold:

* the application has no learned identity rule (if it shows a user name and it does not match, the run still stops
  with *mismatch*);
* the operation is not restricted to a role (a role-restricted operation still needs a role verified from the
  application);
* SkyGuard is not in production mode.

The run's log says which one applied: *"identity verified"* or *"identity confirmed by the application accepting the
sign-in (it does not display the user name)"*. A manual sign-in (the person signs in and presses Resume) is never
"confirmed" this way, because the agent did not submit the credentials.

## What shows the fix

`tests/test_missing_info_231102.py` (21 tests): the parser on the reported answers and other forms, and the whole
conversation on the stand-in shop in Chromium. "checkout an order" asks for three fields; "First Name; muhammad" is
received and two remain; "pre pare it" asks again for those two only; "last name Ali" leaves only Zip/Postal Code;
"54000" completes the request; the preview holds muhammad, Ali and 54000; Confirm & Execute places the order (the
shop has one more order). The authorization rule is tested with and without a role restriction and in production
mode. See TEST_REPORT.md.
