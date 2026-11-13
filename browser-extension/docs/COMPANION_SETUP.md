# The local companion

The companion is a small local service (Python, standard library only) that the extension talks to. It holds
everything that must not live in the browser: the knowledge database, the encrypted credential vault, the audit
trail, companion users, and the connection to the local AI and speech models.

* Listens on `http://127.0.0.1:8790` only (never on the network).
* Answers only the extension with ID `fmiiaecpdgbhhhbbkkfhnjfkbmllladb` (or IDs added to `extension_ids`), and only
  after that browser was paired.
* Runs as your Windows user. Starts when you sign in to Windows (unless installed with `-NoAutostart`).

## Folders

```
%LOCALAPPDATA%\SkyGuardAgent\
  app\                    companion and agent core
  runtime\python\         Python 3.13.17
  runtime\llama\          llama-server b10549 (CPU)
  runtime\faster-whisper\ Faster-Whisper packages
  models\                 Qwen GGUF and Faster-Whisper small.en (Install-Models.ps1)
  extension\              the unpacked extension (Load unpacked points here)
  data\
    config.json           settings (below)
    users.json            companion users (PBKDF2 password hashes)
    devices.json          paired browsers (hashed device secrets)
    opsagent\             knowledge.sqlite3 (knowledge, runs, audit) and backups\
    vault\                saved application passwords, encrypted with Windows DPAPI (your user only)
    logs\                 companion.log, llama-server.log, whisper-server.log (never contain passwords)
```

`SKYGUARD_AGENT_HOME` moves all of it (for example to a data drive); set it for your user before installing.

## Commands

`%LOCALAPPDATA%\SkyGuardAgent\SkyGuardAgent.cmd <command>` (also in the Start menu):

| Command | What |
| --- | --- |
| `pair` | shows a new pairing code (8 digits, valid 10 minutes, single use, 5 attempts) |
| `status` | is the companion running, on which port, which version |
| `check` | read-only prerequisite check (Windows, Python, SQLite, browsers, port, models, vault) |
| `version` | prints the version |
| `serve` | runs the companion (normally started for you) |

## Pairing and users

1. Pairing proves that this browser on this PC may talk to the companion. The extension receives a device secret,
   kept in the browser's extension storage; the companion stores only its SHA-256 hash. To unpair every browser, stop
   the companion and delete `data\devices.json` (each browser then pairs again with a new code).
2. Companion users: the first user created after pairing is an admin. Roles:
   * **admin**: everything, including users, devices, settings, knowledge import and restore,
   * **operator**: register applications and accounts, learn, approve, run,
   * **viewer**: read only.
   Passwords: at least 10 characters, letters plus a digit or symbol. Five wrong passwords within five minutes lock that user name for five minutes.
3. Sessions: a sign-in gives a 15-minute token that the extension renews while you use it; after 8 hours you sign in
   again.

## Settings (`data\config.json`, or Options > AI model and speech for an admin)

| Key | Default | Meaning |
| --- | --- | --- |
| `port` | 8790 | local port (change it in the extension's Options too) |
| `extension_ids` | the fixed ID | extension IDs allowed to connect (add the store or IT ID here) |
| `token_minutes` / `session_hours` | 15 / 8 | token life and absolute session limit |
| `llm.mode` | `auto` | `auto`: use a Qwen server already running on 127.0.0.1:8080 (for example SkyGuard's), else start the bundled llama-server with the model in `models\`; `connect`: only the URL; `launch`: always the bundled server; `off`: no AI |
| `llm.url`, `llm.launch_port` | `http://127.0.0.1:8080/v1`, 8091 | where to find or start Qwen |
| `whisper.mode` | `auto` | same choices for speech; bundled server on `launch_port` 8792 |
| `max_discovery_pages` | 25 | pages visited by discovery |

The AI and speech addresses must be on this PC (127.0.0.1). Restart the companion after editing the file.

## Without models

Everything works without the AI model except AI suggestions after guided learning and free-form requests the
built-in parser does not understand; the panel then says AI is off. Without the speech model the **Speak**
button reports that speech recognition is not available.
