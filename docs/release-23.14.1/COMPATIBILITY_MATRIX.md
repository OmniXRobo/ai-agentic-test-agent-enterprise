# Compatibility matrix: SkyGuard QA 23.14.1

> **23.14.1:** unchanged. TFS: Azure DevOps Online and Azure DevOps Server / TFS 2017 or later, as before; the speed fix
> was tested against a simulated Azure DevOps, not against the real service.

"Tested" means run in this release's test suite against the real product. "Simulator" means a faithful stand-in built
for the tests (the protocol is real, the product is not). "Not tested" means nothing here could exercise it; it must
be checked on the intranet.

| Component | Versions | Status | How |
|---|---|---|---|
| ActiveMQ Classic | 5.18.6 | **Tested (real broker)** | Discovery, publish / verify, selector, priority, performance, TTL, security, consumer, retry / DLQ, Fuse end-to-end. The security test reports a real finding: Classic accepts anonymous STOMP by default. |
| ActiveMQ Classic | 5.15 to 5.17, 6.x | Not tested | Same Jolokia MBeans (`org.apache.activemq:type=Broker`) and STOMP 1.2; expected to work. |
| ActiveMQ Artemis | 2.37.0 | **Tested (real broker)** | Same tests with `--require-login`; security test passes. TTL reported UNVERIFIED when the broker has not yet removed an expired message (Artemis scans every 30 s). |
| ActiveMQ Artemis / AMQ Broker 7.x | 2.16+ | Not tested | Same `org.apache.activemq.artemis` MBeans; expected to work. |
| Red Hat Fuse 7 on Karaf | 7.x (Camel 2.21 to 2.23) | Simulator | Camel Jolokia MBeans simulated (contexts, routes, statistics, `dumpRouteAsXml`); version detection by MBean domain. **Needs intranet check.** |
| Red Hat Fuse 7 on EAP | 7.x | Simulator | As above. **Needs intranet check.** |
| Fuse on Spring Boot (Jolokia / actuator) | 2.x / 3.x | Simulator | Actuator `/actuator/camelroutes` simulated. **Needs intranet check.** |
| Black-box Fuse | any | **Tested** | Route described by hand; end-to-end run through the real Classic and Artemis brokers with a route stand-in. |
| OWASP ZAP | 2.14 to 2.16 (API) | Simulator | Fake ZAP API (context, spider, passive / active scan, alerts). Real ZAP not run here. **Needs intranet check.** |
| SMTP | Exchange / Postfix relay, STARTTLS, SSL, none | Test transport | Message building, policy, single send, failure kept as draft. No real SMTP server here. **Needs intranet check (Test connection).** |
| Local AI | Qwen through the existing AI Runtime (Ollama / OpenAI-compatible) | Mocked in tests | The prompts and the facts / hypotheses split are tested with a mocked model. |
| TFS / Azure DevOps Server | as in 23.13.x | Unchanged | Reuses the existing publisher and Defect Review. |
| Browser | Microsoft Edge (Chromium) | Chromium tested | UI tests ran in Chromium (the engine of Edge). Edge itself not run here. |
| Windows | 10 1909+, 11 | Not run here | Installer logic tested on Linux; the Windows wrappers are unchanged since 23.13.3 and covered by the Windows CI job. |
| Microphone | any | **Not executed** | No audio device here. The test reports FAIL / NO SOUND without one; it never claims PASS without real sound. |
| Arabic transcription | Faster-Whisper `small.ar` | Not executed | Needs the PC's model and a microphone. |
| Python | 3.13 (application runtime) | Tested | |
