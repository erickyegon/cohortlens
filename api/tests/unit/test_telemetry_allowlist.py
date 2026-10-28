"""Issue #21: the allowlist span processor. "Given an allowlist span processor, then attributes not on the list are
dropped." Spans are the part of telemetry most likely to carry data by accident (a database statement, a URL with a
query string, an exception message), so the allowlist drops everything it does not name, plus every event, link and
status description, before a span reaches an exporter.
"""
import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, Status, StatusCode

from app.telemetry import ALLOWED_SPAN_ATTRIBUTES, MAX_ATTRIBUTE_VALUE_LENGTH, AllowlistSpanProcessor


@pytest.fixture()
def exported():
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "cohortlens-api", "deployment.environment": "test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(AllowlistSpanProcessor(SimpleSpanProcessor(exporter)))
    tracer = provider.get_tracer("test")
    yield tracer, exporter
    provider.shutdown()


def only_span(exporter):
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    return spans[0]


def test_attributes_on_the_list_are_kept(exported):
    tracer, exporter = exported
    with tracer.start_as_current_span("cohort.run") as span:
        span.set_attribute("cohortlens.outcome", "success")
        span.set_attribute("cohortlens.rule_count", 3)
        span.set_attribute("http.response.status_code", 201)
    assert dict(only_span(exporter).attributes) == {
        "cohortlens.outcome": "success", "cohortlens.rule_count": 3, "http.response.status_code": 201}


@pytest.mark.parametrize("name, value", [
    ("db.statement", "select * from cdm.person where person_id = 7"),
    ("person_id", 7), ("cohortlens.cohort_name", "my private cohort"), ("http.url", "http://x/cohorts?token=abc"),
    ("http.target", "/cohorts/6f1c1f0e"), ("user.email", "a@b.com"), ("db.parameters", "[1308216]"),
    ("exception.message", "relation cdm.person does not exist"), ("anything.else", "x"),
])
def test_attributes_not_on_the_list_are_dropped(exported, name, value):
    tracer, exporter = exported
    with tracer.start_as_current_span("cohort.run") as span:
        span.set_attribute(name, value)
        span.set_attribute("cohortlens.outcome", "failed")
    assert dict(only_span(exporter).attributes) == {"cohortlens.outcome": "failed"}


def test_an_allowed_key_with_a_long_string_value_is_dropped_too(exported):
    """A key on the list must not become a pipe for free text."""
    tracer, exporter = exported
    with tracer.start_as_current_span("cohort.run") as span:
        span.set_attribute("cohortlens.outcome", "x" * (MAX_ATTRIBUTE_VALUE_LENGTH + 1))
        span.set_attribute("cohortlens.revision", "a5b89d7")
    assert dict(only_span(exporter).attributes) == {"cohortlens.revision": "a5b89d7"}


def test_events_are_dropped_so_an_exception_message_cannot_leave(exported):
    tracer, exporter = exported
    with tracer.start_as_current_span("cohort.run") as span:
        span.record_exception(ValueError("select * from cdm.person where birth = '1960-05-05'"))
        span.add_event("note", {"text": "P1 was in the cohort"})
    assert only_span(exporter).events == ()


def test_the_status_keeps_its_code_but_never_its_description(exported):
    tracer, exporter = exported
    with tracer.start_as_current_span("cohort.run") as span:
        span.set_status(Status(StatusCode.ERROR, "connection to postgresql://u:p@host failed"))
    status = only_span(exporter).status
    assert status.status_code == StatusCode.ERROR and status.description is None


def test_links_are_dropped(exported):
    tracer, exporter = exported
    with tracer.start_as_current_span("other") as other:
        context = other.get_span_context()
    with tracer.start_as_current_span("cohort.run", links=[Link(context, {"x": "y"})]):
        pass
    assert only_span_named(exporter, "cohort.run").links == ()


def only_span_named(exporter, name):
    return next(span for span in exporter.get_finished_spans() if span.name == name)


def test_names_ids_parentage_timing_and_the_resource_pass_through(exported):
    tracer, exporter = exported
    with tracer.start_as_current_span("cohort.run") as parent:
        with tracer.start_as_current_span("compile"):
            pass
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["compile"].parent.span_id == parent.get_span_context().span_id
    assert spans["compile"].context.trace_id == spans["cohort.run"].context.trace_id
    assert spans["cohort.run"].end_time >= spans["cohort.run"].start_time
    assert spans["cohort.run"].resource.attributes["service.name"] == "cohortlens-api"


def test_the_allowlist_is_small_and_has_no_data_like_names():
    assert isinstance(ALLOWED_SPAN_ATTRIBUTES, frozenset) and 0 < len(ALLOWED_SPAN_ATTRIBUTES) <= 12
    data_like = ("person", "name", "query", "sql", "statement", "url", "param", "target", "message", "email", "birth")
    for forbidden in data_like:
        assert not [key for key in ALLOWED_SPAN_ATTRIBUTES if forbidden in key], forbidden


def test_force_flush_and_shutdown_reach_the_exporter(exported):
    tracer, exporter = exported
    with tracer.start_as_current_span("cohort.run"):
        pass
    assert len(exporter.get_finished_spans()) == 1
