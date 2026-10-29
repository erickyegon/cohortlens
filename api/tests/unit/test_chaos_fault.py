"""Issue #27: the one fault the chaos drill S1 injects, and the guarantee that prod can never have it.

`CHAOS_FAULT=fail_runs` makes every cohort run fail in the engine (no data involved), so a revision deployed with that
setting behaves like a bad release: the runs are recorded as failed and the error-rate alert fires. It is ignored when
DEPLOYMENT_ENVIRONMENT is prod, whatever is configured.
"""
import pytest

from app import runs


class Reached(Exception):
    """The run got past the fault hook and into the engine."""


@pytest.fixture()
def engine_reached(monkeypatch):
    def reached(*_args, **_kwargs):
        raise Reached

    monkeypatch.setattr(runs, "build_plan", reached)


def test_without_the_setting_a_run_goes_on_into_the_engine(monkeypatch, engine_reached):
    monkeypatch.delenv("CHAOS_FAULT", raising=False)
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "dev")
    with pytest.raises(Reached):
        runs.execute_run(None, 1000)


def test_fail_runs_makes_the_run_fail_before_it_reaches_the_engine(monkeypatch, engine_reached):
    monkeypatch.setenv("CHAOS_FAULT", "fail_runs")
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "dev")
    with pytest.raises(runs.InjectedFault):
        runs.execute_run(None, 1000)


@pytest.mark.parametrize("environment", ["prod", "local", ""])
def test_the_fault_is_honoured_only_where_a_drill_is_allowed(monkeypatch, engine_reached, environment):
    """prod never; and an unset or odd environment name is not trusted either: only dev (and the compose stack's
    'local' is excluded on purpose, so a developer's machine is never surprised)."""
    monkeypatch.setenv("CHAOS_FAULT", "fail_runs")
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", environment)
    with pytest.raises(Reached):
        runs.execute_run(None, 1000)


@pytest.mark.parametrize("value", ["", "FAIL_RUNS", "fail_runs;", "slow", "1"])
def test_any_other_value_does_nothing(monkeypatch, engine_reached, value):
    monkeypatch.setenv("CHAOS_FAULT", value)
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "dev")
    with pytest.raises(Reached):
        runs.execute_run(None, 1000)
