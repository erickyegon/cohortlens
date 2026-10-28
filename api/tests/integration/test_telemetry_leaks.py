"""Issue #21: the leak test. "Given the truth fixture run through the API, then no exported span or log contains a
fixture person id, birth date or token pattern."

The run goes through the real API and a real database holding the truth fixture, with deliberately hostile input
(a cohort named with an email and a bearer token, a failing run whose exception message carries an email and a
connection string, a timeout, validation errors, a concept search). Everything that would be exported is then collected
(every span with its attributes, events, links and status, every JSON log line and exported log record, every metric
data point and the resource) and searched.
"""
import copy
import io
import json
import logging
import pathlib
import re

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, text

from app import runs, telemetry
from app.cohort_engine.attrition import CohortTimeout

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))
HOSTILE_EMAIL = "patient.zero@example.com"
HOSTILE_TOKEN = "Bearer " + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
HOSTILE_CONNECTION = "postgres" + "ql://admin:hunter2pw@db.internal:5432/cohortlens"
PERSON_ROWS = """select person_id, person_source_value, birth_datetime, year_of_birth,
month_of_birth, day_of_birth from cdm.person order by person_id"""
HOSTILE_QUERY = "select * from cdm.person where email='patient.zero@example.com'"


@pytest.fixture()
def exported(client):
    spans, reader, logs, stream = InMemorySpanExporter(), InMemoryMetricReader(), InMemoryLogExporter(), io.StringIO()
    telemetry.configure(span_exporter=spans, metric_readers=[reader], log_exporter=logs, log_stream=stream)
    yield spans, reader, logs, stream
    telemetry.configure_from_environment()


def everything_exported(spans, reader, logs, stream) -> str:
    """One text blob with every field of everything that would leave the process."""
    parts = []
    for span in spans.get_finished_spans():
        parts.append(span.to_json())
    for record in logs.get_finished_logs():
        parts.append(record.log_record.to_json())
    parts.append(stream.getvalue())
    data = reader.get_metrics_data()
    if data is not None:
        parts.append(data.to_json())
    return "\n".join(parts)


def fixture_identifiers(database_url):
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as conn:
        rows = conn.execute(text(PERSON_ROWS)).all()
    engine.dispose()
    assert len(rows) >= 20
    return rows


def test_nothing_from_the_truth_fixture_or_the_hostile_input_leaves_in_any_span_log_or_metric(
        client, exported, api_database_url, monkeypatch):
    spans, reader, logs, stream = exported

    # a cohort whose name and label are hostile, run successfully
    hostile = copy.deepcopy(REFERENCE)
    hostile["name"] = f"{HOSTILE_EMAIL} {HOSTILE_TOKEN}"
    hostile["rules"][0]["label"] = f"label {HOSTILE_CONNECTION}"
    created = client.post("/cohorts", json=hostile)
    assert created.status_code == 201, created.text
    cohort_id = created.json()["id"]
    assert client.post(f"/cohorts/{cohort_id}/runs").status_code == 201

    # a run that fails with an exception message full of secrets, and one that times out
    def boom(*_args, **_kwargs):
        raise RuntimeError(" ".join([HOSTILE_QUERY, HOSTILE_CONNECTION, HOSTILE_TOKEN]))

    monkeypatch.setattr(runs, "execute_run", boom)
    assert client.post(f"/cohorts/{cohort_id}/runs").status_code == 500

    def slow(*_args, **_kwargs):
        raise CohortTimeout("exceeded 10000 ms")

    monkeypatch.setattr(runs, "execute_run", slow)
    assert client.post(f"/cohorts/{cohort_id}/runs").status_code == 504
    monkeypatch.undo()

    # hostile text sent straight through the logger (this is what actually exercises the redaction), with extras too
    api_log = logging.getLogger("cohortlens.api")
    api_log.error("failed for %s using %s %s", HOSTILE_EMAIL, HOSTILE_CONNECTION, HOSTILE_TOKEN,
                  extra={"person": "P1", "email": HOSTILE_EMAIL})
    api_log.info("query was %s", HOSTILE_QUERY)

    # validation errors, a missing cohort, listing, reading, concept search with hostile text
    assert client.post("/cohorts", json={"name": HOSTILE_EMAIL, "index_event": {"domain": "nope"}}).status_code == 422
    assert client.get("/cohorts/6f1c1f0e-5b1c-4d55-9a0e-0a5f4c2b9a11").status_code == 404
    client.get("/cohorts")
    client.get(f"/cohorts/{cohort_id}")
    client.get(f"/cohorts/{cohort_id}/runs")
    client.get(f"/concepts?q={HOSTILE_EMAIL}")

    blob = everything_exported(spans, reader, logs, stream)
    assert blob.count("cohort.run") >= 3 and "cohortlens_cohort_runs_total" in blob  # the blob really holds telemetry

    # the hostile input
    for needle in (HOSTILE_EMAIL, HOSTILE_TOKEN.split(" ", 1)[1], "hunter2pw", "db.internal", "label postgres"):
        assert needle not in blob, needle

    # the truth fixture: no person id key, no source value, no birth date in any form
    assert "person_id" not in blob and "person_source_value" not in blob
    assert not re.search(r"person[_ ]?id", blob, re.IGNORECASE)
    assert blob.count("[REDACTED]") >= 3  # the hostile log lines really were masked, not just absent
    for _person_id, source, birth, year, month, day in fixture_identifiers(api_database_url):
        assert not re.search(rf"(?<![A-Za-z0-9]){re.escape(source)}(?![A-Za-z0-9])", blob), source
        assert birth.strftime("%Y-%m-%d") not in blob and birth.strftime("%Y-%m-%d %H:%M:%S") not in blob
        assert f"{year}-{month:02d}-{day:02d}" not in blob

    # and nothing in it matches a token pattern at all: redacting the whole blob changes nothing
    assert telemetry.redact(blob) == blob


def test_the_allowlist_would_catch_a_new_leak_even_if_the_code_added_one(client, exported):
    """If someone adds a risky attribute to a span later, it still never leaves."""
    spans, _, _, _ = exported
    with telemetry.get_tracer().start_as_current_span("cohort.run") as span:
        span.set_attribute("db.statement", "select * from cdm.person where person_source_value = 'P1'")
        span.set_attribute("cohortlens.person", "P1")
    exported_span = spans.get_finished_spans()[-1]
    assert "select" not in exported_span.to_json() and "P1" not in exported_span.to_json()
