# Deployment

Cloud Run + Neon; dev auto-deploys on merge, prod promotes the same image digest after approval; rollback by traffic shift.

## Migration rule
Expand-then-contract: add in one release, remove in a later release, so a code rollback never meets a schema it cannot read.

## Google Cloud setup (`infra/gcp/setup.sh`)

A one-time script, **plan-only by default**: `bash infra/gcp/setup.sh` prints every `gcloud` command and runs none; `bash infra/gcp/setup.sh --apply` runs them. Read the plan first. It is safe to run twice (it checks before creating) and it never deletes or removes anything. Prerequisites that are **not** done by the script: the project exists, billing is linked, and you are signed in (`gcloud auth login`). Settings come from `PROJECT_ID` (default `cohortlens`), `REGION` (default `us-east5`, next to the Neon project in AWS US East 2) and `GITHUB_REPO` (default `erickyegon/cohortlens`), and are validated before anything runs.

What it creates:

| Resource | Names | Purpose |
|---|---|---|
| APIs | run, artifactregistry, iam, iamcredentials, sts, secretmanager, cloudresourcemanager | what the deployment needs |
| Artifact Registry | Docker repository `cohortlens` in the region | the images the workflows push |
| Service accounts | `cohortlens-run-dev`, `cohortlens-run-prod`, `cohortlens-deploy-dev`, `cohortlens-deploy-prod` | runtime identity of each Cloud Run service, and the identity GitHub Actions deploys as |
| Workload Identity Federation | pool `github`, OIDC provider `github-actions` | GitHub Actions signs in with no stored key. The provider's own condition (`assertion.repository == '<GITHUB_REPO>'`) refuses every other repository |
| Secret Manager | `cohortlens-{dev,prod}-database-url-{app,reader}` | four **empty** slots; the values are loaded by the Neon script and never printed or stored in the repo |

Who may do what (least privilege):

- `cohortlens-deploy-dev` can be impersonated **only by workflows running on the `main` branch**; `cohortlens-deploy-prod` **only by jobs in the `production` GitHub environment** (which will require your approval). Both bindings go through the pool, never a key.
- A deploy account has `roles/run.admin`, write access to the one registry repository, and may act as **its own environment's runtime account only**. No owner, editor or viewer role is granted to anyone.
- A runtime account can read **only its own environment's two secrets**.

Known limit: dev and prod share one Google Cloud project, so a deploy account's `roles/run.admin` covers all Cloud Run services in it. The real separation is the identity binding above (only `main` can be dev, only the approved `production` environment can be prod). Separate projects per environment would be stronger and is a possible later step.

After `--apply` the script prints the values to set as GitHub repository **variables** (identifiers, not secrets): `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SA_DEV`, `GCP_DEPLOY_SA_PROD`, `GCP_RUNTIME_SA_DEV`, `GCP_RUNTIME_SA_PROD`, `GCP_ARTIFACT_REGISTRY`.

The script is tested against a fake `gcloud` (`api/tests/unit/test_infra_gcp_setup.py`): plan mode runs no command, the exact APIs and roles, the repository-locked trust condition, the two impersonation bindings, empty secrets only, no deletions, and idempotence. Its flags were checked against the real `gcloud` help. **It has not been run against a real project yet.** The first `--apply` is the real test.

## Secrets
Cloud database credentials, the Grafana Cloud token and the Anthropic API key live in **Google Secret Manager** (GitHub Environments hold only what the workflows themselves need). The passwords in `compose.yaml` and `.env.example` are local-only placeholders for `docker compose up`; they must never be reused in any real environment, and a real environment must never rely on those defaults.
