#!/usr/bin/env bash
# Roll one service back by shifting its traffic to an earlier revision (docs/deployment.md, issue #19). Run by
# rollback.yml; usable by hand.
#
#   bash infra/gcp/rollback.sh --env dev|prod --service api|web [--revision <name>] [--incident <issue number>] [--dry-run]
#
# Settings (environment variables, validated before anything runs): PROJECT_ID, REGION. For --incident also
# GITHUB_REPOSITORY (owner/name), and optionally GITHUB_ACTOR and GITHUB_RUN_URL (shown in the comment). SMOKE_SCRIPT
# overrides the smoke test (tests only).
#
# It rebuilds and redeploys NOTHING: Cloud Run revisions are immutable, so a rollback is moving 100% of the service's
# traffic to a revision that already exists. Without --revision the target is the revision BEFORE the one serving now
# (the next older one, so a newer candidate that never served is never chosen). A given --revision must be one of this
# service's own revisions and must not be the one already serving. The service must have exactly one revision serving
# all its traffic. Afterwards it runs the smoke test on the service URL (the web service through its /api proxy) and, with
# --incident, comments on that GitHub issue with what moved and whether the smoke test passed. A failed smoke test fails
# the run but does NOT move the traffic back: that is a decision for a person. --dry-run prints the plan and changes
# nothing. It deletes nothing and touches no IAM.
set -euo pipefail
export LC_ALL=C

ENVIRONMENT="" SERVICE="" REVISION="" INCIDENT="" DRY=0 REVISION_GIVEN=0
while (( $# )); do
  case "$1" in
    --env | --service | --revision | --incident)
      (( $# >= 2 )) || { echo "error: $1 needs a value" >&2; exit 2; }
      case "$1" in
        --env) ENVIRONMENT="$2" ;;
        --service) SERVICE="$2" ;;
        --revision) REVISION="$2"; REVISION_GIVEN=1 ;;
        --incident) INCIDENT="$2" ;;
      esac
      shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h | --help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown argument: '$1'" >&2; exit 2 ;;
  esac
done

refuse() { echo "error: $*" >&2; exit 2; }
die() { echo "error: $*" >&2; exit 1; }

[[ "$ENVIRONMENT" == "dev" || "$ENVIRONMENT" == "prod" ]] || refuse "--env must be 'dev' or 'prod'"
[[ "$SERVICE" == "api" || "$SERVICE" == "web" ]] || refuse "--service must be 'api' or 'web'"
NAME="cohortlens-${SERVICE}-${ENVIRONMENT}"
if (( REVISION_GIVEN )); then
  [[ "$REVISION" =~ ^${NAME}-[0-9]{5}-[a-z0-9]{3}$ ]] || refuse "--revision must be a revision of ${NAME} (${NAME}-00000-abc)"
fi
[[ -z "$INCIDENT" || "$INCIDENT" =~ ^[1-9][0-9]{0,8}$ ]] || refuse "--incident must be a GitHub issue number"
PROJECT_ID="${PROJECT_ID-}"; REGION="${REGION-}"
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || refuse "invalid or missing PROJECT_ID"
[[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] || refuse "invalid or missing REGION"
REPO="${GITHUB_REPOSITORY-}"
if [ -n "$INCIDENT" ]; then
  [[ "$REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || refuse "--incident needs GITHUB_REPOSITORY (owner/name)"
fi
ACTOR="${GITHUB_ACTOR-unknown}"; RUN_URL="${GITHUB_RUN_URL-}"
PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON" ] || die "python is required to read the traffic of the service"

SMOKE="${SMOKE_SCRIPT:-$(dirname "$0")/../../scripts/smoke_cloud.sh}"
TARGET=(--region="$REGION" --project="$PROJECT_ID")
FROM="" TO="" SMOKE_RESULT="not run"

# comment_on_incident OUTCOME: tell the incident issue what happened (the body goes in on stdin, never on a command line)
comment_on_incident() {
  [ -n "$INCIDENT" ] || return 0
  local outcome="$1"
  {
    echo "**Rollback of \`${NAME}\`** (${ENVIRONMENT}), started by @${ACTOR}${RUN_URL:+ ([run](${RUN_URL}))}."
    echo
    echo "- from revision: \`${FROM:-unknown}\`"
    echo "- to revision: \`${TO:-unknown}\`"
    echo "- result: ${outcome}"
  } | gh issue comment "$INCIDENT" --repo="$REPO" --body-file=- > /dev/null \
    || echo "warning: could not comment on issue #${INCIDENT}; the rollback itself is not affected" >&2
}

serving="$(gcloud run services describe "$NAME" "${TARGET[@]}" --format=json)" || die "could not read ${NAME}"
FROM="$(printf '%s' "$serving" | "$PYTHON" -c '
import json, sys
try:
    live = [t for t in json.load(sys.stdin)["status"]["traffic"] if t.get("percent", 0) > 0]
    assert len(live) == 1 and live[0]["percent"] == 100 and live[0]["revisionName"]
    print(live[0]["revisionName"])
except Exception:
    sys.exit(1)
')" || die "${NAME} does not have exactly one revision serving all its traffic; nothing was changed"

# the revisions of this service, newest first
listing="$(gcloud run revisions list --service="$NAME" "${TARGET[@]}" --format='value(metadata.name)' \
  --sort-by='~metadata.creationTimestamp')" || die "could not list the revisions of ${NAME}"
listing="${listing//$'\r'/}"  # gcloud prints CRLF on Windows
mapfile -t REVISIONS <<< "$listing"

position=-1
for i in "${!REVISIONS[@]}"; do [ "${REVISIONS[$i]}" = "$FROM" ] && position=$i; done
(( position >= 0 )) || die "the serving revision ${FROM} is not in the revision list of ${NAME}; nothing was changed"

if (( REVISION_GIVEN )); then
  [ "$REVISION" != "$FROM" ] || die "${REVISION} is already the revision serving ${NAME}; nothing to roll back"
  found=0
  for revision in "${REVISIONS[@]}"; do [ "$revision" = "$REVISION" ] && found=1; done
  (( found )) || die "${REVISION} is not a revision of ${NAME}; nothing was changed"
  TO="$REVISION"
else
  (( position + 1 < ${#REVISIONS[@]} )) || die "no previous revision to roll back to: ${FROM} is the oldest revision of ${NAME}"
  TO="${REVISIONS[$((position + 1))]}"
fi

echo "Rollback ${NAME} (${ENVIRONMENT}): ${FROM} -> ${TO}"
if (( DRY )); then
  echo "dry run: the traffic was NOT moved, nothing was changed"
  exit 0
fi

if ! gcloud run services update-traffic "$NAME" --to-revisions="${TO}=100" "${TARGET[@]}"; then
  comment_on_incident "rollback FAILED: traffic was not moved (see the run for the error)"
  die "could not move the traffic of ${NAME} to ${TO}"
fi
echo "traffic of ${NAME}: 100% on ${TO}"

URL="$(gcloud run services describe "$NAME" "${TARGET[@]}" --format='value(status.url)')" \
  || { comment_on_incident "traffic moved, but the service URL could not be read, so the smoke test did NOT run"; die "could not read the URL of ${NAME}"; }
URL="${URL//$'\r'/}"
SMOKE_ARGS=()
[ "$SERVICE" = "web" ] && SMOKE_ARGS=(--api-prefix /api)
if bash "$SMOKE" "$URL" ${SMOKE_ARGS[@]+"${SMOKE_ARGS[@]}"}; then
  SMOKE_RESULT="smoke test passed"
  comment_on_incident "traffic is on ${TO}; ${SMOKE_RESULT}"
else
  SMOKE_RESULT="smoke test FAILED"
  comment_on_incident "traffic is on ${TO}, but the ${SMOKE_RESULT}; the traffic was NOT moved back, a person has to decide"
  die "the rollback moved the traffic to ${TO} but the smoke test failed; the traffic was NOT moved back"
fi
echo "ROLLBACK OK: ${NAME} serves ${TO}"
