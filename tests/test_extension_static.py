"""Static checks of the browser extension: Manifest V3, least privilege, shared files identical to SkyGuard's."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "browser-extension" / "extension"


def manifest() -> dict:
    return json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_v3_least_privilege():
    m = manifest()
    assert m["manifest_version"] == 3
    assert set(m["permissions"]) == {"storage", "sidePanel", "scripting", "tabs", "notifications"}
    assert m.get("optional_permissions") == ["debugger"]
    assert "host_permissions" not in m  # sites are granted one by one when an application is registered
    assert "<all_urls>" not in json.dumps(m)
    assert "externally_connectable" not in m and "content_scripts" not in m  # injected on demand only
    assert "unsafe" not in m["content_security_policy"]["extension_pages"]


def test_fixed_extension_id_matches_companion():
    m = manifest()
    der = base64.b64decode(m["key"])
    ext_id = "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32])
    cfg = (ROOT / "browser-extension" / "companion" / "skyguard_companion" / "config.py").read_text()
    assert f'DEFAULT_EXTENSION_IDS = ["{ext_id}"]' in cfg


def test_shared_files_are_identical_to_skyguard():
    pairs = [(ROOT / "skyguard_agent" / "js" / "agent_dom.js", EXT / "lib" / "agent_dom.js"),
             (ROOT / "web" / "js" / "secret-check.js", EXT / "lib" / "secret-check.js"),
             (ROOT / "web" / "js" / "wav-recorder.js", EXT / "lib" / "wav-recorder.js")]
    for a, b in pairs:
        assert a.read_bytes() == b.read_bytes(), b


def test_no_remote_code_or_eval():
    for f in EXT.rglob("*"):
        if f.suffix in (".js", ".html"):
            text = f.read_text(encoding="utf-8")
            assert not re.search(r"\beval\(|new Function\(|https?://(?!127\.0\.0\.1|localhost|[a-z.]*example)[a-z]", text), f
            assert "<script src=\"http" not in text


def test_pages_exist():
    m = manifest()
    for path in (m["side_panel"]["default_path"], m["options_page"], m["background"]["service_worker"],
                 *m["icons"].values()):
        assert (EXT / path).exists(), path
