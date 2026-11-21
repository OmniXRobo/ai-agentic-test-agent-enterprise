# Changelog: SkyGuard QA 23.13.2 and extension 1.4.2

Cumulative on 23.13.1 (commit `0a7ed57`, PR #60). One upgrade for every release from 23.3.35 to 23.13.1. Everything
works offline. The database stays at 22 migrations. Earlier changes: `previous-23.13.1/`, `previous-23.13.0/` ...

## Spoken replies on the AI Agent page

* A request made by voice (🎤 Speak) now gets a spoken reply as well as the written one.
* English request: the reply is spoken in English, with the Windows voice chosen in Voice settings.
* Arabic request: the request is translated to English (shown as *Understood*), and the reply is spoken in Arabic
  with the Arabic voice (*Arabic reply voice*: male or female). The Arabic text comes from the local AI; when it is
  not running, a short Arabic sentence for the kind of reply is spoken ("حاضر، فتحت الصفحة", "أحتاج معلومة
  إضافية ..."), and the details stay on screen.
* Web addresses are not read out. Typed requests stay silent, as before.

## Browser extension 1.4.2

* The side panel speaks the reply to a request made by voice: English with the voices installed in Windows, Arabic
  with the same offline Arabic voice as SkyGuard QA (through the companion; an English voice never reads Arabic).
* Options > *Spoken replies*: on or off, and Male or Female.
* `INSTALL_ARABIC_VOICE.cmd` also gives the companion a copy of the Arabic voice when the extension is installed.
  The companion also finds the voice in SkyGuard QA's default folder.
* Update: `UPDATE_EXTENSION.cmd`, then Reload on the extensions page.
