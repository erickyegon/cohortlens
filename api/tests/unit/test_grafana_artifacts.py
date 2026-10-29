"""Issue #23: the core-journey dashboard and the two alert rules, as exported files under infra/grafana (the owner chose
plain files and a small apply script over Terraform).

"Given CohortRunErrorRateHigh, then it fires only with at least 10 runs in 5 minutes and error rate above 5% for 2
minutes. Given CohortRunLatencyHigh, then it fires only with at least 20 runs in 10 minutes and p95 above 3 s for 5
minutes. Given any alert, then it carries service, environment, agent and runbook_url."

The files are Grafana's own JSON (the alert-rule provisioning format and a dashboard model) with three placeholders,
`__PROMETHEUS_UID__`, `__LOKI_UID__` and `__TEMPO_UID__`, that `infra/grafana/apply.sh` fills in from the stack's real
data
sources. The queries are checked here as text; their behaviour was checked against a real Prometheus (see the PR).
"""
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
GRAFANA = ROOT / "infra" / "grafana"
RUNS = "cohortlens_cohort_runs_total"
DURATION = "cohortlens_cohort_run_duration_seconds"


def load(name):
    return json.loads((GRAFANA / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rules():
    found = load("alert-rules.json")
    assert isinstance(found, list)
    return {rule["title"]: rule for rule in found}


def query_of(rule):
    queries = [item for item in rule["data"] if item["datasourceUid"] != "__expr__"]
    assert len(queries) == 1
    return queries[0]["model"]["expr"]


def test_there_are_exactly_the_two_documented_rules(rules):
    assert set(rules) == {"CohortRunErrorRateHigh", "CohortRunLatencyHigh"}


def test_the_error_rate_rule_needs_ten_runs_in_five_minutes_and_an_error_rate_above_five_percent_for_two_minutes(rules):
    rule = rules["CohortRunErrorRateHigh"]
    expr = query_of(rule)
    assert rule["for"] == "2m"
    assert "> 0.05" in expr and ">= 10" in expr
    assert expr.count("[5m]") == 3 and "[10m]" not in expr
    assert f'{RUNS}{{outcome=~"failed|timeout"}}' in expr  # failed AND timed-out runs count as errors
    assert "increase(" in expr and "sum by (environment)" in expr


def test_the_latency_rule_needs_twenty_runs_in_ten_minutes_and_a_p95_above_three_seconds_for_five_minutes(rules):
    rule = rules["CohortRunLatencyHigh"]
    expr = query_of(rule)
    assert rule["for"] == "5m"
    assert "histogram_quantile(0.95" in expr and "> 3" in expr and ">= 20" in expr
    assert expr.count("[10m]") == 2 and "[5m]" not in expr
    assert f"{DURATION}_bucket" in expr and f"{RUNS}" in expr


def test_each_threshold_is_a_guard_on_the_same_query_so_a_quiet_system_never_fires(rules):
    """The minimum-runs guard is part of the query (`and on (environment) ... >= N`), so with too few runs the query
    returns nothing; and no data means OK, not an alert."""
    for rule in rules.values():
        assert " and on (environment) " in query_of(rule)
        assert rule["noDataState"] == "OK"
        assert rule["execErrState"] in ("Error", "Alerting")


@pytest.mark.parametrize("title", ["CohortRunErrorRateHigh", "CohortRunLatencyHigh"])
def test_every_alert_carries_service_environment_agent_and_a_runbook_that_exists(rules, title):
    rule = rules[title]
    labels = rule["labels"]
    assert {"service", "environment", "agent", "runbook_url"} <= set(labels)
    assert labels["service"] == "cohortlens-api" and labels["agent"] == "oncall"
    assert labels["environment"] == "{{ $labels.environment }}"  # the environment the series came from
    assert labels["runbook_url"] == f"https://github.com/erickyegon/cohortlens/blob/main/docs/runbooks/{title}.md"
    assert (ROOT / "docs" / "runbooks" / f"{title}.md").exists()


def test_the_rules_are_well_formed_for_the_provisioning_api(rules):
    for title, rule in rules.items():
        assert re.fullmatch(r"[a-z0-9-]{1,40}", rule["uid"]) and rule["ruleGroup"] == "cohortlens"
        assert rule["folderUID"] == "__FOLDER_UID__" and rule["orgID"] == 1
        assert rule["annotations"]["summary"] and rule["annotations"]["description"]
        refs = {item["refId"] for item in rule["data"]}
        assert rule["condition"] in refs, title
        for item in rule["data"]:
            if item["datasourceUid"] != "__expr__":
                assert item["datasourceUid"] == "__PROMETHEUS_UID__"
                assert item["model"]["instant"] is True and item["model"]["refId"] == item["refId"]
        assert len({rule["uid"] for rule in rules.values()}) == len(rules)


def test_the_metric_names_are_the_ones_the_api_exports():
    from app import telemetry

    assert telemetry.OUTCOMES == {"success", "failed", "timeout"}
    source = (ROOT / "api" / "app" / "telemetry" / "__init__.py").read_text(encoding="utf-8")
    assert f'"{RUNS}"' in source and f'"{DURATION}"' in source


# ---- the dashboard ---------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dashboard():
    return load("dashboard.json")


def test_the_dashboard_is_the_core_journey_with_a_stable_uid(dashboard):
    assert dashboard["title"] == "CohortLens core journey" and dashboard["uid"] == "cohortlens-core-journey"
    assert dashboard["id"] is None and dashboard["schemaVersion"] >= 36
    assert len({panel["id"] for panel in dashboard["panels"]}) == len(dashboard["panels"]) >= 6


def test_the_dashboard_shows_rate_errors_latency_by_revision_traces_and_logs(dashboard):
    text = json.dumps(dashboard)
    for needle in (RUNS, f"{DURATION}_bucket", "histogram_quantile(0.95", "histogram_quantile(0.50", "by (revision)",
                   'outcome=~\\"failed|timeout\\"', "cohortlens-api"):
        assert needle in text, needle
    types = {panel["datasource"]["uid"] for panel in dashboard["panels"]}
    assert types == {"__PROMETHEUS_UID__", "__LOKI_UID__", "__TEMPO_UID__"}


def test_the_dashboard_can_be_filtered_by_environment(dashboard):
    variables = {item["name"]: item for item in dashboard["templating"]["list"]}
    assert "environment" in variables
    assert all("$environment" in json.dumps(panel) for panel in dashboard["panels"] if panel["type"] != "text")


def test_no_file_contains_a_credential_or_a_real_data_source_id():
    for name in ("alert-rules.json", "dashboard.json"):
        text = (GRAFANA / name).read_text(encoding="utf-8")
        pattern = r"glc_|glsa_|Bearer |Basic [A-Za-z0-9+/=]{12,}|password|@[a-z0-9.-]+.grafana.net"
        assert not re.search(pattern, text, re.I)
        assert "grafanacloud-" not in text  # data sources are looked up at apply time, never hard-coded
