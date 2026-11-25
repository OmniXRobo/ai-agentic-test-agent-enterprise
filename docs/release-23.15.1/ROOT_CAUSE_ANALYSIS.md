# Root cause: "The On-Premise AI did not return valid structured output" on the Test Cases page (23.15.1)

## What was reported

Test Cases > *Generate test cases with AI*: plan #1538, sprint "online store python\Sprint 1", suite Functional
Testing, story US1554 ("select 1 item"), 2 cases per criterion, *Create in TFS when done*. The run stopped with:

> No test cases could be generated for US1554: AC-01: no test cases were written (The On-Premise AI did not return
> valid structured output). Generate again for this story.

AI Test Design generated test cases for the same story with the same local model.

## The two paths compared

| | AI Test Design (`design/service.py`) | Test Cases page (`design/suite_cases.py`, 23.15.0) |
|---|---|---|
| AI call | `complete_json(..., enforce_schema=True)` | `complete_json(...)` **without** `enforce_schema` |
| What the AI server receives | `response_format: {type: json_object, schema: ...}`; llama-server turns the schema into a **grammar**: the model can only write JSON of that shape | `response_format: {type: json_object}`: any JSON object |
| Schema | `TestCaseSet` (small: ref, title, priority, category, steps) | `SuiteCaseSet` (14 fields per case, lists of objects, enums) |
| Prompt example | none | a **plain-text** example ("Title: ... 1. Enter ... => ...") |
| Reading the answer | strict JSON, the whole answer validated at once | the same |
| On a bad answer | one repair call, then fail | one repair call (also without grammar), then fail |
| Answer budget | 6000 tokens | min(1400 × criteria, 600 + 550 × cases, 4096) |

## Cause

Without the grammar, a small local model followed the prompt's plain-text example inside the JSON (steps as strings
such as `"1. Click Add to cart => The cart badge shows 1"`), gave `ac_ids` as a string, `preconditions` as a list, or
added a trailing comma. Pydantic refused the whole answer at the first such field, the repair call (again without the
grammar) came back in the same shape, and the criterion failed with `AI_OUTPUT_INVALID`. Because US1554 has one
criterion, the whole story failed. AI Test Design never met this because its answers are grammar-constrained.

## Fix

1. `SuiteCaseService._ask` sends the schema as a grammar, capped to this call: at most N cases, the criterion and
   condition ids of this call only, at least two steps, short texts (`case_set_schema`).
2. The answer is parsed with `qa_platform/ai/json_repair.parse_lenient` (fences, prose, `<think>`, trailing commas,
   comments, Python literals, smart quotes, answer cut off part way) and read by `case_set_from_answer`, which maps
   common shapes onto the model and validates **each case on its own**.
3. A cut-off answer keeps its complete cases; an unusable answer is asked for once more from the start with what was
   wrong; only then `AI_OUTPUT_INVALID` is raised, with the reasons and the beginning of the answer in the details.
4. `gateway.complete_json` falls back to the same lenient parser for every other caller.

## Why it was also slow

Every failing criterion cost two full AI calls before giving up. When the first answer was usable, the rule checks
often found unknown criterion ids, one-step cases or vague results and sent **all** cases to a review call as long as
the first. 23.15.1 prevents those errors with the grammar and reviews only the flagged cases.

## Evidence

`tests/test_testcase_generation_231510.py` reproduces the reported answer shapes (steps as text, ids as text,
trailing commas, comments) through the Test Cases API and checks that the schema is sent; before the fix the same
test gets no test case at all (the criterion is reported as "did not return valid structured output").
