#!/usr/bin/env bash
# Promote what is running in dev to production (docs/deployment.md, issue #18). Run by promote-prod.yml AFTER the
# production environment's reviewer has approved the job.
#
#   bash infra/gcp/promote.sh [--expect-version <git sha>]
#
# Settings (environment variables, validated before anything runs): PROJECT_ID, REGION, REGISTRY (the Artifact Registry
# path, e.g. us-east5-docker.pkg.dev/cohortlens/cohortlens) and RUNTIME_SA (the PROD runtime service account).
# DEPLOY_SCRIPT overrides the deploy script (tests only).
#
# It builds nothing and looks nothing up by tag (tags can be moved). It reads the images of the revisions that SERVE
# the DEV services right now (all traffic on one revision; never the newest, possibly untested, template). Those
# were deployed by digest and passed the dev smoke test. It checks that both are digest-pinned images of this
# project's registry, asks the dev API which commit it is running, and hands exactly those digests to
# infra/gcp/deploy.sh for prod: a no-traffic candidate, a smoke test on its tagged URL, and only then traffic.
# --expect-version is the commit the approver was shown; if dev has moved on to another commit since, it refuses.
# It changes nothing in dev and deletes nothing.
set -euo pipefail
export LC_ALL=C

EXPECTED=""
while (( $# )); do
  case "$1" in
    --expect-version)
      (( $# >= 2 )) || { echo "error: --expect-version needs a value" >&2; exit 2; }
      EXPECTED="$2"; shift 2 ;;
    -h | --help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown argument: '$1'" >&2; exit 2 ;;
  esac
done

refuse() { echo "error: $*" >&2; exit 2; }
die() { echo "error: $*" >&2; exit 1; }

[[ -z "$EXPECTED" || "$EXPECTED" =~ ^[0-9a-f]{40}$ ]] || refuse "--expect-version must be the full 40-character git commit sha"
PROJECT_ID="${PROJECT_ID-}"; REGION="${REGION-}"; REGISTRY="${REGISTRY-}"; RUNTIME_SA="${RUNTIME_SA-}"
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || refuse "invalid or missing PROJECT_ID"
[[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] || refuse "invalid or missing REGION"
[[ "$REGISTRY" =~ ^[a-z0-9.-]+/[a-z0-9-]+/[a-z0-9._-]+$ ]] || refuse "invalid or missing REGISTRY"
[[ "$RUNTIME_SA" =~ ^cohortlens-run-prod@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$ ]] \
  || refuse "RUNTIME_SA must be the cohortlens-run-prod account"
export PROJECT_ID REGION RUNTIME_SA

DEPLOY="${DEPLOY_SCRIPT:-$(dirname "$0")/deploy.sh}"
TARGET=(--region="$REGION" --project="$PROJECT_ID")
ESCAPED_REGISTRY="${REGISTRY//./\\.}"

PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON" ] || die "python is required to read the traffic of the dev services"

# serving_revision SERVICE: the ONE revision that holds all the traffic of a dev service right now. The service's
# newest template is not good enough: after a dev deploy whose smoke test failed, the newest revision is an untested
# candidate with no traffic while the old one still serves. Anything but exactly one revision at 100% is refused.
serving_revision() {
  local service="$1" json
  json="$(gcloud run services describe "$service" "${TARGET[@]}" --format=json)" || die "could not read ${service}"
  printf '%s' "$json" | "$PYTHON" -c '
import json, sys
try:
    live = [t for t in json.load(sys.stdin)["status"]["traffic"] if t.get("percent", 0) > 0]
    assert len(live) == 1 and live[0]["percent"] == 100 and live[0]["revisionName"]
    print(live[0]["revisionName"])
except Exception:
    sys.exit(1)
' || die "${service} does not have exactly one revision serving all its traffic; nothing was promoted"
}

# dev_image SERVICE KIND: the image of the revision serving that dev service, which must be KIND's image, pinned by
# digest, in our registry
dev_image() {
  local service="$1" kind="$2" revision image
  revision="$(serving_revision "$service")" || exit 1
  image="$(gcloud run revisions describe "$revision" "${TARGET[@]}" --format='value(spec.containers[0].image)')"     || die "could not read the image of ${revision}"
  [[ "$image" =~ ^${ESCAPED_REGISTRY}/${kind}@sha256:[0-9a-f]{64}$ ]]     || die "${revision} (serving ${service}) is not a digest-pinned ${kind} image of ${REGISTRY} (found: '${image}'); nothing was promoted"
  echo "$image"
}

API_IMAGE="$(dev_image cohortlens-api-dev api)"
WEB_IMAGE="$(dev_image cohortlens-web-dev web)"

DEV_URL="$(gcloud run services describe cohortlens-api-dev "${TARGET[@]}" --format='value(status.url)')" \
  || die "could not read the URL of the dev API"
[[ "$DEV_URL" =~ ^https://[A-Za-z0-9.-]+$ ]] || die "the dev API has no usable URL"
HEALTH="$(curl -fsS --max-time 30 "${DEV_URL}/healthz")" || die "the dev API did not answer /healthz"
VERSION="$(printf '%s' "$HEALTH" | sed -n 's/.*"version" *: *"\([^"]*\)".*/\1/p')"
[[ "$VERSION" =~ ^[0-9a-f]{7,40}$ ]] || die "the dev API does not report a commit version (reported: '${VERSION:-none}'); nothing was promoted"
if [ -n "$EXPECTED" ] && [ "$VERSION" != "$EXPECTED" ]; then
  die "dev is serving ${VERSION}, but the approval was for ${EXPECTED}; nothing was promoted (start a new promotion for the version you want)"
fi

echo "Promoting to prod: version ${VERSION}"
echo "  api ${API_IMAGE}"
echo "  web ${WEB_IMAGE}"
bash "$DEPLOY" --env prod --version "$VERSION" --api-image "$API_IMAGE" --web-image "$WEB_IMAGE"
