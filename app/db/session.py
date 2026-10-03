"""Identity-scoped database sessions: SET LOCAL ROLE + app.* context from the VERIFIED identity."""
from __future__ import annotations

from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from app.auth.tokens import ROLES, Identity
from app.config import get_settings
from app.db.dsn import dsn_for


@contextmanager
def scoped(identity: Identity):
    """Yield a connection whose Postgres role is the caller's persona. RLS + column grants do the rest.
    All settings are transaction-local, so nothing leaks to the next request."""
    if identity.role not in ROLES:
        raise ValueError("unknown role")
    conn = psycopg.connect(dsn_for(get_settings().database_url_app, identity.role), row_factory=dict_row)
    try:
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(f"cg_{identity.role}")))
            conn.execute("SET LOCAL statement_timeout = '5s'")
            conn.execute(
                "SELECT set_config('app.user_id', %s, true), set_config('app.provider_id', %s, true), "
                "set_config('app.role', %s, true)",
                (str(identity.user_id), "" if identity.provider_id is None else str(identity.provider_id), identity.role))
            yield conn
    finally:
        conn.close()


@contextmanager
def internal(persona: str):
    """Connection for the internal-only personas (auth lookups, audit writes). Never reachable from tools."""
    if persona not in ("auth", "audit"):
        raise ValueError("internal() is only for auth/audit")
    conn = psycopg.connect(dsn_for(get_settings().database_url_app, persona), row_factory=dict_row)
    try:
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(f"cg_{persona}")))
            yield conn
    finally:
        conn.close()
