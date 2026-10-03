"""Per-persona connection strings.

Each persona connects through its OWN login role (cg_app_<persona>), which is a member of exactly one
persona role. SET ROLE permission is checked against the session user, so a shared login role could
pivot between personas if an attacker ever obtained SQL execution; separate logins close that gap.
"""
from __future__ import annotations

from psycopg.conninfo import conninfo_to_dict, make_conninfo

PERSONAS = ("front_desk", "nurse", "billing", "manager", "auth", "audit")


def dsn_for(base_url: str, persona: str) -> str:
    if persona not in PERSONAS:
        raise ValueError(f"unknown persona {persona!r}")
    info = conninfo_to_dict(base_url)
    info["user"] = f"cg_app_{persona}"
    return make_conninfo(**info)
