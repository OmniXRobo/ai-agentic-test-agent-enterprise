# Known limitations: 23.15.1

Everything in `previous-23.15.0/KNOWN_LIMITATIONS.md` still applies. New in 23.15.1:

* **Not run against the person's own local model here.** The fix was verified with a simulated llama-server that
  answers in the shapes a small model writes (steps as text, ids as text, trailing commas, cut-off answers) and with
  the real request format. The speed gain depends on the model and the PC; it is largest where the review call ran
  before.
* **The grammar needs an AI server that supports JSON schema** (llama-server does). A server that refuses it answers
  HTTP 400 and the request is sent again in plain JSON mode, as before; the lenient reading still applies there.
* **Text limits.** A title is at most 140 characters, a step action or expected result at most 240; longer text is not
  possible from a grammar-constrained model. The prompt already asks for compact cases.
* **Cut-off answers.** The case written last before the cut is dropped even when it may have been complete, so no case
  with missing steps is ever kept. Generate again (or lower "Cases per criterion") if a criterion has fewer cases than
  asked.
