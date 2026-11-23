"""CI (Windows, job arabic-speech-kit-windows): spoken Arabic and English understood by SkyGuard QA with the kit's model.

Run with the installed copy's Python, from its app folder, after GET_ARABIC_SPEECH_MODEL.cmd and
INSTALL_ARABIC_SPEECH_MODEL.cmd ran and the offline Arabic voice (Piper) was installed:

    set QA_HOME=<installation>\\data
    <app>\\.venv\\Scripts\\python.exe -E -s ci_end_to_end.py

The real path end to end: SkyGuard QA starts its Faster-Whisper server with the settings the kit wrote, the speech
is rendered by the offline Arabic voice (Arabic) and a Windows voice (English), and each recording goes through
POST /api/voice/transcribe exactly like a recording from the dashboard microphone.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.getcwd())

from fastapi.testclient import TestClient  # noqa: E402

from qa_platform.config import Paths  # noqa: E402
from qa_platform.credentials import MemoryBackend  # noqa: E402
from qa_platform.main import create_app  # noqa: E402
from qa_platform.voice.tts import is_arabic  # noqa: E402

ARABIC = ["كابتن، افتح خطط الاختبار.", "افتح لوحة التحكم.", "أنشئ حالات اختبار لقصة المستخدم."]
ENGLISH = ["Captain, open Test Plans.", "Open the dashboard."]


def plain(text: str) -> str:
    text = re.sub(r"[ً-ْـ]", "", text)  # Arabic diacritics and tatweel
    text = text.translate(str.maketrans("أإآة", "اااه"))
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def main() -> int:
    paths = Paths(Path(os.environ["QA_HOME"])).ensure()
    app = create_app(paths, secret_backend=MemoryBackend())
    failures: list[str] = []
    with TestClient(app) as c:
        rt = c.app.state.runtime
        started = time.monotonic()
        rt.voice.fw.ensure_ready(timeout=300)
        cfg = rt.voice.config()
        print("voice settings:", {k: cfg.get(k) for k in ("stt_engine", "stt_api", "stt_url", "fw_model", "language")},
              flush=True)
        if cfg.get("stt_engine") != "local" or cfg.get("language") != "auto":
            failures.append(f"voice settings are not Faster-Whisper with English and Arabic: {cfg}")
        st = c.get("/api/voice/faster-whisper").json()
        print(st, flush=True)
        print(f"Faster-Whisper {st.get('state')}: model {st.get('model')} (installed {st.get('models')}), "
              f"ready in {time.monotonic() - started:.0f} s", flush=True)
        if st.get("model") not in ("small", "medium"):
            failures.append(f"the service runs model {st.get('model')}, not the kit's multilingual model")

        def hear(phrase: str) -> str:
            wav = rt.voice.system_voice.render(phrase)
            t = time.monotonic()
            r = c.post("/api/voice/transcribe", content=wav, headers={"content-type": "audio/wav"})
            took = time.monotonic() - t
            text = r.json().get("text", "") if r.status_code == 200 else f"HTTP {r.status_code} {r.text[:200]}"
            ratio = difflib.SequenceMatcher(None, plain(phrase), plain(text)).ratio()
            print(f"  said  : {phrase}\n  heard : {text}\n  match : {ratio:.0%} in {took:.1f} s")
            meaning = c.post("/api/voice/interpret", json={"text": text}).json() if r.status_code == 200 else {}
            if meaning:
                print(f"  means : {({k: meaning.get(k) for k in ('english', 'intent', 'action', 'page') if k in meaning})}")
            return text if ratio >= 0.5 else f"LOW MATCH {ratio:.0%}: {text}"

        print("Arabic (offline Arabic voice, Piper):")
        for p in ARABIC:
            heard = hear(p)
            if heard.startswith(("LOW MATCH", "HTTP")) or not is_arabic(heard):
                failures.append(f"Arabic '{p}' -> '{heard}'")
        print("English (Windows voice), same settings (English and Arabic detected):")
        try:
            voices = rt.voice.system_voice.voices()
        except Exception as exc:  # noqa: BLE001 - reported, not fatal: the runner may have no Windows voice
            voices = []
            print("  no Windows voice:", exc)
        if voices:
            for p in ENGLISH:
                heard = hear(p)
                if heard.startswith(("LOW MATCH", "HTTP")) or is_arabic(heard):
                    failures.append(f"English '{p}' -> '{heard}'")
        else:
            print("  SKIPPED: this runner has no Windows voice to say the English phrases")
    for f in failures:
        print("FAIL:", f)
    print("RESULT:", "PASS" if not failures else "FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
