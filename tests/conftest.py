"""Shared fixtures. Tests run against a real Postgres 16 (compose locally, service container in CI)."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest


def _load_dotenv() -> None:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
os.environ.setdefault("PASSWORD_HASH_ITERATIONS", "20000")

from app.db.dsn import dsn_for  # noqa: E402
from db.migrate import apply_migrations, ensure_app_role  # noqa: E402
from db.seed.generate import generate  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database_ready():
    admin = os.environ["DATABASE_URL"]
    with psycopg.connect(admin, autocommit=True) as conn:
        apply_migrations(conn)
        ensure_app_role(conn, os.environ["CG_APP_PASSWORD"], conn.execute("SELECT current_database()").fetchone()[0])
        seeded = conn.execute("SELECT count(*) FROM users").fetchone()[0]
    if not seeded:
        with psycopg.connect(admin) as conn:
            generate(conn)
            conn.commit()
    yield


@pytest.fixture(scope="session")
def admin_url() -> str:
    return os.environ["DATABASE_URL"]


@pytest.fixture()
def admin(admin_url):
    with psycopg.connect(admin_url, autocommit=True) as conn:
        yield conn


@pytest.fixture()
def clean_state(admin):
    """Reset audit + cohort release state so tests don't influence each other."""
    admin.execute("TRUNCATE audit_log, cohort_release_log RESTART IDENTITY")
    yield


@pytest.fixture()
def persona(admin):
    """Open a transaction on the low-privilege cg_app login as a given demo user, exactly as the API does."""
    opened = []

    @contextmanager
    def _persona(username: str, set_provider: bool = True):
        row = admin.execute("SELECT id, role, provider_id FROM users WHERE username=%s", (username,)).fetchone()
        uid, role, prov = row
        conn = psycopg.connect(dsn_for(os.environ["DATABASE_URL_APP"], role))
        opened.append(conn)
        try:
            with conn.transaction(force_rollback=True):
                conn.execute(f"SET LOCAL ROLE cg_{role}")
                conn.execute("SELECT set_config('app.user_id', %s, true), set_config('app.provider_id', %s, true)",
                             (str(uid), str(prov) if (prov and set_provider) else ""))
                yield conn
        finally:
            conn.close()

    yield _persona
