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
#   - Workload Identity Federation (no keys): a pool and a GitHub OIDC provider that trusts ONLY this repository, pinned
#       by name AND by GitHub's numeric repository and owner ids (read from GitHub at apply time), so a renamed or
#       re-registered repository name cannot take its place. dev can be impersonated only from the main branch, prod
#       only from jobs in the `production` GitHub environment.
#   - four EMPTY Secret Manager slots (database URLs). Values are loaded by the Neon script, never here.
# It never deletes or removes anything, grants no broad role, and is safe to run twice: it checks before creating,
# and an EXISTING provider or pool is verified (condition, state), never trusted blindly. It does not create projects,
# billing accounts or budgets, and prints no secret.
#
# Before --apply: the GitHub environment `production` must exist with a required reviewer, administrator bypass off and
# deployment branches limited to main (no tag rules). --apply checks that (read-only, `gh`); --skip-github-check overrides.
set -euo pipefail
export LC_ALL=C

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
SKIP_GITHUB_CHECK=0
for argument in "$@"; do
  case "$argument" in
    --apply) APPLY=1 ;;
    --skip-github-check) SKIP_GITHUB_CHECK=1 ;;
    -h | --help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "unknown argument: '$argument' (use --apply, --skip-github-check or --help)" >&2; exit 2 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }

[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || { echo "invalid PROJECT_ID" >&2; exit 2; }
[[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] || { echo "invalid REGION" >&2; exit 2; }
if [[ "$GITHUB_REPO" =~ ^([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})$ ]]; then
  [[ "${BASH_REMATCH[2]}" != "." && "${BASH_REMATCH[2]}" != ".." ]] || { echo "invalid GITHUB_REPO" >&2; exit 2; }
else
  echo "invalid GITHUB_REPO (expected owner/name)" >&2; exit 2
fi

if (( APPLY )); then
  # Read-only GitHub checks FIRST: nothing in Google Cloud is touched unless the repository and its production
  # environment are what the trust model assumes.
  command -v gh > /dev/null || die "the GitHub CLI (gh) is required to read the repository ids and check the environment"
  REPO_ID="$(gh api "repos/${GITHUB_REPO}" --jq .id)" || die "could not read the repository id from GitHub"
  OWNER_ID="$(gh api "repos/${GITHUB_REPO}" --jq .owner.id)" || die "could not read the repository owner id from GitHub"
  [[ "$REPO_ID" =~ ^[0-9]+$ ]] || die "GitHub returned no valid repository id"
  [[ "$OWNER_ID" =~ ^[0-9]+$ ]] || die "GitHub returned no valid repository owner id"
  if (( SKIP_GITHUB_CHECK )); then
    echo "WARNING: --skip-github-check: the production environment protection was NOT verified" >&2
  else
    environment_url="repos/${GITHUB_REPO}/environments/production"
    reviewers="$(gh api "$environment_url" --jq '[.protection_rules[] | select(.type=="required_reviewers")] | length')" \
      || die "the GitHub environment 'production' does not exist or cannot be read in ${GITHUB_REPO} (GitHub answers 404 for both; check that gh is signed in with access to the repository); create it first (see docs/deployment.md)"
    [[ "$reviewers" =~ ^[0-9]+$ ]] && (( reviewers >= 1 )) \
      || die "the GitHub environment 'production' has no required reviewer; add one first"
    # administrators who may bypass the protection rules could deploy to production without the approval
    bypass="$(gh api "$environment_url" --jq '.can_admins_bypass')" \
      || die "could not read whether administrators can bypass the protection of 'production'"
    [[ "$bypass" == "false" ]] \
      || die "the GitHub environment 'production' lets administrators bypass its protection rules (found: '${bypass}'); turn off 'Allow administrators to bypass configured protection rules' first"
    custom="$(gh api "$environment_url" --jq '.deployment_branch_policy.custom_branch_policies')" \
      || die "could not read the deployment branch policy of 'production'"
    [[ "$custom" == "true" ]] || die "the GitHub environment 'production' must limit deployment branches to main (custom branch policy)"
    # only policies of type "branch" count: a tag policy named main is not the main branch
    branches="$(gh api "${environment_url}/deployment-branch-policies" \
      --jq '[.branch_policies[] | select(.type == "branch") | .name] | sort | join(",")')" \
      || die "could not read the deployment branches of 'production'"
    [[ "$branches" == "main" ]] || die "the GitHub environment 'production' must allow deployments from the main branch only (found: '${branches}')"
    other_policies="$(gh api "${environment_url}/deployment-branch-policies" \
      --jq '[.branch_policies[] | select(.type != "branch")] | length')" \
      || die "could not read the deployment policies of 'production'"
    [[ "$other_policies" == "0" ]] || die "the GitHub environment 'production' has a deployment policy that is not a branch policy (a tag rule would let tags deploy to production); remove it first"
  fi
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
  [[ "$PROJECT_NUMBER" =~ ^[0-9]+$ ]] || die "could not read the project number of ${PROJECT_ID}"
else
  PROJECT_NUMBER="<PROJECT_NUMBER>"
  REPO_ID="<REPO_ID>"
  OWNER_ID="<OWNER_ID>"
fi

EXPECTED_ISSUER="https://token.actions.githubusercontent.com"
EXPECTED_MAPPING="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.environment=assertion.environment"
# the same mapping as gcloud prints it with --format='json(attributeMapping)' once whitespace is stripped (keys sorted)
EXPECTED_MAPPING_JSON='{"attributeMapping":{"attribute.environment":"assertion.environment","attribute.ref":"assertion.ref","attribute.repository":"assertion.repository","google.subject":"assertion.sub"}}'
EXPECTED_CONDITION="assertion.repository_id == '${REPO_ID}' && assertion.repository_owner_id == '${OWNER_ID}' && assertion.repository == '${GITHUB_REPO}'"

sa_email() { echo "cohortlens-$1-$2@${PROJECT_ID}.iam.gserviceaccount.com"; }   # kind (run|deploy), environment
secret_id() { echo "cohortlens-$1-database-url-$2"; }                          # environment, kind
pool_principal() { echo "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/$1"; }

heading() { echo; echo "== $*"; }

# run a gcloud command (apply) or print it, shell-quoted so it can be copied (plan)
run() {
  if (( APPLY )); then
    gcloud "$@"
  else
    printf '  gcloud'; printf ' %q' "$@"; printf '\n'
  fi
}

ENSURE_EXISTED=0
# ensure DESCRIBE-ARGS... -- CREATE-ARGS...   create only when the describe says NOT FOUND. Any other describe failure
# (permission denied, API disabled, unavailable) stops the script instead of being mistaken for "does not exist".
ensure() {
  local describe_args=() create_args=() seen=0 output
  for part in "$@"; do
    if [[ "$part" == "--" ]]; then seen=1; continue; fi
    if (( seen )); then create_args+=("$part"); else describe_args+=("$part"); fi
  done
  ENSURE_EXISTED=0
  if (( APPLY )); then
    if output="$(gcloud "${describe_args[@]}" 2>&1)"; then
      ENSURE_EXISTED=1
      echo "  exists: ${describe_args[*]}"
      return 0
    elif grep -qiE 'permission|denied|forbidden|may not exist|or it may' <<< "$output"; then
      # gcloud words some permission failures as "not found or permission denied" or "(or it may not exist)": that
      # does not prove the resource is absent, so it is never a licence to create
      echo "$output" >&2
      die "could not tell whether this exists or access was denied (${describe_args[*]}); not creating anything on an ambiguous check"
    elif grep -qiE 'not[ _]?found|does not exist' <<< "$output"; then
      run "${create_args[@]}"
    else
      echo "$output" >&2
      die "could not check whether this exists (${describe_args[*]}); not creating anything on a failed check"
    fi
  else
    run "${create_args[@]}"
  fi
}

echo "CohortLens Google Cloud setup"
echo "  project: ${PROJECT_ID}   region: ${REGION}   github repository: ${GITHUB_REPO}"
if (( APPLY )); then
  echo "  github repository id ${REPO_ID}, owner id ${OWNER_ID} (the provider is pinned to these)"
else
  echo "  PLAN ONLY: no command is run. Re-run with --apply to do it."
  echo "  Before --apply: the GitHub environment 'production' must exist with a required reviewer and deployment"
  echo "  branches limited to main. --apply reads the repository ids from GitHub and checks that, and refuses if not."
fi

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
if (( APPLY )) && (( ENSURE_EXISTED )); then
  state="$(gcloud iam workload-identity-pools describe "$POOL" --location=global "--project=${PROJECT_ID}" --format='value(state)')"
  [[ "$state" == "ACTIVE" ]] || die "the existing workload identity pool '${POOL}' is in state '${state}', not ACTIVE; not continuing"
fi
ensure iam workload-identity-pools providers describe "$PROVIDER" "--workload-identity-pool=${POOL}" \
  --location=global "--project=${PROJECT_ID}" -- \
  iam workload-identity-pools providers create-oidc "$PROVIDER" "--workload-identity-pool=${POOL}" --location=global \
  "--issuer-uri=${EXPECTED_ISSUER}" \
  "--attribute-mapping=${EXPECTED_MAPPING}" \
  "--attribute-condition=${EXPECTED_CONDITION}" \
  "--display-name=GitHub Actions" "--project=${PROJECT_ID}"
if (( APPLY )) && (( ENSURE_EXISTED )); then
  # an existing provider is only acceptable if its trust condition is exactly the expected one
  existing="$(gcloud iam workload-identity-pools providers describe "$PROVIDER" "--workload-identity-pool=${POOL}" \
    --location=global "--project=${PROJECT_ID}" --format='value(attributeCondition)')"
  [[ "$existing" == "$EXPECTED_CONDITION" ]] \
    || die "the existing provider '${PROVIDER}' has a different attribute condition than expected; refusing to build on it (condition found: '${existing}')"
  # ... and only if it trusts GitHub's token issuer and maps exactly the attributes the bindings below rely on
  issuer="$(gcloud iam workload-identity-pools providers describe "$PROVIDER" "--workload-identity-pool=${POOL}" \
    --location=global "--project=${PROJECT_ID}" --format='value(oidc.issuerUri)')"
  [[ "$issuer" == "$EXPECTED_ISSUER" ]] \
    || die "the existing provider '${PROVIDER}' has a different issuer than expected; refusing to build on it (issuer found: '${issuer}')"
  mapping="$(gcloud iam workload-identity-pools providers describe "$PROVIDER" "--workload-identity-pool=${POOL}" \
    --location=global "--project=${PROJECT_ID}" --format='json(attributeMapping)' | tr -d ' \t\r\n')"
  [[ "$mapping" == "$EXPECTED_MAPPING_JSON" ]] \
    || die "the existing provider '${PROVIDER}' has a different attribute mapping than expected; refusing to build on it (mapping found: '${mapping}')"
fi

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
