# Deployment

Cloud Run + Neon; dev auto-deploys on merge, prod promotes the same image digest after approval; rollback by traffic shift.

## Migration rule
Expand-then-contract: add in one release, remove in a later release, so a code rollback never meets a schema it cannot read.

## Secrets
Cloud database credentials, the Grafana Cloud token and the Anthropic API key live in **Google Secret Manager** (GitHub Environments hold only what the workflows themselves need). The passwords in `compose.yaml` and `.env.example` are local-only placeholders for `docker compose up`; they must never be reused in any real environment, and a real environment must never rely on those defaults.

## Database version

Production (Neon project `cohortlens`, AWS US East 2) runs **Postgres 18**, so CI, the local Compose stack and the tests run Postgres 18 too. Moving from 16 to 18 was checked, not assumed: the full backend suite (424 tests) and the seed tests (reference cohort 920 to 481, latency budgets) pass on Postgres 18. One practical difference: Postgres 18 images keep their data in `/var/lib/postgresql/<major>/docker`, so `compose.yaml` mounts the volume at `/var/lib/postgresql`, not `/var/lib/postgresql/data` (the image refuses to start with the old mount and says why).

**After pulling this change, run `docker compose down -v` once.** An existing local `pgdata` volume holds a Postgres 16 cluster, which Postgres 18 will not start on; the container exits with a clear error until the old volume is removed. The seed is reloaded on the next `docker compose up --build`. Postgres 18 also turns data checksums on by default; that does not affect restoring the plain-SQL seed.
