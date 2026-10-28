#!/usr/bin/env bash
# Load the committed seed into a throwaway Postgres 18 container and run the seed integration test.
set -euo pipefail
cd "$(dirname "$0")/../.."

NAME=cohortlens-seed-verify
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -e POSTGRES_PASSWORD=verify -e POSTGRES_DB=cohortlens -p 127.0.0.1::5432 postgres:18 >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

# The postgres image starts a temporary server (unix socket only) to run its init, stops it, then starts the real
# one. pg_isready over the socket passes during the temporary phase, and loading then races the shutdown. The real
# server is up only once "ready to accept connections" has been logged a second time AND a TCP query works.
for _ in $(seq 1 120); do
  ready=$(docker logs "$NAME" 2>&1 | grep -c "database system is ready to accept connections" || true)
  if [ "$ready" -ge 2 ] && docker exec "$NAME" psql -h 127.0.0.1 -U postgres -d cohortlens -tAc "select 1" >/dev/null 2>&1; then break; fi
  sleep 1
done
[ "${ready:-0}" -ge 2 ] || { echo "postgres did not become ready in 120 s" >&2; docker logs "$NAME" 2>&1 | tail -20 >&2; exit 1; }
echo "loading seed..."
gunzip -c data/seed/omop_subset.sql.gz | docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d cohortlens >/dev/null
docker exec "$NAME" psql -U postgres -d cohortlens -c "select pg_size_pretty(pg_database_size('cohortlens')) as db_size"
docker exec -i "$NAME" psql -U postgres -d cohortlens < data/etl/example_cohort.sql

PORT=$(docker port "$NAME" 5432/tcp | head -1 | sed "s/.*://")
export COHORTLENS_TEST_DSN="postgresql://postgres:verify@127.0.0.1:$PORT/cohortlens"
python -m pytest api/tests/integration/test_seed_example_cohort.py api/tests/integration/test_seed_engine_performance.py -q
