# Deploying ClinicalGate for free

Everything served is synthetic. The demo password is public by design; pick a throwaway `DEMO_PASSWORD`.

## How the free deployment works

`Dockerfile.demo` builds **one container with Postgres 16 and the API**. On every start `scripts/demo_entrypoint.sh`:

1. creates a fresh, loopback-only Postgres cluster with a random superuser password,
2. runs the migrations, creates the six `cg_app_<persona>` logins, and seeds the deterministic synthetic data,
3. starts the web server as a **different OS user** with the admin connection string and bootstrap password removed from its environment (it also cannot read the Postgres data directory).

Why not a hosted free Postgres? Free tiers tend to expire (Render's free Postgres has been limited to 30 days), and our migrations need `CREATE ROLE`, which managed plans may not allow. Owning the database inside the container removes both problems. Trade-off: data and the audit log reset whenever the container restarts. That is fine for a demo of seeded synthetic data and is stated in the README.

Verified locally against Postgres 16 in both modes: as root (Postgres as `postgres`, web as `clinicalgate`) and as an unprivileged user. The Docker build itself has not been run by the author. If the first Render build fails, open the build log and send me the error.

## Render (recommended)

1. Push the repo to GitHub (see the main README / step list).
2. Render dashboard → **New → Blueprint** → connect GitHub → select the repo. It reads `render.yaml`.
3. When prompted, set `DEMO_PASSWORD` (e.g. `clinicalgate-demo`). Click **Apply**.
4. Wait for the build (about 5 to 8 minutes the first time) and the `/health` check to go green.
5. Open the `https://clinicalgate-xxxx.onrender.com` URL, click a role, enter the demo password.
6. Paste the URL at the top of `README.md` and commit.

**Free-tier behaviour:** the service sleeps after ~15 minutes without traffic. The first request afterwards takes roughly 30 to 60 seconds while the container starts, Postgres initialises and the data re-seeds. Open the URL a minute before a demo. Memory use measured locally is about 200 MB, inside the 512 MB free limit.

## Other free hosts (same image)

Anything that runs a Docker image works, because the entrypoint supports both root and non-root users and honours `$PORT`: Fly.io (`deploy/fly.managed-db.toml` is for a managed Postgres; for the single-container image use `fly launch` with `--dockerfile Dockerfile.demo`), Koyeb, Hugging Face Spaces (Docker SDK; set the Space port to 7860 and `PORT=7860`). Free-tier terms change, so check each provider's current limits.

## Optional: managed Postgres

`deploy/render.managed-db.yaml` and `deploy/fly.managed-db.toml` use the regular `Dockerfile` with a hosted database. The database user must be allowed to `CREATE ROLE`. Use these only if you want data to persist.

## After deploying

- `curl https://<url>/health` returns `{"status":"ok","synthetic_data_only":true}`.
- Real-model behaviour: set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` in the Render environment (the red-team numbers come from `make eval-real` run locally or in CI, not from the live site).
- Rotate sessions by restarting the service (the signing secret is random per start).
