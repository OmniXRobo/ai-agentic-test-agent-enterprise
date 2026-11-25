# Compatibility matrix: SkyGuard QA 23.15.0

> **23.15.0:** TFS / Azure DevOps support is unchanged (Azure DevOps Online and Azure DevOps Server / TFS 2017 or
> later). The defect creation, attachments and duplicate check were run against a faithful Azure DevOps **simulator**,
> not against the real service; one defect should be created on your own TFS after the upgrade. Everything else in
> `previous-23.14.1/COMPATIBILITY_MATRIX.md` still holds.

"Tested" means run in this release's test suite against the real product. "Simulator" means a faithful stand-in built
for the tests (the protocol is real, the product is not). "Not tested" means nothing here could exercise it; it must
be checked on the intranet.

| Component | Versions | Status | How |
|---|---|---|---|
| Azure DevOps / TFS work item creation with attachments | Online, Server 2017+ | Simulator | `tests/fake_ado.py` with the `severity` field: create, attach, duplicate, mandatory-field refusal. **Create one defect on your own TFS after the upgrade.** |
| TFS process with mandatory custom fields | any | Not tested | SkyGuard sends the fields it knows; TFS's refusal is reported and the draft kept. |
| Browser (application UI) | Microsoft Edge (Chromium), Chrome | Chromium tested | The UI tests run in Chromium, the engine of Edge. |
| Browser extension | Chrome / Edge 116+ | Static + Chromium | Manifest, panel wiring and the hands-free loop are checked statically; the panel runs in Chromium in the extension tests. A desktop browser with the extension loaded is needed for the end-to-end test. |
| Microphone and hands-free listening | any | **Not executed** | No audio device in this environment. The wiring, the language choice and the request path are tested; sound must be checked on the PC. |
| Arabic transcription | Faster-Whisper `small` or larger (multilingual) | Not executed | Needs the PC's model and a microphone. An English-only model (`*.en`) cannot produce Arabic. |
| Arabic spoken replies | Piper (ar_JO-kareem-medium) / Windows Hoda | Tested with a stand-in | The Piper call, its arguments and the WAV handling are tested with a fake Piper binary; real audio is not played here. |
| Agent-driven applications | any web application taught by the user | Fixture sites | The saucedemo and transport fixture sites exercise teaching, previews, batches and secrets. Your internal transport application must be taught on the intranet. |
| Windows | 10 1909+, 11 | Not run here | Installer logic tested on Linux; the Windows wrappers are unchanged since 23.13.3 and covered by the Windows CI job. |
| Python | 3.13 (application runtime) | Tested | |
