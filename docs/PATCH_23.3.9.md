# Patch 23.3.9: faster test case generation

Cumulative: one patch for a PC on 23.2.2 or 23.3.0 to 23.3.8. It includes 23.3.8 (test case generation no longer
fails with HTTP 400) and 23.3.7-r5 (Faster-Whisper, resumable model download, reserved-port fallback).

## What the logs showed (US1177, 31 Oct, 19:13)

| Call | Prompt | Answer limit | Time |
| --- | --- | --- | --- |
| AC03 | ~1,717 tokens | 512 | 216.7 s |
| AC07 | ~1,707 | 512 | 151.0 s |
| AC02 | ~1,705 | 512 | 77.8 s |
| AC01 | ~1,708 | 512 | 150.0 s |
| AC10 | ~1,710 | 512 | 72.4 s |

* An answer limit of 512 means the AI server gives each request only about 2,300 tokens of context. That is
  llama-server sharing its `-c` between several parallel slots (newer builds use 4 by default), or a server started
  with a small `-c`. It forced one criterion per call and the shortest prompts.
* 512 tokens in 72 to 217 s is about 2.5 to 7 tokens per second: a 7B/8B model on the CPU. One test case is about
  450 tokens, so 10 criteria took 20 to 35 minutes, one call after the other.

## What changes

* **llama-server started by the application** (Managed Local LLM) now starts with:
  * `-c 16384 -np 2`: 2 slots of 8,192 tokens each, so two answers are written at the same time and each request
    has room for 2 criteria. On a CPU the time of an answer goes into reading the model from memory, so two answers
    take little longer than one;
  * `-ngl 99` when llama-server reports a GPU (`--list-devices`: CUDA, Vulkan, SYCL, ROCm, Metal);
  * `--reasoning-budget 0` (no hidden thinking text), `--cache-reuse 256` (the instructions, the same in every call,
    are read once), and `--jinja` on older builds;
  * only options the installed llama-server lists in `--help`. If it still stops while loading, it is started
    again with the 23.3.8 options (`-c 8192 -np 1`), so the AI keeps working.
  * Settings (`ai.managed` in settings.json): `parallel` (2), `gpu_layers` ("auto", or 0 for CPU only, or a
    number), `threads` (0 = llama-server's choice), `context_size` (per slot, 8192), `extra_args` (a list).
* **Two AI calls at a time.** Test case generation sends as many calls at once as the server has slots (at most
  `ai.parallel`, default 2). The cases are put together in criterion order. A server that reports one slot, or
  none, gets one call at a time as before.
* **Shorter answers.** The instructions ask for 3 to 6 steps, a one-sentence objective and short rationales.
* **Writing speed in the AI check** (AI settings > Test, and at the end of the patch): "Generation Speed: 4.1
  tokens/s (slow ...); one test case takes about 110s to write, 2 written at a time", plus "Parallel Requests" and
  "Context Window". Each AI call in the log now also shows the tokens per second.
* The patch stops the llama-server that an earlier version started (it kept running with its old options when
  the application was stopped), prints how the AI server is set up, and, with `-FastModel`, switches the model.

## Faster model (-FastModel)

`GET_FAST_LOCAL_MODEL.ps1` downloads **Qwen3-4B-Instruct-2507, Q4_K_M** (about 2.5 GB, Apache-2.0) from Hugging Face
and checks its SHA-256. It is an instruct model (no thinking text), writes good structured JSON, and is about twice
as fast as a 7B/8B model on the same CPU. `APPLY_PATCH.ps1 -FastModel` copies it to `<app>\runtime\ai\models`,
makes it the model of the managed llama-server and the AI settings, and starts it. The old model file is kept; to
go back, choose it again under AI settings > Managed Local LLM.

## What to expect

| PC | Before (US1177, 10 criteria) | With 23.3.9 |
| --- | --- | --- |
| CPU, 7B/8B model | 20 to 35 min | about 10 to 18 min (2 at a time, shorter cases) |
| CPU, -FastModel (4B) | | about 5 to 9 min |
| NVIDIA / Vulkan GPU with enough memory | | about 1 to 3 min |

These are estimates from the measured speed. The Generation Speed line of the AI check gives the real figure for
this PC: about 450 tokens per case divided by tokens per second, for each pair of cases.

## If the AI server is not started by the application

The patch says so ("the application does not start the AI server itself"). Start your own llama-server with, for
example:

    llama-server -m <model>.gguf -c 16384 -np 2 -ngl 99 --reasoning-budget 0 --cache-reuse 256 --port 8080

(leave out `-ngl 99` without a GPU). The application uses 2 calls at a time when the server reports 2 slots.

## Files

`qa_platform/__init__.py` (23.3.9), `ai/gateway.py`, `ai/managed.py`, `design/suite_cases.py`,
`design/data_agent.py`; `patch_helper.py` (new commands llm-report, llm-stop-old, llm-speed, llm-model,
llm-find-model), `GET_FAST_LOCAL_MODEL.ps1`. All 23.3.7-r5 files are included unchanged.

## 23.3.10

With 23.3.9 the run showed "10 AI calls, 2 at a time" and both calls failed after about 2.5 minutes with HTTP 500
"Context size has been exceeded." The AI server in use is not the one 23.3.9 starts (2 slots of 8,192 tokens): it
reported two slots but a context under 8,192 tokens per call, and newer llama-server builds let slots share one
context, so two answers together ran out of it. 23.3.10:

* runs two calls at a time only when each gets at least 8,192 tokens, and never again after such an error;
* treats HTTP 500 "Context size has been exceeded" as "the answer did not fit": it is not retried as it is (the
  23.3.9 retry cost another 2.5 minutes); the criterion is written again, one call at a time;
* keeps the answer limit within the context that is left (a larger one made newer llama-server builds fail);
* shows the context in the run log: "10 acceptance criteria, 10 AI calls (AI context 4096 tokens per call)".

To make generation fast, the AI server needs a larger context: see "If the AI server is not started by the
application" above, or let the application start it (AI settings > Managed Local LLM, Start with the application).

## 23.3.11

The screenshots of 23.3.10 showed why it is slow on this PC:

* the AI server is the user's own llama-server on port 8080 (not started by the application), with 4 slots of
  2,048 tokens: the 23.3.9 options never applied;
* it reads the prompt at only 2 to 8 tokens per second and writes about 4 tokens per second. Each call has about
  1,700 prompt tokens, so reading the prompt alone took 3.5 to 14 minutes whenever llama-server could not reuse what
  it had read before; with 4 slots chosen in turn it seldom could.

23.3.11:

* `-UseAppServer` (and `-FastModel`) let the application start llama-server itself: the patch reads the model your
  own llama-server uses from its command line, makes it the managed model, stops your llama-server and the
  application starts it with **one slot of 8,192 tokens on a CPU** (two with a GPU). With one slot llama-server keeps
  the previous request and reads only the part of the next prompt that changed. Your server is only stopped when the
  application has a model and llama-server.exe to start; otherwise it is left running.
* The test case prompt is ordered so everything that is the same for every call of a story (instructions, schema,
  story, all criteria, existing cases) comes first and the criterion of the call last; the number of cases is at the
  end too. Each call after the first reads about 150 to 300 new tokens instead of about 1,700.
* `parallel` of the managed server is now "auto": 1 on a CPU (reading the prompt keeps the CPU busy, so two slots
  only halve each other), 2 with a GPU.
* A MANIFEST.json with a UTF-8 BOM no longer stops the patch with "Unexpected UTF-8 BOM".

Expected on this PC (4 tokens/s writing, slow prompt reading): the first call of a story still reads the whole
prompt once (a few minutes), every next call about 1 to 2 minutes (one case of about 350 tokens at 4 tokens/s plus a
short read). With -FastModel (4B) roughly half of that. A GPU is the only way to make it fast.
