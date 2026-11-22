"""23.3.21: Outlook email for scheduled run reports.

Two ways to send:

* ``smtp``: Outlook / Microsoft 365 (smtp.office365.com:587, STARTTLS), Outlook.com (smtp-mail.outlook.com:587) or
  the company's Exchange SMTP relay. The password (or app password) is kept in the Windows Credential Manager,
  never in settings.
* ``outlook_app``: the Outlook desktop app signed in on this PC sends the mail (PowerShell + Outlook COM). No
  password is needed; Outlook must be installed and set up for the Windows user running the application.
"""

from __future__ import annotations

import base64
import json
import re
import mimetypes
import smtplib
import ssl
import subprocess
import sys
import tempfile
from email.message import EmailMessage
from email.utils import formataddr, getaddresses
from pathlib import Path
from html import escape
from typing import Any

from . import branding
from .config import Settings
from .credentials import SecretStore
from .errors import QAError

PRESETS = {
    "office365": {"host": "smtp.office365.com", "port": 587, "security": "starttls"},
    "outlook_com": {"host": "smtp-mail.outlook.com", "port": 587, "security": "starttls"},
}
DEFAULTS = {"mode": "smtp", "preset": "office365", "host": "smtp.office365.com", "port": 587, "security": "starttls",
            "username": "", "sender": "", "sender_name": "", "default_to": "", "verify_tls": True,
            "timeout_seconds": 30,
            # 23.14.0: intranet SMTP for Email Developer (company CA certificate, recipient policy, attachment limits,
            # developer directory). None of these change how the existing report emails are sent.
            "ca_file": "", "allowed_domains": "", "allow_external": False, "max_attachment_mb": 10,
            "max_total_attachment_mb": 20, "developer_directory": []}
ADDRESS_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
SECRET_NS, SECRET_NAME = "email", "smtp"
OLD_DEFAULT_SENDER_NAME = "QA Enterprise AI"


def _sender_name(cfg: dict[str, Any]) -> str:
    """The display name of the sender: the one set in Outlook Email, otherwise the product name (23.3.30)."""
    value = (cfg.get("sender_name") or "").strip()
    return branding.name() if not value or value == OLD_DEFAULT_SENDER_NAME else value


def split_addresses(value: Any) -> list[str]:
    """'a@x.com; b@y.com, Name <c@z.com>' -> ['a@x.com', 'b@y.com', 'c@z.com']."""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    text = str(value or "").replace(";", ",")
    out = []
    for _name, addr in getaddresses([text]):
        addr = addr.strip()
        if addr and "@" in addr and addr not in out:
            out.append(addr)
    return out


class Mailer:
    def __init__(self, settings: Settings, secrets: SecretStore) -> None:
        self.settings = settings
        self.secrets = secrets
        self.transport: Any = None  # tests: a callable(config, message) that receives what would be sent

    # ---- configuration -------------------------------------------------------------------------

    def config(self) -> dict[str, Any]:
        cfg = {**DEFAULTS, **(self.settings.section("email") or {})}
        cfg["has_password"] = bool(self._password_safe())
        cfg["configured"] = self.configured(cfg)
        return cfg

    def configured(self, cfg: dict[str, Any] | None = None) -> bool:
        cfg = cfg or {**DEFAULTS, **(self.settings.section("email") or {})}
        if cfg.get("mode") == "outlook_app":
            return True
        return bool(cfg.get("host") and (cfg.get("sender") or cfg.get("username")))

    def _password_safe(self) -> str | None:
        try:
            return self.secrets.get(SECRET_NS, SECRET_NAME)
        except Exception:
            return None

    def save(self, values: dict[str, Any], password: str | None = None, clear_password: bool = False) -> dict[str, Any]:
        clean = {k: v for k, v in values.items() if k in DEFAULTS}
        if clean.get("mode") not in (None, "smtp", "outlook_app"):
            raise QAError("EMAIL_MODE", "Choose SMTP (Outlook / Microsoft 365) or the Outlook app on this PC.", status=422)
        preset = clean.get("preset")
        if preset in PRESETS:
            clean.update(PRESETS[preset])
        if "port" in clean:
            try:
                clean["port"] = int(clean["port"])
            except (TypeError, ValueError):
                raise QAError("EMAIL_PORT", "The port must be a number, for example 587.", status=422) from None
        if clean.get("security") not in (None, "starttls", "ssl", "none"):
            raise QAError("EMAIL_SECURITY", "Security must be STARTTLS, SSL or none.", status=422)
        if clean.get("sender") and "@" not in str(clean["sender"]):
            raise QAError("EMAIL_SENDER", "The sender must be an email address.", status=422)
        for key, top in (("max_attachment_mb", 50), ("max_total_attachment_mb", 100)):
            if key in clean:
                try:
                    clean[key] = max(1, min(top, int(clean[key])))
                except (TypeError, ValueError):
                    raise QAError("EMAIL_LIMIT", "Attachment limits are whole numbers of MB.", status=422) from None
        if clean.get("ca_file"):
            ca = Path(str(clean["ca_file"]).strip().strip('"'))
            if ca.suffix.lower() not in (".pem", ".crt", ".cer") or not ca.is_file():
                raise QAError("EMAIL_CA_FILE", "The company CA certificate must be an existing .pem, .crt or .cer file "
                              "on this PC.", status=422)
            clean["ca_file"] = str(ca)
        if "allowed_domains" in clean:
            doms = [d.strip().lower().lstrip("@") for d in re.split(r"[,; ]+", str(clean["allowed_domains"] or "")) if d.strip()]
            if any(not re.match(r"^[a-z0-9.-]+\.[a-z0-9-]+$|^[a-z0-9-]+$", d) for d in doms):
                raise QAError("EMAIL_DOMAINS", "Allowed recipient domains are names such as company.local.", status=422)
            clean["allowed_domains"] = ", ".join(dict.fromkeys(doms))
        if "developer_directory" in clean:
            people = []
            for p in clean["developer_directory"] or []:
                if not isinstance(p, dict):
                    continue
                addr = str(p.get("email") or "").strip()
                if not ADDRESS_RE.match(addr):
                    raise QAError("EMAIL_DIRECTORY", f"'{addr}' in the developer directory is not an email address.",
                                  status=422)
                people.append({"name": str(p.get("name") or addr)[:120], "email": addr,
                               "team": str(p.get("team") or "")[:80], "modules": [str(m)[:40] for m in
                                                                               (p.get("modules") or [])][:10]})
            clean["developer_directory"] = people[:300]
        if "allow_external" in clean:
            clean["allow_external"] = bool(clean["allow_external"])
        self.settings.update({"email": clean})
        if clear_password:
            self.secrets.delete(SECRET_NS, SECRET_NAME)
        elif password:
            self.secrets.set(SECRET_NS, SECRET_NAME, password)
        return self.config()

    # ---- sending ---------------------------------------------------------------------------------

    def send(self, to: Any, subject: str, html: str, text: str | None = None, *, cc: Any = None,
             attachments: list[Path] | None = None, bcc: Any = None) -> dict[str, Any]:
        cfg = self.config()
        recipients = split_addresses(to)
        copies = split_addresses(cc)
        hidden = split_addresses(bcc)
        subject = " ".join(str(subject or "").split())[:250]  # no CR / LF: no header injection
        if not recipients:
            raise QAError("EMAIL_NO_RECIPIENT", "Add at least one email address to send the report to.", status=422)
        if not cfg["configured"]:
            raise QAError("EMAIL_NOT_CONFIGURED", "Set up the Outlook email in Settings > Outlook Email first.",
                          status=409)
        files = [Path(a) for a in attachments or [] if Path(a).is_file()]
        if cfg.get("mode") == "outlook_app":
            if hidden:  # the Outlook app path has no Bcc here; refuse instead of exposing the addresses
                raise QAError("EMAIL_BCC_UNSUPPORTED", "Bcc needs SMTP; the Outlook app on this PC cannot send it.",
                              status=409)
            return self._send_outlook_app(recipients, copies, subject, html, files)
        sender = cfg.get("sender") or cfg.get("username")
        msg = EmailMessage()
        msg["From"] = formataddr((_sender_name(cfg), sender))
        msg["To"] = ", ".join(recipients)
        if copies:
            msg["Cc"] = ", ".join(copies)
        msg["Subject"] = subject
        msg.set_content(text or "This report is in HTML. Open it in Outlook or a browser.")
        msg.add_alternative(html, subtype="html")
        for f in files:
            ctype, _ = mimetypes.guess_type(f.name)
            main, sub = (ctype or "application/octet-stream").split("/", 1)
            msg.add_attachment(f.read_bytes(), maintype=main, subtype=sub, filename=f.name)
        everyone = recipients + copies + hidden
        if self.transport is not None:
            reply = self.transport(cfg, msg) if not hidden else self.transport(cfg, msg, everyone)
            refused = reply if isinstance(reply, dict) else {}
            return {"sent": True, "to": recipients, "cc": copies, "bcc": hidden, "mode": "smtp",
                    "refused": {k: str(v) for k, v in refused.items()}}
        refused = self._smtp(cfg, msg, everyone)
        return {"sent": True, "to": recipients, "cc": copies, "bcc": hidden, "mode": "smtp",
                "refused": {k: str(v) for k, v in (refused or {}).items()}}

    def _smtp(self, cfg: dict[str, Any], msg: EmailMessage, everyone: list[str] | None = None) -> dict[str, Any]:
        host, port = cfg.get("host"), int(cfg.get("port") or 587)
        timeout = float(cfg.get("timeout_seconds") or 30)
        context = ssl.create_default_context()
        if cfg.get("ca_file") and Path(cfg["ca_file"]).is_file():  # 23.14.0: the company's own CA (intranet SMTP)
            context.load_verify_locations(cfg["ca_file"])
        if not cfg.get("verify_tls", True):
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        password = self._password_safe()
        try:
            if cfg.get("security") == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
            else:
                server = smtplib.SMTP(host, port, timeout=timeout)
            with server:
                server.ehlo()
                if cfg.get("security", "starttls") == "starttls":
                    server.starttls(context=context)
                    server.ehlo()
                if cfg.get("username") and password:
                    server.login(cfg["username"], password)
                # Bcc is not a header: the addresses go only into the SMTP envelope
                return server.send_message(msg, to_addrs=everyone) if everyone else server.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise QAError("EMAIL_LOGIN_FAILED", "Outlook refused the user name or password. With Microsoft 365, SMTP "
                          "sign-in (SMTP AUTH) must be allowed for this mailbox, or use an app password.", status=502,
                          details=_short(exc)) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise QAError("EMAIL_SEND_FAILED", f"The email could not be sent through {host}:{port}.", status=502,
                          details=_short(exc)) from exc

    def _send_outlook_app(self, to: list[str], cc: list[str], subject: str, html: str,
                          files: list[Path]) -> dict[str, Any]:
        payload = {"to": "; ".join(to), "cc": "; ".join(cc), "subject": subject, "html": html,
                   "files": [str(f.resolve()) for f in files]}
        if self.transport is not None:
            self.transport({"mode": "outlook_app"}, payload)
            return {"sent": True, "to": to, "cc": cc, "mode": "outlook_app"}
        if sys.platform != "win32":
            raise QAError("EMAIL_OUTLOOK_APP", "The Outlook app can only send on Windows.", status=409)
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "mail.json"
            data.write_text(json.dumps(payload), encoding="utf-8")
            script = (
                "$ErrorActionPreference='Stop';"
                f"$m = Get-Content -Raw -Encoding UTF8 '{data}' | ConvertFrom-Json;"
                "$o = New-Object -ComObject Outlook.Application; $i = $o.CreateItem(0);"
                "$i.To = $m.to; if ($m.cc) { $i.CC = $m.cc }; $i.Subject = $m.subject; $i.HTMLBody = $m.html;"
                "foreach ($f in $m.files) { [void]$i.Attachments.Add($f) }; $i.Send()")
            encoded = base64.b64encode(script.encode("utf-16-le")).decode()
            try:
                done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                      capture_output=True, text=True, timeout=120,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise QAError("EMAIL_OUTLOOK_APP", "Outlook did not answer.", status=502, details=_short(exc)) from exc
            if done.returncode != 0:
                raise QAError("EMAIL_OUTLOOK_APP", "Outlook on this PC could not send the email. Open Outlook once "
                              "and check it is signed in.", status=502, details=(done.stderr or done.stdout)[-400:])
        return {"sent": True, "to": to, "cc": cc, "mode": "outlook_app"}

    # ---- 23.14.0: Email Developer policy and connection check ----------------------------------------------------

    def allowed_domains(self, cfg: dict[str, Any] | None = None) -> list[str]:
        """The recipient domains Email Developer may send to: the configured list, otherwise the sender's own domain."""
        cfg = cfg or self.config()
        doms = [d.strip().lower() for d in str(cfg.get("allowed_domains") or "").split(",") if d.strip()]
        if not doms:
            sender = str(cfg.get("sender") or cfg.get("username") or "")
            if "@" in sender:
                doms = [sender.rsplit("@", 1)[1].lower()]
        return doms

    def check_recipients(self, addresses: list[str]) -> list[str]:
        """Validates every address (syntax, no header characters) and the enterprise recipient policy. Returns the
        clean list; raises with the addresses that are refused."""
        cfg = self.config()
        bad = [a for a in addresses if not ADDRESS_RE.match(a) or any(c in a for c in "\r\n<>,;")]
        if bad:
            raise QAError("EMAIL_ADDRESS_INVALID", "Not a valid email address: " + ", ".join(bad[:5]), status=422)
        if not cfg.get("allow_external"):
            doms = self.allowed_domains(cfg)
            outside = [a for a in addresses if not doms or not any(a.lower().endswith("@" + d) or
                                                                   a.lower().endswith("." + d) for d in doms)]
            if outside:
                raise QAError("EMAIL_RECIPIENT_NOT_ALLOWED", "The enterprise email policy does not allow sending to: " +
                              ", ".join(outside[:5]) + (f". Allowed domains: {', '.join(doms)}." if doms else
                                                        ". Set the sender or the allowed recipient domains first."),
                              status=403)
        return addresses

    def test_connection(self) -> dict[str, Any]:
        """Connects, says EHLO, starts TLS and signs in like a real send, without sending anything."""
        cfg = self.config()
        if cfg.get("mode") == "outlook_app":
            return {"ok": True, "detail": "The Outlook app on this PC sends the email; there is no SMTP server to test."}
        if not cfg["configured"]:
            raise QAError("EMAIL_NOT_CONFIGURED", "Set the SMTP server and the sender first.", status=409)
        host, port = cfg.get("host"), int(cfg.get("port") or 587)
        steps: list[str] = []
        if self.transport is not None:
            return {"ok": True, "detail": "Test transport", "steps": ["connected"]}
        context = ssl.create_default_context()
        if cfg.get("ca_file") and Path(cfg["ca_file"]).is_file():
            context.load_verify_locations(cfg["ca_file"])
        if not cfg.get("verify_tls", True):
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        try:
            timeout = float(cfg.get("timeout_seconds") or 30)
            server: smtplib.SMTP = (smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
                                    if cfg.get("security") == "ssl" else smtplib.SMTP(host, port, timeout=timeout))
            with server:
                code, _ = server.ehlo()
                steps.append(f"connected to {host}:{port} (EHLO {code})")
                if cfg.get("security", "starttls") == "starttls":
                    server.starttls(context=context)
                    server.ehlo()
                    steps.append("STARTTLS: certificate accepted")
                password = self._password_safe()
                if cfg.get("username") and password:
                    server.login(cfg["username"], password)
                    steps.append("signed in")
                server.noop()
        except smtplib.SMTPAuthenticationError as exc:
            raise QAError("EMAIL_LOGIN_FAILED", "The SMTP server refused the user name or password.", status=502,
                          details=_short(exc)) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise QAError("EMAIL_CONNECT_FAILED", f"Could not use the SMTP server {host}:{port}.", status=502,
                          details=_short(exc)) from exc
        return {"ok": True, "detail": "; ".join(steps), "steps": steps}

    def send_test(self, to: Any = None) -> dict[str, Any]:
        cfg = self.config()
        to = to or cfg.get("default_to") or cfg.get("sender") or cfg.get("username")
        product = branding.name()
        html = (f"<p>This is a test email from <b>{escape(product)}</b>.</p><p>Scheduled run reports will be sent "
                "like this.</p>")
        return self.send(to, f"{product} - test email", html, f"This is a test email from {product}.")


def _short(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    return text.splitlines()[0][:300] if text else exc.__class__.__name__


__all__ = ["Mailer", "split_addresses", "PRESETS"]
