#!/usr/bin/env bash
# One-time Google Cloud setup for CohortLens (docs/deployment.md, issue #17).
#
#   bash infra/gcp/setup.sh            print the plan; run NOTHING
#   bash infra/gcp/setup.sh --apply    do it
#
# Settings (environment variables, validated before anything runs):
#   PROJECT_ID   default cohortlens
#   REGION       default us-east5 (Columbus, next to the Neon project in AWS US East 2)
#   GITHUB_REPO  default erickyegon/cohortlens (the only repository allowed to deploy)
#
# What it creates, and only this:
#   - enables the APIs it needs
#   - an Artifact Registry Docker repository
#   - per environment (dev, prod): a runtime service account for Cloud Run and a deploy service account for GitHub
#   - Workload Identity Federation (no keys): a pool and a GitHub OIDC provider that trusts ONLY GITHUB_REPO;
#       dev deploys can be impersonated only from the main branch, prod only from the `production` GitHub environment
#   - four EMPTY Secret Manager slots (database URLs). Values are loaded by the Neon script, never here.
# It never deletes or removes anything, grants no broad role, and is safe to run twice (it checks before creating).
# It does not create projects, billing accounts or budgets, and prints no secret.
set -euo pipefail

PROJECT_ID="${PROJECT_ID-cohortlens}"
REGION="${REGION-us-east5}"
GITHUB_REPO="${GITHUB_REPO-erickyegon/cohortlens}"
AR_REPO="cohortlens"
POOL="github"
PROVIDER="github-actions"
APIS=(run.googleapis.com artifactregistry.googleapis.com iam.googleapis.com iamcredentials.googleapis.com
  sts.googleapis.com secretmanager.googleapis.com cloudresourcemanager.googleapis.com)
ENVIRONMENTS=(dev prod)
SECRET_KINDS=(app reader)

APPLY=0
for argument in "$@"; do
  case "$argument" in
    --apply) APPLY=1 ;;
    -h | --help) sed -n '2,23p' "$0"; exit 0 ;;
    *) echo "unknown argument: $argument (use --apply or --help)" >&2; exit 2 ;;
  esac
done

[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || { echo "invalid PROJECT_ID" >&2; exit 2; }
[[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] || { echo "invalid REGION" >&2; exit 2; }
[[ "$GITHUB_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "invalid GITHUB_REPO (expected owner/name)" >&2; exit 2; }

if (( APPLY )); then
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
else
  PROJECT_NUMBER="<PROJECT_NUMBER>"
fi

sa_email() { echo "cohortlens-$1-$2@${PROJECT_ID}.iam.gserviceaccount.com"; }   # kind (run|deploy), environment
secret_id() { echo "cohortlens-$1-database-url-$2"; }                          # environment, kind
pool_principal() { echo "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/$1"; }

heading() { echo; echo "== $*"; }

# run a gcloud command (apply) or print it (plan)
run() {
  if (( APPLY )); then
    gcloud "$@"
  else
    echo "  gcloud $*"
  fi
}

# create only when the resource does not exist yet; the describe command is the one passed first
ensure() {
  local describe_args=() create_args=() seen=0
  for part in "$@"; do
    if [[ "$part" == "--" ]]; then seen=1; continue; fi
    if (( seen )); then create_args+=("$part"); else describe_args+=("$part"); fi
  done
  if (( APPLY )) && gcloud "${describe_args[@]}" > /dev/null 2>&1; then
    echo "  exists, skipped: ${describe_args[*]}"
  else
    run "${create_args[@]}"
  fi
}

echo "CohortLens Google Cloud setup"
echo "  project: ${PROJECT_ID}   region: ${REGION}   github repository: ${GITHUB_REPO}"
(( APPLY )) || echo "  PLAN ONLY: no command is run. Re-run with --apply to do it."

heading "apis: enable what the deployment needs"
run services enable "${APIS[@]}" "--project=${PROJECT_ID}"

heading "artifact-registry: the Docker repository for the images"
ensure artifacts repositories describe "$AR_REPO" "--location=${REGION}" "--project=${PROJECT_ID}" -- \
  artifacts repositories create "$AR_REPO" --repository-format=docker "--location=${REGION}" \
  "--description=CohortLens images" "--project=${PROJECT_ID}"

heading "service accounts: a runtime and a deploy account per environment"
for environment in "${ENVIRONMENTS[@]}"; do
  for kind in run deploy; do
    ensure iam service-accounts describe "$(sa_email "$kind" "$environment")" "--project=${PROJECT_ID}" -- \
      iam service-accounts create "cohortlens-${kind}-${environment}" \
      "--display-name=CohortLens ${kind} (${environment})" "--project=${PROJECT_ID}"
  done
done

heading "workload identity federation: GitHub Actions without keys, only for ${GITHUB_REPO}"
ensure iam workload-identity-pools describe "$POOL" --location=global "--project=${PROJECT_ID}" -- \
  iam workload-identity-pools create "$POOL" --location=global "--display-name=GitHub" "--project=${PROJECT_ID}"
ensure iam workload-identity-pools providers describe "$PROVIDER" "--workload-identity-pool=${POOL}" \
  --location=global "--project=${PROJECT_ID}" -- \
  iam workload-identity-pools providers create-oidc "$PROVIDER" "--workload-identity-pool=${POOL}" --location=global \
  --issuer-uri=https://token.actions.githubusercontent.com \
  "--attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.environment=assertion.environment" \
  "--attribute-condition=assertion.repository == '${GITHUB_REPO}'" \
  "--display-name=GitHub Actions" "--project=${PROJECT_ID}"

heading "who may impersonate which deploy account"
# dev: only workflows running on the main branch; prod: only jobs in the `production` GitHub environment
run iam service-accounts add-iam-policy-binding "$(sa_email deploy dev)" --role=roles/iam.workloadIdentityUser \
  "--member=$(pool_principal attribute.ref/refs/heads/main)" "--project=${PROJECT_ID}"
run iam service-accounts add-iam-policy-binding "$(sa_email deploy prod)" --role=roles/iam.workloadIdentityUser \
  "--member=$(pool_principal attribute.environment/production)" "--project=${PROJECT_ID}"

heading "what the deploy accounts may do (narrow roles only)"
for environment in "${ENVIRONMENTS[@]}"; do
  deployer="serviceAccount:$(sa_email deploy "$environment")"
  run projects add-iam-policy-binding "$PROJECT_ID" "--member=${deployer}" --role=roles/run.admin --condition=None
  run artifacts repositories add-iam-policy-binding "$AR_REPO" "--location=${REGION}" "--member=${deployer}" \
    --role=roles/artifactregistry.writer "--project=${PROJECT_ID}"
  # act as its OWN environment's runtime account, and nothing else
  run iam service-accounts add-iam-policy-binding "$(sa_email run "$environment")" "--member=${deployer}" \
    --role=roles/iam.serviceAccountUser "--project=${PROJECT_ID}"
done

heading "secret manager: empty slots for the database URLs (values are loaded by the Neon script)"
for environment in "${ENVIRONMENTS[@]}"; do
  for kind in "${SECRET_KINDS[@]}"; do
    secret="$(secret_id "$environment" "$kind")"
    ensure secrets describe "$secret" "--project=${PROJECT_ID}" -- \
      secrets create "$secret" --replication-policy=automatic "--project=${PROJECT_ID}"
    run secrets add-iam-policy-binding "$secret" "--member=serviceAccount:$(sa_email run "$environment")" \
      --role=roles/secretmanager.secretAccessor "--project=${PROJECT_ID}"
  done
done

heading "done: set these as GitHub repository VARIABLES (they are identifiers, not secrets)"
echo "  GCP_PROJECT_ID                 ${PROJECT_ID}"
echo "  GCP_REGION                     ${REGION}"
echo "  GCP_WORKLOAD_IDENTITY_PROVIDER projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "  GCP_DEPLOY_SA_DEV              $(sa_email deploy dev)"
echo "  GCP_DEPLOY_SA_PROD             $(sa_email deploy prod)"
echo "  GCP_RUNTIME_SA_DEV             $(sa_email run dev)"
echo "  GCP_RUNTIME_SA_PROD            $(sa_email run prod)"
echo "  GCP_ARTIFACT_REGISTRY          ${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}"
