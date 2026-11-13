# Offline dependencies

Everything the companion needs is in the package; nothing is downloaded on the user's PC. Every file was
downloaded once, at build time, and checked against the SHA-256 pinned in SkyGuard's `packaging/offline-lock.json`
before it was packaged. The installer checks the whole package against `SHA256_CHECKSUM.txt` before installing, and
pip installs the wheels with `--require-hashes --no-index`.

`offline\OFFLINE_MANIFEST.json` lists the exact files, versions, sources and SHA-256 values of this package.

## Bundled

| Component | Version | File | SHA-256 | Licence |
| --- | --- | --- | --- | --- |
| Python (CPython, NuGet package) | 3.13.17 | `offline\python.3.13.17.nupkg` | `dce409744b088f94c6e37ba41f8100c384c27e029e843237364c602d30d475e5` | PSF-2.0 |
| llama.cpp llama-server, Windows x64 CPU | b10549 | `offline\llama-b10549-bin-win-cpu-x64.zip` | `11d38f2ed878489b2c3d02b3d1a67683c02fbfb3d265876b9ede749a8dff5f1c` | MIT |
| Faster-Whisper 1.2.1 and its dependencies (CTranslate2, onnxruntime, PyAV, numpy, tokenizers, ...) | as pinned | `offline\wheels\*.whl`, list in `offline\fw-requirements.txt` | in `fw-requirements.txt` (pip `--hash`) and `OFFLINE_MANIFEST.json` | each package's own (MIT, Apache-2.0, BSD) |

`hf-xet` (a download accelerator in SkyGuard's speech requirements) is left out: it is only used for downloading.

The companion itself and the agent core use only the Python standard library.

## Not bundled (copied from SkyGuard or your own media)

The AI and speech models are large (about 1.1 GB and 0.5 GB). They are not in this zip. `Install-Models.ps1` copies
them from a SkyGuard QA installation on the same PC or from a folder you give (`-From`), and refuses a file whose
SHA-256 differs:

| Model | File | SHA-256 | Source | Licence |
| --- | --- | --- | --- | --- |
| Qwen2.5-1.5B-Instruct Q4_K_M | `qwen2.5-1.5b-instruct-q4_k_m.gguf` | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` | Qwen/Qwen2.5-1.5B-Instruct-GGUF, revision 91cad511 | Apache-2.0 |
| Faster-Whisper small.en | `model.bin` | `62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a` | Systran/faster-whisper-small.en, revision d1d751a5 | MIT |
| | `config.json` | `666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae` | | |
| | `tokenizer.json` | `929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df` | | |
| | `vocabulary.txt` | `ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf` | | |

To prepare offline media for a PC without SkyGuard, copy these files from any SkyGuard QA installation
(`...\AIAgenticTestAgent\app\runtime\ai\models\` and `...\faster-whisper\models\small.en\`) to a USB drive and run
`Install-Models.ps1 -From E:\models`.

Without the models the agent works with its deterministic request parser and without voice.

## Browser

Google Chrome or Microsoft Edge 116 or later must already be installed. The package does not contain a browser.
