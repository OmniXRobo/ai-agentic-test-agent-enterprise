# SkyGuard QA 23.7.0: why the agent felt immature, and what changed

## What was wrong in 23.5.0 and 23.6.0

The AI Application Learning & Operations Agent could only learn in three ways, and each one needed a person to
*teach* it:

| 23.5.0 / 23.6.0 learning mode | What the person had to do | Why it felt immature |
|---|---|---|
| Discovery | Nothing, but it only visited pages | It saw screens and fields, but never a task, a status, a rule or a role. |
| Guided learning | Press *Start guided learning*, do one task, press *Finish*, name it | Learned one task at a time. Every task had to be announced and demonstrated separately. Statuses, approvals and who-does-what were never connected. |
| Describe it (chat) | Type the rule in words | The person tells the agent the rules, which is the opposite of what was asked. |

The chat could run approved workflows and accept rules in words, but questions such as *"what happens after a trip is
approved?"* or *"who can authorize?"* had nothing to answer from. The local LLM was asked to fill that gap and could
only guess.

So the problem was not the LLM. It was **the way of understanding**: the agent had no record of what the person did,
so there was nothing to understand from.

## What 23.7.0 does instead: watch, mine, answer

```
 you use the app normally         the agent, on this PC
 ─────────────────────────        ───────────────────────────────────────────────────────────────
 sign in, open screens,   ──►  1. JOURNAL      every page (fields, buttons, tables, messages) and every
 create, approve, search,         (observe)     click / choice / typed value, secrets never stored
 get errors, sign out                                │
                                                     ▼
                               2. MINER        rebuilds the application model from the journal:
                                  (understand)  screens · tasks (flows) and their steps · what success and
                                                failure look like · statuses and lifecycles · business rules
                                                from the app's own messages · roles and who did what ·
                                                navigation · records and their last status
                                                     │
                                                     ▼
                               3. KNOWLEDGE    findings become versioned knowledge entries (Discovered /
                                  (propose)     AI suggested / awaiting approval). Nothing runs until approved.
                                                     │
                                                     ▼
                               4. ASSISTANT    chat answers from the model (how, who, what next, which
                                  (chat)        rules, which fields, status of T100, how many planned…);
                                                anything else: retrieval over the model, optionally phrased
                                                by the local LLM; a request for a learned task offers
                                                "Approve and run" once
```

### How each kind of knowledge is found, without anyone naming it

| Knowledge | How the miner finds it |
|---|---|
| **Screens** | URL path templates (`/trips/17` and `/trips/42` are one screen `/trips/{id}`) plus the page heading. Each is classified: sign-in, verification, form, list, search, refused, confirmation, page. |
| **Tasks (flows)** | A *commit* is a click or submit that sends data (Save, Create, Approve, Authorize, Submit…). The values typed and chosen before it become the flow's parameters, in screen order; the clicks become its steps. A click that changes nothing (a "+" stepper, "Add passenger" opening a row) stays a step, not a task. |
| **Outcome** | What the application showed next: a success message or a redirect to the list is *success*; a message on the same form is *failure*; a "not allowed" page is *refused*. |
| **Statuses and lifecycle** | A list column whose values change after a row action (Planned → Approved after *Approve*) is a status column. Records are tracked by their key column (Trip No) across pages, which gives the order of statuses and which task and role moves a record from one to the next. |
| **Business rules** | The application's own error messages, matched to the field whose value they repeat: *"already exists"* → unique; *"not available at that time"* → no overlap (the booking length is estimated from what was refused and accepted); *"in the past"* → date today or later; *"is required"*; *"at least / at most"*; formats. Also the screen's own constraints (required, min/max, pattern, options). |
| **Roles** | The signed-in identity and role shown by the application (header, profile), which users did which tasks, which buttons each role was offered, and which pages refused it. |
| **Sign-in** | The sign-in page, its fields and the button, without the password. |

Every finding has a stable fingerprint, so watching more never proposes the same thing twice, and seeing a task done
consistently two or more times moves it straight to *awaiting approval*.

## Framework and LLM decision

**Kept:** the local `llama.cpp` `llama-server` with a Qwen GGUF model (OpenAI-compatible API), the stdlib-only agent
core with its SQLite knowledge database, Playwright for SkyGuard's own browser and the MV3 extension with its local
companion. **No new dependencies.** It stays offline on Windows 11 with no administrator rights.

**Changed:** the way of understanding, from "a person teaches it" to "it watches and mines". The understanding is
deterministic code, not the LLM.

Why not change the LLM or adopt an agent framework:

| Option | Verdict | Reason |
|---|---|---|
| A bigger local LLM | Not the fix | Without a record of what the person did there is nothing to reason about. A 70B model guessing is still guessing, and needs a GPU most user PCs do not have. |
| LangChain / LlamaIndex / CrewAI | Rejected | Adds hundreds of packages to an offline, no-admin install, and the hard part (turning clicks and pages into flows, statuses and rules) is not something they provide. |
| Ollama instead of llama.cpp | Not needed | Same engine underneath; the current one is already bundled and pinned by SHA-256. Ollama's Windows installer also wants a service. |
| Cloud LLM | Rejected | Breaks the offline requirement and would send application screens off the PC. |
| **Deterministic mining + retrieval + optional local LLM** | **Chosen** | Accurate answers grounded in what was actually observed, traceable to evidence, testable, fast on a normal CPU. The LLM is used only to word flow purposes and phrase answers to open questions, with page text wrapped as untrusted data. |

**Model recommendation (unchanged engine):** Qwen3-8B Q4_K_M (about 5 GB) on a PC with 16 GB RAM; Qwen3-4B
Q4_K_M on 8 GB. Learning and the deterministic answers work with **no model at all**; the model improves only the
wording of open-ended answers.

## What a person still decides

- Nothing the agent learns by watching runs until someone approves it once (Knowledge tab, or *Approve and run* in
  chat). Rules found from application messages start as *Discovered* and block a record only after approval.
- Before any change in the application, the agent still shows the preview and waits for confirmation.
- Passwords, one-time codes and CAPTCHAs stay with the person and are never captured.
