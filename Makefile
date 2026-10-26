.PHONY: up test e2e lint traffic chaos seed-cloud verify-seed smoke

up:
	docker compose up --build

test:
	python -m pytest api/tests -q -rs  # frontend (Vitest) joins this target when web tests exist

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

seed-cloud:
	@echo "TODO: load data/seed/omop_subset.sql.gz into Neon (phase 4)" && exit 1

smoke:
	bash scripts/smoke_compose.sh
