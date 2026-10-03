"""Apply SQL migrations and (re)configure the low-privilege cg_app login role.

Usage: python -m db.migrate        (needs DATABASE_URL and CG_APP_PASSWORD)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

MIGRATIONS = Path(__file__).parent / "migrations"
PERSONA_ROLES = ["cg_front_desk", "cg_nurse", "cg_billing", "cg_manager", "cg_auth", "cg_audit"]


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    applied = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        applied.append(path.name)
    return applied


def ensure_app_role(conn: psycopg.Connection, password: str, db_name: str) -> None:
    """One LOGIN role per persona (cg_app_<persona>), each a member of exactly one persona role."""
    for role in PERSONA_ROLES:
        login = "cg_app_" + role.removeprefix("cg_")
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(verb + " ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {}").format(
                sql.Identifier(login), sql.Literal(password)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db_name), sql.Identifier(login)))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(login)))
        # membership lets the login SET ROLE into its own persona only; NOINHERIT => no privileges until then
        conn.execute(sql.SQL("GRANT {} TO {} WITH INHERIT FALSE").format(sql.Identifier(role), sql.Identifier(login)))


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    pw = os.environ.get("CG_APP_PASSWORD")
    if not url or not pw:
        print("DATABASE_URL and CG_APP_PASSWORD are required", file=sys.stderr)
        return 2
    with psycopg.connect(url, autocommit=True) as conn:
        applied = apply_migrations(conn)
        db_name = conn.execute("SELECT current_database()").fetchone()[0]
        ensure_app_role(conn, pw, db_name)
    print("applied:", applied or "nothing new")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
