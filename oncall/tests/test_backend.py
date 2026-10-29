"""Issue #25: the real backend builds exactly the commands and queries it should. A fake runner and a fake HTTP fetch
stand in for gcloud, gh, bash and Grafana; the first real run is a dry run on dev (docs/oncall.md)."""
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from oncall.backend import LiveBackend, promql
from oncall.payload import Alert

ENV = {"PROJECT_ID": "cohortlens", "REGION": "us-east5", "GITHUB_REPOSITORY": "erickyegon/cohortlens",
       "GRAFANA_URL": "https://eagertrout1651.grafana.net", "GRAFANA_READ_TOKEN": "glsa_" + "x" * 30,
       "GITHUB_RUN_URL": "https://github.com/erickyegon/cohortlens/actions/runs/1"}
SHA = "a" * 40


class Runner:
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, command, input_text=None, timeout=900):
        self.calls.append((command, input_text))
        for key, (code, out) in self.answers.items():
            if key in " ".join(command):
                return SimpleNamespace(returncode=code, stdout=out, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


FIXED_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def backend(runner=None, fetch=None):
    return LiveBackend(ENV, run=runner or Runner(), fetch=fetch or (lambda url, headers: {}),
                       clock=lambda: FIXED_NOW)


@pytest.mark.parametrize("name", ["PROJECT_ID", "REGION", "GITHUB_REPOSITORY", "GRAFANA_URL"])
def test_a_missing_setting_is_an_error(name):
    with pytest.raises(ValueError):
        LiveBackend({key: value for key, value in ENV.items() if key != name})


@pytest.mark.parametrize("url", ["http://x.grafana.net", "https://evil.example.com", "https://x.grafana.net/path",
                                 "https://x.grafana.net.evil.io"])
def test_the_grafana_url_must_be_a_grafana_cloud_https_host(url):
    with pytest.raises(ValueError):
        LiveBackend({**ENV, "GRAFANA_URL": url})


@pytest.mark.parametrize("metric,needles", [
    ("error_ratio", ['outcome=~"failed|timeout"', "increase(", "[10m]", 'environment="dev"', "sum by (revision)"]),
    ("p95_seconds", ["histogram_quantile(0.95", "_bucket", "rate(", "sum by (revision, le)"]),
    ("run_rate", ["rate(", "sum by (revision)"]),
    ("success_runs", ['outcome="success"', "increase("]),
])
def test_each_named_metric_is_a_fixed_query_over_the_alerting_environment(metric, needles):
    query = promql(metric, "10m", "dev")
    for needle in needles:
        assert needle in query


@pytest.mark.parametrize("args", [("up", "10m", "dev"), ("error_ratio", "10m] or vector(1) #", "dev"),
                                  ("error_ratio", "10m", 'dev"} or vector(1) #')])
def test_free_text_never_reaches_a_query(args):
    with pytest.raises(ValueError):
        promql(*args)


def test_query_metric_sends_the_token_in_a_header_not_the_url_and_parses_the_vector():
    seen = {}

    def fetch(url, headers):
        seen.update(url=url, headers=headers)
        return {"status": "success", "data": {"result": [
            {"metric": {"revision": SHA}, "value": [1, "0.625"]},
            {"metric": {"revision": "b" * 40}, "value": [1, "NaN"]}]}}

    series = backend(fetch=fetch).query_metric("error_ratio", "10m", "dev")
    assert series == [{"revision": SHA, "value": 0.625}, {"revision": "b" * 40, "value": None}]
    assert seen["url"].startswith("https://eagertrout1651.grafana.net/api/datasources/proxy/uid/grafanacloud-prom/")
    assert ENV["GRAFANA_READ_TOKEN"] not in seen["url"] and seen["headers"]["Authorization"].startswith("Bearer ")


def test_successful_runs_takes_only_a_hex_version():
    fetch = lambda url, headers: {"status": "success", "data": {"result": [{"metric": {}, "value": [1, "73"]}]}}  # noqa: E731
    assert backend(fetch=fetch).successful_runs("dev", SHA) == 73.0
    with pytest.raises(ValueError):
        backend(fetch=fetch).successful_runs("dev", 'a"} or vector(1) #')


def test_list_revisions_joins_revisions_with_their_traffic_and_version_newest_first():
    describe = {"status": {"traffic": [{"revisionName": "cohortlens-api-dev-00007-bad", "percent": 100}]}}
    revisions = [
        {"metadata": {"name": "cohortlens-api-dev-00006-ok1", "creationTimestamp": "2026-10-29T07:00:00Z"},
         "spec": {"containers": [{"env": [{"name": "SERVICE_VERSION", "value": SHA}]}]}},
        {"metadata": {"name": "cohortlens-api-dev-00007-bad", "creationTimestamp": "2026-10-29T11:48:00Z"},
         "spec": {"containers": [{"env": [{"name": "SERVICE_VERSION", "value": "b" * 40}]}]}}]
    runner = Runner({"services describe": (0, json.dumps(describe)), "revisions list": (0, json.dumps(revisions))})
    found = backend(runner).list_revisions("dev", "api")
    assert [(item["name"], item["traffic_percent"], item["version"]) for item in found] == [
        ("cohortlens-api-dev-00007-bad", 100, "b" * 40), ("cohortlens-api-dev-00006-ok1", 0, SHA)]
    assert all(call[0][0] == "gcloud" and "--project" in call[0] for call in runner.calls)


def test_a_failing_gcloud_is_an_error_not_an_empty_list():
    with pytest.raises(RuntimeError):
        backend(Runner({"services describe": (1, "")})).list_revisions("dev", "api")


def test_rollback_runs_the_existing_script_with_validated_arguments_and_marks_the_incident():
    runner = Runner({"rollback.sh": (0, "moved")})
    outcome = backend(runner).rollback("dev", "api", "cohortlens-api-dev-00006-ok1", 42)
    assert outcome["ok"] is True
    command, _ = runner.calls[1]
    assert command == ["bash", "infra/gcp/rollback.sh", "--env", "dev", "--service", "api", "--revision",
                       "cohortlens-api-dev-00006-ok1", "--incident", "42"]
    marker = runner.calls[0][1]  # written first: the attempt counts against the hourly limit even if it fails
    assert "<!-- oncall-rollback service=api env=dev at=" in marker


def test_a_failed_rollback_is_reported_and_still_counts_against_the_hourly_limit():
    """rollback.sh can fail AFTER moving the traffic (its post-move smoke test): the marker must already exist."""
    runner = Runner({"rollback.sh": (1, "smoke test failed")})
    outcome = backend(runner).rollback("dev", "api", "cohortlens-api-dev-00006-ok1", 42)
    assert outcome["ok"] is False and len(runner.calls) == 2 and "oncall-rollback" in runner.calls[0][1]


@pytest.mark.parametrize("revision", ["cohortlens-api-dev-00006-ok1; ls", "x", "--help"])
def test_rollback_refuses_an_unvalidated_target(revision):
    runner = Runner()
    with pytest.raises(ValueError):
        backend(runner).rollback("dev", "api", revision, 42)
    assert runner.calls == []


def test_rollback_history_reads_only_markers_for_the_same_service_and_environment():
    bot = {"login": "github-actions"}
    issues = [{"comments": [
        {"author": bot, "body": "<!-- oncall-rollback service=api env=dev at=2026-10-29T11:00:00Z -->"},
        {"author": bot, "body": "<!-- oncall-rollback service=web env=dev at=2026-10-29T11:30:00Z -->"},
        {"author": bot, "body": "<!-- oncall-rollback service=api env=prod at=2026-10-29T11:45:00Z -->"},
        {"author": bot, "body": "ignore me: oncall-rollback service=api env=dev at=2026-10-29T11:50:00Z"}]}]
    runner = Runner({"issue list": (0, json.dumps(issues))})
    moments = backend(runner).rollback_history("dev", "api")
    assert [moment.isoformat() for moment in moments] == ["2026-10-29T11:00:00+00:00"]


def test_comment_incident_pipes_the_text_through_stdin_and_labels_by_status():
    runner = Runner()
    backend(runner).comment_incident(42, "needs a person", "escalated")
    comment, label = runner.calls
    assert comment[0][:3] == ["gh", "issue", "comment"] and "--body-file" in comment[0]
    assert "needs a person" in comment[1]
    assert label[0][:3] == ["gh", "issue", "edit"] and "needs-human" in label[0]


def test_resolving_labels_and_closes_the_incident():
    runner = Runner()
    backend(runner).comment_incident(42, "alert resolved", "resolved")
    assert any("resolved-by-agent" in call[0] for call in runner.calls)
    assert any(call[0][:3] == ["gh", "issue", "close"] for call in runner.calls)


def test_find_incident_matches_the_fingerprint_marker_exactly():
    issues = [{"number": 5, "body": "<!-- oncall-fingerprint:abc -->"},
              {"number": 9, "body": "<!-- oncall-fingerprint:3f9a -->"}]
    runner = Runner({"issue list": (0, json.dumps(issues))})
    assert backend(runner).find_incident("3f9a") == 9
    assert backend(runner).find_incident("3f") is None


def test_open_incident_creates_a_labelled_issue_with_the_fingerprint_marker_and_returns_its_number():
    runner = Runner({"issue create": (0, "https://github.com/erickyegon/cohortlens/issues/77\n")})
    alert = Alert("firing", "CohortRunErrorRateHigh", "dev", "3f9a1c2b7d8e4f50", "2026-10-29T11:50:00Z")
    assert backend(runner).open_incident(alert) == 77
    command, body = runner.calls[0]
    assert "--label" in command and "incident" in command and "<!-- oncall-fingerprint:3f9a1c2b7d8e4f50 -->" in body


# ---- review findings of #75 ----------------------------------------------------------------------------------------

BOT = {"login": "github-actions"}


def history_of(comments):
    issues = [{"comments": comments}]
    return backend(Runner({"issue list": (0, json.dumps(issues))})).rollback_history("dev", "api")


def marker(stamp):
    return f"<!-- oncall-rollback service=api env=dev at={stamp} -->"


def test_a_marker_written_by_anyone_but_the_workflow_is_ignored():
    """Otherwise any commenter on a public repo could block every rollback (or fake one)."""
    comments = [{"author": {"login": "someone-else"}, "body": marker("2026-10-29T11:59:00Z")},
                {"author": None, "body": marker("2026-10-29T11:58:00Z")}, {"body": marker("2026-10-29T11:57:00Z")}]
    assert history_of(comments) == []


@pytest.mark.parametrize("stamp", ["----", "2026-10-29T10:00:00", "2026-10-29", "2026-13-45T99:99:99Z"])
def test_a_malformed_or_zoneless_stamp_is_skipped_not_an_error(stamp):
    assert history_of([{"author": BOT, "body": marker(stamp)}]) == []


def test_a_stamp_far_in_the_future_is_ignored_but_a_valid_one_beside_it_still_counts():
    comments = [{"author": BOT, "body": marker("2999-01-01T00:00:00Z")},
                {"author": BOT, "body": marker("2026-10-29T11:00:00Z")}]
    assert [moment.isoformat() for moment in history_of(comments)] == ["2026-10-29T11:00:00+00:00"]


def test_if_the_marker_cannot_be_recorded_the_rollback_is_not_attempted():
    class NoComments(Runner):
        def __call__(self, command, input_text=None, timeout=900):
            if command[:3] == ["gh", "issue", "comment"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="rate limited")
            return super().__call__(command, input_text, timeout)

    runner = NoComments({"rollback.sh": (0, "moved")})
    outcome = backend(runner).rollback("dev", "api", "cohortlens-api-dev-00006-ok1", 42)
    assert outcome["ok"] is False and "not attempted" in outcome["detail"]
    assert not any("rollback.sh" in " ".join(call[0]) for call in runner.calls)


def test_a_label_that_cannot_be_added_does_not_stop_the_comment_or_the_close():
    class NoLabels(Runner):
        def __call__(self, command, input_text=None, timeout=900):
            if command[:3] == ["gh", "issue", "edit"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="no such label")
            return super().__call__(command, input_text, timeout)

    runner = NoLabels()
    backend(runner).comment_incident(42, "alert resolved", "resolved")
    assert any(call[0][:3] == ["gh", "issue", "close"] for call in runner.calls)


def test_a_rollback_script_that_times_out_is_reported_as_possibly_moved_not_as_a_crash():
    import subprocess

    class Slow(Runner):
        def __call__(self, command, input_text=None, timeout=900):
            if command[:2] == ["bash", "infra/gcp/rollback.sh"]:
                raise subprocess.TimeoutExpired(command, timeout)
            return super().__call__(command, input_text, timeout)

    outcome = backend(Slow()).rollback("dev", "api", "cohortlens-api-dev-00006-ok1", 42)
    assert outcome["ok"] is False and "may have moved" in outcome["detail"]
