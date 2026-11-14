"""Windows-only checks for the 23.5.0 agent and the browser extension package (skipped elsewhere; CI runs them on
windows-latest): the DPAPI vault of the companion, the Windows Credential Manager vault of SkyGuard, and that every
shipped PowerShell script parses without errors in Windows PowerShell.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
SCRIPTS = sorted([*(ROOT / "browser-extension" / "scripts").glob("*.ps1"),
                  *(ROOT / "packaging" / "ops-agent-patch").glob("*.ps1"),
                  *(ROOT / "packaging" / "cumulative-patch").glob("*.ps1"),
                  *(ROOT / "packaging" / "autolearn-patch").glob("*.ps1")])


@windows
def test_dpapi_vault_round_trip(tmp_path):
    from skyguard_agent.vault import DpapiBackend, Vault

    backend = DpapiBackend(tmp_path / "vault")
    secret = "Dpapi-" + secrets.token_hex(8)
    vault = Vault(backend)
    ref = vault.save(secret, consent=True)
    stored = list((tmp_path / "vault").glob("*.dpapi"))
    assert len(stored) == 1 and secret.encode() not in stored[0].read_bytes()  # encrypted at rest
    assert backend.get(ref) == secret
    backend.delete(ref)
    assert backend.get(ref) is None and not list((tmp_path / "vault").glob("*.dpapi"))


@windows
def test_windows_credential_manager_round_trip():
    from qa_platform.credentials import SecretStore
    from skyguard_agent.vault import SecretStoreBackend

    store = SecretStore()
    if not store.available:
        pytest.skip("no Windows Credential Manager backend on this runner")
    backend = SecretStoreBackend(store)
    ref = "vault:" + secrets.token_hex(8)
    secret = "Wcm-" + secrets.token_hex(8)
    try:
        backend.set(ref, secret)
        assert backend.get(ref) == secret
    finally:
        backend.delete(ref)
    assert backend.get(ref) is None


@windows
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_powershell_scripts_parse(script):
    ps = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not ps:
        pytest.skip("PowerShell not found")
    cmd = ("$e = $null; $t = $null; "
           f"[System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$t, [ref]$e) | Out-Null; "
           "$e | ForEach-Object { $_.Message + ' (line ' + $_.Extent.StartLineNumber + ')' }; exit $e.Count")
    r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_scripts_found():
    names = {p.name for p in SCRIPTS}
    assert {"Install-SkyGuardAgent.ps1", "Uninstall-SkyGuardAgent.ps1", "Install-Models.ps1",
            "Check-Prerequisites.ps1", "APPLY_PATCH.ps1"} <= names
