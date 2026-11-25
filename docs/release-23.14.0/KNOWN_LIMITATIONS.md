# Known limitations: 23.14.0

Stated plainly so that nobody has to discover them. The first section is new in 23.14.0; the rest still applies from
earlier releases (`previous-23.13.3/KNOWN_LIMITATIONS.md` and before).

## New in 23.14.0

* **Not run against the real product here (needs the intranet):** Red Hat Fuse on Karaf / EAP / Spring Boot (a Camel
  Jolokia simulator and a black-box route stand-in were used), OWASP ZAP (a fake ZAP API was used), a real SMTP server
  (a test transport was used), Microsoft Edge itself (Chromium, its engine, was used), Windows (the installer logic ran
  on Linux), the microphone and Arabic transcription (no audio device here).
* **Tested against real products:** ActiveMQ Classic 5.18.6 and Artemis 2.37.0 (publish, browse, selectors, priority,
  TTL, consumers, retry and DLQ, performance, security, Fuse end-to-end through them).
* Artemis removes expired messages on its own schedule (every 30 s by default); a TTL test that ends before then is
  reported UNVERIFIED, not PASS.
* An output message that the real consumer takes before SkyGuard can browse it is reported UNVERIFIED (the route
  statistics still show whether the route ran).
* Fuse route discovery needs Jolokia (or the actuator) on the runtime; without it use black-box mode and describe the
  route by hand.
* ActiveMQ topics cannot be browsed; topic tests need a durable subscriber on a test topic.
* The security checks find common, well-understood weaknesses; they are not a replacement for a penetration test.
  Business-logic flaws need scenarios written by a tester.
* OWASP ZAP is not included. Install it yourself (with its add-ons, offline) if you want ZAP scans.
* AI analysis, AI-drafted scenarios and message tests need the local AI; without it the facts are still shown and tests
  can be written by hand.
* The microphone test passes only when sound above a small level is recorded in 3 seconds; a very quiet microphone
  shows NO SOUND.
* Voice commands for the new modules open screens with filters; they never start a scan or a broker test.
