# Dependency inventory: SkyGuard QA 23.14.1

> **23.14.1:** no new dependency. The connection reuse uses httpx (already included) and the Python standard library.

**No new Python package and no new download.** Every new feature uses the standard library and packages already
shipped with SkyGuard QA (httpx, pydantic, FastAPI). Nothing in the upgrade downloads anything.

| Feature | Needs | Shipped in the patch? | Notes |
|---|---|---|---|
| Security Testing (passive, active, fuzzing, discovery) | httpx (already installed) | yes (code) | Talks only to the URLs in the approved scope. |
| OWASP ZAP scan (optional) | OWASP ZAP 2.14+ running on 127.0.0.1 with an API key, and Java for ZAP | **no** | Pre-staged by the security team if wanted. SkyGuard only calls its local API; other hosts are refused. |
| Red Hat Fuse testing | Jolokia agent on the Fuse runtime (Karaf: `hawtio`/`jolokia` feature; EAP / Spring Boot: Jolokia or actuator) | n/a (server side) | Read access for the account used. |
| ActiveMQ testing | Jolokia on the broker (Classic web console `/api/jolokia`, Artemis console `/console/jolokia`), STOMP connector enabled | n/a (server side) | STOMP over TCP or TLS (company CA file). |
| STOMP client | Python standard library (`socket`, `ssl`) | yes (code) | No stomp.py dependency. |
| XML comparison / route parsing | Python standard library (`xml.etree`) | yes | DTDs and entities are refused before parsing. |
| PDF reports | the existing offline PDF printer (Playwright Chromium / Edge already used by Reports) | already installed | |
| Analyze with AI | the existing local AI runtime (Qwen) | already configured | Works without it: the button says the AI is offline; facts are still shown. |
| Email Developer | an intranet SMTP server | n/a | `smtplib` from the standard library. |
| TFS defects | the existing TFS connection | already configured | |
| Voice commands and tests | the existing voice stack (Faster-Whisper `small.ar`, Piper Kareem, Windows Hoda) | **kept, not shipped** | Detected and verified by Setup; reported if missing, never downloaded. |
| Agent Workflow looks | browser only (CSS / SVG) | yes | Loaded on demand; no external fonts or scripts. |
| OCR runtime | unchanged wheels from 23.9.0+ | yes (same wheels) | |
