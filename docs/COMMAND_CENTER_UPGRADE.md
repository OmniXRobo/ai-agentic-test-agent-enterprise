# Version 23.0.0: Air Force / Defense Command Center upgrade

This release upgrades the existing application in place. Nothing was rebuilt, and no working feature was
removed. The baseline it started from is tagged `baseline-v22.7.4`; see **Rollback** below.

## What changed

| Area | Change |
| --- | --- |
| Theme | Dark navy HUD theme on every page (`web/css/hud.css`, loaded after `app.css`), with cyan glow. Status colours are green for PASS/READY, amber for WAITING and red for FAILED. A status strip shows TFS, Local AI, Playwright, Speech and Mic. The header clock uses the Windows system time. |
| Agent states | Cards show READY, WAITING, RUNNING, COMPLETED, FAILED and PAUSED, with run ID, start time, elapsed time and last result. Each state comes from runtime events only. WAITING means queued behind an earlier agent of the same pipeline. COMPLETED is held for 30 seconds, then the card shows READY. |
| AI Voice Command Center | A new first page with mic and waveform, the agent name and state, a transcript, the command handoff, the Agent Squadron, radio quick settings and status lights. The handoff runs Voice LISTENING → UNDERSTANDING → specialist agent RUNNING/COMPLETED → Voice REPORTING. |
| Voice & Command Agent settings | Display name (HAMI by default; Arabic and other languages work), command name and reply wording. Also: voice on/off, push-to-talk or wake name, recognizer, voice reply, radio voice, speed, voice volume, radio intensity, wireless tones, static, ambience and background volume. The playback cursor settings are here too. |
| Command safety | `qa_platform/voice/registry.py` lists the about 35 commands the agent may run. The local Qwen model, through the existing llama.cpp endpoint, can only choose one of them. A parameter is kept only if it traces to the spoken sentence, so the model cannot invent IDs, pages or routes. The model never runs OS commands. Commands that change or delete data ask "Confirm this operation?" first. |
| Replies | Understood: "OK, I will do your task now." (or "Command received." in tactical wording). Unclear: "Please clarify the instruction." plus a specific question. Failed: "Unable to complete the command." plus the real reason. "Task complete" is spoken only after the backend reports success. |
| Speech | Offline Vosk, loaded once per session (with warm-up). Push-to-talk, or a wake-name mode where only sentences that start with the command name are used. Half-duplex: the mic never records while the agent speaks, plus a 300 ms tail. Silence detection, no-speech and recognition timeouts, Cancel, and clear messages when mic permission is blocked, no device is found or the device is disconnected. |
| Radio | Radio-open beep → band-limited voice with static → roger beep. Intensity, static and voice volume are adjustable. Optional procedural ambience ducks to almost silent while you speak and very low while the agent speaks. Test Voice, Test Radio and Stop Speaking are provided. Everything is generated locally, and nothing is routed to the microphone. |
| Playback cursor | A larger glowing cursor with a ring, hover state and click pulse, plus an optional quiet click sound. `point()`/`click()` resolve when the cursor arrives, so Playwright acts after move → highlight → click. Native dropdown choices are shown as a list the cursor picks from. Cursor speed, highlight, click sound and volume are configurable. This applies to the runtime runner and to exported pytest frameworks. |
| `pick_date` action | Opens the page's calendar, pages to the month and clicks the day. Native date inputs are filled directly. If no calendar can be read, the date is typed in the field's own format. |
| Voice during recording | Spoken click, entry, selection, next page, scroll and date commands act in the recorded browser with the cursor visible. "Select tomorrow in travel date" is recorded as `pick_date {{TODAY+1d:%Y-%m-%d}}` (Current Date + 1 day) and computed at playback. Calendar clicks are not recorded as a literal date. |
| Managed Local LLM | AI Runtime → Managed Local LLM offers Auto Detect for llama-server and GGUF models, plus alias, host, port, context and startup timeout. Start/Restart points the On-Premise AI at the server. Stop Managed LLM stops only the process this application started. Pages never start, restart or poll it. |

## Update: agentic voice automation and the tactical voice theme

* **"Automate user story 118"** (also "read the requirement, discover the application and create the scripts for user story 118") runs the real Script Studio pipeline for every Test Case of the story. The Requirements agent reads each Test Case, its story, acceptance criteria and data. The Application Discovery agent uses the stored discovery of the application, or crawls it when none exists. The Automation Engineering agent then writes the Page Object scripts. The reply reports how many scripts were created, already existed or failed.
* **"Generate automation for test case 1280"** now uses the same pipeline instead of the older one-step generator, when Script Studio is available.
* The Command Center handoff shows the real chain for the current order: Listening → Understanding → Requirements → Application Discovery → Automation Engineering → Reporting. Each step comes from the agents' own start and finish events.
* The AI Voice Command Center and the Voice & Command Agent settings use the green tactical cockpit: "TACTICAL VOICE COMMAND", CH 07 / SECURE / OFFLINE LINK chips, CALLSIGN / RECOGNITION / REPLY readouts, COMMS LOG, ORDERS YOU CAN GIVE and SYSTEMS CONFIGURATION. The clock shows Windows local time.

## New and changed APIs

* `GET /api/health/summary?refresh=` returns the status strip. The checks are cached for 5 minutes. A TFS or AI connection test updates them at once.
* `POST /api/voice/warmup` loads the offline speech model in the background.
* `GET/PUT /api/ai/managed`, `POST /api/ai/managed/detect`, `POST /api/ai/managed/{start|restart|stop}`.
* `PUT /api/voice/settings` gains these fields: `agent_display_name`, `agent_command_name`, `voice_enabled`, `listen_mode`, `phrase_style`, `radio_intensity`, `wireless_tone`, `static_volume`, `ambience_enabled`, `ambience_volume`, `voice_volume`.
* `PATCH /api/settings` `playback` gains `cursor_speed_ms` (0–2000), `click_sound` and `click_volume` (0–1).
* `POST /api/voice/commands` replies with `speak` (the composed spoken line), `owner` (the agent that took the task) and `agent_name`. Events carry `voice.handoff`, `agent.paused` and `agent.resumed`.
* The DSL has a new action, `pick_date`. The framework BasePage has a new method, `pick_date(loc, value)`.

## Architecture notes

* **One live channel.** Server-Sent Events go to `store.js`. One shared 1-second ticker (`onTick`) drives clocks and elapsed times. It skips hidden tabs and stops when nothing listens. Health is fetched once and then reused from the cache, so there is no TFS or LLM polling. Cards update in place, and the dashboard KPIs re-render only on completed or failed events, debounced.
* **Voice pipeline.** Browser WAV capture with RMS voice detection, then `/api/voice/transcribe` (Vosk), then rules. The local AI JSON intent is the fallback. The result is validated by the registry, then the handler runs, and the backend job result is spoken.
* **Recording.** Voice actions run on the recording thread through Playwright, so `recorder.js` captures them like the person's own actions. Date rules pause the recorder while the calendar is used.
* **Time.** Everything follows the Windows system date and time. There is no GMT setting, no project-time service and no time server.

## Configuration

New keys (defaults are in `qa_platform/config.py`): `voice.agent_display_name`, `voice.agent_command_name`, `voice.listen_mode`, `voice.phrase_style`, the radio and ambience levels, `playback.cursor_speed_ms`, `playback.click_sound`, `playback.click_volume`, and `ai.managed.*`. Existing settings files keep working, because missing keys take the defaults.

## Performance impact

* No new background timers. One shared ticker replaces per-page clocks.
* No MutationObservers.
* Health checks run at most once per 5 minutes, or on refresh.
* The managed LLM is never polled, except for a bounded check while it is starting.
* Playback is slower only by the configured cursor movement time. Set it to 0 for the old speed.

## Tests

Full suite: `python -m pytest -q`. New tests:

* `tests/test_command_center.py` covers derived card states, registry validation and the cached health summary.
* `tests/test_visual_playback.py` uses a real Chromium. It checks that the cursor arrives before the action, the dropdown choice is shown, and `pick_date` handles today, +1, +45 and −40 days through a calendar, a native date input and the typed fallback.
* `tests/test_automation.py::test_voice_actions_during_recording_record_a_date_rule_not_a_literal_date` runs a spoken "select tomorrow in travel date" during a recording. It checks that a rule is recorded rather than the literal date, and that playback passes.
* `tests/test_managed_llm.py` uses a fake llama-server. It covers Auto Detect, Start, settings pointing at the server, Stop, the case where someone else's server on the port is left alone, and a missing model.
* `tests/test_voice.py` is updated for the new reply wording.

## Verified here vs. needs your Windows PC

**Verified in the Linux build environment:**

* The test suite.
* A Playwright walk of every page with no script errors.
* The Command Center with typed commands.
* Calendar and dropdown playback, and voice actions during recording.
* Managed LLM start and stop, with a fake server.

GitHub CI also runs the tests on Windows and builds the non-admin offline package.

**Needs your Windows PC:**

* The microphone, Vosk recognition of your voice, and wake-name accuracy.
* Windows SAPI voices and how the radio effect sounds.
* Headset disconnects.
* The click sound in visible playback.
* The real llama-server with your Qwen GGUF model.
* Your applications' own calendars.

## Known limitations

* Wake-name mode needs the built-in offline recognizer or a local speech server. Browser recognition is push-to-talk only.
* The command name must use Latin letters so the offline English model can hear it. The display name can be any language.
* Calendars are read from common widgets (jQuery UI, Bootstrap, flatpickr, react-datepicker, MUI, Angular Material, Kendo, Ant Design, Pikaday, and generic `role=grid` calendars). An unreadable calendar falls back to typing the date.
* Outlook drafts, User Management and Application Roles were not part of this release.

## Install / update

1. Download the offline package from the pull request's CI run (Actions → `AIAgenticTestAgent-Offline-windows`).
2. Run `INSTALL_OFFLINE.cmd` as your normal user. No Administrator rights are needed. An upgrade keeps your data and one previous version.
3. Optional: to use Managed Local LLM, put `llama-server.exe` and your Qwen `.gguf` in `runtime\ai` (or build the package with `--ai-dir`), then use **Auto Detect** on the AI Runtime page.

## Rollback

* **Installed copy:** run the installer of the previous version. Data in the data folder is kept. The installer also keeps one previous version to switch back to.
* **Source:** `git checkout baseline-v22.7.4`. The settings keys added in 23.0.0 are ignored by the older version.
