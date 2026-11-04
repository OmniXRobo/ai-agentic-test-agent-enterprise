# Patch 23.2.2 -> 23.3.0: global voice agent (milestones 1 and 2)

## What changes

* **Wake-name listening is on by default, on every page.** When the dashboard opens, the assistant opens the
  microphone and listens for the command name set under *Voice & Command Agent* (for example "Captain"). Nothing is
  hard-coded. The new setting `voice.wake_listening` (default on) is saved in settings.json, so it survives refresh
  and restarts.
* **Voice assistant at the top right of every page.** It sits in the shared top bar, so it never covers page
  controls and keeps running while you change pages. Its states are:
  green = listening for the name, red pulse = listening to your instruction, blue ring = processing,
  blue glow = speaking, amber = needs you (click once, or another tab is listening), red = microphone blocked.
  A red dot on the icon means the microphone is open. Click it for the panel: conversation, typed instruction,
  *Talk now*, *Mute mic* (this browser session), *Stop listening* (saved), *Stop speaking*, and links to the
  Voice Command Center and Voice settings. Esc or the - button minimizes it without stopping listening.
* **Two ways to talk.** "Captain, open Test Plans" in one sentence, or "Captain." on its own. The agent answers
  "Yes, Boss. I'm listening." and your next sentence (within *Follow-up time*, 8 s by default) needs no name.
  The same applies when the agent asks you a question.
* **Conversational replies.** New reply wording: "Understood, Boss. I'm opening Test Plans now.", "On it, Boss.",
  "Done, Boss. ... What would you like to do next?", "Sorry, Boss. ...". *Address me as* sets "Boss" (or nothing).
  The patch switches the old default wording (standard) to conversational; a tactical wording is kept.
* **Nothing is reported done before it is.** A page opened by voice is only treated as open after the router has
  loaded it. If it fails, the agent says so ("Sorry, Boss. Test Plans did not open. ...").
* "Go back to the dashboard", "return to Test Plans" and "return to the previous page" are understood.

## Safety and browser limits

* The browser decides about the microphone. If it asks, the assistant says "Allow the microphone..."; if it is
  refused, it shows "Microphone blocked" and does not ask again until the browser reports it allowed.
* Browsers hold audio until you click or press a key once on a newly opened page. Until then the assistant shows
  "Click to start voice", never "listening".
* Only one dashboard tab listens at a time; another tab shows "Listening in another tab" and takes over when the
  first one closes.
* The agent's own replies are never taken as commands (half-duplex microphone plus an echo check), and the same
  sentence recognized twice in a row is sent once.
* A microphone that drops out (unplugged headset, taken by another app) is reopened after 2, 5, 10, 30 and 60 s.
* TFS, On-Premise AI, offline speech settings, the database and the Windows system clock are not touched.

## Files

qa_platform: `__init__.py`, `config.py`, `api/voice.py`, `voice/service.py`, `voice/model.py`, `voice/intents.py`,
`voice/commands_more.py`. web: `index.html`, `css/app.css`, `css/voice-agent.css` (new), `js/app.js`,
`js/voice.js`, `js/voice-agent.js` (new), `js/pages/command-center.js`, `js/pages/voice.js`.

## Undo

Stop the application and copy the folders from `<install>\patch-backups\23.2.2-to-23.3.0-<time>\` back into
`<install>\app`. The previous settings file is kept as `data\settings.before-23.3.0.json`.
