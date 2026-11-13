# Changelog

## 1.0.0 (companion 1.0.0, agent core shared with SkyGuard QA 23.5.0)

First release.

* Manifest V3 extension for Google Chrome and Microsoft Edge 116+: side panel with Chat, Execution, Applications,
  Learn, Knowledge and Audit; Options (pairing, companion users, typing, microphone, AI and speech settings); separate
  credential page with consent; microphone page.
* Learning: guided learning by demonstration in the user's own tab, read-only discovery, AI suggestions (local Qwen),
  approval workflow with versions and rollback.
* Operations: request in plain words or voice, inspection of existing records, rules (required, pattern, allowed values,
  date, time, range, unique, no overlap, depends), preview, confirmation bound to the preview, human-like typing,
  verification of the application's own response, per-record results, pause / resume / stop / cancel remaining,
  manual intervention with learning, self-healing locators, account switching through logout and verified sign-in.
* Local companion (Python standard library): 127.0.0.1 only, origin and host checks, pairing, users and roles,
  short-lived tokens, rate and size limits, DPAPI vault, hash-chained audit, knowledge backup and restore, local Qwen
  (llama-server) and Faster-Whisper management.
* Per-user Windows installer and uninstaller without administrator rights, offline runtimes pinned by SHA-256, model
  installer from SkyGuard or offline media, prerequisite check.
