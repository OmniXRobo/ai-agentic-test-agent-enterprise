# Security report: SkyGuard QA 23.15.1

Everything in `previous-23.15.0/SECURITY_REPORT.md` still applies. 23.15.1 touches no secret, credential, network
setting or permission.

| Check | Result |
|---|---|
| bandit 1.9.4 on every module changed in 23.15.1 (`evidence/bandit-231510.json`: `qa_platform/ai/json_repair.py`, `qa_platform/ai/gateway.py`, `qa_platform/design/suite_cases.py`, `qa_platform/design/data_agent.py`, companion `server.py`) | **No high finding.** 2 medium B608 and 2 low B105 are on unchanged lines (column names fixed in code; the constant `"ai"` namespace and `"<null>"` marker). 1 low B110 is the companion's deliberate best-effort read of a refused request's body. |
| New input parsing | `json_repair` only parses text with the standard `json` decoder after regex clean-ups; it never evaluates code (no `eval`, no `ast.literal_eval`). |
| Data sent to the AI server | Unchanged content; only the JSON schema is added to the request (no story data beyond what 23.15.0 sent). |
| Network | Nothing new; the AI server is the one configured under AI settings. |
