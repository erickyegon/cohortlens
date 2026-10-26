#!/usr/bin/env bash
# Load the committed seed into a throwaway Postgres 16 container and run the seed integration test.
set -euo pipefail
cd "$(dirname "$0")/../.."

NAME=cohortlens-seed-verify
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -e POSTGRES_PASSWORD=verify -e POSTGRES_DB=cohortlens -p 127.0.0.1::5432 postgres:16 >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

until docker exec "$NAME" pg_isready -U postgres -d cohortlens >/dev/null 2>&1; do sleep 1; done
sleep 2
echo "loading seed..."
gunzip -c data/seed/omop_subset.sql.gz | docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d cohortlens >/dev/null
docker exec "$NAME" psql -U postgres -d cohortlens -c "select pg_size_pretty(pg_database_size('cohortlens')) as db_size"
docker exec -i "$NAME" psql -U postgres -d cohortlens < data/etl/example_cohort.sql

PORT=$(docker port "$NAME" 5432/tcp | head -1 | sed "s/.*://")
export COHORTLENS_TEST_DSN="postgresql://postgres:verify@127.0.0.1:$PORT/cohortlens"
python -m pytest api/tests/integration/test_seed_example_cohort.py -q
