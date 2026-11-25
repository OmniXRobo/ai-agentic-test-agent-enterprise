"""23.13.0: the welcome email sent when an admin creates an account.

It carries the address of the application, the user name and the first password, and has the user manual (a PDF with
screenshots of the application, shipped in web/manual) attached. Sending uses the Outlook email set up in Settings; when
email is not set up the account is still created and the admin is told why no email went out.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from . import branding
from .config import APP_ROOT
from .errors import QAError
from .logging_setup import get_logger

log = get_logger("welcome")

MANUAL = APP_ROOT / "web" / "manual" / "SkyGuard_QA_User_Manual.pdf"


def manual_path() -> Path | None:
    return MANUAL if MANUAL.is_file() else None


def compose(user: dict[str, Any], password: str | None, link: str) -> tuple[str, str, str]:
    product = branding.name()
    who = user.get("display_name") or user["username"]
    rows = [("Address", f'<a href="{escape(link)}">{escape(link)}</a>', link), ("User name", escape(user["username"]),
            user["username"])]
    if password:
        rows.append(("First password", f"<code>{escape(password)}</code>", password))
    rows.append(("Role", escape(user.get("role_name") or user.get("role") or ""), user.get("role_name") or ""))
    if user.get("tfs_project"):
        rows.append(("TFS project", escape(user["tfs_project"]), user["tfs_project"]))
    table = "".join(f"<tr><td style='padding:4px 12px 4px 0;color:#555'>{k}</td><td><b>{v}</b></td></tr>"
                    for k, v, _ in rows)
    change = ("<p>Please sign in and change this password on the first visit (your name at the top right, then "
              "<i>Change password</i>).</p>" if password else "")
    html = (f"<p>Hello {escape(who)},</p><p>An account was created for you in <b>{escape(product)}</b>.</p>"
            f"<table>{table}</table>{change}<p>The user manual is attached (PDF). Open the address above in "
            "Microsoft Edge on the office network.</p><p>This message was sent by "
            f"{escape(product)}. Do not forward it: it contains your sign-in details.</p>")
    text = "\n".join([f"Hello {who},", f"An account was created for you in {product}.", ""]
                     + [f"{k}: {t}" for k, _, t in rows]
                     + (["", "Please change this password when you first sign in."] if password else [])
                     + ["", "The user manual is attached (PDF)."])
    return f"{product} - your account", html, text


def send_welcome(rt: Any, user: dict[str, Any], password: str | None, link: str) -> dict[str, Any]:
    """Never raises: the account exists either way; the answer says whether the email went out and why not."""
    to = (user.get("email") or "").strip()
    if not to:
        return {"sent": False, "reason": "The account has no email address."}
    subject, html, text = compose(user, password, link)
    manual = manual_path()
    try:
        result = rt.mailer.send(to, subject, html, text, attachments=[manual] if manual else [])
    except QAError as exc:
        log.warning(f"Welcome email to {user['username']} not sent: {exc.message}")
        return {"sent": False, "reason": exc.message, "code": exc.error_code}
    except Exception as exc:  # a mail server problem must not undo the new account
        log.warning(f"Welcome email to {user['username']} not sent: {exc!r}")
        return {"sent": False, "reason": f"The email could not be sent: {type(exc).__name__}."}
    return {"sent": bool(result.get("sent", True)), "to": to, "manual_attached": manual is not None}
