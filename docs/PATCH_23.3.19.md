# Patch 23.3.19: Data button opens its column list

Fix for 23.3.18. In Guided Browser Automation > Edit Script, the **Data** button next to a step value showed only
its tooltip:

* The column list was drawn, but the card style of the dark theme made it part of the page flow, so it appeared
  below the whole page, out of sight. It is now placed right under the button (above it near the bottom of the
  window), with a solid background, and follows the button when the page scrolls.
* After typing in a Value box, the first click on **Data** or **Rule** was lost (the table was redrawn as the box
  lost focus). The click now always opens the list or the Rule dialog, and the typed value is kept.

Using it: import the Excel / CSV in the Test data card, click **Data** next to the step, pick the column (for
example `id`). The value becomes `{{DATA:id}}` and the hint below shows row 1's value. Save the script.

Files: web/js/pages/automation.js, qa_platform/__init__.py (version, so the browser loads the new page code).
