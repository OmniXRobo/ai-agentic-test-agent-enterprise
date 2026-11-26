# User guide: SkyGuard QA 23.15.1

Nothing changes in how the pages are used. Everything in `previous-23.15.0/USER_GUIDE.md` still applies.

## Generate test cases with AI (Test Cases page)

1. Press Ctrl+F5 once after the upgrade.
2. Open **Test Cases**, choose the test plan and sprint, tick the suites (each suite gets cases of its own testing
   type) and the user stories, set **Cases per criterion**, and optionally **Create in TFS when done**.
3. Press **Generate test cases**. Progress shows each AI call ("writing Functional Testing test cases for AC-01",
   "reviewing AC-01 (1 issue found by the rules; 1 correct case kept as written)").
4. Leave **Thorough review (slower)** off for speed; the rules still send a case with a vague expected result or a
   summary step back for review. Turn it on to also review cases with other warnings.

If a criterion still reports "no test cases were written", the message now says why (for example the answer was cut
off). Generate again, lower **Cases per criterion**, or raise the generation timeout under **AI settings**.
