"""23.3.30: the product name, tagline and logo shown in the header, browser tabs, reports, PDFs and emails.

They are set under Preferences (settings section ``ui``) and read through this module, so every place that prints
the product name follows the same setting. The install folder, patch scripts and the Windows sign-in startup file
keep their fixed file names; only what people read changes.

The logo is a file on this PC (PNG, JPG, GIF, WEBP or SVG, up to 2 MB). The browser cannot open a local path
itself, so the server serves it at ``/api/branding/logo``; reports embed it as a data URI.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from .errors import QAError

DEFAULT_NAME = "SkyGuard QA"
DEFAULT_TAGLINE = "Every release, cleared for takeoff."
MAX_NAME = 40
MAX_TAGLINE = 80
MAX_LOGO_BYTES = 2 * 1024 * 1024
LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
              ".webp": "image/webp", ".svg": "image/svg+xml"}

_settings: Any = None


def configure(settings: Any) -> None:
    """Called once when the runtime starts, so reports, emails and messages read the saved name."""
    global _settings
    _settings = settings


def _ui() -> dict[str, Any]:
    try:
        return _settings.section("ui") if _settings is not None else {}
    except Exception:
        return {}


def name() -> str:
    return (str(_ui().get("brand_name") or "").strip() or DEFAULT_NAME)[:MAX_NAME]


def tagline() -> str:
    value = _ui().get("brand_tagline")
    return DEFAULT_TAGLINE if value is None else str(value).strip()[:MAX_TAGLINE]


def _clean_path(raw: str) -> Path:
    text = os.path.expandvars(raw.strip().strip('"').strip("'"))
    return Path(text).expanduser()


def check_logo(raw: str) -> Path:
    """The logo file for a path typed in Preferences, or a clear reason why it cannot be used."""
    path = _clean_path(raw)
    suffix = path.suffix.lower()
    if suffix not in LOGO_TYPES:
        raise QAError("LOGO_INVALID", "The logo must be a PNG, JPG, GIF, WEBP or SVG file.", status=422)
    if not path.is_file():
        raise QAError("LOGO_INVALID", f"The logo file was not found: {path}", status=422)
    if path.stat().st_size > MAX_LOGO_BYTES:
        raise QAError("LOGO_INVALID", "The logo file is larger than 2 MB.", status=422)
    return path


def logo_file() -> Path | None:
    """The saved logo, or None when none is set or the file is missing (the built-in emblem is shown then)."""
    raw = str(_ui().get("logo_path") or "").strip()
    if not raw:
        return None
    try:
        return check_logo(raw)
    except (QAError, OSError):
        return None


def logo_data_uri() -> str | None:
    path = logo_file()
    if path is None:
        return None
    try:
        return f"data:{LOGO_TYPES[path.suffix.lower()]};base64," + base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return None


def info() -> dict[str, Any]:
    path = logo_file()
    raw = str(_ui().get("logo_path") or "").strip()
    out: dict[str, Any] = {"name": name(), "tagline": tagline(), "logo": path is not None, "logo_url": None,
                           "logo_problem": None}
    if path is not None:
        out["logo_url"] = f"/api/branding/logo?v={int(path.stat().st_mtime)}"
    elif raw:
        try:
            check_logo(raw)
        except QAError as exc:
            out["logo_problem"] = exc.message
    return out


def validate(ui: dict[str, Any]) -> None:
    """Checks the branding fields of a Preferences save."""
    if "brand_name" in ui:
        value = str(ui["brand_name"] or "").strip()
        if not value or len(value) > MAX_NAME:
            raise QAError("SETTINGS_INVALID", f"The product name must be 1 to {MAX_NAME} characters.")
    if "brand_tagline" in ui and len(str(ui["brand_tagline"] or "").strip()) > MAX_TAGLINE:
        raise QAError("SETTINGS_INVALID", f"The tagline can be up to {MAX_TAGLINE} characters.")
    if "logo_path" in ui:
        raw = str(ui["logo_path"] or "").strip()
        if len(raw) > 500:
            raise QAError("SETTINGS_INVALID", "The logo path is too long.")
        if raw:
            check_logo(raw)


def report_header_html() -> str:
    """The brand line at the top of every report and PDF."""
    import html
    logo = logo_data_uri()
    img = f'<img class="brand-logo" src="{logo}" alt="">' if logo else EMBLEM_SVG
    tag = tagline()
    return (f'<div class="brand-line">{img}<div><div class="brand-name">{html.escape(name())}</div>'
            + (f'<div class="brand-tag">{html.escape(tag)}</div>' if tag else "") + "</div></div>")


EMBLEM_SVG = ('<svg class="brand-logo" viewBox="0 0 40 40" aria-hidden="true"><path d="M20 3 37 34 20 26 3 34Z" '
              'fill="none" stroke="#1d6fd8" stroke-width="2.4" stroke-linejoin="round"/><path d="M20 11 29 28 20 23 '
              '11 28Z" fill="#1d6fd8" opacity=".85"/></svg>')

REPORT_CSS = """
.brand-line { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.brand-line .brand-logo { width: 34px; height: 34px; object-fit: contain; flex: none; }
.brand-name { font-weight: 700; font-size: 14px; letter-spacing: .06em; text-transform: uppercase; color: #0f2a52; }
.brand-tag { font-size: 11px; color: var(--muted); letter-spacing: .04em; }
"""
