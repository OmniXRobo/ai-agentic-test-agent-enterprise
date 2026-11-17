# Source and architecture audit (23.9.0 → 23.10.0)

## Source available

* The full git history from before 23.3.35 to 23.9.0 (`33b3a50`) is in the repository. Every release in
  RELEASE_INCLUSION_MANIFEST.md was extracted with `git archive` from its own commit and used as an installation
  baseline in the tests. No source was missing, and no source was reconstructed.
* Baseline test run on the unmodified 23.9.0 tree (Linux, Python 3.12, Chromium): **841 passed, 105 skipped, 0 failed**.
  Most of the skips are tests that need Windows, real Chrome or Edge, a local Qwen server or JMeter.

## Components

```
qa_platform/            SkyGuard QA server (FastAPI) and its modules
  api/                  HTTP API; agent_simple.py (new): the simple AI Agent API
  opsagent/service.py   runs the agent in SkyGuard: browser sessions, watching, jobs, settings
  comparison/           Integration Comparison: workspace, filters (FilterSession), extract_web (WebExtractor),
                        OCR, mapping, results; guided_nav.py (new): the live recorder
  automation/browser.py one window policy for every visible browser (23.9.0)
skyguard_agent/         the agent core, standard library only, shared by SkyGuard and the extension's companion
  agent.py              OperationsAgent: chat, observe, the engine and the knowledge base
  autopilot.py (new)    the simple agent: connect, trust what was learned, plain-language commands, tasks
  navrec.py (new)       navigation recordings: conversion, editing, import checks
  js/agent_dom.js       the page reader and observer (the same file is in the extension)
  engine (executor.py), knowledge.py, journal.py, understanding.py, rules.py, vault.py, secrets_guard.py, audit.py
web/js/pages/           single-page UI; ai-agent.js (new), integration-workspace.js (guided recording panel)
browser-extension/      MV3 extension (background, side panel) and the local companion (127.0.0.1 HTTP API)
packaging/              offline package builder, cumulative patch (install_patch.py), Setup wizard (setup/)
```

## Findings and decisions

| Area | Finding in 23.9.0 | Decision in 23.10.0 |
|---|---|---|
| AI Agent | The agent page had many tabs: knowledge states, approvals per item, locators, rules, workflows to name. Learning needed approval of each item before use, which is the complexity the request asks to remove. | A simple layer (`Autopilot`) over the **same** engine. It adds no second engine. What was seen to succeed is trusted automatically and marked verified or uncertain; AI suggestions stay suggestions. The full page stays for administrators. |
| Plans and execution | The engine already turned a request into a structured plan: validated workflows, records, a preview hash, confirmation, checkpoints, and the *unknown* status after an unverified submit. Qwen proposes. It never executes shell, Python or JavaScript. | Kept. The simple agent rewrites plain language into the engine's labelled request form and can name the workflow. The engine still validates everything. Consequential operations also need an acknowledgement, enforced by the server. |
| Comparison navigation | Steps had to be typed (`click Reports`). The extractor already read every page (Next, numbers, Load more, scrolling) with loop and duplicate protection. | Guided recording produces the same step format, so the extractor is unchanged. Pagination is noticed but not replayed. Master-detail is new and uses parameterized addresses built from each row's key. |
| Browser automation | Three places read pages and record actions: the agent (agent_dom.js), the extension (a copy of agent_dom.js), and the comparison extractor. | agent_dom.js stays the single reader and observer, and its copies are checked to be identical. The comparison recorder uses it too. One converter (`navrec`) serves SkyGuard and the extension. The extractor keeps its own table reader, which is proven on the comparison's own tests. |
| Extension | Chat went straight to the full agent. There was no comparison recording. | The chat uses the simple agent. The comparison recorder saves a file that SkyGuard imports after checking it again. No new permissions. |
| Installer | `install_patch.py` was proven over 23.3.35 to 23.8.0 and had automatic rollback. Only .cmd and .ps1 wrappers existed. | Extended to 23.9.0 (and 23.10.0 as a repair), with stricter refusal messages, `detect` and `--log`. Added a Setup wizard that runs this same installer with the application's own Python, so there is one installation logic. |
| Data | Main database at 19 migrations. Knowledge database at schema 2. | Main database unchanged. Knowledge database gets `conversation_state` (`IF NOT EXISTS`), created by the installer and at start. Rollback needs no database restore. |

## Rules kept

* Read-only comparison: change-like steps are refused when recorded, edited or imported.
* No credentials in prompts, learning memory, scripts, screenshots or logs. Passwords arrive from the page as
  `[secret]` and are dropped. Typed secrets are intercepted. Credentials stay in Windows Credential Manager (DPAPI).
* MFA, CAPTCHA and application permissions are never bypassed: the run pauses for the person.
* No machine-wide changes: no machine registry keys, system PATH, services or protected folders.
