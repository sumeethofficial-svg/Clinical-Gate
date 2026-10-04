#!/bin/bash
# Single-container FREE-TIER entrypoint: Postgres 16 + the API in one container. SYNTHETIC DATA ONLY.
#
# Why: free hosted Postgres plans expire or may not allow CREATE ROLE (our migrations create the persona
# roles). Here Postgres is private to the container, we own the superuser, and the database is rebuilt and
# re-seeded from the deterministic generator on every start. Nothing persists; nothing needs to.
#
# Works as root (Render, Fly: Postgres runs as the `postgres` OS user, the web process as `clinicalgate`)
# and as an unprivileged user (Hugging Face Spaces etc.: both run as that user).
set -Eeuo pipefail
cd "$(dirname "$0")/.."

PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
# NOTE: the postgres base image pre-sets PGDATA=/var/lib/postgresql/data (a Docker VOLUME mount point that
# cannot be deleted). Never inherit it: use our own variable and a scratch directory.
PGDATA="${CG_PGDATA:-/tmp/clinicalgate-pgdata}"
PG_PORT="${PG_PORT:-5432}"
DB_NAME=clinicalgate
rand() { python -c "import secrets,sys;print(secrets.token_hex(int(sys.argv[1])))" "$1"; }

step() { echo "[entrypoint] $*"; }
trap 'echo "[entrypoint] FAILED at line $LINENO (exit $?)" >&2; tail -n 40 "${PGDATA}.log" 2>/dev/null >&2 || true' ERR

IS_ROOT=0; [ "$(id -u)" = "0" ] && IS_ROOT=1
as_pg()  { if [ "$IS_ROOT" = 1 ]; then gosu postgres "$@"; else "$@"; fi; }

ADMIN_PW="$(rand 16)"                       # superuser password: shell-local, never exported to the web process
export CG_APP_PASSWORD="${CG_APP_PASSWORD:-$(rand 16)}"
export JWT_SECRET="${JWT_SECRET:-$(rand 32)}"   # random per start: restarts invalidate sessions, which is fine

step "initialising private Postgres at $PGDATA (uid $(id -u))"
rm -rf "$PGDATA" "${PGDATA}.log"; mkdir -p "$PGDATA"
PWFILE="$(mktemp /tmp/pgpw.XXXXXX)"; echo "$ADMIN_PW" > "$PWFILE"
if [ "$IS_ROOT" = 1 ]; then chown postgres:postgres "$PGDATA" "$PWFILE"; fi
chmod 700 "$PGDATA"; chmod 600 "$PWFILE"

as_pg "$PGBIN/initdb" -D "$PGDATA" -U postgres --pwfile="$PWFILE" -E UTF8 --locale=C.UTF-8 \
      --auth-local=scram-sha-256 --auth-host=scram-sha-256 >/dev/null
rm -f "$PWFILE"

step "starting Postgres"
# Loopback only; throwaway data, so durability knobs are relaxed for speed and low memory.
as_pg "$PGBIN/pg_ctl" -D "$PGDATA" -w -t 90 -l "${PGDATA}.log" \
      -o "-c listen_addresses=127.0.0.1 -c port=$PG_PORT -c unix_socket_directories=/tmp -c max_connections=40 \
-c shared_buffers=32MB -c fsync=off -c synchronous_commit=off -c full_page_writes=off" start >/dev/null

PGPASSWORD="$ADMIN_PW" "$PGBIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U postgres "$DB_NAME"

export DATABASE_URL="postgresql://postgres:${ADMIN_PW}@127.0.0.1:${PG_PORT}/${DB_NAME}"
step "migrating and seeding"
python -m scripts.bootstrap            # migrations + persona logins + seed, with the admin connection

export DATABASE_URL_APP="postgresql://ignored:${CG_APP_PASSWORD}@127.0.0.1:${PG_PORT}/${DB_NAME}"
unset ADMIN_PW
echo "ClinicalGate ready on port ${PORT:-8000}"
# The web process never sees the admin URL or the bootstrap password.
if [ "$IS_ROOT" = 1 ]; then
  exec gosu clinicalgate env -u DATABASE_URL -u CG_APP_PASSWORD \
       python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
else
  exec env -u DATABASE_URL -u CG_APP_PASSWORD \
       python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
fi