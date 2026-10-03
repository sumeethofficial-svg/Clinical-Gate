"""Signed, short-lived session tokens (HS256 JWT). Identity is NEVER read from prompts or tool args."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import jwt

ISSUER = "clinicalgate"
AUDIENCE = "clinicalgate-api"
ROLES = ("front_desk", "nurse", "billing", "manager")


class AuthError(Exception):
    """Any authentication / identity-resolution failure. Message is safe to show; no detail leaks."""


@dataclass(frozen=True)
class Identity:
    user_id: int
    username: str
    role: str
    provider_id: int | None
    session_id: str
    display_name: str = ""


def issue_token(secret: str, *, user_id: int, role: str, session_id: str | None = None, ttl: int = 900,
                now: int | None = None, audience: str = AUDIENCE) -> str:
    now = int(time.time()) if now is None else now
    payload = {"iss": ISSUER, "aud": audience, "sub": str(user_id), "role": role,
               "sid": session_id or uuid.uuid4().hex, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> dict:
    if not token or not isinstance(token, str):
        raise AuthError("missing token")
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER,
                            options={"require": ["exp", "iat", "sub", "sid", "role", "iss", "aud"]})
    except jwt.PyJWTError:
        raise AuthError("invalid or expired token") from None
    if claims.get("role") not in ROLES or not str(claims.get("sub", "")).isdigit():
        raise AuthError("invalid token claims")
    return claims
