"""Local Faster-Whisper speech server (started by the application, see faster_whisper.py).

A small HTTP server on 127.0.0.1 that turns a recording into text with Faster-Whisper (CTranslate2), fully offline:
the model is a folder on this PC and nothing is downloaded. It answers the same requests as a whisper.cpp server,
so the application (and any other local tool) can use it the same way:

  GET  /health                     {"status": "ok" | "loading" | "error", "engine": "faster-whisper", ...}
  POST /inference                  multipart: file, language, prompt, temperature   -> {"text": "..."}
  POST /v1/audio/transcriptions    the OpenAI-compatible form of the same request    -> {"text": "..."}

Run by the application's own Python:

  python -E -s whisper_server.py --lib <runtime\\faster-whisper\\lib> --model <runtime\\faster-whisper\\models\\small.en>
         --port 8766

It only uses the standard library besides Faster-Whisper and numpy, so it does not need the application's packages.
"""

from __future__ import annotations

import os
import sys

# Run as a script, this folder (the application's voice package) would come first on sys.path and its modules could
# shadow the packages Faster-Whisper imports. Only the standard library and --lib are used.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]

import argparse
import io
import json
import logging
import threading
import time
import traceback
import wave
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

ENGINE = "faster-whisper"
SERVER_VERSION = "23.3.7"
MAX_BODY = 25 * 1024 * 1024
RATE = 16000

log = logging.getLogger("whisper_server")


class Engine:
    """Loads the model once (in the background, so /health answers at once) and transcribes one request at a time."""

    def __init__(self, model: str, device: str, compute_type: str, threads: int, language: str, beam_size: int) -> None:
        self.model_ref = model
        self.device = device
        self.compute_type = compute_type
        self.threads = threads
        self.language = language
        self.beam_size = beam_size
        self.model: Any = None
        self.state = "loading"
        self.error = ""
        self.loaded_in = 0.0
        self.versions: dict[str, str] = {}
        self._lock = threading.Lock()

    def load(self) -> None:
        started = time.monotonic()
        try:
            import ctranslate2
            import faster_whisper
            from faster_whisper import WhisperModel

            self.versions = {"faster_whisper": faster_whisper.__version__, "ctranslate2": ctranslate2.__version__}
            if not os.path.isfile(os.path.join(self.model_ref, "model.bin")):
                raise FileNotFoundError(f"No Faster-Whisper model (model.bin) in {self.model_ref}")
            compute = self.compute_type
            try:
                self.model = WhisperModel(self.model_ref, device=self.device, compute_type=compute,
                                          cpu_threads=self.threads, local_files_only=True)
            except ValueError:
                # A CPU without int8 support: fall back to what CTranslate2 picks for this machine.
                compute = "default"
                self.model = WhisperModel(self.model_ref, device=self.device, compute_type=compute,
                                          cpu_threads=self.threads, local_files_only=True)
            self.compute_type = compute
            # One short pass so the first real sentence is not slowed down by lazy initialisation.
            self._run(_silence(0.5), self.language, None)
            self.loaded_in = round(time.monotonic() - started, 1)
            self.state = "ok"
            log.info("model %s loaded in %.1fs (%s, %s)", self.model_ref, self.loaded_in, self.device, compute)
        except Exception as exc:  # reported on /health; the server keeps answering
            self.state, self.error = "error", f"{type(exc).__name__}: {exc}"
            log.error("model could not be loaded: %s\n%s", self.error, traceback.format_exc())

    def _run(self, audio: Any, language: str | None, prompt: str | None, temperature: float = 0.0) -> str:
        segments, _info = self.model.transcribe(
            audio, language=(language or self.language or None), task="transcribe", beam_size=self.beam_size,
            temperature=temperature or 0.0, initial_prompt=prompt or None, condition_on_previous_text=False,
            without_timestamps=True, vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
            no_speech_threshold=0.6, log_prob_threshold=-1.0)
        parts = []
        for seg in segments:
            # Whisper invents a polite sentence ("Thank you.") for noise; drop what it is unsure was speech.
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.7:
                continue
            parts.append(seg.text.strip())
        return " ".join(p for p in parts if p).strip()

    def transcribe(self, audio: Any, language: str | None, prompt: str | None, temperature: float = 0.0) -> str:
        if self.state != "ok":
            raise RuntimeError(self.error or "The speech model is still loading.")
        with self._lock:
            return self._run(audio, language, prompt, temperature)

    def health(self) -> dict[str, Any]:
        return {"status": self.state, "engine": ENGINE, "server_version": SERVER_VERSION,
                "model": os.path.basename(os.path.normpath(self.model_ref)), "model_path": self.model_ref,
                "device": self.device, "compute_type": self.compute_type, "language": self.language,
                "loaded_in_seconds": self.loaded_in, "error": self.error, "pid": os.getpid(), **self.versions}


def _silence(seconds: float) -> Any:
    import numpy as np

    return np.zeros(int(RATE * seconds), dtype=np.float32)


def decode_audio(data: bytes) -> Any:
    """16 kHz mono float32 samples from a WAV (read directly) or any other format (through PyAV)."""
    import numpy as np

    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(data)) as w:
                channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
                raw = w.readframes(w.getnframes())
            if width in (1, 2, 4):
                dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
                pcm = np.frombuffer(raw, dtype=dtype).astype(np.float32)
                pcm = (pcm - 128.0) / 128.0 if width == 1 else pcm / float(2 ** (8 * width - 1))
                if channels > 1:
                    pcm = pcm.reshape(-1, channels).mean(axis=1)
                if rate != RATE and len(pcm):
                    n = int(round(len(pcm) * RATE / rate))
                    pcm = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)
                return pcm.astype(np.float32)
        except (wave.Error, EOFError, ValueError):
            pass  # e.g. a float WAV: PyAV reads it
    from faster_whisper import decode_audio as fw_decode

    return fw_decode(io.BytesIO(data), sampling_rate=RATE)


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], bytes | None]:
    """(form fields, uploaded file) from a multipart/form-data body, without the removed ``cgi`` module."""
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    fields: dict[str, str] = {}
    upload: bytes | None = None
    if not msg.is_multipart():
        return fields, None
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if part.get_filename() is not None or name == "file":
            if name in (None, "file") or upload is None:
                upload = payload
        elif name:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, upload


class Handler(BaseHTTPRequestHandler):
    engine: Engine
    server_version = f"FasterWhisperLocal/{SERVER_VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # the access log goes to the log file, not a console
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("", "/health", "/v1/health"):
            h = self.engine.health()
            self._send(200 if h["status"] == "ok" else 503, h)
        elif path in ("/v1/models", "/models"):
            self._send(200, {"object": "list", "data": [{"id": self.engine.health()["model"], "object": "model",
                                                         "owned_by": ENGINE}]})
        else:
            self._send(404, {"error": f"Unknown path {path}. Use POST /inference or GET /health."})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ("/inference", "/v1/audio/transcriptions", "/audio/transcriptions"):
            self._send(404, {"error": f"Unknown path {path}. Use POST /inference."})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._send(413 if length > MAX_BODY else 400, {"error": "Send the recording as multipart/form-data 'file'."})
            return
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        if ctype.lower().startswith("multipart/"):
            fields, audio = parse_multipart(ctype, body)
        else:
            fields, audio = {}, body  # a raw WAV body works too
        if not audio:
            self._send(400, {"error": "No 'file' in the request."})
            return
        if self.engine.state != "ok":
            self._send(503, {"error": self.engine.error or "The speech model is still loading.", **self.engine.health()})
            return
        started = time.monotonic()
        try:
            samples = decode_audio(audio)
            text = self.engine.transcribe(samples, fields.get("language") or None, fields.get("prompt") or None,
                                          float(fields.get("temperature") or 0))
        except Exception as exc:
            log.error("transcription failed: %s\n%s", exc, traceback.format_exc())
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        ms = int((time.monotonic() - started) * 1000)
        log.info("transcribed %.1fs of audio in %d ms", len(samples) / RATE, ms)
        self._send(200, {"text": text, "duration_ms": ms, "engine": ENGINE})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local Faster-Whisper speech server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--model", required=True, help="folder holding model.bin, config.json, tokenizer.json")
    ap.add_argument("--lib", action="append", default=[], help="folder with the faster-whisper packages")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--language", default="en")
    ap.add_argument("--beam-size", type=int, default=5)
    ap.add_argument("--log", default="")
    args = ap.parse_args(argv)

    for lib in reversed(args.lib):
        if lib and os.path.isdir(lib):
            sys.path.insert(0, lib)
            if sys.platform == "win32":
                for sub in ("", "ctranslate2", "onnxruntime\\capi", "av.libs", "numpy.libs"):
                    d = os.path.join(lib, sub)
                    if os.path.isdir(d):
                        try:
                            os.add_dll_directory(d)
                        except OSError:
                            pass
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if args.log:
        handlers = [logging.FileHandler(args.log, encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(message)s")

    engine = Engine(os.path.abspath(args.model), args.device, args.compute_type, args.threads, args.language,
                    args.beam_size)
    Handler.engine = engine
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        log.error("cannot listen on %s:%s: %s", args.host, args.port, exc)
        return 2
    httpd.daemon_threads = True
    log.info("Faster-Whisper server on http://%s:%s (model %s)", args.host, args.port, args.model)
    threading.Thread(target=engine.load, name="model-load", daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
