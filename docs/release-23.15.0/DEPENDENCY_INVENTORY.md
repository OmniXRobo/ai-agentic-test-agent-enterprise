# Dependency inventory: SkyGuard QA 23.15.0

> **23.15.0:** no new dependency and no new download. Everything added uses the Python standard library
> (`difflib`, `re`, `sqlite3`, `hashlib`) and packages already shipped with SkyGuard QA (FastAPI, pydantic, httpx).
> The browser side uses no library at all (plain ES modules, the Web Audio API and the browser's speech synthesis).

| Feature | Needs | Shipped in the patch? | Notes |
|---|---|---|---|
| Create TFS Defect from every result | the existing TFS connection | yes (code) | Attachments go through the existing publisher. No new API version is required of TFS. |
| Readable defect body | standard library | yes | |
| Defect evidence attachments | the run's own evidence folder | yes | Nothing is copied outside the data folder. |
| Workflow Operator memory (values, prompts) | the agent's knowledge database (SQLite) | yes (migration 3) | Three tables: `remembered_values`, `remembered_prompts`, `app_secrets`. |
| Secrets learned while teaching | the existing credential vault (Windows Credential Manager, DPAPI fallback) | yes (code) | The same vault the accounts already use; no new store, no new key material. |
| Batch requests (one record per day) | standard library | yes | |
| Arabic fuzzy matching, page near-match | `difflib` (standard library) | yes | Works with the local AI switched off. |
| Arabic transcription (second pass) | the speech model already on the PC (Faster-Whisper `small` or larger, multilingual) | **kept, not shipped** | An English-only model cannot produce Arabic; Setup reports which model is installed and never downloads one. |
| Arabic spoken replies | the offline Arabic voice already installed (Piper Kareem / Windows Hoda) | **kept, not shipped** | Unchanged from 23.13.x. |
| Hands-free listening | the browser's microphone permission and Web Audio API | yes (code) | Audio goes only to the local speech recognizer on this PC. |
| Browser extension 1.5.0 | Chrome / Edge 116+ | yes | Same extension ID and permissions as 1.4.3; no new permission is requested. |
| OCR runtime | unchanged wheels from 23.9.0+ | yes (same wheels) | |
