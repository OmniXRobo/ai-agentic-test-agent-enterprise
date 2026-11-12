# Compatibility: SkyGuard QA 23.5.0

| Item | 23.5.0 |
| --- | --- |
| Installs on | SkyGuard QA 23.4.0 (PR #41 final patch + PR #42). Also on 23.5.0 itself (re-apply). Any other version is refused. |
| Operating system | Windows 11 (as 23.4.0). The patch tools need Windows PowerShell 5.1, which ships with Windows. |
| Python | The application's bundled Python 3.13; no new package. |
| Main database | Unchanged: 18 migrations, no new table or column. 23.4.0 can open it after a rollback. |
| New database | `data\opsagent\knowledge.sqlite3`, schema 1, created on first start or by the installer. Ignored by 23.4.0. |
| Settings | Existing sections of `settings.json` unchanged. The agent's settings (browser, visible or headless, use of AI, discovery page limit) are a new `opsagent` section, written only when an admin changes them; 23.4.0 ignores it. |
| AI | Uses the existing On-Premise Qwen (llama-server, 127.0.0.1). Works without AI (deterministic request parser). |
| Speech | Uses the existing Voice page recognizer (Faster-Whisper or built-in). Optional. |
| Credentials | Windows Credential Manager through SkyGuard's secret store, namespace `opsagent`. Existing saved logins are untouched. |
| Browsers | The browser chosen in Preferences (Playwright Chromium, Chrome or Edge), as for test playback. |
| APIs | New `/api/ops/*` only. No existing route changed. Two new admin-only patterns. |
| UI | New menu group and page `#/ops-agent`. Existing pages unchanged (regression page test updated to expect the new route). |
| Rollback | Restores 23.4.0 files; knowledge database is kept and reused on a later re-apply. |
| Browser extension | The separate extension package uses the same agent core but does not need SkyGuard, and SkyGuard does not need it. Both can run on the same PC (different ports: SkyGuard 8765, companion 8790). |

## Target applications

Web applications reachable by http(s) that a person operates through forms, lists and buttons. Not supported:
applications whose sign-in requires a code the agent would have to read (the person completes those steps), canvas-only
or plug-in based screens, and fields that reject typed input (the run pauses for the person).
