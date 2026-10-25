# Installing on a company PC without Internet (23.1.0)

The offline package is built for one path: copy it in, let the security team scan it, install it as a normal user,
and enter three things on one screen. No administrator rights, no Internet and no configuration commands.

## 1. Get the package

GitHub → Actions → the latest green CI run on `main` → Artifacts → **AIAgenticTestAgent-Offline-windows-zip**. It holds:

| File | What it is for |
|---|---|
| `AIAgenticTestAgent-Offline-<version>.zip` | The package. This is the file to scan and copy. |
| `SHA256SUMS.txt` | SHA-256 of the zip, to check that the copy on the company PC is the file that was scanned. |
| `AIAgenticTestAgent-Offline-<version>/sbom.cdx.json` | CycloneDX 1.5 SBOM (also inside the zip). |

## 2. What to give the security team

* **The zip and its SBOM.** The SBOM lists Python, the Temurin JRE, JMeter and every JMeter jar (with SHA-256),
  Chromium (with its real version), llama-server, the speech model and every Python package (as `pkg:pypi` purls).
* **Pinned and hash-checked inputs.** Every download in `packaging/offline-lock.json` has a SHA-256 that was checked
  against the publisher's own checksum before it was pinned; the build refuses a file that does not match. Python
  packages are installed with `pip --require-hashes` from `packaging/requirements-offline-win.txt`; pip itself is
  removed from the package afterwards.
* **Known-vulnerability check.** CI runs `pip-audit --strict` on the exact Python packages in the package and fails
  on any known advisory.
* **No network at install or run time.** `VALIDATE_OFFLINE.cmd` runs every check with outbound connections blocked.
  The dashboard listens on `127.0.0.1` only. The only outbound connections are the ones you configure: your TFS
  server, the applications you test and, if you choose a cloud AI, its endpoint.
* **No administrator rights, no services, no registry changes.** Files go to the user's profile (or a folder chosen
  with `--target`). Shortcuts are per user. Secrets (TFS PAT, application passwords) are kept in Windows Credential
  Manager, never in files or logs.
* **Per-file integrity.** `MANIFEST.json` holds the SHA-256 of every file; the installer and `VALIDATE_OFFLINE.cmd`
  refuse a damaged or changed copy.

Not covered, and worth asking IT about: the `.cmd` launchers cannot carry an Authenticode signature (Windows only
signs `.exe`, `.dll`, `.ps1` and similar), and some companies block programs that run from `%LOCALAPPDATA%`
(AppLocker / WDAC). If so, ask IT for an approved folder and install with `--target "<that folder>"`.

## 3. Install

1. Check the copied zip against `SHA256SUMS.txt` (the security team usually does this).
2. Right-click the zip → Properties → tick **Unblock** → OK. Then Extract All.
3. Double-click `INSTALL_OFFLINE.cmd` in the extracted folder. Default folder:
   `%LOCALAPPDATA%\Programs\AIAgenticTestAgent`. It validates Java, JMeter, Chromium, llama-server and the Python
   packages, and rolls back by itself if anything fails.

IT can pre-fill the setup screen for everyone (the PAT is never put on a command line):

```
INSTALL_OFFLINE.cmd --tfs-url "https://tfs-server/tfs/DefaultCollection" --tfs-project "MyProject" --llm-model "D:\Models\qwen.gguf"
```

## 4. First start: First-Run Setup

Start it from the Desktop or Start Menu shortcut. The first start opens **First-Run Setup**:

1. **TFS**: server URL, project, Personal Access Token. A `dev.azure.com` URL uses Azure DevOps Online; any other
   URL uses Local TFS / Azure DevOps Server. The connection is tested when you save.
2. **LLM model**: the Qwen `.gguf` file. The bundled llama-server is found automatically (another one can be
   chosen). It starts at once and with every start of the dashboard.
3. **Voice model** (optional): a Vosk model folder or `.zip`. Leave it blank to use the bundled offline model.

"Skip for now" opens the dashboard; the screen stays under Settings → First-Run Setup.

## For maintainers: updating pins

* Python packages: edit `requirements.txt`, then
  `uv pip compile requirements.txt --python-platform x86_64-pc-windows-msvc --python-version 3.13 --generate-hashes -o packaging/requirements-offline-win.txt`.
* Java and llama-server: `python packaging/update_pins.py --write` (needs Internet; takes releases at least 14 days
  old and checks each file against the publisher's checksum).
* Python runtime: take the newest 3.13 package from nuget.org, check its SHA-512 against the nuget.org catalog and pin
  its SHA-256 in `offline-lock.json`.
