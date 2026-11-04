# Patch 23.3.7: Faster-Whisper speech recognition

One patch for a PC on 23.2.2 or 23.3.0 to 23.3.6. It includes everything from 23.3.0 to 23.3.6.

## Why

The built-in offline recognizer (Vosk, small English model) often writes down the wrong words. A whisper.cpp
"local speech server" was configured at `http://127.0.0.1:8766`, but nothing that recognises speech ran there:
it answered HTTP 404, so listening for the wake name failed, while Check Voice Setup still showed PASS.

## What changes

* **Faster-Whisper** (1.2.1, CTranslate2 4.8.4, int8 on the CPU) recognises speech on this PC, with no Internet.
  The packages are installed from the patch into `<app>\runtime\faster-whisper\lib`; the application's own Python
  packages are not changed. The model goes to `<app>\runtime\faster-whisper\models\<model>` (small.en by default,
  about 465 MB).
* **Started with the application.** When the application starts it starts the Faster-Whisper server
  (`qa_platform\voice\whisper_server.py`) with its own Python on `127.0.0.1:8766`, and stops it when the application
  stops. If another program uses port 8766, the next free port (8767 to 8785) is used and the settings follow. If
  the server stopped, the next recording starts it again. Only `127.0.0.1` is used: nothing is reachable from the
  network.
* **Voice settings**: Speech recognition "Local speech server (Faster-Whisper)", Server type "Faster-Whisper on this
  PC (started with the application)", and a model list with the installed models. The server URL is set by the
  application. The other server types (whisper.cpp, OpenAI-compatible) and the built-in recognizer still work.
* **Better recognition of the commands**: every recording is sent with a short hint holding the wake name and the
  application's words (Test Plans, Test Suites, TFS, acceptance criteria, ...), so Whisper spells them the way the
  commands expect. Silence and noise are filtered (voice activity detection), so no invented sentence is executed.
* **Honest voice check.** Check Voice Setup and the SPEECH badge now send a short test recording to the real
  recognition address (`/inference`, or `/audio/transcriptions`) and only show PASS when text comes back. "HTTP 404",
  a web page or JSON without text is FAIL, with what to do. Before, any answer below HTTP 500 to the server's home
  page was shown as PASS.
* New: `GET /api/voice/faster-whisper` (installed models, state, URL) and `POST /api/voice/faster-whisper/start`,
  `/restart`, `/stop`. The server's own `GET /health` and `POST /inference` (whisper.cpp form) and
  `POST /v1/audio/transcriptions` (OpenAI form) can be used by other local tools.

## Kept

The wake name, the form of address, the reply wording, voice permissions and privacy settings, the TFS connection,
the On-Premise AI settings, the database and reports. If Faster-Whisper cannot be set up (for example no model on an
offline PC), the application is still updated and the speech recognition setting is left as it was.

## Models

| Model    | Size    | Notes                                          |
|----------|---------|------------------------------------------------|
| small.en | ~465 MB | recommended: accurate English; usually 1 to 3 s per command on a laptop CPU |
| base.en  | ~140 MB | faster, less accurate                          |
| medium.en| ~1.5 GB | most accurate English, slow on a CPU           |

Install another model: `APPLY_PATCH.ps1 -Model base.en` (with Internet or its zip in Downloads), then choose it under
Voice & Command Agent. The model files come from Hugging Face (`Systran/faster-whisper-<model>`, MIT licence) and
are checked against the SHA-256 Hugging Face publishes for each file.

## Files (in addition to 23.3.6)

qa_platform/voice: `fw_service.py` and `whisper_server.py` (new), `service.py`; qa_platform: `__init__.py`,
`config.py`, `health.py`, `runtime.py`, `api/voice.py`; web/js: `pages/voice.js`, `pages/command-center.js`,
`voice-agent.js`. Outside the application folder: `faster-whisper\wheels` (24 Python wheels for Python 3.13,
64-bit Windows), `GET_FASTER_WHISPER_MODEL.ps1`.
