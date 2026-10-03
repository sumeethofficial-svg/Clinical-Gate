"""Append-only audit log. Written on a SEPARATE connection/transaction as the write-only cg_audit role,
so denied attempts persist even when the request transaction fails. Fail closed: if the audit write
fails, the caller must withhold the result."""
from __future__ import annotations

import json
from typing import Any

from app.auth.tokens import Identity
from app.db.session import internal, scoped

_MAX_STR = 200


class AuditWriteError(RuntimeError):
    """Raised when an audit record cannot be persisted. Callers must withhold the result (fail closed)."""


def _clip(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value[:_MAX_STR]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if depth > 3:
        return "<nested>"
    if isinstance(value, dict):
        return {str(k)[:64]: _clip(v, depth + 1) for k, v in list(value.items())[:25]}
    if isinstance(value, (list, tuple)):
        return [_clip(v, depth + 1) for v in list(value)[:25]]
    return str(value)[:_MAX_STR]


def write_audit(identity: Identity | None, tool: str, args: Any, decision: str, reason: str | None = None,
                rows_returned: int | None = None) -> None:
    payload = json.dumps(_clip(args if isinstance(args, dict) else {"_raw": args}), default=str)
    try:
        with internal("audit") as conn:
            conn.execute(
                "INSERT INTO audit_log (user_id, username, role, session_id, tool, args, decision, reason, rows_returned) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
                (identity.user_id if identity else None, identity.username if identity else None,
                 identity.role if identity else None, identity.session_id if identity else None,
                 tool[:64], payload, decision, (reason or None) and reason[:300], rows_returned))
    except Exception as e:
        raise AuditWriteError("audit_unavailable: " + type(e).__name__) from None


def fetch_audit(identity: Identity, denied_only: bool = False, limit: int = 50) -> list[dict]:
    """Read through the persona's own role: RLS limits non-managers to their own entries; managers see
    every entry but the args column is not granted to them."""
    limit = max(1, min(int(limit), 200))
    cols = ("id, ts, username, role, tool, decision, reason, rows_returned" if identity.role == "manager"
            else "id, ts, username, role, tool, args, decision, reason, rows_returned")
    where = "WHERE decision <> 'allow'" if denied_only else ""
    with scoped(identity) as conn:
        return conn.execute(f"SELECT {cols} FROM audit_log {where} ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
