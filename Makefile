.PHONY: up test e2e lint traffic chaos seed-cloud verify-seed smoke

up:
	docker compose up --build

test:
	python -m pytest api/tests -q -rs
	cd web && npm test

e2e:
	@echo "TODO: Playwright (stretch)" && exit 1

lint:
	ruff check .
	cd web && npm run lint && npx tsc --noEmit

traffic:
	k6 run loadgen/journey.js

chaos:
	@echo "TODO: chaos/$(SCENARIO) (phase 6)" && exit 1

verify-seed:
	bash data/etl/verify_seed.sh

# Neon dev branch: roles, schema, seed, and the two database URLs into Secret Manager (see scripts/seed_cloud.sh)
# usage: NEON_DEV_URL='postgresql://...' make seed-cloud ARGS="--confirm-endpoint ep-xxxx"
seed-cloud:
	bash scripts/seed_cloud.sh --env dev $(ARGS)

smoke:
	bash scripts/smoke_compose.sh
