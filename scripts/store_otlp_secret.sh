#!/usr/bin/env bash
# Store the Grafana Cloud OTLP credential in Secret Manager (docs/deployment.md, issue #22). Run by hand, once per
# environment (and again to rotate the token).
#
#   bash scripts/store_otlp_secret.sh --env dev|prod --instance-id <Grafana Cloud instance id>
#
# Run it in a terminal window (Git Bash). It refuses to run with tracing on and it refuses piped input unless you pass
# --stdin, which reads one line from a pipe and is for automation only: `echo <token> | script` puts the token in the
# shell history.
#
# It asks for the API token with HIDDEN input (the token is never an argument, so it cannot end up in the shell history,
# the process list, this chat or the screen), builds the header the OpenTelemetry exporter reads from
# OTEL_EXPORTER_OTLP_HEADERS, `Authorization=Basic%20<base64 of instance-id:token>` (URL-encoded, as the OpenTelemetry
# specification requires), and stores it as a NEW VERSION of the secret cohortlens-<env>-otlp-headers: the secret is
# created if it does not exist, and that environment's runtime account (cohortlens-run-<env>) is allowed to read it.
# Settings: PROJECT_ID (default cohortlens). Needs gcloud signed in as someone who may manage secrets.
# It deletes nothing and prints nothing secret. Never run it with `bash -x`.
set -euo pipefail
export LC_ALL=C

# tracing (bash -x, set -x, or SHELLOPTS=xtrace inherited from the caller) would print the token when it is expanded
case "$-" in
  *x*) echo "error: do not run this script with tracing (bash -x, set -x, SHELLOPTS=xtrace): it would print the token" >&2; exit 2 ;;
esac
# nothing inherited from the caller's environment may carry a value into gcloud
unset TOKEN encoded header 2> /dev/null || true

ENVIRONMENT="" INSTANCE_ID="" STDIN_OK=0
while (( $# )); do
  case "$1" in
    --env | --instance-id)
      (( $# >= 2 )) || { echo "error: $1 needs a value" >&2; exit 2; }
      if [ "$1" = "--env" ]; then ENVIRONMENT="$2"; else INSTANCE_ID="$2"; fi
      shift 2 ;;
    --stdin) STDIN_OK=1; shift ;;
    -h | --help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument: '$1' (the token is asked for at a hidden prompt, never given as an argument)" >&2; exit 2 ;;
  esac
done

refuse() { echo "error: $*" >&2; exit 2; }
die() { echo "error: $*" >&2; exit 1; }

[[ "$ENVIRONMENT" == "dev" || "$ENVIRONMENT" == "prod" ]] || refuse "--env must be 'dev' or 'prod'"
[[ "$INSTANCE_ID" =~ ^[0-9]{3,12}$ ]] || refuse "--instance-id must be the numeric Grafana Cloud instance id"
PROJECT_ID="${PROJECT_ID-cohortlens}"
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || refuse "invalid PROJECT_ID"
SECRET="cohortlens-${ENVIRONMENT}-otlp-headers"
RUNTIME_SA="cohortlens-run-${ENVIRONMENT}@${PROJECT_ID}.iam.gserviceaccount.com"

# the token: hidden input in a terminal, one line from stdin otherwise
TOKEN=""
if [ -t 0 ]; then
  printf 'Grafana Cloud OTLP token for %s (input is hidden, paste it and press Enter): ' "$ENVIRONMENT" >&2
  read -rs TOKEN || true
  echo >&2
elif (( STDIN_OK )); then
  read -r TOKEN || true
else
  refuse "there is no terminal to ask for the token at a hidden prompt. Run this in a Git Bash window (not through a pipe: piping a token puts it in your shell history). --stdin reads one line from a pipe and is meant for automation only"
fi
TOKEN="${TOKEN%$'\r'}"  # a pasted line may end in a carriage return (Windows terminals)
[[ ${#TOKEN} -ge 20 && ! "$TOKEN" =~ [[:space:]] ]] || refuse "that does not look like a token (empty, shorter than 20 characters or containing whitespace); nothing was stored"

encoded="$(printf '%s' "${INSTANCE_ID}:${TOKEN}" | openssl base64 -A | sed 's/+/%2B/g; s#/#%2F#g; s/=/%3D/g')"
TOKEN=""
header="Authorization=Basic%20${encoded}"
encoded=""

# the secret: create it if it is plainly not there; anything ambiguous stops the script
if output="$(gcloud secrets describe "$SECRET" --project="$PROJECT_ID" 2>&1)"; then
  echo "secret ${SECRET} exists; adding a new version" >&2
elif grep -qiE 'permission|denied|forbidden|may not exist|or it may' <<< "$output"; then
  die "could not tell whether ${SECRET} exists or access was denied; nothing was stored"
elif grep -qiE 'not[ _]?found|does not exist' <<< "$output"; then
  gcloud secrets create "$SECRET" --replication-policy=automatic --project="$PROJECT_ID" > /dev/null \
    || die "could not create ${SECRET}"
  echo "created secret ${SECRET}" >&2
else
  die "could not check whether ${SECRET} exists; nothing was stored"
fi

gcloud secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:${RUNTIME_SA}" \
  --role=roles/secretmanager.secretAccessor --project="$PROJECT_ID" > /dev/null \
  || die "could not allow ${RUNTIME_SA} to read ${SECRET}"
printf '%s' "$header" | gcloud secrets versions add "$SECRET" --data-file=- --project="$PROJECT_ID" > /dev/null \
  || die "could not store the new version of ${SECRET}"
header=""

echo "stored a new version of ${SECRET}; ${RUNTIME_SA} may read it. The token was not printed or kept." >&2
