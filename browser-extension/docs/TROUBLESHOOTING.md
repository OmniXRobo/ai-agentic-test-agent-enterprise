# Troubleshooting

Logs: `%LOCALAPPDATA%\SkyGuardAgent\data\logs\companion.log` (also `llama-server.log`, `whisper-server.log`). They never
contain passwords. Start menu > *SkyGuard Agent - Check prerequisites* shows what is missing.

| What you see | Cause and what to do |
| --- | --- |
| Options: *companion offline* | The companion is not running or uses another port. Start menu > *SkyGuard Agent (start companion)*; `SkyGuardAgent.cmd status` shows the port; enter the same port in Options and **Save**. |
| *not paired* / pairing refused | Use a fresh code (Start menu > *SkyGuard Agent - Pairing code*). A code works once, for 10 minutes, 5 tries. After 5 wrong codes show a new one. |
| The extension ID is not `fmiiaecpdgbhhhbbkkfhnjfkbmllladb` | The `manifest.json` was changed or a store copy is used. Load `%LOCALAPPDATA%\SkyGuardAgent\extension` unchanged, or add the ID to `extension_ids` in `data\config.json` (ENTERPRISE_DEPLOYMENT.md). |
| *Too many failed sign-ins. Wait five minutes.* | Five wrong passwords for that user. Wait five minutes. A forgotten password: an admin deletes the user in Options > Companion users and creates it again. |
| *Access to <site> was not granted* | Applications > **Grant access to this site**. If the browser refuses, a policy blocks the site for extensions: ask IT. |
| Load unpacked is missing or greyed out | Developer mode is disabled by policy. Use a store or IT deployment (ENTERPRISE_DEPLOYMENT.md). |
| The application is on `http://127.0.0.1:...` | Not supported: the extension keeps 127.0.0.1 for the companion. Use `http://localhost:...` or the PC's name for a local test application. |
| The run pauses with *'<field>' did not accept typed text* | The field blocks ordinary typing. Type the value yourself and press Resume, or turn on trusted typing in Options. |
| The run pauses with *Authentication required* | The application wants a one-time code, MFA, a driver code or a CAPTCHA. Complete it in the tab, then Resume. The agent never does this step. |
| The run pauses with *Multiple matching controls found* or says a control could not be found | The screen changed. Do that one step in the tab; the agent watches, continues and proposes the new location for approval. |
| A record ends *Unknown / unverified* | The application did not show a clear result. Check it in the application; the agent does not guess. |
| Chat says the message looks like a password and was not sent | Correct. Passwords go only through **Add credential**. If it was not a secret (for example a reference code), rephrase without a label like *password* or *code:*. |
| *AI is off* / requests not understood | No Qwen model or server. Run `Install-Models.ps1`, or set `llm.mode` to `connect` and `llm.url` to a running Qwen server on 127.0.0.1 (for example SkyGuard's). Simple requests work without AI. |
| **Speak** says speech is not available | No Faster-Whisper model. Run `Install-Models.ps1`. Allow the microphone once in Options. |
| Edge opens a window instead of a side panel | That Edge build has no extension side panel; the window works the same. |
| *Disable developer mode extensions* at browser start | Normal for Load unpacked. Choose Keep. |
| INSTALL.cmd: *The package is damaged or was changed* | Extract the whole zip again into an empty folder. |
| INSTALL.cmd closes at once / scripts blocked | PowerShell scripts blocked by policy. Ask IT; the scripts need only the current user's rights. |
| Port 8790 already used | Install with `INSTALL.cmd -Port 8795` and set the same port in Options. |
