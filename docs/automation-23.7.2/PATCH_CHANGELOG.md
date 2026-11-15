# SkyGuard QA 23.7.2: changelog

Cumulative: includes 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0 and 23.7.1 (their notes are under `previous-*`).
Installs on 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0 or 23.7.1. No database change.

## AI-Driven Automation: complete scripts

- Every Test Case step is automated, from the sign-in to the last step, with the precondition set up first. See
  ROOT_CAUSE_REPORT.md.
- New `automation/framework/steps.py`: reads manual steps, expected results and the precondition into intents and
  checks (no AI needed).
- New `automation/framework/walker.py`: performs the steps in a headless browser to find the element of every step on
  the page that is on screen, including pages discovery cannot reach (behind sign-in, cart, multi-page forms).
  When no browser can be used, the AI maps the steps on the discovered elements as before.
- The AI is asked one step at a time (current page's elements only) for a step the patterns cannot read; the
  one-request mapping is used only for steps still open, merged per step.
- Steps naming something the application does not have are written as `not_on_application("...")` steps with the
  reason and the fields that are there; reported as NOT AUTOMATED at run time; never mapped onto another element.
- A Test Case step can no longer disappear from a script: missing steps are flagged.
- Business methods on Page Objects for multi-action steps on one page (`login`, `enter_checkout_information`);
  start page with a sign-in form named `LoginPage`; `checkout_step_one_page` style names.
- One business step per Test Case step, titled with the step text, `tc_step` numbered, precondition first; Flow Logic
  matches the Test Case and shows NOT AUTOMATED steps.
- Checks from what really happened: field values, the URL after navigation, a cart badge that appeared, the
  confirmation heading.
- Credentials saved for the application's host are used when no application is chosen, for generation and Play.
- Setting `automation.live_step_walk` (default `true`) switches the browser walk off.

## Framework runtime (refreshed in every workspace automatically)

- `utilities/locators.py`: `test_id` locators match `data-testid`, `data-test` and `data-qa`.
- `utilities/reporting.py`: `not_on_application(reason)`; the step is reported NOT_AUTOMATED and the test goes on.

## Execution results

- `framework/runner.py`: NOT_AUTOMATED step status with its reason.

## Upgrade note

Scripts generated before 23.7.2 are not changed by the patch: regenerate them (Script Inventory > Regenerate /
Update). Step titles of regenerated scripts are now the Test Case step texts.
