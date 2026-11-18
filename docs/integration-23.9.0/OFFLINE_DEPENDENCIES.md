# Offline dependencies

The patch carries everything it needs. Installing it downloads nothing, and SkyGuard QA makes no network call for
OCR, mapping, calculations or charts. Live capture opens only the application pages you configure.

## OCR runtime (`ocr-runtime/wheels`, for 64-bit Windows and Python 3.13)

| Wheel | Purpose |
|---|---|
| rapidocr 3.10.0 | OCR pipeline. The detection and recognition ONNX models are inside the wheel. |
| onnxruntime 1.32.0 | Runs the models on the CPU. |
| opencv_python_headless 5.0.0.93 | Image operations, chart shapes. |
| numpy 2.5.5, pillow 13.0.0 | Arrays and image decoding and validation. |
| pyclipper 1.4.0, shapely 2.2.0 | Text-box geometry used by RapidOCR. |
| pyyaml 6.0.3, omegaconf 2.4.0, colorlog 6.12.0, tqdm 4.70.1, six 1.17.0, packaging 26.3, flatbuffers 25.12.19, protobuf 7.36.2 | RapidOCR and ONNX Runtime dependencies. |
| msvc_runtime 14.51.36231 | The Microsoft Visual C++ runtime DLLs (msvcp140, concrt140, vcomp140 ...), redistributable files from Microsoft. Unpacked into `site-packages\_msvc` and loaded by the OCR worker before ONNX Runtime and OpenCV, so an older copy in `Windows\System32` is never used. Nothing is installed in Windows and no administrator rights are needed. |
| requests 2.34.2, urllib3 2.8.0, idna 3.20, certifi 2026.10.23, charset_normalizer 3.5.2 | Imported by RapidOCR's model helper. The models are already local, so nothing is fetched. |

`ocr-runtime/WHEELS.json` lists the SHA-256 of each wheel, and the installer refuses a changed file. The wheels are
unpacked into their own folder (`runtime\ocr\site-packages`, or `%LOCALAPPDATA%\SkyGuardQA\ocr\site-packages`), which
only the OCR worker process adds to its path. The application's own packages (for example its pinned requests and
certifi) are not affected.

To build the patch again on a connected machine:

    pip download --only-binary=:all: --platform win_amd64 --python-version 3.13 --implementation cp -d wheels rapidocr==3.10.0 onnxruntime msvc-runtime
    python packaging/cumulative-patch/build_patch.py --out dist --ocr-wheels wheels

## Optional engines (not bundled)

* **Windows OCR**, part of Windows 10/11. It needs an OCR language installed (Settings > Time & language > Language)
  and PowerShell in Full Language Mode.
* **Tesseract**, portable. Put it in `runtime\tesseract`, or set its path in Preferences.

## Already in SkyGuard QA

Playwright with Chromium (live capture, filters), the local AI server (Qwen) for optional mapping suggestions, and
openpyxl/reportlab for the reports. These are unchanged.
