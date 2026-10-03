# Deploying ClinicalGate (Render or Fly.io)

Everything served is synthetic. Because the demo is public, the demo password is public too; use a throwaway `DEMO_PASSWORD`.

## What the container does at start-up

`scripts/entrypoint.sh`: derive `DATABASE_URL_APP` from `DATABASE_URL` → run migrations → create the six `cg_app_<persona>` login roles with `CG_APP_PASSWORD` → seed the demo data if the database is empty → start `uvicorn` **with the admin `DATABASE_URL` and `CG_APP_PASSWORD` removed from its environment**. Requests are served only through the low-privilege per-persona logins.

**Requirement:** the database user in `DATABASE_URL` must be able to `CREATE ROLE` (migrations create the persona roles and logins). Hosted Postgres plans that forbid this will fail at migration `002`; use a plan/user that allows it, or pre-create the roles from `db/migrations/002_roles_rls.sql` and `db/migrate.py::ensure_app_role` by hand.

## Render (Blueprint)

1. Push the repo to GitHub. In Render: **New → Blueprint** and select the repo (`render.yaml` provisions Postgres + a Docker web service; `JWT_SECRET` and `CG_APP_PASSWORD` are generated).
2. In the service's environment set `DEMO_PASSWORD`. Optional: `LLM_PROVIDER=anthropic|openai` and the matching `*_API_KEY`.
3. Wait for the health check at `/health`, open the URL, sign in with a demo user.
4. Put the URL at the top of `README.md`.

## Fly.io

```bash
fly launch --no-deploy --copy-config          # uses fly.toml
fly postgres create --name clinicalgate-db && fly postgres attach clinicalgate-db   # sets DATABASE_URL
fly secrets set CG_APP_PASSWORD=$(openssl rand -hex 24) JWT_SECRET=$(openssl rand -hex 32) DEMO_PASSWORD='<public demo password>'
fly deploy
```

(If the attached Postgres user cannot create roles, use a Postgres provider that allows it, or pre-create the roles as above.)

## After deploying

- `curl https://<url>/health` → `{"status":"ok","synthetic_data_only":true}`
- Re-run the red-team suite against a *disposable* copy of the schema, never against anything holding real data: `make eval` (locally or in CI).
- To re-seed: set `RESEED=1` for one start-up, then unset it.
- Rotate `JWT_SECRET` to invalidate all sessions.
