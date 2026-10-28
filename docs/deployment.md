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

**Known limit, accepted by the owner (2026-10-28; reasoning in [ADR-002](adr/002-cloud-run-and-neon.md)): dev is not isolated from prod. Prod deploys are protected by the approval gate.** Dev and prod share one Google Cloud project and `roles/run.admin` is project-wide, so `cohortlens-deploy-dev`, which has no approval gate, could change, re-route or delete the prod Cloud Run services, or change their IAM. It cannot read the prod secrets and can act only as the dev runtime account, so the worst case is breaking or hijacking prod services, not reading prod data. Options: accept this for a demo (current choice), put prod in its own project (the script is parameterised by `PROJECT_ID`), or add per-service IAM conditions. The identity bindings above do not remove this.

Workflow rules that follow from the trust model (to be applied when the deploy workflows are written): "main" means any run whose ref is `refs/heads/main`, including `pull_request_target`, `workflow_run`, scheduled runs and any new workflow file merged to `main`, so **only the deploy job may hold `id-token: write`**, every other job and workflow gets read-only permissions, and deploy workflows are triggered by `push` to `main` or `workflow_dispatch` only. The deploy workflow must pass `--service-account` with the runtime account of its environment.

After `--apply` the script prints the values to set as GitHub repository **variables** (identifiers, not secrets): `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SA_DEV`, `GCP_DEPLOY_SA_PROD`, `GCP_RUNTIME_SA_DEV`, `GCP_RUNTIME_SA_PROD`, `GCP_ARTIFACT_REGISTRY`.

The script is tested against a fake `gcloud` and a fake `gh` (`api/tests/unit/test_infra_gcp_setup.py`, 75 tests): plan mode runs no command, exact argument lists and the exact set of 12 IAM grants, the exact trust condition and mapping, the environment check, existing-resource verification, input validation, no deletions, idempotence. The tests were mutation-tested: weakening the script 23 different ways (widening the condition, swapping the dev/prod bindings, granting `allUsers`, scoping a grant wrongly, dropping a check or a flag, storing a secret value) is caught every time. Its `gcloud` flags were checked against the real CLI's help. **It has not been run against a real project yet.** The first `--apply` is the real test.

## Dev deploy (`.github/workflows/deploy-dev.yml`, `infra/gcp/deploy.sh`)

Every merge to `main` deploys to dev. The workflow authenticates with workload identity federation (no keys), builds the `api` and `web` images, pushes them tagged with the git sha, and calls `infra/gcp/deploy.sh`, which holds the logic and is tested against a fake `gcloud`. The order is the safety property:

1. **Migrate first.** `alembic upgrade head` runs as the Cloud Run job `cohortlens-migrate-dev` (runtime account, app-database secret by reference) and must finish before any new revision exists. Migrations are expand-then-contract, so the revision that is still serving keeps working.
2. **Candidate, not traffic.** Each service (`cohortlens-api-dev`, then `cohortlens-web-dev`) is deployed as a revision with `--no-traffic --tag candidate`.
3. **Smoke test the candidate** on its tagged URL (`scripts/smoke_cloud.sh`): `/healthz`, `/readyz` (the database answers and has persons), then the reference cohort is created, run (final count **481**) and deleted. The web candidate is tested through its nginx proxy (`/api/...`).
4. **Move traffic** (`update-traffic --to-latest`, tag removed). A failed migration or smoke test leaves traffic where it was and fails the workflow.

Images are deployed **by digest**, never by tag. The first deploy of a service cannot be held back (Cloud Run needs traffic on a new service), so it goes live and is smoke tested afterwards.

The web container renders its nginx config at start from `API_UPSTREAM` and `API_HOST` (Compose: `http://api:8000`; Cloud Run: the API service URL), resolving the upstream per request.

**The API service is public** (`--allow-unauthenticated`), like the web service: the data is synthetic and the browser reaches the API through the web proxy, so there is no sign-in to protect. This is a demo decision; revisit it before any real data.

**Before the first run** (in this order): the `production` environment (done), `bash infra/gcp/setup.sh` plan reviewed and then `--apply`, the repository variables it prints set in GitHub (**Settings → Secrets and variables → Actions → Variables**: `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SA_DEV`, `GCP_RUNTIME_SA_DEV`, `GCP_ARTIFACT_REGISTRY`), and the Neon `dev` branch loaded with the roles, schema grants and seed and the two dev secrets filled (`make seed-cloud`, not built yet). Until `GCP_PROJECT_ID` is set the workflow is skipped.

**Not verified against a real project.** Nothing here has run against Google Cloud or Neon; the tests use fakes. The Cloud Run behaviours it relies on (tagged candidate URLs of the form `https://candidate---<service host>`, `--no-traffic` on an existing service, `update-traffic --to-latest`) are documented Cloud Run behaviour but unconfirmed in this project until the first run.

## Prod promotion (`.github/workflows/promote-prod.yml`, `infra/gcp/promote.sh`)

After a successful dev deploy (or by hand: **Actions → Promote to prod → Run workflow**, optionally with the full 40-character commit sha you expect dev to serve), the workflow **pauses** because its job runs in the `production` environment. Nothing happens until the required reviewer approves, and only a job of that environment can impersonate the prod deploy account.

**To approve (browser):** GitHub → the repository → **Actions** → the **Promote to prod** run that says *Waiting* → **Review deployments** → tick **production** → optionally leave a comment → **Approve and deploy**. Choose **Reject** to stop it. The run page shows the commit the promotion was started for.

After approval, `promote.sh` does this and nothing else:
1. reads the images of the **revisions that serve the dev services right now** (the one revision holding all the traffic, deployed by digest, which passed the dev smoke test; never the newest template, which after a failed dev deploy is an untested candidate) and refuses unless both are digest-pinned images of this project's registry. It never looks anything up by tag, because a tag can be moved;
2. asks the dev API which commit it runs (`/healthz`) and refuses if that is not the commit the approver was shown (dev moved on: start a new promotion);
3. calls `deploy.sh --env prod` with **those same digests**: the migration job on the prod database first, then each service as a `--no-traffic` revision tagged `candidate`, a smoke test on its tagged URL, and only then the traffic move. A failed smoke test leaves traffic where it was and fails the run.

The prod smoke test creates the reference cohort in the **prod** database, runs it and deletes it again. The first prod deploy of a service cannot be held back (Cloud Run needs traffic on a new service), so it goes live directly and is smoke tested afterwards; the approval already happened before it. **If the web smoke test fails after the API went live**, prod runs the new API with the old web (the API is deployed and promoted first). Migrations are expand-then-contract, so the old web keeps working against the new API; fix forward or roll the API back (#19).

Rollback by traffic shift is a separate workflow (#19).

**Not possible yet:** the prod Neon branch has no roles, schema or seed and the two prod secrets have no value: `make seed-cloud` supports `dev` only. Until prod is prepared (a deliberate, separately approved step), a promotion will fail at the prod migration, before any revision exists. Nothing here has run against a real project.

## Secrets
Cloud database credentials, the Grafana Cloud token and the Anthropic API key live in **Google Secret Manager** (GitHub Environments hold only what the workflows themselves need). The passwords in `compose.yaml` and `.env.example` are local-only placeholders for `docker compose up`; they must never be reused in any real environment, and a real environment must never rely on those defaults.

## Database version

Production (Neon project `cohortlens`, AWS US East 2) runs **Postgres 18**, so CI, the local Compose stack and the tests run Postgres 18 too. Moving from 16 to 18 was checked, not assumed: the full backend suite (424 tests) and the seed tests (reference cohort 920 to 481, latency budgets) pass on Postgres 18. One practical difference: Postgres 18 images keep their data in `/var/lib/postgresql/<major>/docker`, so `compose.yaml` mounts the volume at `/var/lib/postgresql`, not `/var/lib/postgresql/data` (the image refuses to start with the old mount and says why).

**After pulling this change, run `docker compose down -v` once.** An existing local `pgdata` volume holds a Postgres 16 cluster, which Postgres 18 will not start on; the container exits with a clear error until the old volume is removed. The seed is reloaded on the next `docker compose up --build`. Postgres 18 also turns data checksums on by default; that does not affect restoring the plain-SQL seed.
