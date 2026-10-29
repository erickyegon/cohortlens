.PHONY: up test e2e lint traffic chaos seed-cloud verify-seed smoke

up:
	docker compose up --build

test:
	python -m pytest api/tests oncall/tests -q -rs
	cd web && npm test

e2e:
	@echo "TODO: Playwright (stretch)" && exit 1

lint:
	ruff check .
	cd web && npm run lint && npx tsc --noEmit

traffic:
	k6 run loadgen/journey.js

# usage: PROJECT_ID=cohortlens REGION=us-east5 make chaos SCENARIO=s1   (dev only; see docs/oncall.md)
chaos:
	@test "$(SCENARIO)" = "s1" || { echo "only SCENARIO=s1 exists"; exit 2; }
	bash chaos/s1.sh $(ARGS)

verify-seed:
	bash data/etl/verify_seed.sh

# Neon dev branch: roles, schema, seed, and the two database URLs into Secret Manager (see scripts/seed_cloud.sh)
# usage: NEON_DEV_URL='postgresql://...' make seed-cloud ARGS="--confirm-endpoint ep-xxxx"   (dev only)
# prod has extra guards and is run directly: bash scripts/seed_cloud.sh --env prod --confirm-endpoint ep-xxxx --yes-this-is-production
seed-cloud:
	bash scripts/seed_cloud.sh --env dev $(ARGS)

smoke:
	bash scripts/smoke_compose.sh
