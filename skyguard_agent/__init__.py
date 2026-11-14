"""SkyGuard Adaptive AI Application Learning & Operations Agent: the shared intelligence core.

One agent, two interfaces. SkyGuard QA (qa_platform.opsagent) and the standalone companion of the Chrome/Edge extension
(companion/) both run this package unchanged. It uses only the Python standard library, so the companion needs nothing
but a Python runtime, and SkyGuard needs no new third-party packages.

Layers (each a module):
  secrets_guard  detects passwords, OTPs and codes in chat before anything stores or forwards them
  store          the persistent knowledge database (SQLite, numbered migrations), outside any model weights
  knowledge      applications, screens, fields, workflows, business rules, relationships, learning states, versions
  vault          credential vault (Windows DPAPI / Credential Manager); the database keeps references only
  audit          hash-chained audit trail without secrets
  llm            local OpenAI-compatible client (llama-server with the installed Qwen model); replaceable
  guard          prompt-injection containment for page content
  rules          deterministic business-rule engine
  locator        adaptive DOM locator engine and self-healing
  sessions       multi-account session manager (identity and role verification, logout verification)
  authz          execution-layer authorization checks
  planner        intent parsing, batch expansion, validation and the preview (with its confirmation hash)
  executor       PARSE -> PLAN -> INSPECT -> VALIDATE -> PREVIEW -> CONFIRM -> EXECUTE -> VERIFY, checkpoints,
                 pause / manual intervention / resume / stop
  learning       automatic read-only discovery and guided demonstration learning
  journal        23.7.0: the observation journal of passive learning (what a person did and saw while browsing)
  understanding  23.7.0: screens, flows, statuses, rules and roles worked out from the journal; the app model
  assistant      23.7.0: chat answers from the app model (how / who / what next / rules / status of a record)
  agent          the facade both interfaces call
"""

__version__ = "1.1.0"
AGENT_NAME = "SkyGuard Adaptive AI Application Learning & Operations Agent"
