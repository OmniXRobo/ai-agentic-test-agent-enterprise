# Source and architecture audit: 23.15.1

23.15.0 (commit `474d0b1`, PR #66) is the latest release; 23.15.1 is built on it, so the version goes 23.15.0 → 23.15.1.

## What 23.15.1 changes

| Area | File | Change |
|---|---|---|
| Lenient JSON | `qa_platform/ai/json_repair.py` (new) | `parse_lenient`, `close_json`: one parser for every structured AI answer |
| AI gateway | `qa_platform/ai/gateway.py` | `complete_json` falls back to the lenient parser for complete answers |
| Test Cases page generation | `qa_platform/design/suite_cases.py` | `case_set_schema` (grammar with caps and the call's ids), `case_set_from_answer` (shape mapping, per-case validation), `SuiteCaseService._ask` (grammar, lenient reading, cut-off handling, one retry), `_grammar`, review of flagged cases only; prompt says the example is content, written as JSON |
| Test data | `qa_platform/design/data_agent.py` | `parse_answer` uses the shared parser (same behaviour) |
| Companion | `browser-extension/companion/skyguard_companion/server.py` | refused request's body read before answering (from the 23.15.0 CI) |
| Version, installer | `qa_platform/__init__.py`, `packaging/cumulative-patch/*`, `packaging/setup/*` | 23.15.1; accepts 23.15.0; new verification check |
