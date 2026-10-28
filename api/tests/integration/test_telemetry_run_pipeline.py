"""Issue #20: a cohort run through the real API and database emits the spans, metrics and log lines the issue asks for,
all under one trace id that is also the X-Trace-Id of the response.

"Given a cohort run, then spans cohort.run, validate, compile, execute, baseline, suppress are emitted with allowlisted
attributes." "Given a run, then log lines carry the same trace_id as the spans." "Given any response, then X-Trace-Id is
present."
"""
import copy
import io
import json
import logging
import pathlib

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import runs, telemetry
from app.cohort_engine.attrition import CohortTimeout

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))
RUN_SPANS = {"cohort.run", "validate", "compile", "execute", "baseline", "suppress"}


class Wired:
    def __init__(self):
        self.spans, self.reader, self.logs, self.stream = (InMemorySpanExporter(), InMemoryMetricReader(),
                                                           InMemoryLogExporter(), io.StringIO())

    def reset_captured(self):
        self.spans.clear()
        self.stream.truncate(0)
        self.stream.seek(0)

    def lines(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines() if line.strip()]

    def metric_points(self, name):
        data = self.reader.get_metrics_data()
        if data is None:
            return []
        return [point for resource in data.resource_metrics for scope in resource.scope_metrics
                for metric in scope.metrics if metric.name == name for point in metric.data.data_points]


@pytest.fixture()
def wired(client):
    captured = Wired()
    telemetry.configure(span_exporter=captured.spans, metric_readers=[captured.reader], log_exporter=captured.logs,
                        log_stream=captured.stream)
    yield captured
    telemetry.configure_from_environment()


def save(client):
    response = client.post("/cohorts", json=copy.deepcopy(REFERENCE))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def by_name(spans):
    return {span.name: span for span in spans}


def trace_hex(span):
    return format(span.context.trace_id, "032x")


def test_a_run_emits_all_six_spans_as_children_of_one_http_request_span(client, wired):
    cohort_id = save(client)
    wired.reset_captured()
    response = client.post(f"/cohorts/{cohort_id}/runs")
    assert response.status_code == 201
    spans = by_name(wired.spans.get_finished_spans())
    assert set(spans) == RUN_SPANS | {"http.request"}
    root = spans["http.request"]
    assert root.parent is None
    assert spans["cohort.run"].parent.span_id == root.context.span_id
    for name in ("validate", "compile", "execute", "baseline", "suppress"):
        assert spans[name].parent.span_id == spans["cohort.run"].context.span_id, name
    assert len({trace_hex(span) for span in spans.values()}) == 1


def test_the_response_header_the_body_and_the_stored_run_all_carry_the_root_spans_trace_id(client, wired):
    cohort_id = save(client)
    wired.reset_captured()
    response = client.post(f"/cohorts/{cohort_id}/runs")
    root = by_name(wired.spans.get_finished_spans())["http.request"]
    assert response.headers["X-Trace-Id"] == trace_hex(root)
    assert response.json()["trace_id"] == trace_hex(root)
    assert client.get(f"/cohorts/{cohort_id}/runs").json()[0]["trace_id"] == trace_hex(root)


def test_every_exported_attribute_is_on_the_allowlist(client, wired):
    cohort_id = save(client)
    client.post(f"/cohorts/{cohort_id}/runs")
    client.get("/cohorts")
    client.get("/concepts?q=hypertension")
    attributes = {key for span in wired.spans.get_finished_spans() for key in span.attributes}
    assert attributes and attributes <= telemetry.ALLOWED_SPAN_ATTRIBUTES


def test_the_run_span_records_the_outcome_and_the_shape_not_the_content(client, wired):
    cohort_id = save(client)
    wired.reset_captured()
    client.post(f"/cohorts/{cohort_id}/runs")
    run_span = by_name(wired.spans.get_finished_spans())["cohort.run"]
    assert dict(run_span.attributes) == {"cohortlens.outcome": "success", "cohortlens.rule_count": 3,
                                         "cohortlens.step_count": 6}


def test_a_log_line_for_the_run_carries_the_same_trace_id_as_the_spans(client, wired):
    cohort_id = save(client)
    wired.reset_captured()
    client.post(f"/cohorts/{cohort_id}/runs")
    root = by_name(wired.spans.get_finished_spans())["http.request"]
    finished = [line for line in wired.lines() if "run finished" in line["message"]]
    assert len(finished) == 1 and finished[0]["trace_id"] == trace_hex(root)
    assert "outcome=success" in finished[0]["message"]
    exported = [record for record in wired.logs.get_finished_logs() if "run finished" in str(record.log_record.body)]
    assert len(exported) == 1 and format(exported[0].log_record.trace_id, "032x") == trace_hex(root)


def test_a_run_is_counted_and_timed_with_outcome_environment_and_revision(client, wired, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "test")
    telemetry.configure(span_exporter=wired.spans, metric_readers=[wired.reader], log_exporter=wired.logs,
                        log_stream=wired.stream)
    client.post(f"/cohorts/{save(client)}/runs")
    (counter,) = wired.metric_points("cohortlens_cohort_runs_total")
    assert counter.value == 1 and set(counter.attributes) == {"outcome", "environment", "revision"}
    assert counter.attributes["outcome"] == "success" and counter.attributes["environment"] == "test"
    (histogram,) = wired.metric_points("cohortlens_cohort_run_duration_seconds")
    assert histogram.count == 1 and 0 < histogram.sum < 30


def test_a_timeout_and_a_failure_are_counted_with_their_own_outcome(client, wired, monkeypatch):
    cohort_id = save(client)

    def slow(*_args, **_kwargs):
        raise CohortTimeout("exceeded 10000 ms")

    monkeypatch.setattr(runs, "execute_run", slow)
    assert client.post(f"/cohorts/{cohort_id}/runs").status_code == 504

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom for a@b.com")

    monkeypatch.setattr(runs, "execute_run", boom)
    assert client.post(f"/cohorts/{cohort_id}/runs").status_code == 500
    outcomes = sorted(point.attributes["outcome"] for point in wired.metric_points("cohortlens_cohort_runs_total"))
    assert outcomes == ["failed", "timeout"]


def test_a_failed_run_marks_the_span_as_an_error_and_logs_the_exception_class_with_the_trace_id(
        client, wired, monkeypatch):
    cohort_id = save(client)
    wired.reset_captured()

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom for a@b.com")

    monkeypatch.setattr(runs, "execute_run", boom)
    response = client.post(f"/cohorts/{cohort_id}/runs")
    spans = by_name(wired.spans.get_finished_spans())
    assert spans["cohort.run"].status.status_code.name == "ERROR" and spans["cohort.run"].status.description is None
    assert dict(spans["cohort.run"].attributes)["cohortlens.outcome"] == "failed"
    failure = [line for line in wired.lines() if "exception=RuntimeError" in line["message"]]
    assert failure and failure[0]["trace_id"] == trace_hex(spans["http.request"]) == response.json()["trace_id"]
    assert "a@b.com" not in wired.stream.getvalue()


def test_every_response_carries_an_x_trace_id_that_is_a_real_trace_id(client, wired):
    for response in (client.get("/healthz"), client.get("/version"), client.get("/cohorts"), client.get("/nowhere"),
                     client.post("/cohorts/not-a-uuid/runs")):
        value = response.headers["X-Trace-Id"]
        assert len(value) == 32 and int(value, 16) != 0
    root_ids = {trace_hex(span) for span in wired.spans.get_finished_spans() if span.name == "http.request"}
    assert len(root_ids) >= 5  # every request is its own trace


def test_a_run_that_never_started_is_not_counted(client, wired):
    assert client.post("/cohorts/6f1c1f0e-5b1c-4d55-9a0e-0a5f4c2b9a11/runs").status_code == 404
    assert wired.metric_points("cohortlens_cohort_runs_total") == []


def test_the_telemetry_does_not_break_a_run_when_the_exporter_fails(client, wired, monkeypatch):
    class Broken:
        def export(self, *_args, **_kwargs):
            raise RuntimeError("collector down")

        def shutdown(self):
            pass

        def force_flush(self, *_args, **_kwargs):
            return True

    telemetry.configure(span_exporter=Broken(), metric_readers=[wired.reader], log_stream=wired.stream)
    response = client.post(f"/cohorts/{save(client)}/runs")
    assert response.status_code == 201 and response.json()["status"] == "success"
    logging.getLogger("cohortlens.api").info("still logging")
