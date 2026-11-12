# Offline installation test report, SkyGuard QA 23.4.0

**Date:** 12 November 2026. **Environment:** Linux build container, Python 3.13, no network used by the installer.
**What was installed on:** a simulated 23.3.35 installation made from the 23.3.35 release commit (`6d448fa`):
`app\` with `INSTALLED.json`, a separate `data\` folder, a database created by 23.3.35 itself (schema 16) holding two
saved logins, and `settings.json`.

**Not covered here:** the Windows wrappers (`APPLY_PATCH.ps1`, `.cmd`), stopping and starting the real application
process, Windows file locking, and the Windows Credential Manager. These need a Windows PC. The
Python installer they call (`install_patch.py`) is the part tested below, with the same commands the wrapper runs.

## Results

| # | Test | Result |
|---|---|---|
| T1 | Every file in the zip matches `SHA256_CHECKSUM.txt` | PASS |
| T2 | An installation that is not 23.3.35 (23.3.30) is refused before anything changes | PASS |
| T3 | A changed patch file is refused (`PATCH_MANIFEST.json` SHA-256) | PASS |
| T4 | Refused while the application's port answers | PASS |
| T5 | A failure after copying files restores 23.3.35 automatically; the file tree is identical to before (hash compared); the database is restored and the other copy kept, not deleted | PASS |
| T6 | Apply: version, application loads, schema 16 to 18, integrity check, new tables and column, no table lost rows, all patch files present | PASS (8 of 8 checks) |
| T7 | The patched application starts: `/api/health` ok, version 23.4.0, both saved logins kept (sign-in method `form`), Comparison and AURA APIs answer | PASS |
| T8 | `verify` on the patched installation | PASS (7 of 7 checks) |
| T9 | Rollback: file tree identical to 23.3.35; database kept; 23.3.35 opens the schema 18 database with both logins | PASS |
| T10 | Apply again after the rollback | PASS |
| T11 | Rollback with `--restore-database`: the pre-upgrade copy is restored, the current database is kept renamed | PASS |

## Log

The log of the run, with the scratch folder shown as `<scratch>`:

```text
Simulated install: 23.3.35 schema 16 credentials 2
Baseline file tree hash: 1f325b09259b4ef8

### T1 SHA256_CHECKSUM.txt
PASS: every file matches

### T2 wrong baseline is refused

PATCH FAILED: This patch installs on 23.3.35 (found 23.3.30). Bring the installation to 23.3.35 first.
exit 0

### T3 tampered patch file is refused

PATCH FAILED: The patch folder is damaged or changed: qa_platform/__init__.py. Extract the zip again.

### T4 application still running is refused

PATCH FAILED: SkyGuard QA is still running (port 8799 answers). Close it, then run the patch again.

### T5 failure after copying rolls back automatically
== Restoring the previous version
Database restored from the backup; the database as it was after the failed step is kept as qa_platform.failed-upgrade-20261112-121300.sqlite3.
Previous version 23.3.35 restored.

PATCH FAILED: simulated failure after copying (--fail-after-copy). Nothing was lost; the previous version was restored. Backup: <scratch>/sim/AIAgenticTestAgent/patch-backups/23.3.35-to-23.4.0-20261112-121300
file tree after automatic rollback: 1f325b09259b4ef8 (baseline 1f325b09259b4ef8)
PASS: identical to 23.3.35

### T6 apply
== Verifying
  Version file                       PASS  23.4.0
  Application loads                  PASS  23.4.0
  Database migrations                PASS  schema version 18 (expected 18)
  Database integrity                 PASS  ok
  Comparison tables                  PASS  present
  Login sign-in method column        PASS  present
  No data lost                       PASS  every table kept its rows
  Patch files present                PASS  all

== Result
Patched 23.3.35 -> 23.4.0. Backup for rollback: <scratch>/sim/AIAgenticTestAgent/patch-backups/23.3.35-to-23.4.0-20261112-121300

### T7 the patched application starts and serves /api/health
health True 23.4.0
credentials kept: ['alice', 'qa.tester'] auth_method ['form']
comparison projects API: 200 | AURA status: 200

### T8 verify

== Verifying <scratch>/sim/AIAgenticTestAgent/app
  Version file                       PASS  23.4.0
  Application loads                  PASS  23.4.0
  Database migrations                PASS  schema version 18 (expected 18)
  Database integrity                 PASS  ok
  Comparison tables                  PASS  present
  Login sign-in method column        PASS  present
  Patch files present                PASS  all

### T9 rollback (database kept)
== Rolling back 23.4.0 -> 23.3.35 from <scratch>/sim/AIAgenticTestAgent/patch-backups/23.3.35-to-23.4.0-20261112-121300
Database kept as it is (23.3.35 ignores the tables and column the patch added; nothing is lost).
Version now 23.3.35.
file tree after rollback: 1f325b09259b4ef8 (baseline 1f325b09259b4ef8)
PASS: identical to 23.3.35
23.3.35 opens the kept database: 23.3.35 schema 18 credentials 2

### T10 apply again after the rollback
== Result
Patched 23.3.35 -> 23.4.0. Backup for rollback: <scratch>/sim/AIAgenticTestAgent/patch-backups/23.3.35-to-23.4.0-20261112-121305

### T11 rollback with --restore-database
== Rolling back 23.4.0 -> 23.3.35 from <scratch>/sim/AIAgenticTestAgent/patch-backups/23.3.35-to-23.4.0-20261112-121305
Current database kept as qa_platform.before-rollback-20261112-121306.sqlite3.
Database restored from the copy taken before the upgrade.
Version now 23.3.35.
qa_platform.before-rollback-20261112-121306.sqlite3
qa_platform.failed-upgrade-20261112-121300.sqlite3
qa_platform.sqlite3
```

To repeat the test: `python packaging/final-patch/build_patch.py --baseline 6d448fa --out $WORK/build`, then
`WORK=$WORK bash packaging/final-patch/test_offline_install.sh`. It builds the simulated 23.3.35 installation from git,
extracts the zip and runs the installer.
