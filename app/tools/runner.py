"""Common wrapper: role check -> DB-enforced query -> audit -> result. Fail closed on audit failure."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.auth.context import require_identity
from app.db.audit import write_audit
from app.policy.rules import PolicyDenied, check_role


def execute(tool: str, args: dict[str, Any], impl: Callable[..., Any]) -> dict:
    identity = require_identity()       # from server-side request context, never from args
    try:
        check_role(tool, identity.role)
        out = impl(identity, **args)
    except PolicyDenied as e:
        write_audit(identity, tool, args, "deny", e.reason, 0)
        return {"ok": False, "decision": "deny", "reason": e.reason, "message": e.message}
    except Exception as e:  # DB errors (e.g. permission denied from Postgres) are denials, not crashes
        name = type(e).__name__
        denied = "InsufficientPrivilege" in name
        write_audit(identity, tool, args, "deny" if denied else "error", "db_" + name, 0)
        return {"ok": False, "decision": "deny" if denied else "error",
                "reason": "database_denied" if denied else "tool_error",
                "message": "The database refused this request." if denied else "The tool failed."}
    write_audit(identity, tool, args, "allow", None, out.rows)    # raises on failure => caller withholds data
    return {"ok": True, "decision": "allow", "row_count": out.rows, "data": out.payload}
