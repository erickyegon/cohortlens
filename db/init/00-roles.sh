#!/bin/bash
# Runs once on first start of an empty data directory (before the OMOP seed is loaded).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE cohortlens_reader LOGIN PASSWORD '${COHORTLENS_READER_PASSWORD}';
CREATE ROLE cohortlens_app LOGIN PASSWORD '${COHORTLENS_APP_PASSWORD}';
CREATE SCHEMA app AUTHORIZATION cohortlens_app;
SQL
