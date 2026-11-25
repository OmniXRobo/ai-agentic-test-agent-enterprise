# Known limitations: 23.11.2

These are stated plainly so that nobody has to discover them.

## Where it was tested

* **Stand-in applications, not your own.**
  * Tests A to H ran against two applications written for the tests:
    * an online store with the checkout of the reported case (`tests/shop_site.py`);
    * a transport application with the full multi-role trip process (`tests/transport_flow_site.py`). In it an Entry
      Operator creates, a Manager approves, an Authorizing Officer authorizes, the Driver authenticates with a code,
      and the Entry Operator records. It also has generated trip numbers, server-side role checks, a one-time code at
      sign-in for one officer, and the rules on date, driver and route.
  * Your Online Store and your transport system were not available here. Before relying on a task, use
    **Test learned knowledge**, then try it once on a test environment.
* **Browsers.**
  * Everything in the browser ran in Playwright's Chromium, in new-headless mode, on Linux.
  * Microsoft Edge and Google Chrome on Windows were not available here.
  * The Windows CI job installs and checks the extension and the cumulative upgrade on a Windows runner (see
    TEST_REPORT.md for its result). It does not drive a person's Edge window.
* **Local AI model.**
  * The local Qwen model was not running here. Commands were tested in two ways:
    * without a model (the deterministic router);
    * with a stand-in model that always picks the "cancel" task, like the reported answer. This shows that a wrong
      model choice is refused.
  * How the real Qwen phrases its answers on your PC was not tested.
* **Code signing.** Setup.exe is not code-signed, so Windows SmartScreen may warn. See INSTALLATION_GUIDE.md.

## The missing-information follow-up (23.11.2)

* **Swag Labs itself was not used.** The answers were tested on the stand-in shop (`tests/shop_site.py`), which has
  the same three checkout fields and the same pages, and does not show the signed-in user, like Swag Labs. Before
  relying on *Finish lab*, try it once.
* **Field names must be recognisable.** An answer is matched to a field by the field's label, its parts ("Zip",
  "Postal Code") or the page's own field name. A nickname the page never uses ("surname" for "Last Name") is not
  understood; use the name the agent asked for, or give the values in the order asked ("Muhammad; Ali; 54000").
* **Two words for three fields are not split.** "muhammad ali" when three fields are asked gives no value, because
  which word belongs to which field would be a guess. Name the fields or separate the values with `;` or `,`.
* **Identity on applications that never show the user.** On such an application, an operation that no role
  restricts runs after the application accepts the sign-in the agent made with the saved account. That shows the
  application accepted those credentials; it is not the same as seeing the name on the page. A role-restricted
  operation, and any operation in Production mode, still needs the identity and role shown by the application, and
  stops otherwise.
* **The local Qwen model** was not running here; the answers are read without a model, so the result does not depend
  on it.

## What the agent does not do yet

* **One task per command.**
  * "Create a trip, then submit it for approval" is not split into two tasks. Send the second command when the first
    one has finished; the suggestions offer the next step.
  * A preview can hold several records of the same task, for example "approve the selected trips".
* **Commands do not wait for another person.** When the next step needs another role, the agent signs in with that
  role's saved account. If no such account exists, it asks you to add one. It does not wait for a person to come and
  do it.
* **Rules need two sightings.** A rule seen once is a candidate and is not enforced until it is seen again or an
  administrator confirms it. Until then, the application itself refuses wrong values, and the agent reports the
  application's message.
* **Single observation of a flow.**
  * A task seen once is usable, and is marked "uncertain" in Applications & Knowledge.
  * Its roles come from the accounts seen doing it. A visible button is never taken as permission.
* **Pages that change by script only.** A page reached only through script (no address change) is learned as a step of
  the task, not as a page of its own, so "open the … page" may not find it.
* **Learning speed in the extension.** The extension sends a page view about one second after a click. When a person
  presses two buttons within a second (Continue, then Finish), the middle page may not be recorded. The task is still
  learned from the actions and the final page.
* **"Add to cart" may be learned as a task.** When you add a product while learning, "Add product" may be learned as
  well as "Checkout order". Mark it wrong in Applications & Knowledge if you do not want it.

## Extension

* **Site permission.** The extension asks for permission to read a site when you press Start learning (or Grant access).
  A company policy that blocks this blocks learning in the extension. SkyGuard's own learning window is not affected.
* **Queue while the companion is offline.** While the companion cannot be reached, at most 500 actions and pages are
  kept. Older ones are dropped first. They are kept in memory, so closing the browser during the outage loses them.
