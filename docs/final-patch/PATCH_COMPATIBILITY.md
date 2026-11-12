# Compatibility, SkyGuard QA 23.4.0

| Item | Requirement |
|---|---|
| Installed version | **23.3.35** exactly (23.4.0 can be applied again). Other versions are refused before anything changes. |
| Windows | Windows 10 or 11, or Windows Server 2016 or later, with Windows PowerShell 5.1. No administrator rights. |
| Python | The application's own Python (`app\runtime\python` or `app\.venv`). No new Python packages; nothing is downloaded. |
| Disk | Free space for the patch, a copy of every replaced file and a copy of the database, plus 300 MB. Checked before starting. |
| Database | SQLite schema 16 becomes 18. Migration 17 adds the `cmp_*` tables; migration 18 adds `app_credentials.auth_method` (default `form`). No existing row is changed or deleted. |
| Rollback | 23.3.35 runs on a schema 18 database (it ignores the new tables and column), so a rollback keeps the database by default. |
| Settings | `settings.json` is kept. New keys (`ui.sidebar_collapsed`, `playback.window_mode`, `playback.monitor`, `playback.headless_recording`, `comparison.*`, `aura.*`) are optional with defaults. |
| Credentials | Saved logins and TFS PATs stay in the Windows Credential Manager. Nothing is moved. |
| Browsers | Chromium (included), Microsoft Edge or Google Chrome, as chosen in Browser Manager. Edge and Chrome must be installed to be used. |
| OCR | Windows OCR (Windows 10 or later, an installed OCR language) or Tesseract (`tesseract.exe` path in Preferences). Without either, screenshot data is entered or corrected by hand. |
| On-Premise AI | Unchanged: the local OpenAI-compatible server (llama-server). No cloud AI. AI features are optional; reconciliation results do not depend on AI. |
| TFS / Azure DevOps Server | REST API 6.0 or later as before. Field discovery needs `_apis/wit/workitemtypes/{type}/fields?$expand=allowedValues`; on older servers the payload is sent as before. |
| Generated automation frameworks | Existing generated workspaces keep working. `utilities/healing.py` gains optional context weights; old fingerprints score as before. |
