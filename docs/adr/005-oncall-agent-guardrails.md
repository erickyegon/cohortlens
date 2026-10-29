# ADR-005: On-call agent guardrails

Status: accepted · Date: 2026-10-29 (written after the decision, from the code, three review rounds and the S1 drill)

## Context
A Grafana alert starts a model that can move production-like traffic. A model can be wrong, and it reads text it does not control (metric labels, revision names, issue comments). The agent must therefore be safe when the model is wrong or is being talked into something, not only when it is right. The project is a demo on synthetic data, and the first version is deliberately lean.

## Decision
**The model proposes; code decides.** The limits are in `oncall/guardrails.py` and the workflow, covered by tests, not in the prompt:
- **Four tools only:** `query_metrics`, `list_revisions`, `rollback` and `update_incident`. Anything else is refused and the model is told so.
- **Dev only.** `ONCALL_ENVIRONMENTS` lists the environments it may act in (`dev`); the workflow refuses to start if it contains `prod`, and it signs in with the dev deploy account, which cannot be used for prod (the prod account works only from the protected `production` environment).
- **Dry run unless `DRY_RUN=false`.** The model and the read tools run; the two write tools only record the call. A real run needs the repository variable `ONCALL_DRY_RUN=false`.
- **Rollback only to a revision that is not serving and whose git version has at least 50 successful runs in the last 7 days**, counted from the metrics.
- **At most one rollback per service per hour,** recorded on the incident by a bot-authored marker written **before** the attempt (a failed attempt counts too, so a flapping alert cannot cause a loop). Markers are read only when authored by the bot, so a user comment cannot forge or hide one.
- **The model cannot resolve or close an incident.** Only the workflow does, on Grafana's resolved notification, without a model call.
- **Alert fields and tool results are untrusted data.** The payload is validated as data (an allowlist of alert names and environments, fixed formats) and reaches the program through the environment, never through a shell line. Tool results are wrapped as data and the model is told so; a hostile recording shows a talked-into call being refused.
- **Tests never call the real Anthropic API.** The loop is replayed from recorded responses; the SDK is imported only in `oncall/__main__.py`. The dispatch token is fine-grained, limited to this repository and expires in 30 days; the Anthropic key has a spending limit.

## Alternatives not chosen
- **Guardrails in the prompt only.** A prompt is not a control; injected text can undo it.
- **Letting the agent act on prod, with the approval step.** It needs its own job in the `production` environment; not built (`docs/oncall.md`, "Not built").
- **A revert pull request or code changes by the model.** More power than the first version needs; not built.

## Consequences
- **Drill S1 passed on dev** (audit section 12): the agent rolled back a faulty revision within about 4.5 minutes of the fault and the incident closed on the resolved notification ([run 40894738766](https://github.com/erickyegon/cohortlens/actions/runs/40894738766), [run 40896403031](https://github.com/erickyegon/cohortlens/actions/runs/40896403031), [incident #78](https://github.com/erickyegon/cohortlens/issues/78)). The same drill showed the guardrails refusing a malformed alert (attempt 1).
- **`ONCALL_DRY_RUN=false` stays on** in the repository. The worst case is dev traffic moved to an older revision that passed the 50-run check, once an hour; setting the variable to `true` stops every change.
- **A failed attempt before traffic moved still blocks a retry for an hour.** Safe, but a person has to step in.
- **During an alert storm an event can be dropped:** the workflow keeps one running and one pending run. A queue would need an external service.
- **Whether the live model makes the right call is shown only by the dry run and the drill,** not by the tests.
- **The agent did not check recovery itself;** the drill script and the alert resolving did.
