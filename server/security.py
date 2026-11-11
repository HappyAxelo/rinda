"""Authentication, sessions and server-side authorisation.

Roles map to permission sets; every protected route declares the permission it
needs, so the interface can hide what a role cannot do but the server is what
actually refuses it.

Configuration (environment):
  RINDA_ADMIN_EMAIL / RINDA_ADMIN_PASSWORD  bootstrap administrator on first start
  RINDA_PUBLIC_READ=1                       anonymous visitors get viewer access
  RINDA_DEMO=1                              demonstration sign-in per role; every
                                            record they create is flagged demo
  RINDA_SECURE_COOKIES=1                    cookies marked Secure (set behind HTTPS)
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response

from db import now, tx
from engine import VAR

ROLES = ["viewer", "field_officer", "analyst", "supervisor", "programme_manager", "admin"]
ROLE_LABEL = {"viewer": "Viewer", "field_officer": "Field officer", "analyst": "Technical analyst",
              "supervisor": "Supervisor", "programme_manager": "Programme manager",
              "admin": "Institution administrator"}
# The job each role is meant for in Rwanda's malaria programme. PROPOSED mapping, to be
# confirmed with RBC Malaria & NTD Division before accounts are issued.
ROLE_TITLE = {
    "viewer": "Partner or evaluator (read only)",
    "field_officer": "Environmental Health Officer / larval survey technician",
    "analyst": "Entomologist or data manager",
    "supervisor": "District Vector Control Officer",
    "programme_manager": "National Malaria Programme Manager (RBC)",
    "admin": "System administrator (RBC ICT)",
}

_READ = {"read:forecast"}
_FIELD = {"read:field", "write:observation", "update:own_task", "read:alerts"}
_ANALYST = {"plan:mission", "upload:flightlog", "score:batch", "export:validation", "read:field", "read:alerts",
            "write:observation"}
_SUPERVISOR = {"assign:task", "review:observation", "authorize:intervention", "record:intervention",
               "approve:mission", "act:alert", "delete:flightlog", "read:audit", "export:field",
               "read:me", "export:report", "read:outbox", "write:outbox"}
# National view: aggregate indicators and reports across districts, no record-level
# field detail (officer names, GPS of individual records) and no operational actions.
_PROGRAMME = {"read:me", "export:report", "read:alerts"}
# Analyst workspace: data registry and training. Approval of a model is separate.
_MODELS = {"read:models", "manage:data", "train:model"}
PERMS = {
    "viewer": _READ,
    "field_officer": _READ | _FIELD | {"record:intervention"},
    "analyst": _READ | _ANALYST | _MODELS | {"read:me"},
    "supervisor": _READ | _FIELD | _ANALYST | _SUPERVISOR,
    "programme_manager": _READ | _PROGRAMME,
    "admin": _READ | _FIELD | _ANALYST | _SUPERVISOR | _PROGRAMME | _MODELS | {"admin:users", "admin:system", "decide:model"},
}

SESSION_HOURS = 12
COOKIE = "rinda_session"


def flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


DEMO = flag("RINDA_DEMO")
PUBLIC_READ = flag("RINDA_PUBLIC_READ", "1")


@dataclass
class User:
    id: int | None
    email: str
    name: str
    role: str
    organisation: str | None = None
    demo: bool = False
    district: str | None = None
    language: str = "en"

    @property
    def perms(self) -> set[str]:
        return PERMS.get(self.role, set())

    def can(self, perm: str) -> bool:
        return perm in self.perms

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "name": self.name, "role": self.role,
                "role_label": ROLE_LABEL.get(self.role, self.role), "role_title": ROLE_TITLE.get(self.role),
                "organisation": self.organisation, "district": self.district, "language": self.language,
                "demo": self.demo, "permissions": sorted(self.perms), "authenticated": self.id is not None}


ANONYMOUS = User(None, "", "Public visitor", "viewer")
NOBODY = User(None, "", "Not signed in", "none")


# ------------------------------------------------------------------ passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got.hex(), dk)
    except Exception:
        return False


def password_problem(pw: str) -> str | None:
    if len(pw) < 12:
        return "Password must be at least 12 characters."
    if pw.lower() == pw or pw.upper() == pw or not any(ch.isdigit() for ch in pw):
        return "Password needs upper- and lower-case letters and a digit."
    return None


# ------------------------------------------------------------------- users
def _row_user(r) -> User:
    k = r.keys()
    return User(r["id"], r["email"], r["name"], r["role"], r["organisation"], bool(r["demo"]),
                r["district"] if "district" in k else None, (r["language"] if "language" in k else None) or "en")


def create_user(email: str, name: str, role: str, password: str, organisation: str | None = None,
                demo: bool = False, district: str | None = None, phone: str | None = None,
                language: str = "en") -> int:
    if role not in ROLES:
        raise ValueError("unknown role")
    with tx() as c:
        cur = c.execute("INSERT INTO users (email, name, role, organisation, pw_hash, demo, created_utc, district, "
                        "phone, language) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (email.strip().lower(), name.strip(), role, organisation, hash_password(password),
                         int(demo), now(), district, phone, language))
        return int(cur.lastrowid)


def list_users() -> list[dict]:
    with tx() as c:
        rows = c.execute("SELECT id, email, name, role, organisation, district, phone, language, active, demo, "
                         "created_utc, last_login_utc FROM users ORDER BY id").fetchall()
    return [dict(r) | {"role_label": ROLE_LABEL.get(r["role"], r["role"]), "role_title": ROLE_TITLE.get(r["role"])}
            for r in rows]


def user_by_id(uid: int) -> User | None:
    with tx() as c:
        r = c.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
    return _row_user(r) if r else None


def user_names() -> dict[int, str]:
    with tx() as c:
        return {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM users")}


def bootstrap_admin() -> str | None:
    """First start only: create an administrator. Returns where the credential
    went (never the credential itself)."""
    with tx() as c:
        n = c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND demo=0").fetchone()[0]
    if n:
        return None
    email = os.environ.get("RINDA_ADMIN_EMAIL", "admin@rinda.local")
    pw = os.environ.get("RINDA_ADMIN_PASSWORD")
    where = "environment"
    if not pw:
        pw = secrets.token_urlsafe(14) + "A1"
        f = VAR / "initial_admin.txt"
        f.write_text(f"email: {email}\npassword: {pw}\nChange this password after first sign-in.\n")
        where = str(f)
    create_user(email, "Administrator", "admin", pw)
    return where


DEMO_USERS = {
    "viewer": ("demo.viewer@rinda.demo", "Demo viewer"),
    "field_officer": ("demo.field@rinda.demo", "Demo field officer"),
    "analyst": ("demo.analyst@rinda.demo", "Demo analyst"),
    "supervisor": ("demo.supervisor@rinda.demo", "Demo supervisor"),
    "programme_manager": ("demo.programme@rinda.demo", "Demo programme manager"),
    "admin": ("demo.admin@rinda.demo", "Demo administrator"),
}


def ensure_demo_users() -> None:
    if not DEMO:
        return
    with tx() as c:
        have = {r[0] for r in c.execute("SELECT email FROM users WHERE demo=1")}
    for role, (email, name) in DEMO_USERS.items():
        if email not in have:
            create_user(email, name, role, secrets.token_urlsafe(24) + "Aa1", "Demonstration", demo=True,
                        district="Gasabo")


# ----------------------------------------------------------------- sessions
def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(resp: Response, uid: int) -> None:
    token = secrets.token_urlsafe(32)
    exp = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    with tx() as c:
        c.execute("DELETE FROM sessions WHERE expires_utc < ?", (now(),))
        c.execute("INSERT INTO sessions VALUES (?,?,?,?)", (_h(token), uid, now(), exp.strftime("%Y-%m-%dT%H:%M:%SZ")))
        c.execute("UPDATE users SET last_login_utc=? WHERE id=?", (now(), uid))
    resp.set_cookie(COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True, samesite="strict",
                    secure=flag("RINDA_SECURE_COOKIES"), path="/")


def end_session(request: Request, resp: Response) -> None:
    tok = request.cookies.get(COOKIE)
    if tok:
        with tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_h(tok),))
    resp.delete_cookie(COOKIE, path="/")


def login(email: str, password: str) -> User | None:
    with tx() as c:
        r = c.execute("SELECT * FROM users WHERE email=? AND active=1 AND demo=0", (email.strip().lower(),)).fetchone()
    if not r or not verify_password(password, r["pw_hash"]):
        return None
    return _row_user(r)


def demo_user(role: str) -> User | None:
    if not DEMO or role not in DEMO_USERS:
        return None
    with tx() as c:
        r = c.execute("SELECT * FROM users WHERE email=? AND demo=1", (DEMO_USERS[role][0],)).fetchone()
    return _row_user(r) if r else None


def current_user(request: Request) -> User:
    tok = request.cookies.get(COOKIE)
    if tok:
        with tx() as c:
            r = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                          "WHERE s.token_hash=? AND s.expires_utc > ? AND u.active=1 AND (u.demo=0 OR ?=1)",
                          (_h(tok), now(), int(DEMO))).fetchone()
        if r:
            return _row_user(r)
    return ANONYMOUS if PUBLIC_READ else NOBODY


def require(perm: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.can(perm):
            return user
        if user.id is None:
            raise HTTPException(401, "Sign in to continue.")
        raise HTTPException(403, f"Your role ({ROLE_LABEL.get(user.role, user.role)}) does not allow this action.")
    return dep


# -------------------------------------------------------------- rate limit
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, window_s: int = 60) -> None:
    q = _hits[key]
    t = time.monotonic()
    while q and t - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests. Wait a minute and try again.")
    q.append(t)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?"))
