#!/usr/bin/env bash
# Cancels "Promote to prod" runs that are still WAITING for approval and are older than this run, so a stale promotion
# cannot sit approvable for hours (audit section 13). Called by the `supersede` job of .github/workflows/promote-prod.yml.
#
#   GITHUB_REPOSITORY=owner/name GITHUB_RUN_ID=<this run> GH_TOKEN=... bash infra/gcp/cancel_stale_promotions.sh [--dry-run]
#
# - Only runs whose status is `waiting` (paused at the `production` approval) are touched. A run that is deploying
#   (in progress) is not cancelled: only runs seen waiting at that moment are. (A run approved between the listing and the
#   cancel could still be cut off; the deploy is candidate-first, so prod keeps serving the previous revision.)
# - Only runs with a LOWER id than this one are cancelled, so two runs started together cannot cancel each other.
# - It never fails the promotion that called it: a failure to list or to cancel is a warning.
# The token needs `actions: write`. It builds nothing, deploys nothing and has no cloud credentials.
set -euo pipefail
export LC_ALL=C

DRY=0
while (( $# )); do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: '$1'" >&2; exit 2 ;;
  esac
done
refuse() { echo "error: $*" >&2; exit 2; }

REPO="${GITHUB_REPOSITORY-}"; CURRENT="${GITHUB_RUN_ID-}"
[[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || refuse "GITHUB_REPOSITORY must look like owner/name"
[[ "$CURRENT" =~ ^[0-9]+$ ]] || refuse "GITHUB_RUN_ID must be a number"

if ! LISTING="$(gh api "repos/${REPO}/actions/workflows/promote-prod.yml/runs?status=waiting&per_page=100" \
    --jq '.workflow_runs[] | [.id, .status, .head_sha] | @tsv')"; then
  echo "warning: could not list the waiting promotion runs; nothing was cancelled" >&2
  exit 0
fi

CANCELLED=() FAILED=0
while IFS=$'\t' read -r id status sha; do
  id="${id%$'\r'}"
  [ -n "$id" ] || continue
  [[ "$id" =~ ^[0-9]+$ ]] || continue            # never pass anything but a number to gh
  [ "${status%$'\r'}" = "waiting" ] || continue   # only a run paused at the approval
  (( 10#$id < 10#$CURRENT )) || continue                  # only older runs than this one
  if (( DRY )); then
    echo "dry run: would cancel the waiting promotion run ${id} (commit ${sha:0:7})"
    CANCELLED+=("$id")
    continue
  fi
  if gh api -X POST "repos/${REPO}/actions/runs/${id}/cancel" > /dev/null; then
    echo "cancelled the stale promotion run ${id} (commit ${sha:0:7}), which was waiting for approval"
    CANCELLED+=("$id")
  else
    echo "warning: could not cancel the promotion run ${id}; it stays waiting" >&2
    FAILED=$(( FAILED + 1 ))
  fi
done <<< "$LISTING"

if (( ${#CANCELLED[@]} == 0 && FAILED == 0 )); then
  echo "no stale promotion runs were waiting"
fi
exit 0
