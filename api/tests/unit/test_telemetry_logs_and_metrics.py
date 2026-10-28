"""Issue #20: JSON logs carrying the trace id, and the two run metrics.

"Given a run, then log lines carry the same trace_id as the spans." "Given a cohort run, then
cohortlens_cohort_runs_total and cohortlens_cohort_run_duration_seconds are recorded with outcome, environment,
revision." Everything goes through `telemetry.configure`, which swaps in in-memory exporters for the test.
"""
import io
import json
import logging

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import telemetry

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture()
def wired(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "test")
    monkeypatch.setenv("SERVICE_VERSION", SHA)
    spans, reader, logs, stream = InMemorySpanExporter(), InMemoryMetricReader(), InMemoryLogExporter(), io.StringIO()
    telemetry.configure(span_exporter=spans, metric_readers=[reader], log_exporter=logs, log_stream=stream)
    yield spans, reader, logs, stream
    telemetry.shutdown()


def log_lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# ---- logs ---------------------------------------------------------------------------------------------------------

def test_a_log_line_inside_a_span_carries_that_spans_trace_and_span_id(wired):
    spans, _, _, stream = wired
    with telemetry.get_tracer().start_as_current_span("cohort.run") as span:
        logging.getLogger("cohortlens.api").info("run finished outcome=success")
        context = span.get_span_context()
    (line,) = log_lines(stream)
    assert line["trace_id"] == format(context.trace_id, "032x") and line["span_id"] == format(context.span_id, "016x")
    assert spans.get_finished_spans()[0].context.trace_id == context.trace_id  # the same id the span was exported with


def test_the_json_line_has_exactly_the_documented_fields(wired):
    _, _, _, stream = wired
    logging.getLogger("cohortlens.api").warning("slow run")
    (line,) = log_lines(stream)
    assert set(line) == {"timestamp", "level", "logger", "message", "service", "environment", "revision", "trace_id",
                         "span_id"}
    assert line["level"] == "WARNING" and line["logger"] == "cohortlens.api" and line["message"] == "slow run"
    assert line["service"] == "cohortlens-api" and line["environment"] == "test" and line["revision"] == SHA
    assert line["trace_id"] == "" and line["span_id"] == ""  # outside any span


def test_an_exception_is_logged_as_its_class_only_never_its_message_or_traceback(wired):
    _, _, _, stream = wired
    try:
        raise ValueError("select * from cdm.person where birth = '1960-05-05'")
    except ValueError:
        logging.getLogger("cohortlens.api").exception("request failed")
    (line,) = log_lines(stream)
    assert line["exception"] == "ValueError"
    assert "1960-05-05" not in stream.getvalue() and "Traceback" not in stream.getvalue()


def test_the_same_trace_id_reaches_the_exported_log_record(wired):
    _, _, logs, _ = wired
    with telemetry.get_tracer().start_as_current_span("cohort.run") as span:
        logging.getLogger("cohortlens.api").info("run finished")
        trace_id = span.get_span_context().trace_id
    records = logs.get_finished_logs()
    assert len(records) == 1 and records[0].log_record.trace_id == trace_id


def test_secrets_are_masked_in_the_exported_log_record_too(wired):
    _, _, logs, stream = wired
    logging.getLogger("cohortlens.api").info("contact a@b.com")
    assert "a@b.com" not in stream.getvalue()
    assert "a@b.com" not in str(logs.get_finished_logs()[0].log_record.body)


def test_reconfiguring_replaces_the_handlers_instead_of_duplicating_the_lines(wired):
    _, _, _, stream = wired
    telemetry.configure(log_stream=stream)
    logging.getLogger("cohortlens.api").info("once")
    assert len(log_lines(stream)) == 1


# ---- metrics ------------------------------------------------------------------------------------------------------

def points(reader, name):
    data = reader.get_metrics_data()
    found = [metric for resource in data.resource_metrics for scope in resource.scope_metrics
             for metric in scope.metrics if metric.name == name]
    assert len(found) == 1, name
    return found[0].data.data_points


@pytest.mark.parametrize("outcome", ["success", "failed", "timeout"])
def test_a_run_is_counted_with_outcome_environment_and_revision(wired, outcome):
    _, reader, _, _ = wired
    telemetry.record_run(outcome, 0.42)
    (point,) = points(reader, "cohortlens_cohort_runs_total")
    assert point.value == 1
    assert dict(point.attributes) == {"outcome": outcome, "environment": "test", "revision": SHA}


def test_the_duration_histogram_records_seconds_with_the_same_attributes(wired):
    _, reader, _, _ = wired
    telemetry.record_run("success", 0.42)
    telemetry.record_run("success", 2.5)
    (point,) = points(reader, "cohortlens_cohort_run_duration_seconds")
    assert point.count == 2 and point.sum == pytest.approx(2.92)
    assert dict(point.attributes) == {"outcome": "success", "environment": "test", "revision": SHA}


def test_the_histogram_has_boundaries_around_the_three_second_alert_threshold(wired):
    _, reader, _, _ = wired
    telemetry.record_run("success", 1.0)
    (point,) = points(reader, "cohortlens_cohort_run_duration_seconds")
    boundaries = list(point.explicit_bounds)
    assert 3 in boundaries and 10 in boundaries and min(boundaries) <= 0.1


def test_an_unknown_outcome_is_refused_so_label_values_stay_a_closed_set(wired):
    with pytest.raises(ValueError):
        telemetry.record_run("a cohort named Alice", 1.0)


def test_nothing_is_exported_without_a_configured_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    handles = telemetry.configure_from_environment()
    assert handles.exporting is False
    telemetry.shutdown()
