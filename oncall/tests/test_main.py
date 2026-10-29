"""Issue #24 and #26: the workflow entry point, with fakes for the backend and the model."""
import json

import pytest

from oncall.__main__ import main
from oncall.tests.conftest import NOW, OLD, FakeBackend, ReplayClient, load_recording

FIRING = {"status": "firing", "alertname": "CohortRunErrorRateHigh", "environment": "dev",
          "fingerprint": "3f9a1c2b7d8e4f50", "startsAt": "2026-10-29T11:50:00Z"}


class IncidentBackend(FakeBackend):
    def __init__(self, existing=None, **kwargs):
        super().__init__(**kwargs)
        self.existing, self.opened = existing, []

    def find_incident(self, fingerprint):
        return self.existing

    def open_incident(self, alert):
        self.opened.append(alert.fingerprint)
        return 42


def run(payload, *, backend=None, recording="bad_deploy", extra=None):
    backend = backend or IncidentBackend()
    client = ReplayClient(load_recording(recording))
    env = {"ALERT_PAYLOAD": json.dumps(payload), "ONCALL_ENVIRONMENTS": "dev", "DRY_RUN": "false", **(extra or {})}
    code = main(env, backend_factory=lambda _env: backend, client_factory=lambda: client, now=lambda: NOW)
    return code, backend, client


def test_a_firing_alert_opens_an_incident_and_the_agent_rolls_back_and_comments_on_it():
    code, backend, _ = run(FIRING)
    assert code == 0 and backend.opened == [FIRING["fingerprint"]]
    assert backend.rollbacks == [("dev", "api", OLD, 42)]
    assert [(number, status) for number, status, _ in backend.comments] == [(42, "rolled_back")]


def test_an_existing_incident_for_the_same_fingerprint_is_reused_not_duplicated():
    code, backend, _ = run(FIRING, backend=IncidentBackend(existing=17))
    assert code == 0 and backend.opened == [] and backend.rollbacks == [("dev", "api", OLD, 17)]


def test_dry_run_opens_no_incident_and_changes_nothing():
    code, backend, _ = run(FIRING, extra={"DRY_RUN": "true"})
    assert code == 0 and backend.opened == [] and backend.rollbacks == [] and backend.comments == []


def test_an_alert_for_an_environment_that_is_not_enabled_is_ignored_without_touching_anything():
    code, backend, client = run({**FIRING, "environment": "prod"})
    assert code == 0 and backend.opened == [] and backend.queries == [] and client.requests == []


def test_a_resolved_notification_closes_the_incident_without_asking_the_model():
    code, backend, client = run({**FIRING, "status": "resolved"}, backend=IncidentBackend(existing=17))
    assert code == 0 and client.requests == []
    assert [(number, status) for number, status, _ in backend.comments] == [(17, "resolved")]


def test_a_resolved_notification_with_no_open_incident_does_nothing():
    code, backend, _ = run({**FIRING, "status": "resolved"})
    assert code == 0 and backend.comments == []


def test_a_bad_payload_is_refused_before_anything_runs():
    code, backend, client = run({**FIRING, "alertname": "DropEverything"})
    assert code == 2 and backend.opened == [] and client.requests == []


def test_a_settings_error_is_refused():
    code, *_ = run(FIRING, extra={"ONCALL_ENVIRONMENTS": "dev,staging"})
    assert code == 2


def test_if_the_model_never_posts_an_update_the_workflow_escalates_with_a_fallback_comment():
    silent = [{"stop_reason": "end_turn", "content": [{"type": "text", "text": "Nothing to do."}]}]
    backend = IncidentBackend()
    client = ReplayClient(silent)
    env = {"ALERT_PAYLOAD": json.dumps(FIRING), "ONCALL_ENVIRONMENTS": "dev", "DRY_RUN": "false"}
    code = main(env, backend_factory=lambda _env: backend, client_factory=lambda: client, now=lambda: NOW)
    assert code == 0 and [(n, status) for n, status, _ in backend.comments] == [(42, "escalated")]


def test_a_model_that_hits_the_turn_limit_fails_the_run_but_still_escalates():
    endless = [{"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": f"t{n}", "name": "list_revisions", "input": {"service": "api"}}]} for n in range(20)]
    backend = IncidentBackend()
    env = {"ALERT_PAYLOAD": json.dumps(FIRING), "ONCALL_ENVIRONMENTS": "dev", "DRY_RUN": "false"}
    client = ReplayClient(endless)
    code = main(env, backend_factory=lambda _env: backend, client_factory=lambda: client, now=lambda: NOW)
    assert code == 1 and [status for _, status, _ in backend.comments] == ["escalated"]


def test_if_the_model_call_itself_fails_the_incident_is_still_escalated_and_the_error_propagates():
    class Down:
        class messages:  # noqa: N801 - mimics the SDK's attribute
            @staticmethod
            def create(**_request):
                raise ConnectionError("api unreachable")

    backend = IncidentBackend()
    env = {"ALERT_PAYLOAD": json.dumps(FIRING), "ONCALL_ENVIRONMENTS": "dev", "DRY_RUN": "false"}
    with pytest.raises(ConnectionError):
        main(env, backend_factory=lambda _env: backend, client_factory=Down, now=lambda: NOW)
    assert [status for _, status, _ in backend.comments] == ["escalated"]


def test_a_crash_before_the_update_still_escalates_and_the_log_lists_the_calls_made(capsys):
    class DiesBeforeTheUpdate(ReplayClient):
        def _create(self, **request):
            if len(self.requests) >= 3:
                raise ConnectionError("dropped")
            return super()._create(**request)

    backend = IncidentBackend()
    client = DiesBeforeTheUpdate(load_recording("bad_deploy"))
    env = {"ALERT_PAYLOAD": json.dumps(FIRING), "ONCALL_ENVIRONMENTS": "dev", "DRY_RUN": "false"}
    with pytest.raises(ConnectionError):
        main(env, backend_factory=lambda _env: backend, client_factory=lambda: client, now=lambda: NOW)
    assert backend.rollbacks == [("dev", "api", OLD, 42)]
    # no update was posted, so the fallback is right
    assert [status for _, status, _ in backend.comments] == ["escalated"]
    assert "tool rollback" in capsys.readouterr().out  # the calls made before the crash are in the log


def test_the_fallback_is_skipped_when_an_update_was_posted_before_the_crash():
    recording = load_recording("db_outage")

    class DiesAtTheEnd(ReplayClient):
        def _create(self, **request):
            if len(self.requests) >= 3:
                raise ConnectionError("dropped")
            return super()._create(**request)

    backend = IncidentBackend()
    client = DiesAtTheEnd(recording)
    env = {"ALERT_PAYLOAD": json.dumps(FIRING), "ONCALL_ENVIRONMENTS": "dev", "DRY_RUN": "false"}
    with pytest.raises(ConnectionError):
        main(env, backend_factory=lambda _env: backend, client_factory=lambda: client, now=lambda: NOW)
    assert [status for _, status, _ in backend.comments] == ["escalated"]  # the agent's own update only
