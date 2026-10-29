#!/usr/bin/env bash
# Deploy CohortLens to one environment on Cloud Run (docs/deployment.md, issues #17 and #18). Run by deploy-dev.yml
# (dev) and, through promote.sh and after the reviewer's approval, by promote-prod.yml (prod).
#
#   bash infra/gcp/deploy.sh --env dev|prod --version <git sha> --api-image <ref@sha256:...> --web-image <ref@sha256:...>
#
# Settings (environment variables, validated before anything runs): PROJECT_ID, REGION, RUNTIME_SA (the runtime
# service account of the environment). SMOKE_SCRIPT overrides the smoke test (tests only).
#
# The order is the safety property:
#   1. the database migrations run as a Cloud Run job and must finish BEFORE any new revision exists (migrations are
#      expand-then-contract, so the revision that is still serving keeps working against the migrated schema);
#   2. each service is deployed as a revision that gets NO traffic, under the tag `candidate`;
#   3. the candidate is smoke tested on its own tagged URL;
#   4. only then does traffic move to it. A failed migration or smoke test leaves traffic where it was.
# The very first deploy of a service has nothing to protect (Cloud Run cannot create a service without traffic), so it
# is deployed directly and smoke tested afterwards.
# Images must be pinned by digest. Secrets are passed by reference (Secret Manager), never by value. Nothing is deleted.
set -euo pipefail
export LC_ALL=C

ENVIRONMENT="" VERSION="" API_IMAGE="" WEB_IMAGE=""
while (( $# )); do
  case "$1" in
    --env | --version | --api-image | --web-image)
      (( $# >= 2 )) || { echo "error: $1 needs a value" >&2; exit 2; }
      case "$1" in
        --env) ENVIRONMENT="$2" ;;
        --version) VERSION="$2" ;;
        --api-image) API_IMAGE="$2" ;;
        --web-image) WEB_IMAGE="$2" ;;
      esac
      shift 2 ;;
    -h | --help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown argument: '$1'" >&2; exit 2 ;;
  esac
done

refuse() { echo "error: $*" >&2; exit 2; }
die() { echo "error: $*" >&2; exit 1; }

[[ "$ENVIRONMENT" == "dev" || "$ENVIRONMENT" == "prod" ]] || refuse "--env must be 'dev' or 'prod'"
[[ "$VERSION" =~ ^[0-9a-f]{7,40}$ ]] || refuse "--version must be a git commit sha"
IMAGE_PATTERN='^[a-z0-9.-]+/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$'
[[ "$API_IMAGE" =~ $IMAGE_PATTERN ]] || refuse "--api-image must be pinned by digest (registry/path@sha256:...)"
[[ "$WEB_IMAGE" =~ $IMAGE_PATTERN ]] || refuse "--web-image must be pinned by digest (registry/path@sha256:...)"
PROJECT_ID="${PROJECT_ID-}"; REGION="${REGION-}"; RUNTIME_SA="${RUNTIME_SA-}"
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || refuse "invalid or missing PROJECT_ID"
[[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] || refuse "invalid or missing REGION"
[[ "$RUNTIME_SA" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$ ]] \
  || refuse "invalid or missing RUNTIME_SA"
# an environment only ever runs as its own runtime account (cohortlens-run-dev / cohortlens-run-prod)
[[ "$RUNTIME_SA" == "cohortlens-run-${ENVIRONMENT}@"* ]] || refuse "RUNTIME_SA must be the cohortlens-run-${ENVIRONMENT} account for --env ${ENVIRONMENT}"

# optional: export the api's telemetry to Grafana Cloud (a repository variable; the credential is a Secret Manager secret
# made by scripts/store_otlp_secret.sh, never an argument or a variable)
OTLP_ENDPOINT="${OTLP_ENDPOINT-}"
[[ -z "$OTLP_ENDPOINT" || "$OTLP_ENDPOINT" =~ ^https://[a-z0-9-]+(\.[a-z0-9-]+)*\.grafana\.net/otlp$ ]] \
  || refuse "OTLP_ENDPOINT must be a Grafana Cloud OTLP URL (https://<stack>.grafana.net/otlp)"
PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON" ] || refuse "python is required to read the traffic of a service"
SMOKE="${SMOKE_SCRIPT:-$(dirname "$0")/../../scripts/smoke_cloud.sh}"
TARGET=(--region="$REGION" --project="$PROJECT_ID")
API="cohortlens-api-${ENVIRONMENT}"
WEB="cohortlens-web-${ENVIRONMENT}"
JOB="cohortlens-migrate-${ENVIRONMENT}"
APP_SECRET="cohortlens-${ENVIRONMENT}-database-url-app"
READER_SECRET="cohortlens-${ENVIRONMENT}-database-url-reader"

heading() { echo; echo "== $*"; }

# serving_revision NAME: the ONE revision that holds all the traffic of a service right now (anything else is refused).
# Read before the candidate is deployed and again before traffic moves: if it changed in between, someone rolled the
# service back (or deployed) while this run was going, and moving the traffic now would undo that.
serving_revision() {
  local json
  json="$(gcloud run services describe "$1" "${TARGET[@]}" --format=json)" || die "could not read $1"
  printf '%s' "$json" | "$PYTHON" -c '
import json, sys
try:
    live = [t for t in json.load(sys.stdin)["status"]["traffic"] if t.get("percent", 0) > 0]
    assert len(live) == 1 and live[0]["percent"] == 100 and live[0]["revisionName"]
    print(live[0]["revisionName"])
except Exception:
    sys.exit(1)
' || die "$1 does not have exactly one revision serving all its traffic; not deploying on top of that"
}

# service_url NAME: the main URL of a service
service_url() { gcloud run services describe "$1" "${TARGET[@]}" --format='value(status.url)'; }

# deploy_service NAME SMOKE_ARGS... -- DEPLOY_FLAGS...
# Deploys NAME as a no-traffic candidate (or directly, the first time), smoke tests it, then moves traffic.
deploy_service() {
  local name="$1"; shift
  local smoke_args=() flags=() seen=0 part output first=0 candidate_url url
  for part in "$@"; do
    if [[ "$part" == "--" ]]; then seen=1; continue; fi
    if (( seen )); then flags+=("$part"); else smoke_args+=("$part"); fi
  done

  if output="$(gcloud run services describe "$name" "${TARGET[@]}" --format='value(metadata.name)' 2>&1)"; then
    first=0
  elif grep -qiE 'permission|denied|forbidden|may not exist|or it may' <<< "$output"; then
    echo "$output" >&2
    die "could not tell whether ${name} exists or access was denied; deploying nothing on an ambiguous check"
  elif grep -qiE 'cannot find service|not[ _]?found|does not exist' <<< "$output"; then
    first=1
  else
    echo "$output" >&2
    die "could not check whether ${name} exists; deploying nothing on a failed check"
  fi

  if (( first )); then
    heading "${name}: first deploy (nothing is serving yet, so it goes live directly)"
    gcloud run deploy "$name" "${flags[@]}" "${TARGET[@]}"
    url="$(service_url "$name")"
    bash "$SMOKE" "$url" "${smoke_args[@]}" || die "smoke test failed on the first deploy of ${name}"
    return 0
  fi

  local serving_before serving_now
  serving_before="$(serving_revision "$name")" || exit 1
  heading "${name}: candidate revision (no traffic); serving now: ${serving_before}"
  gcloud run deploy "$name" "${flags[@]}" --no-traffic --tag=candidate "${TARGET[@]}"
  url="$(service_url "$name")"
  candidate_url="${url/https:\/\//https:\/\/candidate---}"
  heading "${name}: smoke test on ${candidate_url}"
  bash "$SMOKE" "$candidate_url" "${smoke_args[@]}" || die "smoke test failed on the candidate of ${name}; traffic was NOT moved"
  serving_now="$(serving_revision "$name")" || exit 1
  [ "$serving_before" = "$serving_now" ] \
    || die "the traffic of ${name} changed during this deploy (it was on ${serving_before}, now ${serving_now}); NOT moving it to the new revision, so a rollback is not undone. The new revision stays as a no-traffic candidate"
  heading "${name}: move traffic to the new revision"
  gcloud run services update-traffic "$name" --to-latest --remove-tags=candidate "${TARGET[@]}"
}

echo "CohortLens deploy: ${ENVIRONMENT}   version ${VERSION}   project ${PROJECT_ID}   region ${REGION}"

heading "migrations: run to completion before any new revision exists"
gcloud run jobs deploy "$JOB" --image="$API_IMAGE" --service-account="$RUNTIME_SA" \
  --set-secrets="DATABASE_URL_APP=${APP_SECRET}:latest" --command=alembic --args=upgrade,head \
  --max-retries=0 --task-timeout=300s "${TARGET[@]}"
gcloud run jobs execute "$JOB" --wait "${TARGET[@]}"

API_ENV_VARS="SERVICE_VERSION=${VERSION},DEPLOYMENT_ENVIRONMENT=${ENVIRONMENT}"
API_SECRETS="DATABASE_URL_APP=${APP_SECRET}:latest,DATABASE_URL_READER=${READER_SECRET}:latest"
if [ -n "$OTLP_ENDPOINT" ]; then
  API_ENV_VARS+=",OTEL_EXPORTER_OTLP_ENDPOINT=${OTLP_ENDPOINT}"
  API_SECRETS+=",OTEL_EXPORTER_OTLP_HEADERS=cohortlens-${ENVIRONMENT}-otlp-headers:latest"
fi
deploy_service "$API" --expect-version "$VERSION" -- --image="$API_IMAGE" --service-account="$RUNTIME_SA" --port=8000 --allow-unauthenticated \
  --set-secrets="$API_SECRETS" --set-env-vars="$API_ENV_VARS"

API_URL="$(service_url "$API")"
API_HOST="${API_URL#https://}"
deploy_service "$WEB" --api-prefix /api --expect-version "$VERSION" -- --image="$WEB_IMAGE" --service-account="$RUNTIME_SA" --port=8080 \
  --allow-unauthenticated \
  --set-env-vars="API_UPSTREAM=${API_URL},API_HOST=${API_HOST},NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1"

heading "done"
echo "  api  $(service_url "$API")"
echo "  web  $(service_url "$WEB")"
