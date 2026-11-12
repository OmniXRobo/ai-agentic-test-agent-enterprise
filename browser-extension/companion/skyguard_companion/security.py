"""Who may talk to the companion, and how.

Layers, all required for every request except GET /health:

1. The socket listens on 127.0.0.1 only; the Host header must name 127.0.0.1 or localhost on the companion's port
   (DNS-rebinding protection).
2. The Origin must be the SkyGuard extension (``chrome-extension://<fixed id>``). Web pages cannot fake it; other
   extensions have other IDs. Requests without an Origin (another local program) are refused.
3. Pairing: the first time, the person types the pairing code the companion shows (single use, 10 minutes,
   5 attempts). The extension then holds a device secret; the companion stores only its SHA-256.
4. Sign-in: a companion user (PBKDF2-SHA256, 310 000 iterations) gets a short-lived bearer token (15 minutes,
   refreshed while in use, at most 8 hours). Tokens live in memory only and are bound to the paired device.
5. Roles: admin (everything), operator (learn, run, accounts), viewer (read only).
6. Every POST/PUT/DELETE must be JSON (or audio for transcription) with a size limit and a schema; rate limits apply.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ITERATIONS = 310_000
ROLES = ("admin", "operator", "viewer")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")
PAIRING_TTL_S = 600
PAIRING_ATTEMPTS = 5


class Refused(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iters))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "Use at least 10 characters."
    if not (re.search(r"[A-Za-z]", password) and re.search(r"[^A-Za-z]", password)):
        return "Use letters and at least one digit or symbol."
    return None


# ---- origin and host -----------------------------------------------------------------------------------------------


def check_host(host: str | None, port: int) -> None:
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if (host or "").lower() not in allowed:
        raise Refused(403, "HOST_REFUSED", "This service only answers on 127.0.0.1.")


def check_origin(origin: str | None, allowed: set[str]) -> str:
    if not origin or origin == "null":
        raise Refused(403, "ORIGIN_REQUIRED", "Only the SkyGuard extension can use this service.")
    if origin not in allowed:
        raise Refused(403, "ORIGIN_REFUSED", "This extension is not allowed to use the companion.")
    return origin.split("://", 1)[1]


# ---- JSON files (atomic writes) ------------------------------------------------------------------------------------


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---- pairing ---------------------------------------------------------------------------------------------------------


class Pairing:
    """The active pairing code lives in data\\pairing.json as a salted hash with its expiry and attempt count, so the
    'SkyGuardAgent.cmd pair' command can issue a code to the running companion. The code itself is only shown to the
    person (console and pairing-code.txt, both in the user's own profile)."""

    def __init__(self, devices_file: Path, code_file: Path) -> None:
        self.devices_file = devices_file
        self.code_file = code_file
        self.state_file = devices_file.with_name("pairing.json")
        self._lock = threading.Lock()

    def devices(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in d.items() if k != "secret_sha256"} for d in _read(self.devices_file, [])]

    def new_code(self) -> str:
        """A fresh single-use code, shown to the person (console, pairing-code.txt, SkyGuardAgent.cmd pair)."""
        with self._lock:
            code = f"{secrets.randbelow(10**8):08d}"
            salt = secrets.token_hex(8)
            _write(self.state_file, {"salt": salt, "sha256": _sha(salt + code), "expires": time.time() + PAIRING_TTL_S,
                                     "attempts": 0})
            try:
                self.code_file.write_text(f"{code[:4]}-{code[4:]}\n(valid for 10 minutes, single use)\n",
                                          encoding="utf-8")
            except OSError:
                pass
            return code

    def _close(self) -> None:
        for f in (self.state_file, self.code_file):
            try:
                f.unlink()
            except OSError:
                pass

    def pair(self, code: str, extension_id: str, name: str) -> dict[str, Any]:
        with self._lock:
            state = _read(self.state_file, None)
            if not state or time.time() > state.get("expires", 0):
                self._close()
                raise Refused(409, "PAIRING_CLOSED", "No pairing code is active. Run 'SkyGuardAgent.cmd pair' to "
                                                     "show a new one.")
            state["attempts"] = state.get("attempts", 0) + 1
            if state["attempts"] > PAIRING_ATTEMPTS:
                self._close()
                raise Refused(429, "PAIRING_LOCKED", "Too many wrong codes. Show a new code and try again.")
            _write(self.state_file, state)
            if not hmac.compare_digest(_sha(state["salt"] + re.sub(r"\D", "", code or "")), state["sha256"]):
                raise Refused(401, "PAIRING_CODE_WRONG", "That pairing code is not correct.")
            self._close()
            secret = secrets.token_urlsafe(32)
            device = {"id": "dev-" + secrets.token_hex(6), "extension_id": extension_id, "name": (name or "")[:60],
                      "paired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "secret_sha256": _sha(secret)}
            devices = _read(self.devices_file, [])
            devices.append(device)
            _write(self.devices_file, devices)
            return {"device_id": device["id"], "device_secret": secret}

    def device(self, device_id: str | None, secret: str | None, extension_id: str) -> dict[str, Any]:
        if not device_id or not secret:
            raise Refused(401, "NOT_PAIRED", "Pair the extension with the companion first.")
        for d in _read(self.devices_file, []):
            if d["id"] == device_id and hmac.compare_digest(d["secret_sha256"], _sha(secret)):
                if d["extension_id"] != extension_id:
                    break
                return d
        raise Refused(401, "NOT_PAIRED", "This browser is not paired with the companion (or was removed).")

    def remove(self, device_id: str) -> None:
        with self._lock:
            _write(self.devices_file, [d for d in _read(self.devices_file, []) if d["id"] != device_id])


# ---- users and tokens ----------------------------------------------------------------------------------------------


@dataclass
class Session:
    username: str
    role: str
    device_id: str
    expires: float
    absolute: float


class Users:
    def __init__(self, users_file: Path, token_minutes: int = 15, session_hours: int = 8) -> None:
        self.file = users_file
        self.ttl = token_minutes * 60
        self.max_age = session_hours * 3600
        self._lock = threading.Lock()
        self._tokens: dict[str, Session] = {}
        self._failures: dict[str, list[float]] = {}

    def list(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in u.items() if k != "password"} for u in _read(self.file, [])]

    def empty(self) -> bool:
        return not _read(self.file, [])

    def create(self, username: str, password: str, role: str) -> dict[str, Any]:
        if not USERNAME_RE.match(username or ""):
            raise Refused(400, "USERNAME_INVALID", "Use 3 to 40 letters, digits, dots, dashes or underscores.")
        if role not in ROLES:
            raise Refused(400, "ROLE_INVALID", "The role must be admin, operator or viewer.")
        problem = password_problem(password or "")
        if problem:
            raise Refused(400, "PASSWORD_WEAK", problem)
        with self._lock:
            users = _read(self.file, [])
            if any(u["username"].lower() == username.lower() for u in users):
                raise Refused(409, "USER_EXISTS", "That user already exists.")
            users.append({"username": username, "role": role, "password": hash_password(password),
                          "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            _write(self.file, users)
        return {"username": username, "role": role}

    def delete(self, username: str) -> None:
        with self._lock:
            users = _read(self.file, [])
            left = [u for u in users if u["username"] != username]
            if not any(u["role"] == "admin" for u in left) and users:
                raise Refused(409, "LAST_ADMIN", "Keep at least one admin.")
            _write(self.file, left)
            self._tokens = {t: s for t, s in self._tokens.items() if s.username != username}

    def change_password(self, username: str, current: str, new: str) -> None:
        with self._lock:
            users = _read(self.file, [])
            user = next((u for u in users if u["username"] == username), None)
            if not user or not verify_password(current, user["password"]):
                raise Refused(401, "PASSWORD_WRONG", "The current password is not correct.")
            problem = password_problem(new)
            if problem:
                raise Refused(400, "PASSWORD_WEAK", problem)
            user["password"] = hash_password(new)
            _write(self.file, users)
            self._tokens = {t: s for t, s in self._tokens.items() if s.username != username}

    def login(self, username: str, password: str, device_id: str) -> dict[str, Any]:
        now = time.time()
        key = (username or "").lower()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < 300]
            if len(recent) >= 5:
                raise Refused(429, "LOGIN_LOCKED", "Too many failed sign-ins. Wait five minutes.")
            user = next((u for u in _read(self.file, []) if u["username"].lower() == key), None)
            ok = bool(user) and verify_password(password or "", user["password"])
            if not ok:
                if not user:  # same time either way
                    verify_password(password or "", hash_password("x", 1000))
                self._failures[key] = recent + [now]
                raise Refused(401, "LOGIN_FAILED", "The user name or password is not correct.")
            self._failures.pop(key, None)
            token = secrets.token_urlsafe(32)
            self._tokens[_sha(token)] = Session(user["username"], user["role"], device_id, now + self.ttl,
                                                now + self.max_age)
            self._sweep(now)
        return {"token": token, "expires_in": self.ttl, "user": {"username": user["username"], "role": user["role"]}}

    def session(self, token: str | None, device_id: str) -> Session:
        if not token:
            raise Refused(401, "AUTH_REQUIRED", "Sign in to the companion.")
        now = time.time()
        with self._lock:
            s = self._tokens.get(_sha(token))
            if not s or s.expires < now or s.absolute < now or s.device_id != device_id:
                self._tokens.pop(_sha(token), None)
                raise Refused(401, "AUTH_EXPIRED", "Your sign-in expired. Sign in again.")
            return s

    def refresh(self, token: str, device_id: str) -> dict[str, Any]:
        s = self.session(token, device_id)
        now = time.time()
        with self._lock:
            self._tokens.pop(_sha(token), None)
            new = secrets.token_urlsafe(32)
            self._tokens[_sha(new)] = Session(s.username, s.role, s.device_id, min(now + self.ttl, s.absolute),
                                              s.absolute)
        return {"token": new, "expires_in": int(min(self.ttl, s.absolute - now))}

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._tokens.pop(_sha(token), None)

    def revoke_device(self, device_id: str) -> None:
        with self._lock:
            self._tokens = {t: s for t, s in self._tokens.items() if s.device_id != device_id}

    def _sweep(self, now: float) -> None:
        for t in [t for t, s in self._tokens.items() if s.expires < now]:
            self._tokens.pop(t, None)


# ---- rate limiting -------------------------------------------------------------------------------------------------


class RateLimiter:
    """Token buckets per (client, bucket)."""

    LIMITS = {"default": (40.0, 80.0), "pair": (0.05, 5.0), "login": (0.2, 5.0), "llm": (1.0, 5.0),
              "voice": (0.5, 4.0)}

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}

    def check(self, client: str, bucket: str = "default") -> None:
        rate, burst = self.LIMITS.get(bucket, self.LIMITS["default"])
        now = time.monotonic()
        with self._lock:
            tokens, at = self._buckets.get((client, bucket), (burst, now))
            tokens = min(burst, tokens + (now - at) * rate)
            if tokens < 1:
                raise Refused(429, "RATE_LIMITED", "Too many requests. Wait a moment.")
            self._buckets[(client, bucket)] = (tokens - 1, now)


# ---- schema validation ---------------------------------------------------------------------------------------------

_TYPES = {"str": str, "int": int, "bool": bool, "dict": dict, "list": list, "num": (int, float)}


def validate(body: Any, schema: dict[str, tuple]) -> dict[str, Any]:
    """schema: field -> (type, required, max) where max is a length (str / list) or a value bound (int).
    Unknown fields are refused, so nothing unexpected reaches the agent."""
    if not isinstance(body, dict):
        raise Refused(400, "BODY_INVALID", "The request body must be a JSON object.")
    unknown = set(body) - set(schema)
    if unknown:
        raise Refused(400, "FIELD_UNKNOWN", f"Unknown field(s): {', '.join(sorted(unknown))[:200]}.")
    out: dict[str, Any] = {}
    for name, spec in schema.items():
        kind, required, limit = (list(spec) + [None, None])[:3]
        if name not in body or body[name] is None:
            if required:
                raise Refused(400, "FIELD_REQUIRED", f"'{name}' is required.")
            continue
        value = body[name]
        types = _TYPES[kind]
        if kind in ("int", "num") and isinstance(value, bool) or not isinstance(value, types):
            raise Refused(400, "FIELD_INVALID", f"'{name}' must be {kind}.")
        if limit is not None:
            size = len(value) if isinstance(value, (str, list, dict)) else value
            if size > limit or (kind in ("int", "num") and value < 0):
                raise Refused(400, "FIELD_TOO_LARGE", f"'{name}' is too large.")
        out[name] = value
    return out
