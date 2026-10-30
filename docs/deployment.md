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
3. **Smoke test the candidate** on its tagged URL (`scripts/smoke_cloud.sh`): `/version` (must be the commit being deployed; **not** `/healthz`, which Cloud Run reserves and answers 404 itself), `/readyz` (the database answers and has persons), then the reference cohort is created, run (final count **481**) and deleted. The web candidate is tested through its nginx proxy (`/api/...`).
4. **Move traffic** (`update-traffic --to-latest`, tag removed). A failed migration or smoke test leaves traffic where it was and fails the workflow.

Images are deployed **by digest**, never by tag. The first deploy of a service cannot be held back (Cloud Run needs traffic on a new service), so it goes live and is smoke tested afterwards.

The web container renders its nginx config at start from `API_UPSTREAM` and `API_HOST` (Compose: `http://api:8000`; Cloud Run: the API service URL), resolving the upstream per request.

**The API service is public** (`--allow-unauthenticated`), like the web service: the data is synthetic and the browser reaches the API through the web proxy, so there is no sign-in to protect. This is a demo decision; revisit it before any real data.

**Before the first run** (in this order): the `production` environment (done), `bash infra/gcp/setup.sh` plan reviewed and then `--apply`, the repository variables it prints set in GitHub (**Settings → Secrets and variables → Actions → Variables**: `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SA_DEV`, `GCP_RUNTIME_SA_DEV`, `GCP_ARTIFACT_REGISTRY`), and the Neon `dev` branch prepared with `make seed-cloud` (below), after which `GCP_PROJECT_ID` is set last to switch the workflow on. Until `GCP_PROJECT_ID` is set the workflow is skipped.

**Not verified against a real project.** Nothing here has run against Google Cloud or Neon; the tests use fakes. The Cloud Run behaviours it relies on (tagged candidate URLs of the form `https://candidate---<service host>`, `--no-traffic` on an existing service, `update-traffic --to-latest`) are documented Cloud Run behaviour but unconfirmed in this project until the first run.

## Neon dev branch (`make seed-cloud`, `scripts/seed_cloud.sh`)

Run once per environment, by hand, before the first deploy. It prepares the Neon branch and gives the Cloud Run services their database URLs without anyone seeing a password:

1. creates the roles `cohortlens_reader` and `cohortlens_app` with **generated** passwords and the schema `app` (`db/cloud/00-roles.sql`), then stores the two database URLs as new versions of `cohortlens-dev-database-url-app` and `-reader` (the value goes to `gcloud` on stdin, never on a command line or to the screen);
2. loads `data/seed/omop_subset.sql.gz` in **one transaction**, only if the database has no persons yet;
3. applies `db/init/20-grants.sql` (the same grants as the local stack) and checks that `cdm.person` has 5,724 rows.

**What you do** (Git Bash, in the repository folder, signed in to `gcloud`):
1. In the Neon console open project `cohortlens` → **Branches** → `dev` → **Connect**. Choose database `neondb`, role `neondb_owner`, and **untick "Connection pooling"** (the script needs the direct connection and refuses a pooled one). Copy the connection string. The host in it starts with the branch's endpoint id, `ep-...`.
2. In Git Bash, set it for this terminal only (it is never written to a file or printed) and run the script, passing the endpoint id from the host name as the confirmation:
   `export NEON_DEV_URL='postgresql://neondb_owner:...@ep-xxxx....neon.tech/neondb?sslmode=require'`
   `bash scripts/seed_cloud.sh --env dev --confirm-endpoint ep-xxxx` (or `make seed-cloud ARGS="--confirm-endpoint ep-xxxx"` where `make` is installed)
3. Close the terminal or `unset NEON_DEV_URL` afterwards.

Use the connection string exactly as Neon gives it (the host must end in `.neon.tech`; a password with special characters stays percent-encoded and is passed on as is). **Never run the script with `bash -x`**: that would print the passwords.

**If it stops half way.** The order is roles, then the two secret versions, then the seed, then the grants, so each stop has one way out:
- *The roles were created or rotated but the secrets could not be stored* (the script says so): run it again with `--rotate-passwords`, which sets new passwords and stores them.
- *The roles exist and a secret has no enabled version*: the script refuses and tells you to run again with `--rotate-passwords`.
- *The seed failed*: nothing of it was kept (one transaction); run it again, and it reloads the seed because the database has no persons.
- A plain re-run reports "passwords unchanged" when the roles and both secrets exist; it cannot tell whether the stored URL still works. After any doubt, `--rotate-passwords` makes them agree again.

The confirmation exists so that a production connection string pasted by mistake is refused: the endpoint in the URL must be the one you typed. Needs `psql` or Docker (it falls back to the `postgres:18` image's `psql`). It is safe to run twice (existing roles keep their passwords, existing data is not reloaded); `--rotate-passwords` sets new passwords on the existing roles and stores new secret versions. It never drops, truncates or deletes anything. **Prod** is the same script with extra guards (below), run after dev has been prepared.

**Preparing prod (`--env prod`).** Same steps as above, with the **`production`** branch in the Neon console and its own variable and flags. In Git Bash, in the repository folder:
1. In the Neon console open Branches → `production` → Connect, database `neondb`, role `neondb_owner`, **untick "Connection pooling"**, and copy the string. Its host starts with that branch's endpoint id, `ep-...`.
2. `export NEON_PROD_URL='postgresql://neondb_owner:...@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require'`
3. `bash scripts/seed_cloud.sh --env prod --confirm-endpoint ep-xxxx --yes-this-is-production`
4. `unset NEON_PROD_URL`

The extra guards for prod, all checked **before anything is touched**: it needs `--yes-this-is-production` (the flag is refused for dev); it reads its own variable, `NEON_PROD_URL`, and never `NEON_DEV_URL`; dev must already have been prepared (its stored secret must have a value); and **the endpoint must differ from the one dev uses**, read from dev's stored database URL in Secret Manager (compared and never printed). The same check runs the other way round for dev: a dev run refuses an endpoint that prod's stored secret already points at. That catches the mistake of pasting the wrong branch's connection string, which the endpoint confirmation alone cannot see. If the other environment's secret cannot be listed or read (a gcloud access denial, a disabled latest version) or does not hold a readable database URL, the run stops before the database is touched; the guard looks only at the app secret of the other environment.

The cloud role SQL differs from the local init script in one way: the Neon owner is not a superuser, so the `app` schema is created by the owner and `cohortlens_app` is granted `USAGE, CREATE` on it (instead of `CREATE SCHEMA ... AUTHORIZATION cohortlens_app`). Rehearsed against a throwaway Postgres 18 with a non-superuser owner: roles, grants, password enforcement, the Alembic migration as `cohortlens_app`, a second run and a rotation all behaved as described. Not run against Neon itself.

## Prod promotion (`.github/workflows/promote-prod.yml`, `infra/gcp/promote.sh`)

After a successful dev deploy (or by hand: **Actions → Promote to prod → Run workflow**, **with the full 40-character commit sha you expect dev to serve**: it is required, so a by-hand run is guarded by the same version check as an automatic one and is refused if dev serves anything else), the workflow **pauses** because its job runs in the `production` environment. Nothing happens until the required reviewer approves, and only a job of that environment can impersonate the prod deploy account.

**To approve (browser):** GitHub → the repository → **Actions** → the **Promote to prod** run that says *Waiting* → **Review deployments** → tick **production** → optionally leave a comment → **Approve and deploy**. Choose **Reject** to stop it. The run name is `Promote to prod: <full commit sha> (<what the dev deploy was for>)`, so check the sha against what dev serves before you approve. **A newer promotion cancels older ones that are still waiting for approval** (the `supersede` job, `infra/gcp/cancel_stale_promotions.sh`): only runs seen waiting for approval at that moment are cancelled, not one that is already deploying (a run approved in the instant between listing and cancelling could still be cut off; the deploy is candidate-first, so prod keeps serving the previous revision), and a failure of the cleanup never blocks the promotion. A stale run that is somehow still approvable is refused anyway by the version check below (it happened on 2026-10-29, audit section 13).

After approval, `promote.sh` does this and nothing else:
1. reads the images of the **revisions that serve the dev services right now** (the one revision holding all the traffic, deployed by digest, which passed the dev smoke test; never the newest template, which after a failed dev deploy is an untested candidate) and refuses unless both are digest-pinned images of this project's registry. It never looks anything up by tag, because a tag can be moved;
2. asks the dev API which commit it runs (`/version`) and refuses if that is not the commit the approver was shown (dev moved on: start a new promotion);
3. calls `deploy.sh --env prod` with **those same digests**: the migration job on the prod database first, then each service as a `--no-traffic` revision tagged `candidate`, a smoke test on its tagged URL, and only then the traffic move. A failed smoke test leaves traffic where it was and fails the run.

The prod smoke test creates the reference cohort in the **prod** database, runs it and deletes it again. The first prod deploy of a service cannot be held back (Cloud Run needs traffic on a new service), so it goes live directly and is smoke tested afterwards; the approval already happened before it. **If the web smoke test fails after the API went live**, prod runs the new API with the old web (the API is deployed and promoted first). Migrations are expand-then-contract, so the old web keeps working against the new API; fix forward or roll the API back (see Rollback below).

Rollback by traffic shift is a separate workflow (see Rollback below).

**Before the first promotion:** prepare the prod Neon branch with `seed_cloud.sh --env prod` (see above), which creates the prod roles, schema and seed and fills the two prod secrets. Until then a promotion fails at the prod migration, before any revision exists. A promotion has not run against the real project yet.

## Rollback (`.github/workflows/rollback.yml`, `infra/gcp/rollback.sh`)

A rollback is a **traffic shift**, not a rebuild or a redeploy: Cloud Run revisions are immutable, so recovering means sending 100% of a service's traffic to a revision that already exists. It takes seconds.

**To roll back (browser):** GitHub → the repository → **Actions** → **Rollback** → **Run workflow** (branch `main`) and fill in:
- **environment** `dev` or `prod`, and **service** `api` or `web` (roll each service back separately);
- **revision**: leave empty to go to the revision **before the one serving now**, or paste a revision name such as `cohortlens-api-dev-00002-abc` (the names are on the service's *Revisions* tab in the Cloud Run console);
- **incident**: an issue number to have the result commented there (optional);
- **dry run**: tick it first if unsure: it prints `from -> to` and changes nothing.

A **prod** rollback runs in the protected `production` environment, so it waits for the reviewer's approval exactly like a promotion (**Review deployments → production → Approve and deploy**): the prod deploy account can only be used from that environment.

**Rollbacks and deploys.** A rollback has its **own concurrency group**, so a queued deploy or promotion can never replace a waiting rollback (GitHub keeps only one pending run per group). The reverse is covered in the deploy script: it reads which revision serves the service before it deploys the candidate and again before it moves traffic, and if that changed in between (a rollback landed meanwhile) it **refuses to move the traffic**, leaves the new revision as a no-traffic candidate and fails, so a rollback is never undone by a deploy that finishes later.

What `rollback.sh` checks and does, in this order:
1. the service must have **exactly one revision serving all its traffic** (anything else is refused and nothing is changed);
2. without `--revision` the target is the **next older** revision than the serving one, so a newer candidate that never served is never chosen; a given revision must be one of this service's own revisions (the name must match the environment and service) and must not be the one already serving; the oldest revision has no previous one;
3. **before anything moves, the target is checked**: it is tagged (`rollback-check`) and smoke tested on its own tagged URL, then the tag is removed. An older revision may be one that failed its candidate smoke test and never served, so a revision that fails this check is **never** moved to (the run fails and says so; pick another revision). `--no-verify` (command line only, not in the workflow) skips just this check, for an emergency where the target is known good, and the incident comment says so;
4. `gcloud run services update-traffic ... --to-revisions=<target>=100`;
5. the **smoke test** on the service URL (the web service through its `/api` proxy); it checks that the rolled-back revision is alive and ready and runs the reference cohort. A failed smoke test **fails the run but does not move the traffic back**: that is a decision for a person. To undo the rollback, run **Rollback** again with `revision` set to the original revision (the incident comment names it);
6. with an incident number, a **comment** on that issue: from revision, to revision, who started it, a link to the run, and whether the smoke test passed (or that it failed, or that the traffic was not moved). A comment that cannot be posted is a warning, not a failed rollback.

It builds nothing, deploys nothing, deletes nothing and touches no IAM.

**Rolling forward again.** A rollback pins the traffic to the chosen revision. The next dev deploy (or prod promotion) puts the new revision back in charge (`update-traffic --to-latest`). To go back sooner, run **Rollback** again with `revision` set to the newer revision.

**Verified against the real project:** a `--dry-run` for dev `api` and `web` (it reads the real revisions and traffic and moves nothing). That run found a bug (Windows `gcloud` prints CRLF line endings, so revision names carried a trailing carriage return), now fixed and tested. **Real traffic moves have since run on dev:** [run 40727516793](https://github.com/erickyegon/cohortlens/actions/runs/40727516793) (by hand) and the on-call agent's rollback in drill S1 ([run 40894738766](https://github.com/erickyegon/cohortlens/actions/runs/40894738766)). No prod rollback has been run.

## Telemetry export (optional)

Set the repository variable `GCP_OTLP_ENDPOINT` (a Grafana Cloud OTLP URL such as `https://otlp-gateway-prod-us-east-2.grafana.net/otlp`) and store the credential with `scripts/store_otlp_secret.sh`; the api service then exports traces, metrics and logs. The secret slot is created by that script, not by `setup.sh`. The steps are in `docs/observability.md`. Unset, nothing is exported and the deploys are unchanged.

## Secrets
Cloud database credentials, the Grafana Cloud token and the Anthropic API key live in **Google Secret Manager** (GitHub Environments hold only what the workflows themselves need). The passwords in `compose.yaml` and `.env.example` are local-only placeholders for `docker compose up`; they must never be reused in any real environment, and a real environment must never rely on those defaults.

## Database version

Production (Neon project `cohortlens`, AWS US East 2) runs **Postgres 18**, so CI, the local Compose stack and the tests run Postgres 18 too. Moving from 16 to 18 was checked, not assumed: the full backend suite (424 tests) and the seed tests (reference cohort 920 to 481, latency budgets) pass on Postgres 18. One practical difference: Postgres 18 images keep their data in `/var/lib/postgresql/<major>/docker`, so `compose.yaml` mounts the volume at `/var/lib/postgresql`, not `/var/lib/postgresql/data` (the image refuses to start with the old mount and says why).

**After pulling this change, run `docker compose down -v` once.** An existing local `pgdata` volume holds a Postgres 16 cluster, which Postgres 18 will not start on; the container exits with a clear error until the old volume is removed. The seed is reloaded on the next `docker compose up --build`. Postgres 18 also turns data checksums on by default; that does not affect restoring the plain-SQL seed.
