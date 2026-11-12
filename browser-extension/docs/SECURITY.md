# Security

## The extension

* Manifest V3. Permissions at install: `storage`, `sidePanel`, `scripting`, `tabs`, `notifications`. No host
  permissions at install: access to each application is asked for when you register it, and only for that site.
* Optional `debugger` permission (trusted typing), asked for only when you turn it on in Options; the browser shows a
  notice while it is used.
* Content Security Policy `script-src 'self'; object-src 'self'`; no remote code, no `eval`, no inline scripts.
* The extension acts only in tabs of registered applications on granted sites. It never reads other tabs.
* It stores in browser storage only: the companion port, the device ID and secret from pairing, the current
  short-lived token and signed-in user name, the trusted-typing preference, and which tab a running execution uses.
  No application password is ever stored in the browser.
* Typing uses the browser's own editing commands (or, when enabled, Chrome's input pipeline through `debugger`).
  It never assigns `.value` from script, so the application's own validation runs as for a person.

## The companion

* Listens on 127.0.0.1 only; requests whose `Host` is not 127.0.0.1 or localhost with the right port are refused
  (protects against DNS rebinding).
* Only requests whose `Origin` is the allowed extension ID are accepted. Web pages cannot call it.
* Pairing: 8-digit code shown on this PC, single use, 10 minutes, 5 attempts. Device secrets are stored hashed.
* Users: PBKDF2-SHA256 with 310 000 iterations, roles admin / operator / viewer, lockout after 5 failures.
* Tokens: 15 minutes, rotated on refresh, 8-hour absolute limit.
* Requests: JSON only, size limits (256 KB, 4 MB for page observations, 10 MB for audio), schema checked with unknown
  fields refused, rate limits.
* Vault: Windows DPAPI, current user scope; a saved password can be read only by the same Windows user on this PC.
* AI and speech run on this PC; their addresses must be loopback.

## Secrets

* A password, one-time code or similar typed or spoken into chat is detected in the side panel and not sent. The
  companion checks again; if one gets through, it is replaced by *message withheld* before anything is stored, logged,
  audited or sent to the AI.
* OTPs, MFA secrets, driver authentication codes and recovery codes are never stored and never typed by the agent:
  the run pauses for you.
* Passwords are saved only with the consent tick on the credential page.

## Web content

Page text is treated as data. It reaches the AI only fenced and neutralised, and nothing a page says can approve,
confirm, change a rule, reveal a credential or send the agent to another site. Decisions are made by fixed code
(rules, roles, confirmation hash), not by the AI.

## What it will not do

Write to an application's database, bypass sign-in, MFA, OTP or CAPTCHA, share or copy cookies between accounts, act as
an account you did not name, change browser or enterprise policy, or need administrator rights.

See also SkyGuard's `SECURITY_AUDIT.md` (the same agent core) and TEST_REPORT.md.
