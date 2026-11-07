"""23.3.34: user accounts, sign-in and roles, so colleagues can use SkyGuard QA from their own browsers.

Accounts
* The first start creates one account, ``admin`` with the password ``admin``. Signing in with it asks for a new
  password at once; nothing else works until it is changed.
* The admin creates the other accounts (name, password, role) on Settings > Users, and can reset a password, change a
  role, disable or delete an account. The last active admin can never be removed, disabled or demoted.

Roles
* admin: everything, plus Users, Network access and Exit.
* tester: full access to the work (create, edit, delete, run, record, schedules, settings, voice), not Users.
* developer, manager: view only. They see every page and report, run (play) existing tests and schedules, export
  report PDFs and use voice and AI commands that look things up or run tests. They cannot create, change or delete
  anything. The server enforces this for every request; hidden buttons are only a convenience.

Passwords are stored as PBKDF2-SHA256 hashes with a random salt, never as text. Five wrong passwords lock the account
for five minutes. A sign-in lasts until the browser is closed or after 8 hours without activity.

Programs on this PC (the installer, the desktop launcher, PowerShell, the test suite) send no browser headers and are
not asked to sign in, the same as before this version: anything on this PC can already read the data folder.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from .clock import now_iso
from .errors import QAError

ROLES = {"admin": "Admin", "tester": "Tester", "developer": "Developer", "manager": "Manager"}
FULL_ROLES = {"admin", "tester"}
VIEW_ROLES = {"developer", "manager"}

DEFAULT_ADMIN = ("admin", "admin")
PBKDF2_ITERATIONS = 310_000
MIN_PASSWORD = 8
MAX_FAILURES = 5
LOCK_SECONDS = 300
IDLE_SECONDS = 8 * 3600
COOKIE = "sgqa_session"
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    must_change INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);
"""

# The account of the request being handled (read by the voice agent to limit what a view-only account may say).
_current: contextvars.ContextVar["User | None"] = contextvars.ContextVar("sgqa_user", default=None)


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str
    must_change: bool = False
    local: bool = False  # a program on this PC, not a signed-in person

    @property
    def view_only(self) -> bool:
        return self.role in VIEW_ROLES

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "display_name": self.display_name or self.username,
                "role": self.role, "role_name": ROLES.get(self.role, self.role), "view_only": self.view_only,
                "must_change": self.must_change, "local": self.local}


LOCAL_PROGRAM = User(0, "local", "This PC", "admin", local=True)


def current_user() -> User | None:
    return _current.get()


def set_current_user(user: User | None) -> contextvars.Token:
    return _current.set(user)


def reset_current_user(token: contextvars.Token) -> None:
    _current.reset(token)


# ---- passwords ------------------------------------------------------------------------------------------------------


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        test = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, TypeError):
        return False


def check_password_rules(password: str, username: str = "") -> None:
    if len(password or "") < MIN_PASSWORD:
        raise QAError("PASSWORD_TOO_SHORT", f"The password must have at least {MIN_PASSWORD} characters.")
    if len(password) > 128:
        raise QAError("PASSWORD_TOO_LONG", "The password can have at most 128 characters.")
    if username and password.lower() == username.lower():
        raise QAError("PASSWORD_IS_USERNAME", "The password cannot be the same as the user name.")
    if password.lower() in ("admin", "password", "12345678", "123456789", "qwertyui"):
        raise QAError("PASSWORD_TOO_SIMPLE", "Choose a password that is harder to guess.")


# ---- accounts and sign-ins --------------------------------------------------------------------------------------------


class UserStore:
    """Accounts in the application database; sign-ins kept in memory (a restart signs everyone out)."""

    def __init__(self, db: Any, *, iterations: int = PBKDF2_ITERATIONS) -> None:
        self.db = db
        self.iterations = iterations
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._failures: dict[str, dict[str, float]] = {}
        self._dummy = ""  # a hash for unknown user names, made at the first one
        db.execute(SCHEMA.strip())
        if not db.query_one("SELECT id FROM users LIMIT 1"):
            stamp = now_iso()
            db.execute("INSERT INTO users (username, display_name, role, password_hash, must_change, created_at, "
                       "updated_at) VALUES (?, ?, 'admin', ?, 1, ?, ?)",
                       (DEFAULT_ADMIN[0], "Administrator", hash_password(DEFAULT_ADMIN[1], iterations=iterations),
                        stamp, stamp))

    # -- rows --
    @staticmethod
    def _user(row: dict[str, Any]) -> User:
        return User(int(row["id"]), row["username"], row["display_name"] or "", row["role"], bool(row["must_change"]))

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {"id": row["id"], "username": row["username"], "display_name": row["display_name"] or row["username"],
                "role": row["role"], "role_name": ROLES.get(row["role"], row["role"]),
                "view_only": row["role"] in VIEW_ROLES, "disabled": bool(row["disabled"]),
                "must_change": bool(row["must_change"]), "created_at": row["created_at"],
                "last_login_at": row["last_login_at"]}

    def _row(self, user_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        if row is None:
            raise QAError("USER_NOT_FOUND", "That account does not exist.", status=404)
        return row

    def list(self) -> list[dict[str, Any]]:
        return [self._public(r) for r in self.db.query("SELECT * FROM users ORDER BY username COLLATE NOCASE")]

    def _active_admins(self, excluding: int | None = None) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
                                (excluding if excluding is not None else -1,))
        return int(row["n"]) if row else 0

    # -- admin actions --
    def create(self, username: str, password: str, role: str, display_name: str = "") -> dict[str, Any]:
        username = (username or "").strip()
        if not USERNAME_RE.match(username):
            raise QAError("USERNAME_INVALID", "The user name needs 2 to 32 letters, digits, dots, dashes or "
                                              "underscores, starting with a letter or digit.")
        if role not in ROLES:
            raise QAError("ROLE_INVALID", f"The role must be one of: {', '.join(ROLES.values())}.")
        check_password_rules(password, username)
        if self.db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
            raise QAError("USERNAME_TAKEN", f"An account named {username} already exists.", status=409)
        stamp = now_iso()
        cur = self.db.execute(
            "INSERT INTO users (username, display_name, role, password_hash, must_change, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?)",
            (username, (display_name or "").strip()[:80], role, hash_password(password, iterations=self.iterations),
             stamp, stamp))
        return self._public(self._row(int(cur.lastrowid)))

    def update(self, user_id: int, *, role: str | None = None, display_name: str | None = None,
               disabled: bool | None = None, password: str | None = None) -> dict[str, Any]:
        with self._lock:
            row = self._row(user_id)
            fields: dict[str, Any] = {}
            if role is not None:
                if role not in ROLES:
                    raise QAError("ROLE_INVALID", f"The role must be one of: {', '.join(ROLES.values())}.")
                if row["role"] == "admin" and role != "admin" and self._active_admins(excluding=user_id) == 0:
                    raise QAError("LAST_ADMIN", "This is the only admin account. Make another account admin first.",
                                  status=409)
                fields["role"] = role
            if display_name is not None:
                fields["display_name"] = display_name.strip()[:80]
            if disabled is not None:
                if disabled and row["role"] == "admin" and self._active_admins(excluding=user_id) == 0:
                    raise QAError("LAST_ADMIN", "The only admin account cannot be disabled.", status=409)
                fields["disabled"] = 1 if disabled else 0
            if password is not None:
                check_password_rules(password, row["username"])
                fields["password_hash"] = hash_password(password, iterations=self.iterations)
                fields["must_change"] = 0
            if not fields:
                return self._public(row)
            fields["updated_at"] = now_iso()
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), user_id))
            if password is not None or disabled or (role is not None and role != row["role"]):
                self.end_sessions(user_id)  # a reset, a disabled account or a new role signs that person out
            self._failures.pop(row["username"].lower(), None)
            return self._public(self._row(user_id))

    def delete(self, user_id: int) -> None:
        with self._lock:
            row = self._row(user_id)
            if row["role"] == "admin" and self._active_admins(excluding=user_id) == 0:
                raise QAError("LAST_ADMIN", "The only admin account cannot be deleted.", status=409)
            self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self.end_sessions(user_id)

    def change_own_password(self, user: User, current: str, new: str) -> None:
        row = self._row(user.id)
        if not verify_password(current or "", row["password_hash"]):
            raise QAError("PASSWORD_WRONG", "The current password is not correct.", status=403)
        if current == new:
            raise QAError("PASSWORD_SAME", "Choose a new password that is different from the current one.")
        check_password_rules(new, row["username"])
        self.db.execute("UPDATE users SET password_hash = ?, must_change = 0, updated_at = ? WHERE id = ?",
                        (hash_password(new, iterations=self.iterations), now_iso(), user.id))

    # -- sign-in --
    def authenticate(self, username: str, password: str) -> User:
        key = (username or "").strip().lower()
        with self._lock:
            fail = self._failures.get(key)
            if fail and fail.get("until", 0) > time.time():
                wait = int(fail["until"] - time.time()) // 60 + 1
                raise QAError("ACCOUNT_LOCKED", f"Too many wrong passwords. Try again in {wait} minute"
                                                f"{'s' if wait != 1 else ''}.", status=429)
        row = self.db.query_one("SELECT * FROM users WHERE username = ?", ((username or "").strip(),))
        # Always run one hash, so a wrong user name takes as long as a wrong password.
        if not row and not self._dummy:
            self._dummy = hash_password(secrets.token_hex(8), iterations=self.iterations)
        ok = verify_password(password or "", row["password_hash"] if row else self._dummy)
        if not row or not ok or row["disabled"]:
            with self._lock:
                fail = self._failures.setdefault(key, {"count": 0, "until": 0})
                fail["count"] += 1
                if fail["count"] >= MAX_FAILURES:
                    fail.update(count=0, until=time.time() + LOCK_SECONDS)
            if row and ok and row["disabled"]:
                raise QAError("ACCOUNT_DISABLED", "This account is disabled. Ask your admin.", status=403)
            raise QAError("SIGN_IN_FAILED", "The user name or password is not correct.", status=401)
        with self._lock:
            self._failures.pop(key, None)
        self.db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), row["id"]))
        return self._user(row)

    def start_session(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[hashlib.sha256(token.encode()).hexdigest()] = {"user_id": user.id, "seen": time.time()}
        return token

    def session_user(self, token: str | None) -> User | None:
        if not token:
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            sess = self._sessions.get(key)
            if sess is None:
                return None
            if time.time() - sess["seen"] > IDLE_SECONDS:
                self._sessions.pop(key, None)
                return None
            sess["seen"] = time.time()
            user_id = sess["user_id"]
        row = self.db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        if row is None or row["disabled"]:
            self.end_session(token)
            return None
        return self._user(row)

    def end_session(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def end_sessions(self, user_id: int, *, keep: str | None = None) -> None:
        keep_key = hashlib.sha256(keep.encode()).hexdigest() if keep else None
        with self._lock:
            for key in [k for k, s in self._sessions.items() if s["user_id"] == user_id and k != keep_key]:
                self._sessions.pop(key, None)


# ---- what each role may do ------------------------------------------------------------------------------------------

READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# Anyone signed in, also with a password that still has to be changed.
OPEN_PATHS = {("GET", "/api/auth/me"), ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"),
              ("POST", "/api/auth/password"), ("GET", "/api/branding"), ("GET", "/api/branding/logo")}

ADMIN_ONLY = [re.compile(p) for p in (r"^/api/auth/users(/.*)?$", r"^/api/auth/network(/.*)?$", r"^/api/app/exit$")]

# What a view-only account may still send besides reads: play existing tests and schedules, stop them, export
# report PDFs, mark its notifications read, and talk to the voice agent (which checks each command itself).
VIEW_WRITES = [re.compile(p) for p in (
    r"^/api/notifications/read$",
    r"^/api/automation/scripts/[^/]+/run$",
    r"^/api/automation/run-many$",
    r"^/api/automation/runs/[^/]+/stop$",
    r"^/api/automation/scripts/[^/]+/data-runs$",
    r"^/api/automation/data-runs/[^/]+/stop$",
    r"^/api/automation/studio/scripts/[^/]+/run$",
    r"^/api/automation/studio/run-many$",
    r"^/api/automation/studio/runs/[^/]+/stop$",
    r"^/api/performance/(plans|scenarios)/[^/]+/run$",
    r"^/api/performance/runs/[^/]+/(stop|cancel)$",
    r"^/api/performance/compare$",
    r"^/api/performance/schedules/[^/]+/run$",
    r"^/api/schedules/[^/]+/run$",
    r"^/api/reports/[^/]+/pdf$",
    r"^/api/ai-reports/[^/]+/[^/]+/pdf$",
    r"^/api/voice/(commands|transcribe|warmup|speak)$",
    r"^/api/app/window$",
)]


def refusal(user: User, method: str, path: str) -> tuple[str, str] | None:
    """(error code, message) when this account may not make this request, else None."""
    method = method.upper()
    if (method, path) in OPEN_PATHS:
        return None
    if user.must_change:
        return ("PASSWORD_CHANGE_REQUIRED", "Choose a new password first.")
    if any(p.match(path) for p in ADMIN_ONLY):
        if user.role == "admin" or (method in READ_METHODS and path == "/api/auth/network"):
            return None
        return ("ADMIN_ONLY", "Only an admin can do this.")
    if method in READ_METHODS or user.role in FULL_ROLES:
        return None
    if any(p.match(path) for p in VIEW_WRITES):
        return None
    return ("VIEW_ONLY", f"Your account ({ROLES.get(user.role, user.role)}) is view only: you can look at everything "
                         "and run existing tests, but not create, change or delete.")
