"""Issues #25 and #26: the guardrails are code, not instructions to the model."""
from datetime import timedelta

import pytest

from oncall.guardrails import (
    MIN_SUCCESSFUL_RUNS,
    TOOLS,
    WRITE_TOOLS,
    Policy,
    Refused,
    check_environment,
    check_rollback,
    check_tool,
)
from oncall.tests.conftest import ANCIENT, NEW, NOW, OLD, default_revisions


def test_the_tool_allowlist_is_exactly_the_four_documented_tools():
    assert TOOLS == {"query_metrics", "list_revisions", "rollback", "update_incident"}
    assert WRITE_TOOLS == {"rollback", "update_incident"}


@pytest.mark.parametrize("name", ["delete_service", "run_shell", "gcloud", "rollback ", "Rollback", "",
                                  "update_traffic"])
def test_a_tool_outside_the_allowlist_is_refused(name):
    with pytest.raises(Refused):
        check_tool(name)


def test_the_policy_acts_nowhere_and_only_pretends_unless_configured():
    policy = Policy.from_environment({})
    assert policy.environments == frozenset() and policy.dry_run is True
    with pytest.raises(Refused):
        check_environment(policy, "dev")


@pytest.mark.parametrize("value,expected", [("dev", {"dev"}), ("dev,prod", {"dev", "prod"}), (" prod ", {"prod"}),
                                            ("", set())])
def test_environments_come_from_the_setting(value, expected):
    assert Policy.from_environment({"ONCALL_ENVIRONMENTS": value}).environments == expected


@pytest.mark.parametrize("value", ["staging", "dev,all", "*", "dev;prod"])
def test_an_unknown_environment_in_the_setting_is_an_error_not_ignored(value):
    with pytest.raises(ValueError):
        Policy.from_environment({"ONCALL_ENVIRONMENTS": value})


@pytest.mark.parametrize("value,dry", [("true", True), ("TRUE", True), ("", True), ("yes", True), ("false", False)])
def test_only_an_explicit_false_makes_it_live(value, dry):
    assert Policy.from_environment({"DRY_RUN": value}).dry_run is dry


def test_an_environment_that_is_not_enabled_is_refused():
    policy = Policy(frozenset({"dev"}), dry_run=False)
    check_environment(policy, "dev")
    with pytest.raises(Refused, match="prod"):
        check_environment(policy, "prod")


def ok(**overrides):
    args = {"service": "api", "revision": OLD, "revisions": default_revisions(), "successful_runs": 120.0,
            "history": [], "now": NOW}
    args.update(overrides)
    return check_rollback(**args)


def test_a_rollback_to_a_known_older_revision_with_enough_successful_runs_is_allowed():
    assert ok() is None


def test_the_target_must_be_an_existing_revision():
    with pytest.raises(Refused, match="exist"):
        ok(revision="cohortlens-api-dev-00099-zzz")


def test_the_target_must_not_be_the_revision_already_serving():
    with pytest.raises(Refused, match="serving"):
        ok(revision=NEW)


def test_the_target_needs_at_least_fifty_successful_runs():
    assert MIN_SUCCESSFUL_RUNS == 50
    ok(successful_runs=50.0)
    with pytest.raises(Refused, match="50"):
        ok(successful_runs=49.0)
    with pytest.raises(Refused):
        ok(revision=ANCIENT, successful_runs=0.0)


def test_one_rollback_per_service_per_hour():
    with pytest.raises(Refused, match="hour"):
        ok(history=[NOW - timedelta(minutes=59)])
    ok(history=[NOW - timedelta(minutes=61)])


def test_a_rollback_in_the_future_cannot_be_used_to_dodge_the_limit():
    """A history entry dated ahead of now (clock skew, or a forged timestamp) still counts."""
    with pytest.raises(Refused):
        ok(history=[NOW + timedelta(minutes=30)])


@pytest.mark.parametrize("service", ["db", "api; ls", "", "API"])
def test_only_the_api_and_web_services_can_be_rolled_back(service):
    with pytest.raises(Refused):
        ok(service=service)
