"""Helper for APPLY_PATCH.ps1 (23.3.15; the Faster-Whisper steps are those of 23.3.7), run by the application's own Python. Standard library only.

  manifest        record the replaced files in the app's MANIFEST.json, so VALIDATE_OFFLINE.cmd stays clean
  voice-style     switch the reply wording from the old default "standard" to "conversational" (kept from 23.3.6)
  fw-install      install the Faster-Whisper packages from the patch's wheels folder (offline) into
                  <app>\\runtime\\faster-whisper\\lib, without touching the application's own packages
  fw-model        install a Faster-Whisper model from a zip or folder into <app>\\runtime\\faster-whisper\\models\\<name>
  fw-find-model   print the path of a model zip or folder found next to the patch, in Downloads, Desktop or Documents
  fw-selftest     load the model and transcribe a recording (a Windows voice saying a command), as the service will
  fw-settings     point the voice settings at Faster-Whisper (wake name, wording, TFS and AI settings are kept)
  llm-report      print how the local AI server is set up (managed by the application or not, model, URL)
  llm-stop-old    stop the llama-server this application started before the patch, so it restarts with 23.3.9 options
  llm-speed       switch the managed llama-server to 2 parallel slots with a GPU when present (kept if already set)
  llm-find-model  print the path of a fast model .gguf next to the patch, in Downloads, Desktop or Documents
  llm-manage      let the application start llama-server itself (one slot on a CPU, 8,192 tokens), with a model
  llm-model       copy a .gguf into <app>\\runtime\\ai\\models and make it the managed server's model
  psutil-install  (23.3.22) install psutil from the patch's wheels folder into the application's Python, offline;
                  it records the load generator's CPU, memory and disk during performance runs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

PATCH_VERSION = "23.3.28"
PSUTIL_VERSION = "7.2.2"
FW_VERSION = "1.2.1"
MODEL_FILES = ("model.bin", "config.json")
TOKENIZER_FILES = ("tokenizer.json", "vocabulary.txt", "vocabulary.json")


def say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


# --- manifest and wording (as in 23.3.6) -------------------------------------------------------------------------

def cmd_manifest(args: argparse.Namespace) -> int:
    app = Path(args.app)
    path = app / "MANIFEST.json"
    if not path.exists():
        say("No MANIFEST.json (not an offline-kit install); nothing to record.")
        return 0
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))  # 23.3.11: a BOM written by a Windows tool is fine
    files = manifest.setdefault("files", {})
    rels = [line.strip().replace("\\", "/") for line in Path(args.files).read_text(encoding="utf-8-sig").splitlines()]
    for rel in filter(None, rels):
        target = app / rel
        if target.is_file():
            files[rel] = sha256(target)
    before = manifest.get("version", "unknown")
    manifest["version"] = PATCH_VERSION
    manifest.setdefault("patches", []).append({"patch": PATCH_VERSION, "from": before,
                                              "applied_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                              "files": len(rels)})
    path.write_text(json.dumps(manifest, indent=1, sort_keys=False), encoding="utf-8")
    say(f"MANIFEST.json updated: {len(rels)} files recorded, version {before} -> {PATCH_VERSION}.")
    return 0


def _load_settings(data: Path) -> tuple[Path, dict]:
    path = data / "settings.json"
    if path.exists():
        return path, json.loads(path.read_text(encoding="utf-8-sig"))
    return path, {}


def _save_settings(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"settings.before-{PATCH_VERSION}.json")
        if not backup.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    tmp.replace(path)


def cmd_voice_style(args: argparse.Namespace) -> int:
    path, data = _load_settings(Path(args.data))
    if not data:
        say("No settings file yet: new installations start with the conversational wording.")
        return 0
    voice = data.setdefault("voice", {})
    style = voice.get("phrase_style", "standard")
    if style != "standard":
        say(f"Reply wording kept as '{style}'.")
        return 0
    voice["phrase_style"] = "conversational"
    _save_settings(path, data)
    say("Reply wording set to conversational (\"Understood, Boss.\").")
    return 0


# --- Faster-Whisper packages -------------------------------------------------------------------------------------

def _installed_version(lib: Path) -> str | None:
    for d in lib.glob("faster_whisper-*.dist-info"):
        return d.name.removesuffix(".dist-info").split("-", 1)[1]
    return None


def _check_import(lib: Path) -> tuple[bool, str]:
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import os\n"
            "if hasattr(os, 'add_dll_directory'):\n"
            "    [os.add_dll_directory(os.path.join(sys.argv[1], d)) for d in ('ctranslate2', 'onnxruntime/capi') "
            "if os.path.isdir(os.path.join(sys.argv[1], d))]\n"
            "import ctranslate2, onnxruntime, av, numpy, tokenizers, faster_whisper\n"
            "print('faster-whisper', faster_whisper.__version__, '| ctranslate2', ctranslate2.__version__, "
            "'| CPU int8:', 'int8' in ctranslate2.get_supported_compute_types('cpu'))")
    out = subprocess.run([sys.executable, "-E", "-s", "-c", code, str(lib)], capture_output=True, text=True,
                         cwd=str(lib.parent))
    lines = (out.stdout or out.stderr).strip().splitlines()
    return out.returncode == 0, (lines[-1] if lines else "")


def cmd_fw_install(args: argparse.Namespace) -> int:
    if sys.version_info[:2] != (3, 13) or sys.platform != "win32":
        say(f"The bundled Faster-Whisper packages are for Python 3.13 on 64-bit Windows; this is Python "
            f"{sys.version.split()[0]} on {sys.platform}.")
        if not args.force:
            return 3
    wheels = Path(args.wheels)
    lib = Path(args.home) / "lib"
    if _installed_version(lib) == FW_VERSION:
        ok, detail = _check_import(lib)
        if ok:
            say(f"Faster-Whisper packages already installed: {detail}")
            return 0
        say(f"The installed packages do not load ({detail}); installing them again.")
    whls = sorted(wheels.glob("*.whl"))
    if not whls:
        say("The patch's faster-whisper\\wheels folder is empty. Extract the whole patch zip again.")
        return 2
    staging = lib.with_name("lib.new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    pip = subprocess.run([sys.executable, "-E", "-s", "-m", "pip", "--version"], capture_output=True, text=True)
    done = False
    if pip.returncode == 0:
        say(f"Installing faster-whisper {FW_VERSION} and its packages from the patch (offline, {len(whls)} wheels)...")
        out = subprocess.run([sys.executable, "-E", "-s", "-m", "pip", "install", "--no-index", "--find-links",
                              str(wheels), "--target", str(staging), "--disable-pip-version-check",
                              "--no-warn-script-location", "--no-compile", f"faster-whisper=={FW_VERSION}"],
                             capture_output=True, text=True)
        done = out.returncode == 0
        if not done:
            say("pip could not install them (" + (out.stderr.strip().splitlines() or ["?"])[-1] + "); unpacking instead.")
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
    if not done:
        say(f"Unpacking {len(whls)} wheels into {lib} ...")
        for whl in whls:
            with zipfile.ZipFile(whl) as z:
                for name in z.namelist():
                    top, _, rest = name.partition("/")
                    if top.endswith(".data"):  # <pkg>.data/purelib|platlib/<files> go to the top; scripts are skipped
                        kind, _, rest2 = rest.partition("/")
                        if kind not in ("purelib", "platlib") or not rest2:
                            continue
                        target = staging / rest2
                    else:
                        target = staging / name
                    if name.endswith("/"):
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(name) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    ok, detail = _check_import(staging)
    if not ok:
        say(f"The Faster-Whisper packages do not load on this PC: {detail}")
        if "DLL" in detail or "dll" in detail:
            say("Install the Microsoft Visual C++ Redistributable 2015-2022 (x64), vc_redist.x64.exe, then run the "
                "patch again.")
        shutil.rmtree(staging, ignore_errors=True)
        return 4
    old = lib.with_name("lib.old")
    shutil.rmtree(old, ignore_errors=True)
    if lib.exists():
        lib.rename(old)
    staging.rename(lib)
    shutil.rmtree(old, ignore_errors=True)
    say(f"Faster-Whisper packages installed in {lib}: {detail}")
    return 0


# --- models ------------------------------------------------------------------------------------------------------

def _is_model(folder: Path) -> bool:
    return folder.is_dir() and all((folder / f).is_file() for f in MODEL_FILES) and \
        any((folder / f).is_file() for f in TOKENIZER_FILES)


def _model_name_from(path: Path) -> str | None:
    stem = path.name[:-4] if path.name.lower().endswith(".zip") else path.name
    if stem.lower().startswith("faster-whisper-"):
        return stem[len("faster-whisper-"):]
    return None


def candidates(patch_dir: Path, model: str) -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    dirs = [patch_dir, patch_dir.parent, home / "Downloads", home / "Desktop", home / "Documents"]
    out: list[Path] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in (d / f"faster-whisper-{model}.zip", d / f"faster-whisper-{model}"):
            if p.exists():
                out.append(p)
    return out


def cmd_fw_find_model(args: argparse.Namespace) -> int:
    found = candidates(Path(args.patch_dir), args.model)
    if found:
        print(found[0])
        return 0
    return 1


def cmd_fw_model(args: argparse.Namespace) -> int:
    src = Path(args.source)
    models = Path(args.home) / "models"
    models.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="fw-model-", dir=str(models)))
    try:
        if src.is_file() and src.suffix.lower() == ".zip":
            say(f"Unpacking {src.name} ({src.stat().st_size // 1048576} MB)...")
            with zipfile.ZipFile(src) as z:
                bad = z.testzip()
                if bad:
                    say(f"The zip is damaged ({bad}). Copy it again.")
                    return 2
                for name in z.namelist():
                    target = (tmp / name).resolve()
                    if not str(target).startswith(str(tmp.resolve())):  # no paths outside the folder
                        continue
                    if name.endswith("/"):
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(name) as s, target.open("wb") as d:
                        shutil.copyfileobj(s, d)
            root = tmp
        elif src.is_dir():
            root = src
        else:
            say(f"Not a model zip or folder: {src}")
            return 2
        found = [p.parent for p in root.rglob("model.bin") if _is_model(p.parent)]
        if not found:
            say(f"No Faster-Whisper model (model.bin, config.json, tokenizer.json) in {src}.")
            return 2
        folder = found[0]
        name = args.name or _model_name_from(folder) or _model_name_from(src) or "small.en"
        source = folder / "SOURCE.json"
        if source.is_file():  # written by GET_FASTER_WHISPER_MODEL.ps1: the checksums Hugging Face published
            info = json.loads(source.read_text(encoding="utf-8-sig"))
            for fname, meta in (info.get("files") or {}).items():
                want = (meta or {}).get("sha256")
                f = folder / fname
                if want and f.is_file() and sha256(f).lower() != want.lower():
                    say(f"{fname} does not match its published SHA-256. Download the model again.")
                    return 2
            say(f"Checksums verified against {info.get('url', 'the source')} (revision "
                f"{str(info.get('revision', '?'))[:12]}).")
            name = info.get("model") or name
        dest = models / name
        if dest.exists():
            shutil.rmtree(dest)
        if folder.is_relative_to(tmp):
            folder.rename(dest)
        else:
            shutil.copytree(folder, dest)
        size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file()) // 1048576
        say(f"Model {name} installed in {dest} ({size} MB).")
        print(f"MODEL={name}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_fw_selftest(args: argparse.Namespace) -> int:
    home = Path(args.home)
    model = home / "models" / args.model
    code = r'''
import os, sys, time, wave
lib, model, wav_path, prompt = sys.argv[1:5]
sys.path.insert(0, lib)
for d in ("ctranslate2", "onnxruntime/capi"):
    if hasattr(os, "add_dll_directory") and os.path.isdir(os.path.join(lib, d)):
        os.add_dll_directory(os.path.join(lib, d))
import numpy as np
from faster_whisper import WhisperModel
t = time.monotonic()
try:
    m = WhisperModel(model, device="cpu", compute_type="int8", local_files_only=True)
except ValueError:
    m = WhisperModel(model, device="cpu", compute_type="default", local_files_only=True)
print(f"LOAD={time.monotonic() - t:.1f}")
if wav_path and os.path.isfile(wav_path):
    with wave.open(wav_path) as w:
        ch, width, rate, raw = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if rate != 16000:
        n = int(round(len(a) * 16000 / rate))
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
else:
    a = np.zeros(16000, dtype=np.float32)
t = time.monotonic()
segs, _ = m.transcribe(a, language="en", beam_size=5, vad_filter=True, initial_prompt=prompt or None,
                       condition_on_previous_text=False, without_timestamps=True)
text = " ".join(s.text.strip() for s in segs).strip()
print(f"TIME={time.monotonic() - t:.1f}")
print(f"TEXT={text}")
'''
    out = subprocess.run([sys.executable, "-E", "-s", "-c", code, str(home / "lib"), str(model), args.wav or "",
                          args.prompt or ""], capture_output=True, text=True, cwd=str(home))
    res = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line and line[:4] in ("LOAD", "TIME", "TEXT"))
    if out.returncode != 0 or "TEXT" not in res:
        say("The speech model could not be run: " + ((out.stderr or out.stdout).strip().splitlines() or ["?"])[-1])
        return 4
    say(f"Model {args.model} loaded in {res['LOAD']} s; a {'spoken test sentence' if args.wav else 'silent test'} "
        f"was transcribed in {res['TIME']} s.")
    if args.wav:
        say(f"Heard: \"{res['TEXT']}\"")
        if args.expect:
            want = [w for w in "".join(c.lower() if c.isalnum() else " " for c in args.expect).split()]
            got = "".join(c.lower() if c.isalnum() else " " for c in res["TEXT"]).split()
            hits = sum(1 for w in want if w in got)
            say(f"Expected \"{args.expect}\": {hits} of {len(want)} words recognised.")
    return 0


def cmd_fw_settings(args: argparse.Namespace) -> int:
    path, data = _load_settings(Path(args.data))
    voice = data.setdefault("voice", {})
    before = {k: voice.get(k) for k in ("stt_engine", "stt_api", "stt_url")}
    port = int(voice.get("fw_port") or 8766)
    voice.update({"stt_engine": "local", "stt_api": "faster_whisper", "stt_url": f"http://127.0.0.1:{port}",
                  "fw_model": args.model, "fw_port": port, "fw_auto_start": True})
    if voice.get("voice_enabled") is False:
        voice["voice_enabled"] = True
        say("Voice input was off; it is on again (typed commands still work).")
    _save_settings(path, data)
    say(f"Speech recognition: Faster-Whisper ({args.model}) at http://127.0.0.1:{port}, started with the "
        f"application (was {before['stt_engine'] or 'built-in'} {before['stt_api'] or ''} {before['stt_url'] or ''})."
        .replace("  ", " "))
    names = (voice.get("agent_command_name"), voice.get("address_as"))
    say(f"Kept: wake name \"{names[0] or 'Hami'}\", address \"{names[1] or ''}\", TFS and On-Premise AI settings.")
    return 0


# --- local AI server (23.3.9) ---------------------------------------------------------------------------------------

FAST_MODEL_FILE = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
FAST_MODEL_ALIAS = "qwen3-4b-instruct"


def _ai(data: dict) -> tuple[dict, dict]:
    ai = data.setdefault("ai", {})
    return ai, ai.setdefault("managed", {}) if isinstance(ai.get("managed", {}), dict) else {}


def _llama_servers(app: Path) -> list[Path]:
    found: list[Path] = []
    for folder in (app / "runtime" / "ai", app.parent / "runtime" / "ai"):
        if folder.is_dir():
            found += [p for p in folder.rglob("llama-server*") if p.suffix.lower() in (".exe", "")]
    return found


def cmd_llm_report(args: argparse.Namespace) -> int:
    _, data = _load_settings(Path(args.data))
    ai = data.get("ai") or {}
    managed = ai.get("managed") or {}
    servers = _llama_servers(Path(args.app))
    say(f"AI mode         : {ai.get('mode') or 'on_premise'}")
    say(f"AI server URL   : {ai.get('base_url') or 'http://127.0.0.1:8080/v1 (default)'}   model name: "
        f"{ai.get('model') or '(server default)'}")
    model = managed.get("model_path") or ""
    if model:
        size = Path(model).stat().st_size / 1048576 if Path(model).is_file() else 0
        say(f"Managed model   : {model}" + (f" ({size:,.0f} MB)" if size else " (file not found)"))
    say(f"Managed server  : {managed.get('server_path') or (str(servers[0]) if servers else 'not found in runtime\\ai')}"
        f"; started with the application: {'yes' if managed.get('auto_start') else 'no'}")
    if not managed.get("auto_start"):
        say("NOTE: the application does not start the AI server itself. The speed options of 23.3.9 (2 slots, GPU)")
        say("      only apply to a server the application starts; for your own server see PATCH_23.3.9.md.")
    return 0


def cmd_llm_stop_old(args: argparse.Namespace) -> int:
    pid_file = Path(args.data) / "managed-llm.json"
    try:
        saved = json.loads(pid_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        say("No AI server started by the application is running.")
        return 0
    pid, exe = int(saved.get("pid") or 0), str(saved.get("executable") or "")
    if not pid or "llama-server" not in Path(exe).name.lower():
        return 0
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True)
        if Path(exe).name.lower() not in out.stdout.lower():
            say("The AI server started earlier is no longer running.")
            pid_file.unlink(missing_ok=True)
            return 0
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    pid_file.unlink(missing_ok=True)
    say(f"Stopped the AI server the application started before (llama-server, PID {pid}); it restarts with the "
        "23.3.9 options when the application starts.")
    return 0


def cmd_llm_speed(args: argparse.Namespace) -> int:
    path, data = _load_settings(Path(args.data))
    ai, managed = _ai(data)
    changed = []
    for key, value in (("parallel", "auto"), ("gpu_layers", "auto")):
        if managed.get(key) in (None, 2) and managed.get(key) != value:  # 2 was the 23.3.9 value
            managed[key] = value
            changed.append(f"{key}={value}")
    if "parallel" not in ai:
        ai["parallel"] = 2
        changed.append("2 AI requests at a time")
    if changed:
        _save_settings(path, data)
        say("AI speed settings: " + ", ".join(changed) + ".")
    else:
        say("AI speed settings kept as they are.")
    return 0


def cmd_llm_manage(args: argparse.Namespace) -> int:
    path, data = _load_settings(Path(args.data))
    ai, managed = _ai(data)
    if args.source and Path(args.source).is_file():
        managed["model_path"] = args.source
    elif args.source:
        say(f"Your server's model {args.source} was not found.")
    servers = _llama_servers(Path(args.app))
    models = [p for f in (Path(args.app) / "runtime" / "ai", Path(args.app).parent / "runtime" / "ai") if f.is_dir()
              for p in f.rglob("*.gguf") if "mmproj" not in p.name.lower()]
    if not (managed.get("model_path") and Path(managed["model_path"]).is_file()) and not models:
        say("No model (.gguf) for the application's llama-server: run the patch with -FastModel, or pass "
            "-FastModelFile <your .gguf>. Nothing was changed.")
        return 3
    if not (managed.get("server_path") and Path(managed["server_path"]).is_file()) and not servers:
        say("llama-server.exe was not found in <app>\\runtime\\ai. Nothing was changed.")
        return 3
    if not managed.get("server_path") and servers:
        managed["server_path"] = str(servers[0])
    url = str(ai.get("base_url") or "http://127.0.0.1:8080/v1")
    try:
        managed["port"] = int(url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1])
    except (IndexError, ValueError):
        pass
    managed.update({"auto_start": True, "parallel": "auto", "gpu_layers": managed.get("gpu_layers", "auto"),
                    "context_size": max(8192, int(managed.get("context_size") or 8192))})
    managed.setdefault("alias", "qwen")
    ai["model"] = managed["alias"]
    _save_settings(path, data)
    say(f"The application now starts llama-server itself: {Path(managed['model_path']).name if managed.get('model_path') else 'the model found in runtime\\ai'}, "
        f"one slot of {managed['context_size']} tokens on a CPU (two with a GPU), port {managed.get('port', 8080)}.")
    return 0


def cmd_llm_find_model(args: argparse.Namespace) -> int:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for folder in (Path(args.patch_dir), Path(args.patch_dir).parent, home / "Downloads", home / "Desktop",
                   home / "Documents"):
        f = folder / FAST_MODEL_FILE
        if f.is_file() and f.stat().st_size > 1_000_000_000:
            print(f)
            return 0
    return 1


def cmd_llm_model(args: argparse.Namespace) -> int:
    src = Path(args.source)
    with open(src, "rb") as fh:
        if fh.read(4) != b"GGUF":
            say(f"{src} is not a GGUF model file.")
            return 2
    dest_dir = Path(args.app) / "runtime" / "ai" / "models"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        say(f"Copying {src.name} ({src.stat().st_size / 1048576:,.0f} MB) to {dest_dir} ...")
        shutil.copyfile(src, dest.with_suffix(".part"))
        dest.with_suffix(".part").replace(dest)
    path, data = _load_settings(Path(args.data))
    ai, managed = _ai(data)
    before = managed.get("model_path") or "(found automatically)"
    managed.update({"model_path": str(dest), "alias": args.alias, "auto_start": True})
    ai["model"] = args.alias
    ai["mode"] = "on_premise"
    _save_settings(path, data)
    say(f"Local AI model: {dest.name} (was {before}). The application starts llama-server with it.")
    return 0


# --- psutil (23.3.22) ---------------------------------------------------------------------------------------------

def _psutil_ok() -> tuple[bool, str]:
    out = subprocess.run([sys.executable, "-E", "-s", "-c", "import psutil; print(psutil.__version__)"],
                         capture_output=True, text=True)
    return out.returncode == 0, (out.stdout or out.stderr).strip().splitlines()[-1:][0] if (out.stdout or out.stderr).strip() else ""


def cmd_psutil_install(args: argparse.Namespace) -> int:
    ok, version = _psutil_ok()
    if ok and version == PSUTIL_VERSION:
        say(f"psutil {version} is already installed.")
        return 0
    wheels = sorted(Path(args.wheels).glob("psutil-*.whl"))
    if not wheels:
        say("The patch's wheels folder has no psutil wheel. Extract the whole patch zip again.")
        return 2
    import sysconfig
    target = Path(sysconfig.get_paths()["purelib"])
    pip = subprocess.run([sys.executable, "-E", "-s", "-m", "pip", "--version"], capture_output=True, text=True)
    done = False
    if pip.returncode == 0:
        out = subprocess.run([sys.executable, "-E", "-s", "-m", "pip", "install", "--no-index", "--find-links",
                              str(Path(args.wheels)), "--disable-pip-version-check", "--no-warn-script-location",
                              "--upgrade", f"psutil=={PSUTIL_VERSION}"], capture_output=True, text=True)
        done = out.returncode == 0
        if not done:
            say("pip could not install psutil (" + (out.stderr.strip().splitlines() or ["?"])[-1] + "); unpacking instead.")
    if not done:
        for old in target.glob("psutil*"):
            shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
        with zipfile.ZipFile(wheels[-1]) as z:
            z.extractall(target)
    ok, version = _psutil_ok()
    if not ok:
        say(f"psutil does not load on this PC ({version}). Performance runs still work; the load generator's CPU and "
            "memory are then not recorded.")
        return 4
    say(f"psutil {version} installed in {target}.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest")
    m.add_argument("--app", required=True)
    m.add_argument("--files", required=True)
    v = sub.add_parser("voice-style")
    v.add_argument("--data", required=True)
    i = sub.add_parser("fw-install")
    i.add_argument("--wheels", required=True)
    i.add_argument("--home", required=True)
    i.add_argument("--force", action="store_true")
    f = sub.add_parser("fw-find-model")
    f.add_argument("--patch-dir", required=True)
    f.add_argument("--model", required=True)
    md = sub.add_parser("fw-model")
    md.add_argument("--source", required=True)
    md.add_argument("--home", required=True)
    md.add_argument("--name", default="")
    t = sub.add_parser("fw-selftest")
    t.add_argument("--home", required=True)
    t.add_argument("--model", required=True)
    t.add_argument("--wav", default="")
    t.add_argument("--expect", default="")
    t.add_argument("--prompt", default="")
    s = sub.add_parser("fw-settings")
    s.add_argument("--data", required=True)
    s.add_argument("--model", required=True)
    for name in ("llm-report", "llm-stop-old", "llm-speed", "llm-model", "llm-find-model", "llm-manage"):
        x = sub.add_parser(name)
        x.add_argument("--data", default="")
        x.add_argument("--app", default="")
        x.add_argument("--source", default="")
        x.add_argument("--alias", default=FAST_MODEL_ALIAS)
        x.add_argument("--patch-dir", default="")
    ps = sub.add_parser("psutil-install")
    ps.add_argument("--wheels", required=True)
    args = ap.parse_args()
    return {"manifest": cmd_manifest, "voice-style": cmd_voice_style, "fw-install": cmd_fw_install,
            "fw-find-model": cmd_fw_find_model, "fw-model": cmd_fw_model, "fw-selftest": cmd_fw_selftest,
            "fw-settings": cmd_fw_settings, "llm-report": cmd_llm_report, "llm-stop-old": cmd_llm_stop_old,
            "llm-speed": cmd_llm_speed, "llm-model": cmd_llm_model, "llm-find-model": cmd_llm_find_model,
            "llm-manage": cmd_llm_manage, "psutil-install": cmd_psutil_install}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
