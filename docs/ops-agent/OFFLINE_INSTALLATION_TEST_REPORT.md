# Offline installation test report (23.5.0 patch and browser extension package)

Only what was actually executed is reported. Environment: Linux container, Python 3.13, no Windows, no PowerShell.
Offline means each step ran in a new network namespace that has only loopback (`unshare -rn`); an internet request
from inside it fails, which the script checks first.

Script: `packaging/offline_install_check.sh <folder with both zips> <work folder>`. Full output:
`evidence/offline_install_check.txt` (in this zip). It was run on zips built from the same commit as the final
zips; the final zips differ from those only in the test reports and this evidence file. The final zips were then
checked again with `packaging/validate_final_zips.py` and the same script (see the pull request).

## SkyGuard patch (executed, offline)

| Step | Result |
| --- | --- |
| Zip SHA-256 matches `.zip.sha256`; every file matches `SHA256_CHECKSUM.txt` | PASS |
| 23.4.0 installation made from the baseline commit 53e8009 (`git archive`), database created by 23.4.0 (schema 18) | done |
| `install_patch.py apply`: version check, payload SHA-256, space and permissions, backup, copy, knowledge database created, verify | PASS (all checks PASS, *No data lost*) |
| `install_patch.py verify` | PASS (version, application loads, agent loads, knowledge database schema 1 and integrity, 18 migrations, integrity, patch files) |
| Patched SkyGuard started offline: `/api/health` reports 23.5.0; dashboard 200; `/api/ops/info` from a browser without sign-in 401 | PASS |
| `rollback` back to 23.4.0 (data kept), then `apply` again | PASS |

The same steps, plus refusal of a wrong version, refusal of a changed patch file and the automatic rollback after a
failure, are automated in `tests/test_ops_patch_install.py` (5 passed).

## Browser extension package (executed, offline)

| Step | Result |
| --- | --- |
| Zip and every file match their SHA-256 | PASS |
| Bundled Python 3.13.17 package contains `python.exe` / `pythonw.exe`; llama.cpp b10549 zip contains `llama-server.exe` | PASS |
| 23 Faster-Whisper wheels install with `--no-index --require-hashes` for win_amd64 / CPython 3.13 | PASS (resolution and hash check for the Windows target, done on Linux) |
| Companion from the extracted package: `version`, `pair`, `check`, `serve`, `status` | PASS |
| `/health` 200; web page origin 403; foreign Host 403; extension origin 200; data route without pairing 401 | PASS |

## Not executed

* `APPLY_PATCH.cmd` / `APPLY_PATCH.ps1` / `VERIFY_PATCH.cmd` / `ROLLBACK_PATCH.cmd` and the extension's
  `INSTALL.cmd`, `UNINSTALL.cmd`, `Install-SkyGuardAgent.ps1`, `Install-Models.ps1`, `Check-Prerequisites.ps1`,
  `Uninstall-SkyGuardAgent.ps1`: Windows PowerShell is not available here. They call the Python steps tested above.
* Unpacking the bundled Python and running Faster-Whisper on Windows; llama-server and Qwen; DPAPI and Windows
  Credential Manager; HKCU Run entry and Start menu shortcuts.
* Installation on a real SkyGuard 23.4.0 Windows installation (the test used a tree from the baseline commit and the
  host's Python with SkyGuard's packages).
