#!/bin/bash
# Runs once on first start of an empty data directory (before the OMOP seed is loaded).
# Passwords are passed as psql variables and quoted by psql (:'var'), never interpolated into the SQL text.
# They also appear in DATABASE_URL_* without percent-encoding, so use URL-safe characters (letters, digits, - _ . ~).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v reader_pw="$COHORTLENS_READER_PASSWORD" -v app_pw="$COHORTLENS_APP_PASSWORD" <<'SQL'
CREATE ROLE cohortlens_reader LOGIN PASSWORD :'reader_pw';
CREATE ROLE cohortlens_app LOGIN PASSWORD :'app_pw';
CREATE SCHEMA app AUTHORIZATION cohortlens_app;
SQL
