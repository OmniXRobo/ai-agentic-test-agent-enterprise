# Known limitations and what was not verified

This list keeps to what was actually run. Anything not checked here is stated as not checked.

## Not verified (no Windows desktop in this environment; GitHub's Windows runners ran the test suite)

* **Windows install as a standard user.** `install_patch.py` apply / verify / rollback, the automatic rollback, the
  OCR runtime staging and swap, and the side-by-side install were run on Linux against real 23.3.35 to 23.8.0 trees
  (`tests/test_cumulative_patch_23900.py`). `APPLY_PATCH.ps1/.cmd` were not executed. Their logic is unchanged apart
  from `-SideBySide` and the log-folder fallback.
* **The bundled OCR runtime on Windows.** The same RapidOCR 3.10.0, ONNX Runtime and OpenCV versions were run here on
  Linux wheels. The Windows `win_amd64` wheels in the patch were checked by hash and by unpacking, but not executed.
  The installer's OCR engine probe is the first check on Windows. If it fails, the patch rolls back.
* **Windows OCR (`Windows.Media.Ocr`).** Executed on GitHub's Windows runners (Windows Server), where it read the
  three test screenshots and the comparison passed after two fixes it made necessary ("IO" read for 10, a lone "0"
  not read). That is a server image with one language; Windows 10/11 desktops with other display languages were not
  tested. A cell that Windows OCR still cannot read stays empty and is reported as needing review.
* **Window maximize on a real Windows desktop.** Tested under Xvfb without a window manager: the CDP request and the
  work-area fallback fill the screen for the first page and for a popup. The fixed size and headless modes were also
  checked. Windows 10/11 with several monitors and DPI scaling was not tested.
* **Public live sites.** Live capture and filters were tested on local pages that reproduce the patterns: a JS table
  with pagination, a virtual ARIA grid, iframes with rowspan/colspan and totals, shadow DOM, a div grid, a
  DataTables-style API, SVG/d3/aria charts and canvas charts read by OCR. **The DataTables page is a stand-in that
  implements the `$.fn.dataTable` API shape. It is not the real DataTables library**, which is not available offline
  here. Real enterprise applications with SSO, Citrix or RDP were not available.
* **Your screenshots.** The screenshots mentioned in the request were not in the project files. Representative
  fixtures were generated instead (tables, a detailed table with tax and a grand total, bar, unlabeled bar, horizontal
  bar, pie, donut, summary with Share %), rendered by Chromium and read by the real OCR. Results on your own
  screenshots may differ. Each table and chart shows its extraction status and issues so that you can see it.

## Behaviour to know

* **Values read from an axis** (a bar without a label) are approximate. Within the axis tolerance they are NEEDS
  REVIEW, never PASS.
* **Pie charts without percentage labels** are measured from the slice areas. They are approximate and need review.
* **The OCR quality depends on the image.** Very small text (under about 9 px), heavy JPEG compression and
  low-contrast themes give lower-confidence words. Those are marked NEEDS REVIEW, and values are never guessed.
* **Filters:** custom dropdowns that only open on hover, canvas-drawn controls and controls inside closed shadow
  roots cannot be set automatically. The session pauses, and the tester sets them in the visible browser. Filter
  links are suggestions until a tester approves them. Filter sets run only when approved.
* **Record key with different scopes:** when two applications currently hold different records, the identifier is
  suggested by name only, for review. A run without an approved key reports INCOMPLETE ("Record key").
* **The local AI** helps only with field names the rules cannot match. If it is offline or answers badly, the rules
  alone decide, and the analysis says so.
* **Python version:** the bundled OCR runtime is for Python 3.13 (the SkyGuard QA runtime). An installation on
  another Python keeps working without it: choose Windows OCR or Tesseract in Preferences.
* **Disk space:** about 270 MB for the OCR runtime, plus the previous runtime kept until rollback is no longer needed
  (the `site-packages.before-23.9.0-*` folder can be deleted after a successful check).
