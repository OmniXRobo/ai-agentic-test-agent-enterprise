# Patch 23.3.8: test case generation no longer fails with "The AI server returned HTTP 400"

Cumulative: one patch for a PC on 23.2.2 or 23.3.0 to 23.3.7. It includes everything from 23.3.7 (Faster-Whisper
speech recognition, voice agent) and 23.3.0 to 23.3.6. An installed Faster-Whisper and its model are kept.

## What went wrong

Functional Testing test cases for US1177 (10 acceptance criteria, 5 AI calls) failed on the first call (AC03 to
AC07) after about 4.5 minutes with "The AI server returned HTTP 400."

1. Each call sends the instructions, the JSON schema (about 800 tokens), the story description, the other criteria
   and the titles of every existing test case, and asks for an answer of up to 2,800 tokens. On a local
   llama-server whose context window is small (`-c 4096`, or `-c 8192` shared between 4 parallel slots in newer
   builds) prompt plus answer does not fit. The model writes until the context is full (the 4.5 minutes) and the
   JSON is cut off.
2. The gateway then asked the model to "repair" the reply by sending the whole cut-off answer back with the
   original prompt. That request is larger than the context window, so llama-server refused it with
   HTTP 400 ("the request exceeds the available context size").
3. HTTP 400 was not one of the errors that split a call into one criterion per call, and the message hid what the
   server had said.

The simulated llama-server in `tests/test_suite_cases_context.py` reproduces exactly this on 23.3.7 and passes on
23.3.8.

## What changes

* **Requests fit the context window.** The AI gateway reads the model's context size from llama-server
  (`GET /props`, per slot), from an HTTP 400 that reports it, or from an answer that stopped because the context was
  full. It lowers the answer limit so prompt plus answer fit, and refuses a prompt that can never fit before
  sending it, instead of after minutes. `ai.context_tokens` in settings.json sets the size by hand (0 = automatic).
* **Smaller test case requests.** The story description is capped at 4,000 characters, the other criteria at 30
  and the existing test case titles at 60. The JSON schema is sent without its generated titles. With a context
  under 8,192 tokens one criterion is sent per call (`design.criteria_per_call` still overrides it), and the number
  of cases per call is limited to what the answer can hold.
* **A cut-off answer is not sent back.** It is reported as "answer was cut off" and the criteria are split one per
  call; a single criterion is retried with a compact prompt, then a minimal one (criteria and conditions only,
  one case). A reply that is invalid but complete is repaired as before, but a long reply is no longer echoed back:
  the model is asked again from the start.
* **Readable errors.** "The AI server returned HTTP 400: the request exceeds the available context size, try
  increasing it", and for a context that is too small even for one test case: "even one test case for AC03 does
  not fit the On-Premise AI model's context window (2048 tokens). Start the AI server with a larger context, e.g.
  llama-server -c 16384 ...". The server's full error body is written to the application log.
* **Retry.** HTTP 408, 429, 500, 502, 503, 504 and dropped connections are retried twice (after 3 s and 8 s). A
  server that rejects an unknown request field is retried without it.
* **Reasoning models** (Qwen3): the On-Premise request asks the chat template not to think
  (`chat_template_kwargs.enable_thinking = false`), so the answer budget is not spent on a `<think>` block.
  `ai.disable_thinking: false` in settings.json turns this off.
* **AI check** (AI settings > Test, and the end of APPLY_PATCH.ps1) shows "Context Window: N tokens".
* The llama-server that this application starts itself (Managed LLM) gets `-np 1`, so one request gets the whole
  `-c` context. The application sends one request at a time anyway.
* Test data generation (23.2.2 batching) also splits on the new "cut off" and "context" errors.

## Files

`qa_platform/__init__.py` (23.3.8), `qa_platform/ai/gateway.py`, `qa_platform/ai/managed.py`,
`qa_platform/design/suite_cases.py`, `qa_platform/design/data_agent.py`; all 23.3.7 files are included unchanged.

## If generation is still slow or fails

Start llama-server with a larger context, for example `llama-server -m <model>.gguf -c 16384 -np 1`. The AI check
shows the context the server really gives each request.
