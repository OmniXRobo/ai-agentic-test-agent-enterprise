# Patch 23.3.15: AI-Driven Automation no longer times out mapping the steps

Cumulative: includes 23.3.14 and everything before it. No database change. Your AI server, model, settings and
data are kept.

## What went wrong
Generate Automation failed for every Test Case with "On-Premise AI unavailable: The On-Premise AI server did not
answer in time." about 2 minutes after "Asking the On-Premise AI to map the steps", while the header showed
LOCAL AI READY. The AI server was working; the application stopped waiting too early:

* the whole element inventory (for example 290 selectors on 5 pages, about 6,000 tokens) was sent with every
  Test Case, even one with 2 steps; a local model on a CPU needs most of 2 minutes just to read it;
* this call still used the 2-minute request timeout (`ai.timeout_seconds`), not the generation timeout
  (`ai.generation_timeout_seconds`, 10 minutes) that test case and test data generation use;
* the answer limit was 6,000 tokens and the answer shape was not enforced, so a small model could keep writing.

## What changed
* **Only the elements the Test Case needs.** Every page is still listed (so the AI can go to any of them), but only
  the elements whose selector, role or name match the words of the Test Case title, steps, expected results and
  test data, plus the elements Intelligent Discovery mapped to its steps, up to 60. A word found on many elements
  (such as "cart" on every link of the cart page) counts less than a rare one (such as "quantity"). The full
  inventory is still used to check every selector the AI chooses.
* **Generation timeout.** The mapping call waits up to `ai.generation_timeout_seconds` (default 600 s).
* **Answer sized to the steps.** 600 + 450 tokens per manual step (at least 1,200, at most 6,000), and the AI server
  (llama-server) enforces the answer's JSON shape with at most 4 automation steps per manual step + 2. A server that
  does not support this gets plain JSON mode as before.
* **One more try with fewer elements.** If the call still times out, runs out of context or is cut off, it is asked
  once more with 25 elements before the Test Case is marked Failed.
* **Clearer log.** Each Test Case log shows e.g. "Elements for this Test Case: 58 of 290 selectors on 5 page(s);
  prompt about 2100 tokens, answer limit 1500 tokens, waiting up to 10 min".

Optional settings (settings.json, section `automation`): `ai_max_selectors` (default 60), `ai_retry_selectors`
(default 25).

## Files
* `qa_platform/automation/framework/focus.py` (new): picks the elements for a Test Case.
* `qa_platform/automation/framework/studio.py`: the mapping call uses it, with the generation timeout, sized answer
  limit, enforced answer shape and the retry.
* `qa_platform/ai/gateway.py`: `complete_json` can send the JSON schema for the AI server to enforce.

## After installing
Open AI-Driven Automation, open the failed job and press Retry on the failed Test Cases (or start the job again).
