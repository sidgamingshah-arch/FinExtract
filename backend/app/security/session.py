"""Simple session layer — seeded users + in-memory session tokens.

A pragmatic, self-contained stand-in for a real identity provider so the login/session
flow is genuinely end-to-end without external infrastructure:

* Three demo users, one per role (admin / reviewer / analyst).
* ``POST /auth/login`` authenticates and mints an opaque bearer token; the token maps
  to a session held in memory (swap for Redis / a signed JWT in production).
* In *demo mode* (``auth.demo_mode`` in config.toml) the seeded users can be logged in
  passwordlessly — that's what the "Sign in as …" quick-login buttons use.

Sessions live in a process-global dict, so they reset on restart and are not shared
across workers. That is fine for the demo and clearly documented; production would use
a shared, persistent session/token store.
"""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass

from .rbac import Role


@dataclass(frozen=True)
class User:
    username: str
    name: str
    role: Role


# Seeded demo users. Password equals the username for the demo; in demo mode the
# password is optional (passwordless quick-login). Replace with a real user store.
_SEED: list[tuple[str, str, Role, str]] = [
    ("admin", "Priya Nair", Role.ADMIN, "admin"),
    ("reviewer", "Rahul Mehta", Role.REVIEWER, "reviewer"),
    ("analyst", "Ana Ferreira", Role.ANALYST, "analyst"),
]
USERS: dict[str, dict] = {
    username: {"user": User(username, name, role), "password": password}
    for (username, name, role, password) in _SEED
}


@dataclass
class Session:
    # THE ID IS NOT THE TOKEN, deliberately. The token is a credential and must not be written
    # anywhere but this process's memory; the id is an opaque handle that may be stored — which is
    # what lets a configuration version record the editing session it belongs to
    # (`LineItemVersion.authored_in_session`) without putting a bearer token in the database.
    id: str
    token: str
    user: User
    created: float
    expires: float


_SESSIONS: dict[str, Session] = {}


def authenticate(username: str | None, password: str | None, *, demo_mode: bool = False) -> User | None:
    """Return the user if credentials are valid, else None.

    When ``demo_mode`` is on, a missing password is accepted for the seeded users
    (passwordless quick-login). Otherwise the password must match.
    """
    rec = USERS.get((username or "").strip().lower())
    if rec is None:
        return None
    if password is None or password == "":
        return rec["user"] if demo_mode else None
    if secrets.compare_digest(password, rec["password"]):
        return rec["user"]
    return None


def create_session(user: User, ttl_minutes: int) -> Session:
    token = secrets.token_urlsafe(32)
    now = time.time()
    sess = Session(id=uuid.uuid4().hex, token=token, user=user, created=now,
                   expires=now + ttl_minutes * 60)
    _SESSIONS[token] = sess
    return sess


def resolve_session(token: str) -> Session | None:
    sess = _SESSIONS.get(token)
    if sess is None:
        return None
    if sess.expires < time.time():
        _SESSIONS.pop(token, None)
        return None
    return sess


def destroy_session(token: str) -> None:
    _SESSIONS.pop(token, None)


def demo_users() -> list[dict]:
    """Public (non-secret) view of the seeded users for the login screen."""
    return [
        {"username": r["user"].username, "name": r["user"].name, "role": r["user"].role.value}
        for r in USERS.values()
    ]
