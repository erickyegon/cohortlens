#!/usr/bin/env bash
# Chaos scenario S1 on dev (issue #27): a bad release is promoted, the alert fires, the on-call agent rolls back, and the
# alert clears. Dev only. See docs/oncall.md for the one-time setup (secrets, Grafana contact point) and the evidence.
#
#   bash chaos/s1.sh [--dry-run] [--minutes <how long to wait for the agent, default 25>]
#
# Settings (environment variables): PROJECT_ID, REGION (the same as the deploy scripts). Needs gcloud logged in with
# access to the dev services, gh, and python. It touches only cohortlens-api-dev.
#
# What it does:
#   1. warm-up: runs the reference cohort 60 times against the healthy serving revision, so that revision has the 50
#      successful runs the rollback guardrail requires, then waits for the metrics to arrive;
#   2. injects the fault: a new api revision from the SAME image with CHAOS_FAULT=fail_runs (every run fails; the API
#      honours this only in dev) and a synthetic version label (deadbee + the rest of the real sha) so that Grafana tells
#      the two revisions apart. It becomes the revision serving 100% of traffic, like a bad release;
#   3. keeps sending runs (about 5 a minute) and watches which revision serves: when the agent rolls back, the serving
#      revision changes; it then keeps sending runs until the runs succeed again and reports the incident issue;
#   4. if the agent has NOT acted when the time is up, it restores the healthy revision itself and says so (exit 1):
#      the drill never leaves dev broken.
set -euo pipefail
export LC_ALL=C

DRY=0 MINUTES=25
while (( $# )); do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --minutes)
      (( $# >= 2 )) || { echo "error: --minutes needs a value" >&2; exit 2; }
      MINUTES="$2"; shift 2 ;;
    -h | --help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: '$1'" >&2; exit 2 ;;
  esac
done
refuse() { echo "error: $*" >&2; exit 2; }
[[ "$MINUTES" =~ ^[0-9]+$ ]] && (( MINUTES >= 5 && MINUTES <= 60 )) || refuse "--minutes must be 5 to 60"
PROJECT_ID="${PROJECT_ID-}"; REGION="${REGION-}"
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || refuse "invalid or missing PROJECT_ID"
[[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] || refuse "invalid or missing REGION"

SERVICE="cohortlens-api-dev"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOADGEN="$HERE/../loadgen/runs.py"

if (( DRY )); then
  cat <<EOF
dry run: nothing is changed. It would, on ${SERVICE} in ${PROJECT_ID}/${REGION}:
  1. read which revision serves 100% of traffic and its version (the healthy revision);
  2. warm-up: run the reference cohort 60 times against it, then wait 120 s for the metrics;
  3. deploy a new revision of the same image with CHAOS_FAULT=fail_runs and a synthetic version label, serving all traffic;
  4. send about 5 runs a minute and, for up to ${MINUTES} minutes, watch for the on-call agent to move the traffic;
  5. report the revision now serving, whether runs succeed again, and the incident issue;
  6. if the agent did not act in time, restore the healthy revision and exit 1.
EOF
  exit 0
fi

PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON" ] && "$PYTHON" -c 'import sys' > /dev/null 2>&1 || refuse "a working python is required"
command -v gcloud > /dev/null || refuse "gcloud is required"
command -v gh > /dev/null || refuse "gh is required"
G=(--region "$REGION" --project "$PROJECT_ID")

serving() {  # the single revision serving all traffic, or nothing
  gcloud run services describe "$SERVICE" "${G[@]}" --format=json | "$PYTHON" -c '
import json, sys
traffic = [t for t in json.load(sys.stdin)["status"].get("traffic", []) if t.get("percent")]
print(traffic[0]["revisionName"] if len(traffic) == 1 and traffic[0]["percent"] == 100 else "")' | tr -d '\r'
}
GOOD="$(serving)"
[[ "$GOOD" =~ ^cohortlens-api-dev-[0-9]{5}-[a-z0-9]{3}$ ]] || refuse "${SERVICE} must have exactly one revision serving 100% of traffic (now: '${GOOD}')"
GOOD_VERSION="$(gcloud run revisions describe "$GOOD" "${G[@]}" --format=json | "$PYTHON" -c '
import json, sys
env = json.load(sys.stdin)["spec"]["containers"][0].get("env", [])
print(next((e.get("value", "") for e in env if e["name"] == "SERVICE_VERSION"), ""))' | tr -d '\r')"
[[ "$GOOD_VERSION" =~ ^[0-9a-f]{40}$ ]] || refuse "the serving revision has no 40-character SERVICE_VERSION (${GOOD_VERSION})"
BAD_VERSION="deadbee${GOOD_VERSION:7}"
API_URL="$(gcloud run services describe "$SERVICE" "${G[@]}" --format='value(status.url)' | tr -d '\r')"
echo "S1 on ${SERVICE}: healthy revision ${GOOD} (version ${GOOD_VERSION:0:7}), api ${API_URL}"

restore() {
  echo "restoring ${GOOD}"
  gcloud run services update-traffic "$SERVICE" "${G[@]}" --to-revisions="${GOOD}=100" > /dev/null
}

echo "== 1. warm-up: 60 successful runs on the healthy revision"
"$PYTHON" "$LOADGEN" --url "$API_URL" --count 60 --interval 2 | tail -2
echo "waiting 120 s for the metrics to reach Grafana"
sleep 120

echo "== 2. inject the fault: same image, CHAOS_FAULT=fail_runs, version ${BAD_VERSION:0:7}"
SUFFIX="s1-$(date -u +%H%M%S)"
gcloud run services update "$SERVICE" "${G[@]}" --revision-suffix "$SUFFIX" \
  --update-env-vars "CHAOS_FAULT=fail_runs,SERVICE_VERSION=${BAD_VERSION}" > /dev/null
BAD="$(serving)"
[ -n "$BAD" ] && [ "$BAD" != "$GOOD" ] || { echo "error: the bad revision is not serving; restoring"; restore; exit 1; }
echo "bad revision serving: ${BAD}"

echo "== 3. traffic for up to ${MINUTES} minutes; waiting for the agent to move it"
COUNT=$(( MINUTES * 5 ))
"$PYTHON" "$LOADGEN" --url "$API_URL" --count "$COUNT" --interval 12 > "${TMPDIR:-/tmp}/s1-traffic.log" 2>&1 &
TRAFFIC=$!
trap 'kill $TRAFFIC 2> /dev/null || true' EXIT
DEADLINE=$(( $(date +%s) + MINUTES * 60 )) NOW_SERVING="$BAD"
while (( $(date +%s) < DEADLINE )); do
  NOW_SERVING="$(serving)"
  if [ -n "$NOW_SERVING" ] && [ "$NOW_SERVING" != "$BAD" ]; then break; fi
  sleep 30
done

if [ "$NOW_SERVING" = "$BAD" ] || [ -z "$NOW_SERVING" ]; then
  echo "RESULT: FAILED. The agent did not move the traffic within ${MINUTES} minutes."
  kill $TRAFFIC 2> /dev/null || true
  restore
  exit 1
fi
echo "agent moved the traffic: now serving ${NOW_SERVING} (was ${BAD})"
[ "$NOW_SERVING" = "$GOOD" ] || echo "note: that is not the healthy revision I started from (${GOOD})"

echo "== 4. recovery: runs should succeed again; the incident issue should close when the alert resolves"
sleep 90
RECENT="$(tail -5 "${TMPDIR:-/tmp}/s1-traffic.log" | grep -c 'HTTP 201' || true)"
echo "last five runs: ${RECENT} succeeded (HTTP 201)"
gh issue list --label incident --state all --limit 3 --json number,title,state,labels \
  --jq '.[] | "incident #\(.number) [\(.state)] \(.title) labels: \([.labels[].name] | join(","))"'
kill $TRAFFIC 2> /dev/null || true
if (( RECENT >= 4 )); then echo "RESULT: OK. Check Grafana for the alert returning to Normal and the incident closing."; else
  echo "RESULT: the agent rolled back but runs are not succeeding yet; look at ${NOW_SERVING}"; exit 1; fi
