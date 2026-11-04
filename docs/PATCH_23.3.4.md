# Patch 23.3.4: Guided Browser Automation by voice (milestone 7)

One patch for a PC on 23.2.2 or 23.3.0 to 23.3.3. It includes everything from the earlier 23.3 patches.

## What you can say

* "Launch my application": opens the application under test in the Guided Browser Automation session (the browser
  of the automation settings, visible) and records. The address comes from this conversation, the test case's
  script or your latest discovery; otherwise the page asks for it.
* "Enter the username", "Enter the password from the configured test credentials": typed from Application
  Credentials (the credential whose login URL is this site, or the only one for the role). They are recorded as
  {{CRED:user.username}} and {{CRED:user.password}}, never as text, and the password is never said or logged.
  A password said aloud is never typed; the agent asks you to save it in Application Credentials instead.
* "Click Login", "Click Save", "Open the Products page", "Select Electronics", "Check gift wrap",
  "Select the express shipping option", "Set the ZIP code to 90210", "Enter 90210 in the ZIP code",
  "Open the calendar", "Select tomorrow" (recorded as a date rule, as before), "Scroll down", "Next page".
* Named without a value, the agent checks the field is there and asks: "Enter the product name" -> "What should I
  enter in Product name?"; "Choose the country" -> "Which Country? The options are Canada, United States."
* "Verify the confirmation message", "Verify the page shows Product saved": checked on the page and recorded as a
  check step. "Where am I?" reads the page title and address.

## How each action is checked

The element is found by role and accessible name, label, placeholder, test id or name, and the agent waits for it
while the page loads (up to the element timeout of the automation settings). After the action it waits for the page
to settle and checks the real result: the value now in the field, the option now selected, the box now checked, or
the page now shown ("Clicked Login. The page is now Shop Home."). If something is not there, it says so.

Clicks, entries and choices are still recorded by the existing recorder, and "stop recording" saves the script as
before.

## Files (in addition to 23.3.3)

qa_platform/automation: `guided_voice.py` (new), `recorder.py` (hands these actions to guided_voice).
qa_platform/voice: `commands_guided.py` (new), `intents_guided.py` (new), `intents.py`, `service.py`.
