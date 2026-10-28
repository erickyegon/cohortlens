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

**Prerequisite, checked by `--apply`: the GitHub environment `production`.** Production's trust is `attribute.environment/production`, which does **not** look at the branch: a job on any branch that declares `environment: production` gets the prod identity if GitHub lets it start. GitHub creates an unprotected environment automatically the first time a workflow names it, so the protection must exist first. In GitHub: **Settings → Environments → New environment → `production`**, tick **Required reviewers** and add yourself (leave **Prevent self-review** off if you are the only reviewer, or you could not approve your own deploys), **untick Allow administrators to bypass configured protection rules**, and under **Deployment branches and tags** choose **Selected branches and tags** and add `main` as a **branch** rule only (no tag rules). `--apply` reads this through `gh` (read-only) and **refuses** if the environment is missing or cannot be read (GitHub answers 404 for both), has no required reviewer, lets administrators bypass the protection, or allows anything other than the `main` branch; `--skip-github-check` overrides it, loudly. The same run also checks an **existing** workload identity provider for its condition, its issuer and its attribute mapping, and treats gcloud errors that read "not found or permission denied" or "(or it may not exist)" as failed checks, never as "does not exist".

Who may do what (least privilege):

- The provider trusts a token only if `assertion.repository_id`, `assertion.repository_owner_id` **and** `assertion.repository` all match. The two numeric ids are read from GitHub at apply time and pinned, so renaming or transferring the repository (or someone registering the old name) cannot take its place. An existing provider is **verified** (its condition must be exactly this; its pool must be ACTIVE), never trusted blindly, and a failed check (permission denied, API disabled) stops the script instead of being treated as 'does not exist'.
- `cohortlens-deploy-dev` can be impersonated **only by workflows running on the `main` branch**; `cohortlens-deploy-prod` **only by jobs in the `production` environment**. Both go through the pool, never a key.
- A deploy account has `roles/run.admin`, write access to the one registry repository, and may act as **its own environment's runtime account only**. No owner, editor or viewer role is granted to anyone, and nothing is granted to `allUsers`.
- A runtime account can read **only its own environment's two secrets**; the deploy accounts can read none.

**Known limit (decision for the owner): dev is not isolated from prod.** Dev and prod share one Google Cloud project and `roles/run.admin` is project-wide, so `cohortlens-deploy-dev`, which has no approval gate, could change, re-route or delete the prod Cloud Run services, or change their IAM. It cannot read the prod secrets and can act only as the dev runtime account, so the worst case is breaking or hijacking prod services, not reading prod data. Options: accept this for a demo (current choice), put prod in its own project (the script is parameterised by `PROJECT_ID`), or add per-service IAM conditions. The identity bindings above do not remove this.

Workflow rules that follow from the trust model (to be applied when the deploy workflows are written): "main" means any run whose ref is `refs/heads/main`, including `pull_request_target`, `workflow_run`, scheduled runs and any new workflow file merged to `main`, so **only the deploy job may hold `id-token: write`**, every other job and workflow gets read-only permissions, and deploy workflows are triggered by `push` to `main` or `workflow_dispatch` only. The deploy workflow must pass `--service-account` with the runtime account of its environment.

After `--apply` the script prints the values to set as GitHub repository **variables** (identifiers, not secrets): `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SA_DEV`, `GCP_DEPLOY_SA_PROD`, `GCP_RUNTIME_SA_DEV`, `GCP_RUNTIME_SA_PROD`, `GCP_ARTIFACT_REGISTRY`.

The script is tested against a fake `gcloud` and a fake `gh` (`api/tests/unit/test_infra_gcp_setup.py`, 75 tests): plan mode runs no command, exact argument lists and the exact set of 12 IAM grants, the exact trust condition and mapping, the environment check, existing-resource verification, input validation, no deletions, idempotence. The tests were mutation-tested: weakening the script 23 different ways (widening the condition, swapping the dev/prod bindings, granting `allUsers`, scoping a grant wrongly, dropping a check or a flag, storing a secret value) is caught every time. Its `gcloud` flags were checked against the real CLI's help. **It has not been run against a real project yet.** The first `--apply` is the real test.

## Secrets
Cloud database credentials, the Grafana Cloud token and the Anthropic API key live in **Google Secret Manager** (GitHub Environments hold only what the workflows themselves need). The passwords in `compose.yaml` and `.env.example` are local-only placeholders for `docker compose up`; they must never be reused in any real environment, and a real environment must never rely on those defaults.

## Database version

Production (Neon project `cohortlens`, AWS US East 2) runs **Postgres 18**, so CI, the local Compose stack and the tests run Postgres 18 too. Moving from 16 to 18 was checked, not assumed: the full backend suite (424 tests) and the seed tests (reference cohort 920 to 481, latency budgets) pass on Postgres 18. One practical difference: Postgres 18 images keep their data in `/var/lib/postgresql/<major>/docker`, so `compose.yaml` mounts the volume at `/var/lib/postgresql`, not `/var/lib/postgresql/data` (the image refuses to start with the old mount and says why).

**After pulling this change, run `docker compose down -v` once.** An existing local `pgdata` volume holds a Postgres 16 cluster, which Postgres 18 will not start on; the container exits with a clear error until the old volume is removed. The seed is reloaded on the next `docker compose up --build`. Postgres 18 also turns data checksums on by default; that does not affect restoring the plain-SQL seed.
