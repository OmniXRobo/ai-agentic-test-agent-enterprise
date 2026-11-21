# Root cause analysis: what 23.13.2 changes and why

| Reported or requested | What was found in 23.13.1 | Cause | Change in 23.13.2 |
|---|---|---|---|
| AI Agent page: a spoken request is only answered in writing | No sound after a voice request, in English or Arabic. | The AI Agent page never used the voice reply code; only the Voice Command Center spoke. The reply had no Arabic text. | The API returns `speak` and `language` with every reply (Arabic by the local AI, or a short Arabic line); the page speaks it for voice requests with the English or the Arabic voice. |
| The same in the browser extension | The side panel only wrote the reply. | No speaking code; the companion had no Arabic voice. | The companion adds the spoken line and `/v1/voice/speak` (offline Piper, male or female); the side panel speaks English with Windows voices and Arabic through the companion. |
