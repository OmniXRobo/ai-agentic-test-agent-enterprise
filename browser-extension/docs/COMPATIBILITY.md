# Compatibility notes: extension and companion 1.3.1 (same as 1.3.0)

## Browsers

| Browser | Supported | Tested here |
|---|---|---|
| Microsoft Edge 116 or later (Windows 10 1909+, Windows 11) | Yes. `minimum_chrome_version` is 116 in the manifest. | No. Edge was not available in the build environment. |
| Google Chrome 116 or later (Windows 10 1909+, Windows 11) | Yes. | No. Chrome on Windows was not available. |
| Chromium (Playwright build, Linux, new headless mode) | Used for the automated tests only. | Yes: `tests/test_extension_130.py`, `tests/test_extension_e2e.py`. |
| Firefox, Safari, older Edge (EdgeHTML) | No. The extension is Manifest V3 for Chromium browsers and uses the side panel. | No. |

The extension is loaded unpacked (developer mode) or pushed by an administrator policy, as before. Its ID is fixed by
the `key` in the manifest, so the companion accepts only this extension's origin.

## Companion and SkyGuard QA

* **Extension 1.3.0 needs companion 1.3.0.** The side panel calls routes that 1.3.0 adds
  (`/v1/simple/suggestions`, `/v1/simple/learn/{app}/save`, `/v1/simple/verify/{app}`, `/v1/simple/runs/{id}`).
  With an older companion those calls fail, and suggestions, Stop & save and the short preview do not work.
  `UPDATE_EXTENSION.cmd` updates both together; then press *Reload* on the SkyGuard card.
* **Agent core.** The companion carries the same agent core as SkyGuard QA 23.11.1 (`skyguard_agent`, including
  `lib/agent_dom.js`, which a test keeps identical in both).
* **Knowledge kept.** What the companion learned with 1.0, 1.1 or 1.2 stays in its data folder and is used by 1.3.0.
  The update replaces only `app\` and `extension\`; `data\` is never touched, and `UPDATE_EXTENSION.cmd -Rollback`
  puts the previous files back.
* **Recording for Integration Comparison.** Files saved by *Record for Integration Comparison* are imported by
  SkyGuard QA 23.10.0 and later (unchanged from 1.2.0).

## Permissions (unchanged from 1.2.0)

* Required: `storage`, `sidePanel`, `scripting`, `tabs`, `notifications`.
* Site access is optional and asked per site when you press *Start learning* (or *Grant access*).
* A company policy that blocks optional site access (for example `runtime_blocked_hosts`) blocks learning in the
  extension for those sites. SkyGuard QA's own learning window is not affected.

## Upgrading from earlier extension versions

| From | How | Notes |
|---|---|---|
| 1.2.0 | `UPDATE_EXTENSION.cmd` | "Learn automatically while I browse" still works; Start/Stop learning in the Learn tab is the new explicit way. |
| 1.0.0, 1.1.x | `UPDATE_EXTENSION.cmd` | Pairing, users and the credential vault are kept. |
| Not installed | `INSTALL.cmd` | See INSTALL_EDGE.md or INSTALL_CHROME.md. |

## Known limits

* While the companion is offline, up to 500 actions and pages are kept in the browser's memory, oldest dropped first.
  Closing the browser during the outage loses them.
* Two clicks within about one second (Continue, then Finish) may leave out the page in between; the task is still
  learned from the actions and the final page.
