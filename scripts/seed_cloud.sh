#!/usr/bin/env bash
# Prepare a Neon branch for CohortLens: the two application roles, the app schema, the OMOP seed and the grants, and put
# the two database URLs into Secret Manager (docs/deployment.md, issue #17). Run by hand, once per environment.
#
#   export NEON_DEV_URL='postgresql://neondb_owner:...@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require'
#   make seed-cloud ARGS="--confirm-endpoint ep-xxxx"        (or: bash scripts/seed_cloud.sh --env dev --confirm-endpoint ep-xxxx)
#
# NEON_DEV_URL is the DIRECT (not pooled) connection string of the owner role on the `dev` branch. It is read from the
# environment only; it is never printed and never put on a command line (psql gets it through libpq variables).
# --confirm-endpoint must be the endpoint id (ep-...) of the branch you mean, so a production URL pasted by mistake is
# refused. Settings: PROJECT_ID (default cohortlens). Needs gcloud signed in, and psql or Docker.
#
# What it does, in this order, and only this:
#   1. creates the roles cohortlens_reader and cohortlens_app with generated passwords and the schema app
#      (db/cloud/00-roles.sql), then writes the two database URLs to the Secret Manager slots as new secret versions;
#   2. loads data/seed/omop_subset.sql.gz in ONE transaction, and only if the database has no persons yet;
#   3. applies db/init/20-grants.sql (the same grants as the local stack; idempotent) and checks the person count.
# Safe to run twice: existing roles keep their passwords and secrets, existing data is never reloaded.
# --rotate-passwords sets new passwords on the existing roles and writes new secret versions. Nothing is ever dropped,
# truncated or deleted. Prod is a separate, deliberate step and is not supported here.
set -euo pipefail
export LC_ALL=C
cd "$(dirname "$0")/.."

ENVIRONMENT="" CONFIRM="" ROTATE=0
while (( $# )); do
  case "$1" in
    --env | --confirm-endpoint)
      (( $# >= 2 )) || { echo "error: $1 needs a value" >&2; exit 2; }
      if [ "$1" = "--env" ]; then ENVIRONMENT="$2"; else CONFIRM="$2"; fi
      shift 2 ;;
    --rotate-passwords) ROTATE=1; shift ;;
    -h | --help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown argument: '$1'" >&2; exit 2 ;;
  esac
done

refuse() { echo "error: $*" >&2; exit 2; }
die() { echo "error: $*" >&2; exit 1; }

[ "$ENVIRONMENT" = "dev" ] || refuse "--env must be 'dev' (prod is a separate, deliberate step)"
[[ "$CONFIRM" =~ ^ep-[a-z0-9-]+$ ]] || refuse "--confirm-endpoint ep-... is required: the endpoint id of the Neon branch you mean"
PROJECT_ID="${PROJECT_ID-cohortlens}"
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || refuse "invalid PROJECT_ID"

URL="${NEON_DEV_URL-}"
URL_PATTERN='^postgres(ql)?://([^:@/]+):([^@/]+)@([a-z0-9.-]+)(:([0-9]+))?/([A-Za-z0-9_-]+)\?(.*)$'
[[ "$URL" =~ $URL_PATTERN ]] || refuse "NEON_DEV_URL is missing or not a postgresql:// URL with a password, host, database and ?sslmode=require"
OWNER="${BASH_REMATCH[2]}" OWNER_PASSWORD="${BASH_REMATCH[3]}" DB_HOST="${BASH_REMATCH[4]}" DB_PORT="${BASH_REMATCH[6]}"
DB_NAME="${BASH_REMATCH[7]}" QUERY="${BASH_REMATCH[8]}"
URL=""
[[ "&${QUERY}&" == *"&sslmode=require&"* ]] || refuse "NEON_DEV_URL must contain sslmode=require"
[[ "$DB_HOST" != *-pooler* ]] || refuse "NEON_DEV_URL is a pooled connection; use the direct one (untick 'Connection pooling' in the Neon console)"
[ "${DB_HOST%%.*}" = "$CONFIRM" ] || refuse "the endpoint in NEON_DEV_URL (${DB_HOST%%.*}) is not the one you confirmed (${CONFIRM}); nothing was touched"

export PGHOST="$DB_HOST" PGUSER="$OWNER" PGPASSWORD="$OWNER_PASSWORD" PGDATABASE="$DB_NAME" PGSSLMODE=require
[ -z "$DB_PORT" ] || export PGPORT="$DB_PORT"
unset OWNER_PASSWORD

# psql, or the postgres image's psql when it is not installed (libpq variables are passed through by name)
sql() {
  if command -v psql > /dev/null; then
    psql -X "$@"
  else
    docker run --rm -i -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE -e PGSSLMODE postgres:18 psql -X "$@"
  fi
}
query() { sql -tA -v ON_ERROR_STOP=1 -c "$1"; }
secret_name() { echo "cohortlens-${ENVIRONMENT}-database-url-$1"; }
heading() { echo; echo "== $*"; }

echo "CohortLens Neon setup: ${ENVIRONMENT}   endpoint ${CONFIRM}   database ${DB_NAME}   project ${PROJECT_ID}"

heading "state"
roles_existing="$(query "select count(*) from pg_roles where rolname in ('cohortlens_app', 'cohortlens_reader')")"
# the table is looked up first: a query that names cdm.person fails to parse while the schema does not exist yet
if [ "$(query "select to_regclass('cdm.person') is not null")" = "t" ]; then
  persons="$(query "select count(*) from cdm.person")"
else
  persons=0
fi
echo "  application roles present: ${roles_existing} of 2; persons loaded: ${persons}"
[[ "$roles_existing" =~ ^[0-2]$ && "$persons" =~ ^[0-9]+$ ]] || die "could not read the state of the database"
[ "$roles_existing" != "1" ] || die "only one of the two application roles exists; fix that by hand before running this"

# write_secret KIND PASSWORD: a new secret version holding the database URL of that role (value on stdin, never an argument)
write_secret() {
  local kind="$1" password="$2" host="$DB_HOST"
  [ -z "$DB_PORT" ] || host="${host}:${DB_PORT}"
  printf '%s' "postgresql://cohortlens_${kind}:${password}@${host}/${DB_NAME}?sslmode=require" \
    | gcloud secrets versions add "$(secret_name "$kind")" --data-file=- --project="$PROJECT_ID" > /dev/null
}

new_password() { openssl rand -hex 24; }  # hex only: URL-safe, as db/init/00-roles.sh requires

if [ "$roles_existing" = "0" ] || (( ROTATE )); then
  if [ "$roles_existing" = "0" ]; then heading "roles: create, then store the URLs"; else heading "roles: rotate the passwords, then store the URLs"; fi
  app_password="$(new_password)"
  reader_password="$(new_password)"
  [ "$roles_existing" = "0" ] && role_sql=db/cloud/00-roles.sql || role_sql=db/cloud/01-rotate.sql
  { printf "\\\\set app_pw '%s'\n" "$app_password"; printf "\\\\set reader_pw '%s'\n" "$reader_password"; cat "$role_sql"; } \
    | sql --single-transaction -v ON_ERROR_STOP=1 -q -f -
  write_secret app "$app_password"
  write_secret reader "$reader_password"
  unset app_password reader_password
  echo "  stored new versions of $(secret_name app) and $(secret_name reader)"
else
  heading "roles: already there"
  for kind in app reader; do
    [ -n "$(gcloud secrets versions list "$(secret_name "$kind")" --limit=1 --format='value(name)' --project="$PROJECT_ID")" ] \
      || die "the roles already exist but $(secret_name "$kind") has no value; run again with --rotate-passwords to set new passwords and store them"
  done
  echo "  the roles and both secrets already exist; passwords unchanged (use --rotate-passwords to change them)"
fi

if [ "$persons" = "0" ]; then
  heading "seed: load data/seed/omop_subset.sql.gz in one transaction"
  gunzip -c data/seed/omop_subset.sql.gz | sql --single-transaction -v ON_ERROR_STOP=1 -q -f -
else
  heading "seed: the database already has ${persons} persons; not reloaded"
fi

heading "grants"
sql -v ON_ERROR_STOP=1 -q -f - < db/init/20-grants.sql

heading "check"
final="$(query "select count(*) from cdm.person")"
[ "$final" = "5724" ] || die "cdm.person has ${final} rows, expected 5724"
echo "  cdm.person: ${final}"
echo
echo "DONE: ${ENVIRONMENT} is ready. Next: set the GCP_PROJECT_ID repository variable to switch the dev deploy on."
