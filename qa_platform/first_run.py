"""First-run setup: the three things a new install needs, on one screen, with no commands.

1. TFS: server URL, project and Personal Access Token. The mode follows the URL (dev.azure.com or
   *.visualstudio.com = Azure DevOps Online, anything else = Local TFS / Azure DevOps Server). The PAT goes to the
   Windows Credential Manager like everywhere else.
2. Local LLM: the Qwen ``.gguf`` model file. llama-server is bundled in ``runtime\\ai`` and found automatically
   (another llama-server can still be chosen). The managed server starts now and with the application from then on.
3. Voice (optional): a Vosk model folder or ``.zip``. A zip is unpacked once into the data folder. Without one,
   the bundled offline model is used.

The installer can pre-fill everything except the PAT (``INSTALL_OFFLINE.cmd --tfs-url ... --llm-model ...``), so
the screen only asks for what is still missing.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .clock import now_iso
from .errors import QAError
from .logging_setup import get_logger
from .voice.offline_stt import _find_model

log = get_logger("first_run")

def tfs_mode_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return "online" if host == "dev.azure.com" or host.endswith(".visualstudio.com") else "offline"


def _clean_path(value: str | None) -> str:
    return (value or "").strip().strip('"').strip()


def prefill(settings_path: Path, *, tfs_url: str = "", tfs_project: str = "", llm_model: str = "",
            llm_server: str = "", voice_model: str = "") -> dict[str, Any]:
    """Settings the installer writes for the setup screen (never a PAT). Returns the patch that was applied."""
    from .config import Settings

    patch: dict[str, Any] = {}
    tfs_url = tfs_url.strip().rstrip("/")
    if tfs_url:
        mode = tfs_mode_for(tfs_url)
        key = "organization_url" if mode == "online" else "collection_url"
        patch["tfs"] = {"active_mode": mode, mode: {key: tfs_url, **({"project": tfs_project.strip()} if tfs_project else {})}}
    managed = {k: v for k, v in (("model_path", _clean_path(llm_model)), ("server_path", _clean_path(llm_server))) if v}
    if managed:
        patch["ai"] = {"mode": "on_premise", "managed": {**managed, "auto_start": True}}
    if _clean_path(voice_model):
        folder = voice_folder(Path(_clean_path(voice_model)), settings_path.parent)
        patch["voice"] = {"stt_engine": "builtin", "stt_model_dir": str(folder)}
    if patch:
        Settings(settings_path).update(patch)
    return patch


class FirstRunSetup:
    def __init__(self, settings: Any, paths: Any, tfs: Any, llm: Any, voice: Any, health: Any) -> None:
        self.settings, self.paths, self.tfs, self.llm, self.voice, self.health = settings, paths, tfs, llm, voice, health

    # ---- state -------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        profile = self.tfs.profile()
        managed = self.llm.config()
        found = self.llm.detect()
        server = managed.get("server_path") or (found["servers"][0] if found["servers"] else "")
        model = managed.get("model_path") or ""
        voice_dir = self.voice.offline.model_dir()
        tfs_ok = bool(profile["url"] and profile["project"] and profile["has_pat"])
        llm_ok = bool(model and Path(model).is_file() and server and Path(server).is_file())
        done = self.settings.section("setup").get("completed_at")
        return {
            "needed": not done and not (tfs_ok and llm_ok),
            "completed_at": done,
            "credential_store_available": self.tfs.secrets.available,
            "tfs": {"mode": profile["mode"], "url": profile["url"], "project": profile["project"],
                    "has_pat": profile["has_pat"], "ready": tfs_ok},
            "llm": {"model_path": model, "server_path": server, "server_bundled": bool(found["servers"]),
                    "models_found": [m["path"] for m in found["models"]], "ready": llm_ok},
            "voice": {"model_dir": self.settings.section("voice").get("stt_model_dir") or "",
                      "active_model": str(voice_dir) if voice_dir else "", "ready": voice_dir is not None},
        }

    # ---- save --------------------------------------------------------------------------------

    def save(self, *, tfs_url: str, tfs_project: str, tfs_pat: str | None, llm_model: str,
             llm_server: str | None = None, voice_model: str | None = None, verify_tls: bool | None = None,
             start_llm: bool = True) -> dict[str, Any]:
        steps: list[dict[str, str]] = []

        def step(name: str, status: str, detail: str) -> None:
            steps.append({"name": name, "status": status, "detail": detail})

        # 1. TFS
        tfs_url = (tfs_url or "").strip().rstrip("/")
        if not tfs_url or not (tfs_project or "").strip():
            raise QAError("SETUP_INVALID", "Enter the TFS server URL and the project.")
        mode = tfs_mode_for(tfs_url)
        current = self.tfs.profile(mode)
        if not tfs_pat and not current["has_pat"]:
            raise QAError("SETUP_INVALID", "Paste the TFS Personal Access Token.")
        self.tfs.save_profile(mode, url=tfs_url, project=tfs_project, pat=tfs_pat or None, make_active=True,
                              **({"verify_tls": verify_tls} if verify_tls is not None and mode == "offline" else {}))
        try:
            result = self.tfs.test_connection(mode)
            self.health.tfs_tested(result)
            failed = [c for c in result["checks"] if c["status"] == "FAIL"]
            step("TFS", "PASS" if result["ok"] else "FAIL",
                 f"Connected to {result.get('label') or tfs_url}." if result["ok"]
                 else f"Saved, but the connection test failed: {failed[0]['name']}: {failed[0]['detail']}" if failed
                 else "Saved, but the connection test failed.")
        except QAError as exc:
            step("TFS", "FAIL", f"Saved, but the connection test failed: {exc.message}")

        # 2. Local LLM
        model = Path(_clean_path(llm_model))
        if not model.is_file() or model.suffix.lower() != ".gguf":
            raise QAError("SETUP_INVALID", f"The LLM model must be an existing .gguf file: {model}")
        managed: dict[str, Any] = {"model_path": str(model), "auto_start": True}
        server = _clean_path(llm_server)
        if server:
            if not Path(server).is_file():
                raise QAError("SETUP_INVALID", f"llama-server was not found at {server}.")
            managed["server_path"] = server
        self.settings.update({"ai": {"mode": "on_premise", "managed": managed}})
        if start_llm:
            try:
                if self.llm.status()["state"] in ("RUNNING", "STARTING"):
                    self.llm.stop()
                state = self.llm.start(wait=False)
                step("Local LLM", "PASS", f"Starting {model.name} on {state['url']}. It starts with the application from now on.")
            except QAError as exc:
                step("Local LLM", "FAIL", exc.message)
        else:
            step("Local LLM", "PASS", f"{model.name} saved. It starts with the application.")

        # 3. Voice (optional)
        voice_model = _clean_path(voice_model)
        if voice_model:
            folder = voice_folder(Path(voice_model), self.paths.root)
            self.settings.update({"voice": {"stt_engine": "builtin", "stt_model_dir": str(folder)}})
            step("Voice", "PASS", f"Speech model {folder.name}.")
        else:
            found = self.voice.offline.model_dir()
            step("Voice", "PASS" if found else "FAIL",
                 f"Bundled offline speech model {found.name}." if found else "No speech model found; typed commands still work.")

        self.settings.update({"setup": {"completed_at": now_iso()}})
        log.info("First-run setup saved: " + ", ".join(f"{s['name']} {s['status']}" for s in steps))
        return {"steps": steps, **self.status()}

    def dismiss(self) -> dict[str, Any]:
        self.settings.update({"setup": {"completed_at": now_iso(), "skipped": True}})
        return self.status()


def voice_folder(path: Path, data_root: Path) -> Path:
    """A Vosk model folder as given, or a .zip unpacked once into <data>/speech-model."""
    if path.is_dir():
        found = _find_model(path)
        if not found:
            raise QAError("SETUP_INVALID", f"{path} is not a Vosk speech model folder (it needs 'am' and 'conf').")
        return found
    if path.is_file() and path.suffix.lower() == ".zip":
        dest = data_root / "speech-model" / path.stem
        if not _find_model(dest):
            tmp = dest.with_name(dest.name + ".partial")
            shutil.rmtree(tmp, ignore_errors=True)
            with zipfile.ZipFile(path) as z:
                root = tmp.resolve()
                for name in z.namelist():
                    if not (tmp / name).resolve().is_relative_to(root):
                        raise QAError("SETUP_INVALID", f"{path.name} contains a file outside its folder ({name}).")
                z.extractall(tmp)
            shutil.rmtree(dest, ignore_errors=True)
            tmp.rename(dest)
        found = _find_model(dest)
        if not found:
            shutil.rmtree(dest, ignore_errors=True)
            raise QAError("SETUP_INVALID", f"{path.name} does not contain a Vosk speech model.")
        return found
    raise QAError("SETUP_INVALID", f"The voice model must be a Vosk model folder or .zip: {path}")
