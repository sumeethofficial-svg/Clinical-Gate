"""Per-request identity carried in a ContextVar, so tools resolve WHO is calling from server-side
request context and never from anything the model (or user) can type."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from app.auth.tokens import AuthError, Identity

_current: ContextVar[Identity | None] = ContextVar("clinicalgate_identity", default=None)


@contextmanager
def identity_scope(identity: Identity):
    token = _current.set(identity)
    try:
        yield identity
    finally:
        _current.reset(token)


def require_identity() -> Identity:
    ident = _current.get()
    if ident is None:
        raise AuthError("no authenticated identity in request context")
    return ident
