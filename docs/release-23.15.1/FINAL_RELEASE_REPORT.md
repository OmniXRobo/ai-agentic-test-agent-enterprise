# Final release report: SkyGuard QA 23.15.1

| Item | Status |
|---|---|
| Test Cases page "Generate test cases with AI" with a local model | Fixed: answer schema sent as a grammar (as AI Test Design), lenient JSON repair, each case validated on its own (ROOT_CAUSE_ANALYSIS.md) |
| Generation speed | Faster with the same quality rules: ids and step count enforced by the grammar, short texts, review of flagged cases only |
| Companion on Windows | Refused request read before answering (from the 23.15.0 CI) |
| Upgrade path | 23.3.35 to 23.15.0 (23.15.1 again repairs); no database change, no new dependency, extension 1.5.0 |
| Tests | TEST_REPORT.md |
| Security | SECURITY_REPORT.md: no high finding |
| Not verified here | the person's own local model (simulated llama-server used), Windows wrappers (CI) |
