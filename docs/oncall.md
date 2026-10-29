# On-call agent (E6, lean)

A Grafana alert starts a GitHub Actions workflow. A model, given four guarded tools, investigates, rolls back a bad
release if the evidence says so, and records what it did on an incident issue. This page says what is built, how to set it
up once, how to run the drill, and what is **not** built.

## Flow

1. `CohortRunErrorRateHigh` or `CohortRunLatencyHigh` fires (`infra/grafana/alert-rules.json`; label `agent=oncall`).
2. A Grafana **webhook contact point** posts a `repository_dispatch` event `grafana-alert` to GitHub with five fields:
   `status`, `alertname`, `environment`, `fingerprint`, `startsAt` (issue #24).
3. `.github/workflows/oncall.yml` runs `python -m oncall` (`oncall/`). It signs in with the **dev** deploy account only.
4. The payload is validated (`oncall/payload.py`): closed sets and strict patterns; unknown fields are dropped.
5. An incident issue is found by the alert fingerprint (marker in its body) or opened, labelled `incident`.
6. The model gets the validated alert and four tools (`oncall/tools.py`):

   | Tool | Kind | What it does |
   |---|---|---|
   | `query_metrics` | read | one of four named metrics (error ratio, p95, run rate, successful runs) per revision over a fixed window, from Grafana Cloud. Never free-text PromQL. |
   | `list_revisions` | read | recent Cloud Run revisions: name, created, git version, traffic share (`gcloud`) |
   | `rollback` | write | moves all traffic to an earlier revision through the existing `infra/gcp/rollback.sh` (target smoke tested first, incident comment) |
   | `update_incident` | write | a status comment on the incident issue: `investigating`, `rolled_back` or `escalated` (adds `needs-human`). The model cannot resolve or close an incident |

7. A `resolved` notification does not use the model: the workflow comments, labels the issue `resolved-by-agent` and closes it (only the workflow can: the tool does not offer `resolved`).

## Guardrails (code, `oncall/guardrails.py`, tested)

- **Allowlist:** four tools; anything else is refused and the model is told so.
- **Environments:** only those in the repository variable `ONCALL_ENVIRONMENTS` (none by default). The workflow additionally refuses `prod` (see "Not built").
- **Dry run unless `DRY_RUN=false`:** the model and the read tools run for real; the two write tools record the call and do nothing.
- **Rollback only to** an existing revision of that service that is **not serving** and whose **git version has at least 50 successful runs in the last 7 days** (counted from the metrics; the count is per version, not per revision, and says nothing about recent health, so the model is also told to check the error ratio); **at most one rollback per service per hour**. The attempt is recorded on the incident **before** `rollback.sh` runs (it can move the traffic and then fail its smoke test), and if it cannot be recorded nothing is attempted. The hour is read from markers in incident comments, and **only comments made by the workflow's own account count** (a public repository takes anyone's comment); malformed, zoneless or far-future markers are ignored, and the model's text cannot contain a marker (`<!--` and `-->` are stripped).
- **Untrusted data:** tool results are wrapped as untrusted data and the system prompt says so, but the protection is the code above, not the prompt: a model that is talked into a dangerous call is refused (`oncall/tests/recordings/hostile.json`). Summaries are cut to 1,500 characters, stripped of control characters, and `@` is replaced so a comment cannot notify strangers.
- **Tests never call the real Anthropic API.** The agent is replayed from recorded responses (`oncall/tests/recordings/`); the SDK is imported only in `oncall/__main__.py`. Decisions covered: bad deploy (rolls back), database outage (escalates, no rollback), hostile output (everything refused).

## One-time setup (the owner; exact clicks are given one at a time in the session)

1. **Secret `ANTHROPIC_API_KEY`** (repository secret).
2. **Secret `GRAFANA_READ_TOKEN`**: a Grafana service account with the **Viewer** role, one token. **Variables** `GRAFANA_URL` (`https://<stack>.grafana.net`), `ONCALL_ENVIRONMENTS` = `dev`, `ONCALL_DRY_RUN` = `true`.
3. **Label** `incident` (created by the setup PR).
4. **Grafana contact point `github-oncall`** (type Webhook) with a fine-grained GitHub token, a **notification template** and a **notification policy** for `agent=oncall`. The template (name `github.dispatch`):

   ```
   {{ define "github.dispatch" }}{{ $a := index .Alerts 0 }}{{ coll.Dict "event_type" "grafana-alert" "client_payload" (coll.Dict "status" $a.Status "alertname" $a.Labels.alertname "environment" $a.Labels.environment "fingerprint" $a.Fingerprint "startsAt" ($a.StartsAt.Format "2006-01-02T15:04:05Z07:00")) | data.ToJSON }}{{ end }}
   ```

   Contact point: URL `https://api.github.com/repos/erickyegon/cohortlens/dispatches`, HTTP method POST, Authentication Header Scheme `Bearer`, Credentials = the token, Custom Payload template `{{ template "github.dispatch" . }}`. The token is a **fine-grained personal access token** limited to this repository with **Contents: Read and write** (what `repository_dispatch` needs), expiring in 30 days. It lives only in Grafana's contact point.
5. **Notification policy:** a child policy matching `agent = oncall`, contact point `github-oncall`, **group by `alertname` and `environment`** (the template sends one alert per notification: the first in the group, so a group holding two alerts would lose one), group wait 30 s, repeat interval 12 h. Leave "Disable resolved message" off: the resolved notification is what closes the incident.
6. **The contact point's Test button** sends a synthetic alert without an `environment` label, so the workflow starts and **refuses** it (exit 2). That proves delivery, not behaviour. Then a dry-run dispatch from the Actions tab, then `ONCALL_DRY_RUN=false`.

## Drill S1 on dev (`make chaos SCENARIO=s1`, `chaos/s1.sh`)

A bad release is promoted, the alert fires, the agent rolls back, the alert clears. It runs on dev (no production approval).

1. 60 successful warm-up runs on the healthy revision (so it has the 50 runs the guardrail requires), then 120 s for the metrics.
2. A new revision from the **same image** with `CHAOS_FAULT=fail_runs` (the API fails every run; honoured only when `DEPLOYMENT_ENVIRONMENT` is exactly `dev`, `api/app/runs.py`) and a synthetic version label `deadbee…`, so Grafana tells the two revisions apart. It serves 100% of traffic.
3. About 5 runs a minute (`loadgen/runs.py`; realistic definitions only). The error rate passes 5% with at least 10 runs; after the 2-minute hold the alert fires, Grafana posts to GitHub, the agent runs.
4. The script watches the serving revision; when the agent moves it, it keeps sending runs until they succeed, and lists the incident issues. If the agent has not acted when the time is up, **the script restores the healthy revision itself and exits 1**. The restore also runs on an error or Ctrl-C (a second Ctrl-C is ignored while it restores); only SIGKILL or a lost machine can leave dev on the faulty revision, and then the `update-traffic` command the script prints restores it. Stopping the traffic loop early skips its cohort cleanup, so a reference cohort may remain in dev. A second drill needs a deploy (or the rollback workflow) in between, because traffic stays pinned to a revision.

## Not built (honest list)

- **Prod automation.** US-11 describes prod. This workflow authenticates with the dev account and refuses `ONCALL_ENVIRONMENTS` containing `prod`: the prod deploy account can only be used from a job in the protected `production` environment, which waits for an approval, and the dev account must never be a way around that. A prod rollback by the agent would be a separate job in that environment (approval included). So the lean S1 drill runs on dev.
- **A revert PR** (issue #26) and **query_logs, query_traces, recent_changes** (issue #25). The four tools above are the lean set the owner chose.
- **Scenario S2** (latency regression), the demo recording, and the 10-minute recovery verification before a postmortem: recovery is shown by the alert resolving and the incident closing.
- **`agent=enabled`** in the issue text: the rules carry `agent=oncall`. A dev alert is not email-only; the workflow decides by `ONCALL_ENVIRONMENTS`.
- **Alert storms:** the workflow's concurrency group keeps one running and one pending run; a third event replaces the pending one, so during a storm a firing or resolved event can be dropped. Acceptable for the drill; a queue would need an external service.
- **Real model behaviour is not covered by tests.** The replay tests pin the guardrails and the loop; whether the live model makes the right call is shown only by the dry-run and the real drill.
