#!/usr/bin/env bash
# Smoke test of one deployed URL: it is alive, it is ready (database reachable and seeded), and it can run one real
# cohort (docs/deployment.md, issue #17).
#
#   bash scripts/smoke_cloud.sh <https://base-url> [--api-prefix /api]
#
# --api-prefix is for the web service, which proxies the API under /api. The cohort it creates is always deleted again.
# The reference cohort (new-user lisinopril, docs/cohort-engine.md) must come back with its documented final count of
# 481 from the committed seed. Sends no credentials.
set -euo pipefail

BASE="" PREFIX=""
while (( $# )); do
  case "$1" in
    --api-prefix) PREFIX="${2-}"; shift 2 || true ;;
    -h | --help) sed -n '2,9p' "$0"; exit 0 ;;
    -*) echo "unknown argument: '$1'" >&2; exit 2 ;;
    *) [ -z "$BASE" ] || { echo "unexpected argument: '$1'" >&2; exit 2; }; BASE="$1"; shift ;;
  esac
done
[[ "$BASE" =~ ^https://[A-Za-z0-9.-]+$ ]] || { echo "usage: smoke_cloud.sh https://host [--api-prefix /api]" >&2; exit 2; }
[[ -z "$PREFIX" || "$PREFIX" =~ ^/[A-Za-z0-9/_-]*$ ]] || { echo "invalid --api-prefix" >&2; exit 2; }

PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON" ] || { echo "python is required to read the JSON answers" >&2; exit 2; }
COHORT_FILE="$(dirname "$0")/../api/tests/fixtures/reference_cohort.json"
EXPECTED_FINAL=481
CURL_COMMON=(-sS --max-time 30 -w '\n%{http_code}')
CREATED=""
fail() { echo "FAIL: $*" >&2; exit 1; }

# request METHOD PATH [DATA_FILE] -> sets STATUS and BODY
request() {
  local method="$1" path="$2" data="${3-}" output
  if [ -n "$data" ]; then
    output="$(curl "${CURL_COMMON[@]}" -X "$method" -H 'Content-Type: application/json' --data-binary "@${data}" "${BASE}${PREFIX}${path}")"
  else
    output="$(curl "${CURL_COMMON[@]}" -X "$method" "${BASE}${PREFIX}${path}")"
  fi
  STATUS="${output##*$'\n'}"
  BODY="${output%$'\n'*}"
}
json_field() { "$PYTHON" -c 'import json,sys
try:
    value = json.loads(sys.stdin.read() or "null")
    for key in sys.argv[1].split("."):
        value = value[key]
    print(value)
except Exception:
    print("")' "$1"; }

cleanup() {
  if [ -n "$CREATED" ]; then
    request DELETE "/cohorts/${CREATED}" || true
    if [ "${STATUS:-}" = "204" ]; then echo "cleanup: deleted the smoke cohort"; else echo "warning: could not delete the smoke cohort ${CREATED} (status ${STATUS:-none})" >&2; fi
  fi
}
trap cleanup EXIT

echo "smoke: ${BASE}${PREFIX}"
request GET /healthz
[ "$STATUS" = "200" ] || fail "/healthz answered ${STATUS}"
request GET /readyz
[ "$STATUS" = "200" ] || fail "/readyz answered ${STATUS}: ${BODY}"
[ "$(echo "$BODY" | json_field status)" = "ready" ] || fail "/readyz is not ready: ${BODY}"
persons="$(echo "$BODY" | json_field cdm_persons)"
[[ "$persons" =~ ^[0-9]+$ ]] && (( persons > 0 )) || fail "the database has no persons (cdm_persons=${persons:-none})"
echo "ready: ${persons} persons"

request POST /cohorts "$COHORT_FILE"
[ "$STATUS" = "201" ] || fail "creating the reference cohort answered ${STATUS}: ${BODY}"
CREATED="$(echo "$BODY" | json_field id)"
[ -n "$CREATED" ] || fail "no cohort id in the answer"
request POST "/cohorts/${CREATED}/runs"
[ "$STATUS" = "201" ] || fail "running the reference cohort answered ${STATUS}: ${BODY}"
[ "$(echo "$BODY" | json_field status)" = "success" ] || fail "the run did not succeed: ${BODY}"
final="$(echo "$BODY" | json_field final_count)"
[ "$final" = "$EXPECTED_FINAL" ] || fail "reference cohort final count ${final:-none}, expected ${EXPECTED_FINAL}"
echo "reference cohort: final count ${final}"
echo "SMOKE OK"
