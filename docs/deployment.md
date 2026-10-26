# Deployment

Cloud Run + Neon; dev auto-deploys on merge, prod promotes the same image digest after approval; rollback by traffic shift.

## Migration rule
Expand-then-contract: add in one release, remove in a later release, so a code rollback never meets a schema it cannot read.

## Secrets
Cloud database credentials, the Grafana Cloud token and the Anthropic API key live in **Google Secret Manager** (GitHub Environments hold only what the workflows themselves need). The passwords in `compose.yaml` and `.env.example` are local-only placeholders for `docker compose up`; they must never be reused in any real environment, and a real environment must never rely on those defaults.
