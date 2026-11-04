# Patch 23.3.5: authorized third-party web applications by voice (milestone 8)

One patch for a PC on 23.2.2 or 23.3.0 to 23.3.4. It includes everything from the earlier 23.3 patches.

## What you can say

* "Open my e-commerce application", "Launch the CRM test portal": opens a test application configured in
  Application Credentials (the name may be said loosely: "e-commerce" finds "E-Commerce Shop"), at its login address,
  in the Guided Browser Automation session, and records. The agent only reports it open once the browser really
  shows the page. An application that is not configured is never opened; the agent lists the configured ones.
* "Go to the registration page", "Enter the customer name as John Smith", "Select United Arab Emirates",
  "Enter ZIP code 00000", "Choose standard delivery", "Submit the form", plus everything from 23.3.4.
* "What is on this page?" reads the heading, fields, buttons and links. "Close the dialog" closes an open modal.

## Safety

* Submitting a form, and clicking buttons such as Pay, Buy, Place order, Checkout, Delete, Remove, Transfer, Send,
  Confirm or Approve, asks "Please confirm" first. In test applications you list under Voice Settings, "Approved test
  applications", the agent does them without asking.
* A dialog the page opens itself (alert, confirm): an alert is acknowledged; a confirm question is answered OK only
  when you confirmed the action, otherwise Cancel, and the agent tells you what the page asked.
* After Submit the agent reports what really happened: the new page, the message shown, or the fields the
  application rejected ("the form was not accepted: Customer name: Please fill out this field").
* Only the guided session's own page is driven. A link that opens a new tab is reported, and the agent stays on its
  page. Buttons inside an open modal are found first. An element the page re-renders is found again.
* If the browser cannot be started, the agent says so instead of claiming the application is open.

## Files (in addition to 23.3.4)

qa_platform/automation/guided_voice.py, qa_platform/voice: `commands_guided.py`, `intents_guided.py`, `intents.py`.
qa_platform/config.py and api/voice.py (approved_test_apps), web/js/pages/voice.js (Approved test applications).
