"""Policy table: which persona may invoke which tool, and which columns each persona may be shown.

This is the application-layer *second* line of defense. The first line is Postgres itself (column grants
+ RLS): even if this file were wrong, the database would still refuse the over-broad read.
"""
from __future__ import annotations

TOOL_ROLES: dict[str, frozenset[str]] = {
    "search_patients": frozenset({"front_desk", "nurse", "billing"}),
    "get_appointments": frozenset({"front_desk", "nurse"}),
    "get_chart_summary": frozenset({"nurse"}),
    "get_claims": frozenset({"billing"}),
    "cohort_counts": frozenset({"manager"}),
}

# Columns each persona may read from the patients table via search_patients (also the searchable columns).
PATIENT_COLUMNS = {
    "front_desk": ("id", "mrn", "full_name", "phone"),
    "nurse": ("id", "mrn", "full_name", "dob", "sex", "phone"),
    "billing": ("id", "mrn", "full_name", "billing_address"),
}
SEARCHABLE = {
    "front_desk": ("full_name", "phone", "mrn"),
    "nurse": ("full_name", "phone", "mrn"),
    "billing": ("full_name", "mrn"),
}

# Argument names that look like attempts to smuggle identity/privilege through a tool call.
SUSPICIOUS_ARG_HINTS = ("user", "role", "provider", "session", "token", "admin", "impersonat", "as_", "auth",
                        "identity", "context", "scope", "permission", "sudo", "override")


class PolicyDenied(Exception):
    def __init__(self, reason: str, message: str = "Access denied."):
        super().__init__(reason)
        self.reason = reason
        self.message = message


def tools_for(role: str) -> list[str]:
    return [t for t, roles in TOOL_ROLES.items() if role in roles]


def check_role(tool: str, role: str) -> None:
    allowed = TOOL_ROLES.get(tool)
    if allowed is None:
        raise PolicyDenied("unknown_tool", "Unknown tool.")
    if role not in allowed:
        raise PolicyDenied("role_not_permitted", f"The {role} role is not permitted to use {tool}.")


def looks_like_identity_arg(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in SUSPICIOUS_ARG_HINTS)
