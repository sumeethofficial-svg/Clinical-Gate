#!/bin/sh
# Container entrypoint: bootstrap with admin credentials, then drop them before serving traffic.
set -eu
eval "$(python -m scripts.derive_env)"
python -m scripts.bootstrap
exec env -u DATABASE_URL -u CG_APP_PASSWORD \
  uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
