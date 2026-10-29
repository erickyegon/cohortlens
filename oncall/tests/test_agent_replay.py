"""Issues #25 and #26: the on-call agent's decisions, replayed from recorded model responses.

The model is replaced by `ReplayClient`, which plays back the recordings in `recordings/`; the real Anthropic API is
never called (and the SDK is not even imported). The backend is a fake that records every write, so each test asserts
what would have happened to the cloud and to the incident issue.
"""
import subprocess
import sys

import pytest

from oncall.agent import MAX_TURNS, run_agent
from oncall.guardrails import Policy
from oncall.payload import parse_alert
from oncall.tests.conftest import NEW, NOW, OLD, FakeBackend, ReplayClient, load_recording
from oncall.tools import Tools

ALERT = parse_alert({"status": "firing", "alertname": "CohortRunErrorRateHigh", "environment": "dev",
                     "fingerprint": "3f9a1c2b7d8e4f50", "startsAt": "2026-10-29T11:50:00Z"})
LIVE = Policy(frozenset({"dev"}), dry_run=False)
DRY = Policy(frozenset({"dev"}), dry_run=True)


def play(recording, *, policy=LIVE, backend=None, incident=42):
    backend = backend or FakeBackend()
    tools = Tools(policy, backend, ALERT, incident, now=lambda: NOW)
    client = ReplayClient(load_recording(recording))
    result = run_agent(ALERT, tools, client, model="test-model")
    return result, tools, backend, client


def test_a_bad_deploy_is_rolled_back_and_the_incident_is_updated():
    result, _, backend, client = play("bad_deploy")
    assert backend.rollbacks == [("dev", "api", OLD, 42)]
    assert [(number, status) for number, status, _ in backend.comments] == [(42, "rolled_back")]
    assert client.unused == 0 and result.finished


def test_a_database_outage_is_escalated_and_nothing_is_rolled_back():
    result, _, backend, _ = play("db_outage")
    assert backend.rollbacks == []
    assert [status for _, status, _ in backend.comments] == ["escalated"]
    assert result.finished


def test_dry_run_executes_no_write_and_records_the_calls():
    result, tools, backend, _ = play("bad_deploy", policy=DRY)
    assert backend.rollbacks == [] and backend.comments == []
    writes = ("rollback", "update_incident")
    recorded = [(call["tool"], call["dry_run"]) for call in tools.calls if call["tool"] in writes]
    assert recorded == [("rollback", True), ("update_incident", True)]
    assert result.finished


def test_read_tools_still_run_in_dry_run():
    _, _, backend, _ = play("bad_deploy", policy=DRY)
    assert backend.queries == [("error_ratio", "10m", "dev")]


def test_an_environment_that_is_not_enabled_gets_no_action_at_all():
    backend = FakeBackend()
    _, tools, backend, _ = play("bad_deploy", policy=Policy(frozenset(), dry_run=False), backend=backend)
    assert backend.rollbacks == [] and backend.comments == [] and backend.queries == []
    assert all(call["refused"] for call in tools.calls)


def test_instructions_in_the_data_cannot_make_the_agent_run_anything_outside_the_allowlist():
    """The hostile recording is a model that was talked into dangerous calls: all are refused, nothing changes."""
    result, tools, backend, _ = play("hostile")
    assert backend.rollbacks == []
    refused = {call["tool"]: call["refused"] for call in tools.calls}
    assert "not an allowed tool" in refused["delete_service"]
    reasons = [call["refused"] for call in tools.calls if call["tool"] == "rollback"]
    assert len(reasons) == 3 and all(reasons)
    assert [status for _, status, _ in backend.comments] == ["escalated"]
    assert result.finished


def test_a_second_rollback_within_the_hour_is_refused_even_when_the_model_asks_for_it():
    backend = FakeBackend(history=[NOW.replace(minute=30)])
    _, tools, backend, _ = play("bad_deploy", backend=backend)
    assert backend.rollbacks == []
    (rollback,) = [call for call in tools.calls if call["tool"] == "rollback"]
    assert "hour" in rollback["refused"]


def test_the_model_sees_why_a_call_was_refused_so_it_can_escalate():
    _, _, _, client = play("hostile")
    answers = [message for request in client.requests for message in request["messages"] if message["role"] == "user"]
    text = str(answers)
    assert "not an allowed tool" in text and "refused" in text


def test_tool_results_are_marked_as_data_not_instructions():
    _, _, _, client = play("bad_deploy")
    assert "untrusted" in client.requests[-1]["system"].lower()
    assert "untrusted" in str(client.requests[1]["messages"][-1]).lower()


def test_the_model_is_only_offered_the_allowlisted_tools():
    _, _, _, client = play("bad_deploy")
    assert {tool["name"] for tool in client.requests[0]["tools"]} == {
        "query_metrics", "list_revisions", "rollback", "update_incident"}


def test_the_alert_the_model_sees_is_the_validated_five_fields_only():
    _, _, _, client = play("bad_deploy")
    first = str(client.requests[0]["messages"][0])
    for field in ("CohortRunErrorRateHigh", "dev", "3f9a1c2b7d8e4f50", "2026-10-29T11:50:00Z", "firing"):
        assert field in first


def test_a_model_that_never_stops_is_cut_off_after_the_turn_limit():
    endless = [{"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": f"toolu_{n}", "name": "list_revisions", "input": {"service": "api"}}]}
        for n in range(MAX_TURNS + 5)]
    backend = FakeBackend()
    tools = Tools(LIVE, backend, ALERT, 42, now=lambda: NOW)
    result = run_agent(ALERT, tools, ReplayClient(endless), model="test-model")
    assert not result.finished and len(tools.calls) == MAX_TURNS


def test_a_failing_tool_is_reported_to_the_model_instead_of_crashing_the_run():
    class Broken(FakeBackend):
        def list_revisions(self, environment, service):
            raise RuntimeError("gcloud exploded with token tok-s3cr3t-xyz")

    _, tools, _, client = play("db_outage", backend=Broken())
    call = next(call for call in tools.calls if call["tool"] == "list_revisions")
    assert call["error"] and "tok-s3cr3t-xyz" not in str(client.requests)


def test_the_agent_module_never_imports_the_anthropic_sdk():
    code = "import sys, oncall.agent, oncall.tools, oncall.guardrails; assert 'anthropic' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("recording", ["bad_deploy", "db_outage", "hostile"])
def test_every_recording_ends_with_the_model_finishing(recording):
    result, *_ = play(recording)
    assert result.finished


def test_the_serving_revision_in_the_fixture_is_the_new_one():
    assert any(item["name"] == NEW and item["traffic_percent"] == 100 for item in FakeBackend().revisions)


def test_an_empty_text_block_is_not_sent_back_because_the_api_rejects_it():
    recording = [{"stop_reason": "tool_use", "content": [
        {"type": "text", "text": ""},
        {"type": "tool_use", "id": "t1", "name": "list_revisions", "input": {"service": "api"}}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}]}]
    tools = Tools(LIVE, FakeBackend(), ALERT, 42, now=lambda: NOW)
    client = ReplayClient(recording)
    run_agent(ALERT, tools, client, model="test-model")
    assistant = client.requests[1]["messages"][1]["content"]
    assert [block["type"] for block in assistant] == ["tool_use"]


def test_a_reply_cut_off_by_the_token_limit_is_not_a_finished_run():
    recording = [{"stop_reason": "max_tokens", "content": [{"type": "text", "text": "I was saying"}]}]
    tools = Tools(LIVE, FakeBackend(), ALERT, 42, now=lambda: NOW)
    result = run_agent(ALERT, tools, ReplayClient(recording), model="test-model")
    assert not result.finished
