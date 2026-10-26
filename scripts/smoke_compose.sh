#!/usr/bin/env bash
# Smoke test for the Compose stack: dependency order, seed, roles, UI proxy, persistence across restart.
# Usage: bash scripts/smoke_compose.sh   (leaves the stack stopped, keeps the volume unless --clean)
set -euo pipefail
cd "$(dirname "$0")/.."
fail() { echo "FAIL: $*" >&2; exit 1; }
psql_db() { docker compose exec -T db psql -U postgres -d cohortlens -tA "$@"; }

[ "${1:-}" = "--clean" ] && docker compose down -v >/dev/null 2>&1 || true
docker compose up -d --build --wait --wait-timeout 300 db api web lgtm

echo "== services"; docker compose ps --format '{{.Service}} {{.State}} {{.Health}}'
[ "$(docker compose ps -a --format '{{.State}} {{.ExitCode}}' migrate)" = "exited 0" ] || fail "migrate did not exit 0"

echo "== seed"
[ "$(psql_db -c 'select count(*) from cdm.person')" = "5724" ] || fail "cdm.person != 5724"

echo "== roles"
docker compose exec -T db psql -U cohortlens_reader -d cohortlens -tA -c 'select count(*) from cdm.person' >/dev/null || fail "reader cannot read cdm"
if docker compose exec -T db psql -U cohortlens_reader -d cohortlens -c 'select 1 from app.cohort_definition' >/dev/null 2>&1; then fail "reader can read app schema"; fi
if docker compose exec -T db psql -U cohortlens_reader -d cohortlens -c 'create table cdm.x(i int)' >/dev/null 2>&1; then fail "reader can write cdm"; fi
if docker compose exec -T db psql -U cohortlens_app -d cohortlens -c 'select 1 from cdm.person limit 1' >/dev/null 2>&1; then fail "app role can read cdm"; fi
docker compose exec -T db psql -U cohortlens_app -d cohortlens -c "insert into app.cohort_definition(name, definition) values ('smoke', '{}')" >/dev/null || fail "app cannot write app schema"

echo "== api + proxy"
curl -fsS http://127.0.0.1:8000/healthz | grep -q '"ok"' || fail "healthz"
curl -fsS -D - -o /dev/null http://127.0.0.1:8000/healthz | grep -qi '^x-trace-id:' || fail "X-Trace-Id header missing"
curl -fsS http://127.0.0.1:8080/api/readyz | grep -q '"cdm_persons":5724' || fail "readyz through nginx proxy"
curl -fsS http://127.0.0.1:8080/ | grep -qi '<title>CohortLens' || fail "web index"

echo "== restart keeps data"
docker compose restart db >/dev/null
docker compose up -d --wait --wait-timeout 120 db api >/dev/null
[ "$(psql_db -c "select count(*) from app.cohort_definition where name='smoke'")" = "1" ] || fail "saved cohort lost after restart"
[ "$(psql_db -c 'select count(*) from cdm.person')" = "5724" ] || fail "seed missing after restart"

psql_db -c "delete from app.cohort_definition where name='smoke'" >/dev/null
echo "SMOKE OK"
