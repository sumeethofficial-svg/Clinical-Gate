"""Container start-up: wait for Postgres, apply migrations, create the per-persona logins, seed if empty.
Runs with the ADMIN connection; the web process afterwards is started WITHOUT it (least privilege)."""
from __future__ import annotations

import os
import sys
import time

import psycopg

from db.migrate import apply_migrations, ensure_app_role
from db.seed.generate import generate


def main() -> int:
    url, pw = os.environ.get("DATABASE_URL"), os.environ.get("CG_APP_PASSWORD")
    if not url or not pw:
        print("DATABASE_URL and CG_APP_PASSWORD are required", file=sys.stderr)
        return 2
    for attempt in range(40):
        try:
            conn = psycopg.connect(url, autocommit=True)
            break
        except psycopg.OperationalError:
            print(f"waiting for database ({attempt + 1}/40)…", flush=True)
            time.sleep(1.5)
    else:
        print("database unreachable", file=sys.stderr)
        return 1
    with conn:
        print("migrations applied:", apply_migrations(conn) or "none new")
        ensure_app_role(conn, pw, conn.execute("SELECT current_database()").fetchone()[0])
        empty = conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    if empty or os.environ.get("RESEED") == "1":
        with psycopg.connect(url) as c2:
            print("seeded:", generate(c2))
            c2.commit()
    else:
        print("database already seeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
