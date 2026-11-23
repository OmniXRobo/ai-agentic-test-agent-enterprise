"""SkyGuard QA Arabic speech kit: sets Voice settings to the multilingual speech model (run by INSTALL_ARABIC_SPEECH_MODEL.ps1).

    python -I set_speech_language.py --app <SkyGuard QA app folder> --model small [--language auto|ar]

Changes only the voice section of settings.json, and keeps a copy of the file first:
  stt_engine = local, stt_api = faster_whisper   when Faster-Whisper is installed (runtime\\faster-whisper\\lib)
  fw_model   = <model>                           the multilingual model just installed (small or medium)
  language   = auto | ar                         auto = English and Arabic, the model detects which one is spoken

SkyGuard QA reads settings.json when it starts and writes it back when settings are saved, so a running SkyGuard QA
would not see the change and could overwrite it: when it answers on its port nothing is changed (exit code 3).
Only the standard library is used, so any Python 3 works.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

RUNNING = 3


def data_dir(app: Path) -> Path:
    env = os.environ.get("QA_HOME")
    if env:
        return Path(env).expanduser()
    # START_QA_PLATFORM.cmd: an installed copy keeps its data beside the app folder
    return (app.parent / "data") if (app / "INSTALLED.json").is_file() else (app / "data")


def fw_installed(app: Path) -> bool:
    return any((h / "lib" / "faster_whisper").is_dir()
               for h in (app / "runtime" / "faster-whisper", app.parent / "runtime" / "faster-whisper"))


def answering(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--app", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--language", default="auto", choices=("auto", "ar"))
    ap.add_argument("--no-running-check", action="store_true")
    a = ap.parse_args(argv)

    if a.model.endswith(".en"):
        print(f"{a.model} is English only; Voice settings were not changed.")
        return 2
    app = Path(a.app).resolve()
    settings_file = data_dir(app) / "settings.json"
    stored: dict = {}
    if settings_file.is_file():
        try:
            stored = json.loads(settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"{settings_file} could not be read ({exc}); Voice settings were not changed.")
            return 1
        if not isinstance(stored, dict):
            print(f"{settings_file} is not a settings file; Voice settings were not changed.")
            return 1
    server = stored.get("server") if isinstance(stored.get("server"), dict) else {}
    port = int(server.get("port") or 8765)
    if not a.no_running_check and answering(port):
        print(f"SkyGuard QA is running (it answers on port {port}); Voice settings were not changed.")
        return RUNNING

    voice = stored.get("voice") if isinstance(stored.get("voice"), dict) else {}
    keys = ("stt_engine", "stt_api", "fw_model", "language")
    before = {k: voice.get(k) for k in keys}
    if fw_installed(app):
        voice["stt_engine"], voice["stt_api"] = "local", "faster_whisper"
    else:
        print("Faster-Whisper itself is not installed in runtime\\faster-whisper\\lib, so the speech recognizer is not "
              "changed; Arabic speech is understood once Faster-Whisper is installed (the 23.3.7 patch or the full "
              "offline package).")
    voice["fw_model"], voice["language"] = a.model, a.language
    stored["voice"] = voice

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    if settings_file.is_file():
        backup = settings_file.with_name(f"settings.before-arabic-speech-{time.strftime('%Y%m%d-%H%M%S')}.json")
        backup.write_bytes(settings_file.read_bytes())
        print(f"Copy of the previous settings: {backup}")
    tmp = settings_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    tmp.replace(settings_file)
    after = {k: voice.get(k) for k in keys}
    for k in keys:
        print(f"  voice.{k}: {before[k]!r} -> {after[k]!r}" if before[k] != after[k] else f"  voice.{k}: {after[k]!r}")
    print(f"Voice settings updated in {settings_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
