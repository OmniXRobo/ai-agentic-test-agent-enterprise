# SkyGuard QA 23.7.2: why AI-Driven Automation wrote only the first step

## What was reported

Script Inventory > Generate Automation (AI-Driven Automation, Python + Playwright + Page Object Model) produced, for
TC1532 "User completes an online purchase" (6 steps: navigate, log in, browse, add to cart, review, complete
checkout) and TC1523 "Customer can enter the required checkout information" (8 field steps), a script with the
traceability header and a single step:

```python
with step("Navigate to the online store", tc_step=1):
    swaglabs_page.open()
```

The script was marked GENERATED and its run PASSED, because opening the start page is all it did. Flow Logic showed
one step.

## Root causes

1. **The whole Test Case was mapped in one AI request, and whatever came back was saved.**
   `ScriptStudio._generate` sent every manual step plus up to 60 element selectors to the local model in one prompt
   and asked for the complete step mapping as one JSON object. The answer schema only required *at least one* step
   (`MappedTest.steps: min_length=1`). Qwen2.5-coder 3B, with the answer constrained to that schema, answers with
   the first, easiest step (a `goto` whose description it copies from the prompt: "Start URL: https://www.saucedemo.com/"
   in TC1523, "Navigate to the online store" in TC1532) and closes the array. `FrameworkBuilder.build` then accepted
   any mapping with one action: there was no check that every manual step had been implemented, and a mapped step
   without a selector was dropped with only a note ("has no element; skipped for review").

2. **The steps after the sign-in had no elements to map to.** The element inventory comes from Application
   Discovery. On saucedemo.com everything except the sign-in page is behind the login, the cart link is a script
   link, and the checkout pages are reached by filling a form and pressing Continue / Finish, which discovery
   deliberately never submits. So even a perfect model had no selectors for the inventory, cart, checkout and
   confirmation pages: steps 2 to 6 of TC1532 and all 8 steps of TC1523 could not be mapped.

3. **The Test Case precondition was never read.** TC1523 starts "on the checkout page" (precondition: "The customer
   is logged in and is on the checkout page"). The generator only read the step table, so no setup steps (sign in,
   add a product, open the cart, Checkout) were written.

4. **Locators by `data-test` waited the full timeout.** The discovery reads `data-testid`, `data-test` and `data-qa`
   as the test id, but the framework's `Loc("test_id", ...)` used Playwright's `get_by_test_id`, which only looks at
   `data-testid`. saucedemo.com uses `data-test`, so every such locator waited 10 seconds before its recorded
   alternate was tried.

## The fix (generic, not saucedemo-specific)

1. **Every step is read on its own, without the AI** (`automation/framework/steps.py`). Manual actions and expected
   results are turned into intents and checks with patterns that cover how test cases are written: navigate, log
   in, enter 'X' in the Y field, select, check, click, add to cart, open/review the cart, proceed to, complete the
   checkout/order/registration, browse/view/verify; and "the Y field shows 'X'", "logged in / redirected", "added to
   the cart", "completes successfully / confirmation". The precondition line of the description becomes setup
   intents (logged in, has items in the cart, on the X page).

2. **The steps are walked through the application in a browser** (`automation/framework/walker.py`). The
   Automation Engineering Agent opens the application headless, signs in with the saved Application Credentials and
   performs the steps in order, exactly as the test will. For each step it picks the element on the page that is on
   screen at that moment (label, placeholder, accessible name, text, test id, id), performs it and records the element
   and its page. This is how the pages discovery never reaches (cart, checkout step one and two, confirmation) are
   seen. Checks are taken from what actually happened: the value of the field, the new URL, the cart badge that
   appeared, the confirmation heading.

3. **The AI is used one step at a time.** A step the patterns cannot read is sent to the AI alone, with the elements of
   the current page only (at most 40), a request a 3B model answers well; the selector it returns must be on the
   page. The old one-request mapping is still used for steps the walk could not automate, and its answer is merged
   per step.

4. **Nothing is invented and nothing is dropped.** A step that names something the application does not have is
   kept in the script as a flagged step:

   ```python
   with step("Enter '123 Main St' in the Address field", tc_step=3):
       not_on_application("The application has no 'Address' field on Swag Labs - Checkout: Your Information "
                          "(checkout-step-one.html). Available there: First Name, Last Name, Zip/Postal Code.")
   ```

   At run time the step is reported as NOT AUTOMATED with that reason (and a pytest warning); the rest of the test
   runs. Values of missing fields are not put in the test data. The builder also refuses to let any Test Case step
   disappear: a step without an action is written as a flagged step, so a one-step answer can no longer pass as the
   automation of the whole case.

5. **Page objects get business methods.** When a step does several things on one page (sign in; enter the checkout
   information and continue), the Page Object gets one method for it (`LoginPage.login(username, password)`,
   `CheckoutStepOnePage.enter_checkout_information(first_name, last_name, zip_postal_code)`), built from the element
   methods. The start page with a sign-in form is named `LoginPage`; page modules and variables split CamelCase
   (`checkout_step_one_page`).

6. **One business step per Test Case step**, titled with the step itself and numbered with `tc_step`, the
   precondition first. Flow Logic shows the same steps as the Test Case.

7. **`test_id` locators search `data-testid`, `data-test` and `data-qa`** (framework runtime file, refreshed in every
   workspace automatically).

8. **Credentials:** when no application is chosen, the credentials saved for the application's host (for example
   `saucedemo.com`) are used for the walk and recorded on the script, so Play uses the same ones.

## TC1532 after the fix

```python
with step("Navigate to the online store", checkpoint=True, tc_step=1):
    login_page.open()
    login_page.expect_username_visible()
with step("Log in", checkpoint=True, tc_step=2):
    login_page.login(credentials.username, credentials.password)
    inventory_page.expect_url("inventory.html")
with step("Browse products", checkpoint=True, tc_step=3):
    inventory_page.expect_products_text(data["products_expected"])
with step("Add products to the cart", checkpoint=True, tc_step=4):
    inventory_page.click_add_sauce_labs_backpack()
    inventory_page.expect_shopping_cart_badge_text(data["shopping_cart_badge_expected"])
with step("Review the order", checkpoint=True, tc_step=5):
    inventory_page.click_shopping_cart()
    cart_page.expect_url("cart.html")
with step("Complete checkout", checkpoint=True, tc_step=6):
    cart_page.click_checkout()
    checkout_step_one_page.enter_checkout_information(data["first_name"], data["last_name"], data["zip_postal_code"])
    checkout_step_two_page.click_finish()
    checkout_complete_page.expect_complete_header_text(data["complete_header_expected"])
```

TC1532 has no checkout values in its Test Data, so First Name, Last Name and Zip/Postal Code get sample values
(Test, User, 12345); the generation log says so and they can be changed in the Test Data tab.

## TC1523 after the fix

The precondition is set up (open, `login_page.login(...)`, add a product, open the cart, Checkout, then a check that
the checkout page is open). Steps 1, 2 and 6 fill First Name, Last Name and Zip/Postal Code with the Test Case
values and check them. Steps 3, 4, 5, 7 and 8 (Address, City, State, Email, Phone) are flagged: saucedemo.com's
checkout form has only First Name, Last Name and Zip/Postal Code. Update the Test Case (or the application), then
regenerate. Note that step 1 enters "John Doe" as the first name, as the Test Case says.
