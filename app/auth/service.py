"""Login + server-side identity resolution."""
from __future__ import annotations

import threading
import time

from app.auth.passwords import verify_password
from app.auth.tokens import AuthError, Identity, decode_token, issue_token
from app.config import get_settings
from app.db.session import internal

_DUMMY_HASH = "pbkdf2_sha256$1000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_MAX_FAILS, _LOCK_SECONDS = 5, 60
_fails: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()


def _check_lockout(username: str) -> None:
    with _lock:
        n, until = _fails.get(username, (0, 0.0))
        if n >= _MAX_FAILS and time.time() < until:
            raise AuthError("too many failed attempts; try again later")


def _record(username: str, ok: bool) -> None:
    with _lock:
        if ok:
            _fails.pop(username, None)
        else:
            n, _ = _fails.get(username, (0, 0.0))
            _fails[username] = (n + 1, time.time() + _LOCK_SECONDS)


def reset_lockouts() -> None:
    with _lock:
        _fails.clear()


def login(username: str, password: str) -> dict:
    settings = get_settings()
    username = (username or "")[:64]
    _check_lockout(username)
    with internal("auth") as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, role, provider_id, display_name, active FROM users WHERE username = %s",
            (username,)).fetchone()
    ok = verify_password(password or "", row["password_hash"] if row else _DUMMY_HASH)
    if not (row and ok and row["active"]):
        _record(username, False)
        raise AuthError("invalid credentials")
    _record(username, True)
    token = issue_token(settings.jwt_secret, user_id=row["id"], role=row["role"], ttl=settings.token_ttl_seconds)
    return {"access_token": token, "token_type": "bearer", "expires_in": settings.token_ttl_seconds,
            "user": {"username": row["username"], "role": row["role"], "display_name": row["display_name"]}}


def resolve_identity(token: str) -> Identity:
    """Verify the signature, then re-load the user from the database. Role and provider come from the
    DB row, not from token claims; a token whose role claim disagrees with the DB is rejected."""
    claims = decode_token(get_settings().jwt_secret, token)
    with internal("auth") as conn:
        row = conn.execute(
            "SELECT id, username, role, provider_id, display_name, active FROM users WHERE id = %s",
            (int(claims["sub"]),)).fetchone()
    if not row or not row["active"]:
        raise AuthError("invalid or expired token")
    if row["role"] != claims["role"]:
        raise AuthError("invalid or expired token")
    if row["role"] == "nurse" and row["provider_id"] is None:
        raise AuthError("account misconfigured")
    return Identity(user_id=row["id"], username=row["username"], role=row["role"],
                    provider_id=row["provider_id"], session_id=claims["sid"], display_name=row["display_name"])
