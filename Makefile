.PHONY: up test e2e lint traffic chaos seed-cloud

up:
	docker compose up --build

test:
	@echo "TODO: wire pytest + vitest (phase 2/3)" && exit 1

e2e:
	@echo "TODO: Playwright (stretch)" && exit 1

lint:
	@echo "TODO: ruff + eslint (phase 2/3)" && exit 1

traffic:
	k6 run loadgen/journey.js

chaos:
	@echo "TODO: chaos/$(SCENARIO) (phase 6)" && exit 1

seed-cloud:
	@echo "TODO: load data/seed/omop_subset.sql.gz into Neon (phase 4)" && exit 1
