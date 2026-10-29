"""Issue #25: each tool validates its input and returns structured evidence; the write tools are guarded."""
import pytest

from oncall.guardrails import Policy
from oncall.payload import parse_alert
from oncall.tests.conftest import NOW, OLD, FakeBackend
from oncall.tools import TOOL_SPECS, Tools

ALERT = parse_alert({"status": "firing", "alertname": "CohortRunLatencyHigh", "environment": "dev",
                     "fingerprint": "abcdef0123456789", "startsAt": "2026-10-29T11:50:00Z"})


def make(policy=None, backend=None):
    backend = backend or FakeBackend()
    return Tools(policy or Policy(frozenset({"dev"}), dry_run=False), backend, ALERT, 7, now=lambda: NOW), backend


def test_the_specs_are_the_four_allowlisted_tools_with_closed_input_schemas():
    assert {spec["name"] for spec in TOOL_SPECS} == {"query_metrics", "list_revisions", "rollback", "update_incident"}
    for spec in TOOL_SPECS:
        assert spec["input_schema"]["additionalProperties"] is False and spec["description"]


def test_query_metrics_returns_a_series_per_revision():
    tools, backend = make()
    result, error = tools.call("query_metrics", {"metric": "p95_seconds", "window": "30m"})
    assert not error and result["series"][0]["revision"] and backend.queries == [("p95_seconds", "30m", "dev")]


@pytest.mark.parametrize("args", [
    {"metric": "up{job=~'.*'}", "window": "10m"}, {"metric": "error_ratio", "window": "10m] or vector(1) #"},
    {"metric": "error_ratio"}, {"window": "10m"}, {"metric": "error_ratio", "window": "10m", "extra": 1}, "error_ratio",
])
def test_query_metrics_takes_only_named_metrics_and_fixed_windows_never_free_text(args):
    tools, backend = make()
    result, error = tools.call("query_metrics", args)
    assert error and "refused" in result and backend.queries == []


def test_list_revisions_returns_name_created_version_and_traffic():
    tools, _ = make()
    result, error = tools.call("list_revisions", {"service": "api"})
    assert not error
    assert {"name", "created", "version", "traffic_percent"} <= set(result["revisions"][0])


@pytest.mark.parametrize("service", ["db", "api; ls", ""])
def test_list_revisions_refuses_other_services(service):
    tools, _ = make()
    assert tools.call("list_revisions", {"service": service})[1]


@pytest.mark.parametrize("status", ["done", "", "ESCALATED", "rolled_back; rm"])
def test_update_incident_takes_only_the_four_statuses(status):
    tools, backend = make()
    assert tools.call("update_incident", {"status": status, "summary": "x"})[1] and backend.comments == []


def test_update_incident_truncates_long_text_and_defuses_mentions_and_control_characters():
    tools, backend = make()
    controls = chr(27) + "[31m" + chr(0)
    tools.call("update_incident", {"status": "investigating", "summary": controls + "ping @someone " + "x" * 5000})
    ((incident, status, body),) = backend.comments
    assert incident == 7 and status == "investigating"
    assert "@someone" not in body and len(body) < 2500 and "\x1b" not in body and "\x00" not in body


def test_escalating_marks_the_incident_as_needing_a_human():
    tools, backend = make()
    result, _ = tools.call("update_incident", {"status": "escalated", "summary": "needs a person"})
    assert result["needs_human"] is True and backend.comments[0][1] == "escalated"


def test_a_successful_rollback_reports_what_moved():
    tools, backend = make()
    result, error = tools.call("rollback", {"service": "api", "revision": OLD})
    assert not error and result["ok"] is True and backend.rollbacks == [("dev", "api", OLD, 7)]


def test_a_failed_rollback_is_reported_as_an_error_and_not_retried():
    class Failing(FakeBackend):
        def rollback(self, environment, service, revision, incident):
            self.rollbacks.append((environment, service, revision, incident))
            return {"ok": False, "detail": "smoke test failed on the target"}

    tools, backend = make(backend=Failing())
    result, error = tools.call("rollback", {"service": "api", "revision": OLD})
    assert error and result["ok"] is False and len(backend.rollbacks) == 1


def test_a_rollback_whose_backend_check_is_unavailable_is_refused_not_assumed_safe():
    class NoHistory(FakeBackend):
        def rollback_history(self, environment, service):
            raise RuntimeError("cannot read incident comments")

    tools, backend = make(backend=NoHistory())
    result, error = tools.call("rollback", {"service": "api", "revision": OLD})
    assert error and backend.rollbacks == []
