# AI Agent: user guide (23.11.2)

Open **AI Agent** in the menu. It has four tabs.

## 1. Connect & Learn

1. Enter the website's address. Give it a name if you want one, and choose the **Environment** (Test or Production).
   Use a test environment when you can: in Production, everything you do while learning works on real records.
2. Press **Connect and start learning**. A maximized Edge or Chrome window opens on the website.
3. Use the website normally:
   * sign in (your password and any code are never recorded);
   * open the pages you use;
   * do each task once, as you always do. If a task is done by several people in turn (an Entry Operator creates a
     trip, a Manager approves it, an Authorizing Officer authorizes it, the Driver authenticates, the Entry Operator
     records it), sign out and in as each of them and do their step.
4. Press **Stop & save learning**. SkyGuard works out pages, tasks, fields, statuses, roles and rules. It then tests that
   every task and page can be found again by a command, and shows the result. If something cannot be found, it says
   which one.

The agent only watches. It does not click or type while you learn. A rule seen once (for example "Vehicle is
required") is kept as a candidate. It is shown but not enforced until it is seen again or an administrator confirms it.
Showing a button is not taken as permission to use it.

**Explore (read-only)** opens the menus and pages by itself without submitting anything. It is optional.

## 2. AI Chat & Voice

Choose the website, then type or speak. Examples of what works:

| You say | What happens |
|---|---|
| Navigate to Online Store | The website opens. No plan, no preview. |
| Open the trip creation page | The learned page opens. |
| Show pending trips | The trips with that status are listed. |
| Create a trip tomorrow from Dubai to Abu Dhabi for Aslam | If something is missing: "Missing information: Vehicle (BUS-101, BUS-102, VAN-7)". Answer "Vehicle: VAN-7". |
| Approve trip TRP-0003 | SkyGuard signs in as an account with the Manager role, checks the trip's current status (read-only), then shows the preview. |
| Record trip TRP-0003 odometer end 500 fuel used 30 | Values can be given in plain words. |

### Answering "I still need: ..." (new in 23.11.2)

When a task needs values you did not give, the agent lists them, for example *"I still need: First Name, Last Name,
Zip/Postal Code"*. Answer in your own words, in one message or several:

| You answer | The agent understands |
|---|---|
| First Name; muhammad | First Name = muhammad |
| first name muhammad / First Name: muhammad / my first name is muhammad | First Name = muhammad |
| last name Ali, zip 54000 | Last Name = Ali, Zip/Postal Code = 54000 |
| Muhammad; Ali; 54000 | the three values in the order they were asked |
| 54000 (when only Zip/Postal Code is still missing) | Zip/Postal Code = 54000 |
| prepare it / go ahead | no value: it asks again for what is still missing |

Each reply says what was received ("Got it: First Name = muhammad.") and asks only for what is still missing. What you
answered is kept for 30 minutes for the same application. A new request (for example "navigate to online store")
replaces the question. For a list field such as a vehicle, the value must be one of the listed options; anything else
is reported, never guessed.

The buttons under the chat are **suggestions**. They come from what was learned, the page you are on, records waiting
for a step that your accounts may do, and your recent requests. Click one to put it in the box.

### The preview

| Action | Approve trip |
|---|---|
| Application | Transport |
| Trip No | TRP-0003 |
| Role | manager (account manager) |
| Status | Ready for confirmation |

* **Confirm & Execute** appears only when at least one record can run. For a change (create, approve, authorize,
  record, cancel, delete), tick the box first. The tick stays while the card refreshes.
* When nothing can run, you see why, for example "TRP-0005 has the status Recorded; Approve trip needs Pending
  Approval". The buttons are then **Provide missing data**, **Edit request** and **Cancel**.
* After execution, the card shows the result in the application's own words, for example "Create trip: done, confirmed
  by the application, number TRP-0003, status Pending Approval".

Reading and navigation need no confirmation. The same request sent twice returns the same preview, not a second one.

### Sign-in, codes and roles

* Each task runs as an account with the role it was learned with. You can add accounts in **Applications &
  Knowledge**; passwords go to Windows Credential Manager, never to the chat.
* If the application asks for a one-time code, or a step needs a code such as a driver's authentication code, the agent
  pauses. Type the code yourself in the application window and press **Resume**. The agent never reads, types or
  stores it.
* Some applications (Swag Labs, for example) never show who is signed in. There, the agent accepts the
  application's own acceptance of the sign-in it made with the saved account, but only for operations that no role
  restricts and never in Production mode. The run log says "identity confirmed by the application accepting the
  sign-in". A role-restricted operation still needs the role shown by the application.
* If no account with the needed role can sign in, the agent asks you to save one. If the application refuses the
  account, the task stops and says so.

## 3. Applications & Knowledge

For each website you can see:

* the pages, tasks (with their fields, roles and how often they were seen), the status flow, and the rules;
* the items that are **not verified yet**;
* the last learning session;
* **Test learned knowledge**, which checks that every task and page can be found again by a command;
* **Test learned workflow** for each task, which puts the task into the chat so you can try it;
* the role accounts.

Administrators can confirm a candidate rule or mark it wrong.

## 4. Tasks & History

Every request, its preview, the confirmation, the result, the number and status that the application showed, and the
evidence.

## Browser extension (Edge / Chrome) 1.3.1

The Learn tab has **Start learning** and **Stop & save learning** for the selected application. While learning, the
toolbar icon shows a red **REC**. If the companion is restarting, your actions are kept and sent when it is back; the
badge turns grey meanwhile. The chat has the same suggestions and the same short preview as SkyGuard.
