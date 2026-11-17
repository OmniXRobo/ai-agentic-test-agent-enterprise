# Test report: 23.10.0 and extension 1.2.0

Every result below comes from a run that actually happened. Passed, failed, skipped (blocked) and not executed are
listed separately. The JUnit files and logs of these runs are in `test-results/`.

Environment: Linux container, Python 3.13.18, pytest 9.1.1, Playwright 1.63 with Chromium, Xvfb. Wine 9.0 for the
Setup wizard. Windows results come from GitHub Actions (`windows-latest`, Windows PowerShell 5.1).

## 1. Full suite on Linux

    python -m pytest -q --junitxml=full-suite-linux.xml --ignore=tests/test_cumulative_patch_231000.py

**868 passed, 106 skipped, 0 failed** in 30 minutes (`full-suite-linux.xml`, `full-suite-linux.log`). The patch
test is run on its own (section 3) because it builds the patch from this checkout.

| Skipped | Reason, as reported by pytest | Run elsewhere? |
|---:|---|---|
| 18 | 23.9.0 patch test: only meaningful on the 23.9.0 tree | Superseded by the 23.10.0 patch test (section 3), which covers every baseline |
| 46 | Older cumulative-patch tests superseded by a later patch test (23.6.0, 23.7.0, 23.7.1, 23.7.2, 23.8.0) | Same |
| 16 + 1 | JMeter / Java not installed (performance tests) | **Not executed** (no JMeter here; unchanged since 23.9.0) |
| 13 | Windows only | Windows CI (section 4) |
| 9 + 1 | OCR runtime not installed in that run | Run in section 2: passed |
| 1 | Needs a display | Run in section 2: passed |
| 1 | Host name resolves to loopback | **Not executed** (container network) |

## 2. OCR and window tests under Xvfb with the OCR runtime

The same RapidOCR 3.10.0 and ONNX Runtime 1.32.0 versions as the patch, Linux wheels:

    xvfb-run python -m pytest tests/test_integration_ocr_browser_23900.py \
        tests/test_integration_workspace_23800.py::test_07_three_screenshots_ocr_and_compare \
        tests/test_integration_ai_browser_23900.py::test_09_visible_windows_and_popups_fill_the_screen

**31 passed, 0 failed** (`ocr-display-linux.xml`). These are the 11 tests skipped in section 1 plus the rest of the
OCR file.

## 3. Installer (`install_patch.py`) on every real release

`tests/test_cumulative_patch_231000.py`. Each baseline is extracted with `git archive` from its own release commit
(RELEASE_INCLUSION_MANIFEST.md) and given a database created by that release's own code.

| Test | What it proves | Result |
|---|---|---|
| `test_patch_contents` | The built patch holds every changed file with its SHA-256, the OCR wheels, the docs and the tools | see below |
| `test_installer_needs_no_network_and_no_administrator` | No network module imported; no elevation, machine registry, service or protected-folder use in the installer, wrappers and Setup script | passed |
| `test_wrong_version_is_refused` | 23.3.31 and an unknown version are refused before anything changes | passed |
| `test_changed_patch_file_is_refused` | A changed patch file stops the install before anything changes | passed |
| `test_failure_after_copy_rolls_back` | A failure after copying restores the previous files and database automatically | passed |
| `test_apply_verify_rollback_and_apply_again` [23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0] | Apply, verify, roll back to the exact previous files and rows, apply again | passed (10 of 10) |
| `test_ocr_runtime_is_staged_swapped_and_rolled_back` | OCR runtime staged next to the old one, swapped, rolled back | passed |
| `test_failed_apply_restores_the_previous_ocr_runtime` | | passed |
| `test_damaged_ocr_wheel_is_refused` | | passed |
| `test_protected_application_folder_is_never_forced_and_side_by_side_works` | A folder that cannot be written is never forced; the side-by-side copy works and the original is untouched | passed |
| `test_detect_reports_what_setup_shows` [23.3.35, 23.9.0] | The Detect page data (version, supported, folders, running); changes nothing | see below |
| `test_setup_log_option_writes_every_message` | `--log` writes every step | passed |

First run: **19 passed, 3 failed**. The failures were in the tests, not the installer: `test_patch_contents` ran
before this report existed (the patch must contain it), and `test_detect_reports_what_setup_shows` did not give the
test installation a Python (Setup finds the application's own). Both were fixed. The final run is in
`cumulative-patch-231000-linux.xml`: **RESULT_PATCH_FINAL**.

## 4. Windows (GitHub Actions)

RESULT_WINDOWS

## 5. Setup wizard under Wine 9.0 over a real 23.9.0 installation

`tests/wine_setup_walkthrough.sh`. The installation was 23.9.0 extracted from its release commit with its own
Windows Python 3.13.17 (the offline package's embeddable Python) and its Windows packages, a database with data, and
the knowledge database. Screenshots: `evidence/setup-*.png`.

* Welcome, Detect (found 23.9.0, supported), Backup (backup and data folders shown), Install, Verify, Finish: each
  page shown and passed with Next.
* Result in the setup log: **Patched 23.9.0 -> 23.10.0**. Every verification check passed except the offline OCR
  engine, which is a **WARN** under Wine: Windows NumPy does not start under Wine 9.0 (`import numpy` ends with
  exit code 0x80000100). The installer reports it as a warning and says screenshots will be read with Windows OCR;
  the Finish page counts the warning. On real Windows the OCR engine is checked by the CI job (section 4).
* Found and fixed on the way: Python's `shutil.copy2` uses the Windows `CopyFile2` function, which crashes under
  Wine. The installer now copies with `copyfile` + `copystat` (contents and timestamps), which works on both.

## 6. End-to-end runs in a browser (Playwright, Chromium)

| Flow | Test | Result |
|---|---|---|
| Simple agent: connect, learn while browsing the stand-in trips site, plain-language commands, one question for what is missing, preview, ticked confirmation for consequential runs, roles (manager approves, authorizer authorizes), live status check, tasks history | `test_autopilot_231000.py` (11), `test_agent_simple_api_231000.py` (8) | passed |
| Password typed in the chat or recorded: never in history, recording, database files or logs | `test_secrets_in_commands_are_withheld`, `test_a_password_in_a_command_is_withheld`, `test_conversion_drops_sign_in_pagination_and_changes` | passed |
| Voice: audio becomes text (stand-in transcriber), then the same command path | `test_voice_becomes_text_then_a_command` | passed |
| Guided recording on a stand-in report site: sign-in by hand, menu, filter, pagination, detail page; saved without sign-in, pagination or Delete; replay reads every page and every detail; the Delete button is never pressed | `test_guided_comparison_231000.py` (2) | passed |
| Extension 1.2.0 loaded unpacked in Chromium with the companion: pairing, sign-in, credential page, run with confirmation; Record for Integration Comparison saves a file that SkyGuard imports | `test_extension_e2e.py` (2), `test_extension_120.py` (5) | passed |
| UI pages driven in Chromium against a running SkyGuard (AI Agent tabs, Connect dialog with guided recording) | `evidence/ui-*.png` | screenshots |

## 7. Security tools

See SECURITY_REPORT.md: pip-audit (no known vulnerabilities), bandit (8 findings reviewed, no real issue), secret
scan (no secrets).

## 8. Not executed

* Windows 10 1909 itself, Edge and Chrome release builds, enterprise policies (KNOWN_LIMITATIONS.md).
* A local Qwen server and real Faster-Whisper speech recognition: the model paths are unchanged from 23.9.0.
* Real enterprise applications with SSO, MFA or Citrix.
* JMeter performance tests (no Java here).
