# CohortLens

Tells a real-world-evidence scientist, in seconds, whether enough patients exist in an OMOP database to run a proposed study.

> Status: phase 1 (spec and scaffolding). See [docs/spec.md](docs/spec.md).

## Problem
Feasibility counts for observational studies are hand-written SQL, slow to change and hard to share.

## Users
RWE scientists, epidemiologists and HEOR analysts scoping a study protocol.

## Core journey
Create a cohort → Run feasibility → final count, attrition waterfall, baseline table → save, reopen, edit, re-run.

## Expected behaviour
p95 run under 3 s; counts below 11 shown as "<11"; synthetic data only.

## Out of scope
Real patient data, accounts beyond a demo login, statistics beyond descriptive counts, free-text cohort entry (stretch).

## Quick start
`make up` → http://localhost:8080 (not yet implemented)

## Live URLs / demo video
TBD
