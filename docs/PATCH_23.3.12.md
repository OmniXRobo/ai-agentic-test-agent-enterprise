# Patch 23.3.12: test data with a small local model

Cumulative: includes 23.3.11 and everything before it.

## What the log showed (test case #1395, 2 Nov, 06:36)

* "writing Smoke Testing data rows 1 to 4 of up to 12" took 5 minutes, then "rows 1 to 1" failed after another
  minute with "The On-Premise AI did not return valid structured output."
* The model was qwen2.5-1.5b-instruct on the CPU. The request asked for the full data schema (field list, rows with
  long key names, not-applicable scenarios, gaps, preconditions, cleanup) in one JSON object. A 1.5B model writes
  such an answer slowly and often in a shape that does not match: a missing key, a list where one value was
  expected, prose around it. One mistake failed the whole answer, and the "repair" call cost as much again.

## What changes

* **JSON schema enforced by the AI server.** On-Premise requests send `response_format: {"type": "json_object",
  "schema": ...}`. llama-server turns it into a grammar, so the model can only write a valid answer and stops when
  the object is complete. A server that refuses it (HTTP 400) gets plain JSON mode. Setting `ai.constrained_json`
  false turns it off. Cloud AI is unchanged.
* **Shorter answers.** Short keys (`valid`, `breaks`, `expected`, `ac`), one-sentence texts, the field list and
  gaps only in the first call; later calls list the field names to use.
* **3 rows per call** (`design.data_rows_per_call`, was 4), answer limit 120 + 220 tokens per row (first call 450 +).
* **Lenient reading.** Code fences, prose, trailing commas and Python literals are accepted; each row is checked on
  its own and a broken row is left out; an answer cut off part way keeps its complete rows. No "repair" call.
* If the first call gives no usable row, it is asked once more for one row; a later call that fails keeps the
  rows already written and notes it in the gaps.

## Files

`qa_platform/__init__.py` (23.3.12), `ai/gateway.py`, `design/data_agent.py`; `tests/test_data_2312.py`.
