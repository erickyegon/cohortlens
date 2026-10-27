"""Issue #11, review round 1: failure behaviour shared by every route (docs/architecture.md, API section).

A failure must leave a log line that carries the trace id the user was given (FR-12) without any message text or
data; only genuine connection-level database failures are reported as "unavailable"; unknown routes use the standard
error body; and the request-size limit holds for chunked bodies and exactly at the boundary.
"""
import json
import logging

import pytest
from sqlalchemy.exc import OperationalError

VALID = json.dumps({"name": "x", "index_event": {"domain": "drug", "concept_ids": [1308216]}}).encode()
LIMIT = 256 * 1024


class FakeDriverError(Exception):
    """Stands in for psycopg's error: only the SQLSTATE matters to the handler."""

    def __init__(self, sqlstate):
        super().__init__("SECRET-DRIVER-DETAIL host=10.1.2.3 password=hunter2")
        self.sqlstate = sqlstate


def fail_with(monkeypatch, error):
    import app.cohorts as cohorts

    def boom():
        raise error

    monkeypatch.setattr(cohorts, "_list", boom)


# ---- the request-size limit ---------------------------------------------------------------------------------------

def padded(size):
    return VALID + b" " * (size - len(VALID))  # trailing whitespace keeps it valid JSON


def test_a_body_of_exactly_the_limit_is_accepted_and_one_byte_more_is_rejected(client):
    headers = {"content-type": "application/json"}
    assert client.post("/cohorts", content=padded(LIMIT), headers=headers).status_code == 201
    assert client.post("/cohorts", content=padded(LIMIT + 1), headers=headers).status_code == 413


def test_a_chunked_body_without_content_length_is_cut_off_at_the_limit(client, api_env):
    def chunks():
        for _ in range(80):
            yield b" " * 65_536  # 5 MB in total, sent with Transfer-Encoding: chunked

    response = client.post("/cohorts", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert client.get("/cohorts").json() == []


# ---- failures leave a log line with the trace id and nothing else --------------------------------------------------

def test_an_unexpected_error_is_logged_with_its_class_and_the_trace_id_but_not_its_message(client, monkeypatch, caplog):
    fail_with(monkeypatch, ValueError("SECRET-MESSAGE-123 patient 42"))
    with caplog.at_level(logging.ERROR):
        response = client.get("/cohorts")
    assert response.status_code == 500
    trace_id = response.headers["x-trace-id"]
    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("ValueError" in line and trace_id in line for line in lines), lines
    assert "SECRET" not in caplog.text and "patient" not in caplog.text


def test_a_database_failure_is_logged_with_its_class_and_the_trace_id_only(client, monkeypatch, caplog):
    fail_with(monkeypatch, OperationalError("select 1", {}, FakeDriverError(None)))
    with caplog.at_level(logging.ERROR):
        response = client.get("/cohorts")
    assert response.status_code == 503
    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("OperationalError" in line and response.headers["x-trace-id"] in line for line in lines), lines
    assert "SECRET" not in caplog.text and "hunter2" not in caplog.text and "10.1.2.3" not in caplog.text


# ---- which database errors mean "unavailable" ----------------------------------------------------------------------

@pytest.mark.parametrize("sqlstate", [None, "08006", "08001", "57P01", "53300"])
def test_connection_level_failures_are_reported_as_the_database_being_unavailable(client, monkeypatch, sqlstate):
    fail_with(monkeypatch, OperationalError("select 1", {}, FakeDriverError(sqlstate)))
    response = client.get("/cohorts")
    assert response.status_code == 503 and response.json()["error"].startswith("The database is unavailable")
    assert "SECRET" not in response.text and "hunter2" not in response.text


@pytest.mark.parametrize("sqlstate", ["57014", "55P03"])
def test_a_statement_or_lock_timeout_is_a_503_that_says_it_took_too_long(client, monkeypatch, sqlstate):
    fail_with(monkeypatch, OperationalError("select 1", {}, FakeDriverError(sqlstate)))
    response = client.get("/cohorts")
    assert response.status_code == 503
    assert "took too long" in response.json()["error"] and "unavailable" not in response.json()["error"]


@pytest.mark.parametrize("sqlstate", ["42P01", "22P02", "40001", "XX000"])
def test_a_statement_level_database_error_is_a_plain_500_not_unavailable(client, monkeypatch, sqlstate):
    fail_with(monkeypatch, OperationalError("select 1", {}, FakeDriverError(sqlstate)))
    response = client.get("/cohorts")
    assert response.status_code == 500 and response.json()["error"] == "Internal Server Error"
    assert response.json()["trace_id"] == response.headers["x-trace-id"]


# ---- unknown routes and methods use the standard error body --------------------------------------------------------

def test_an_unknown_route_has_the_standard_error_body_with_the_trace_id(client):
    response = client.get("/nothing-here")
    assert response.status_code == 404
    assert response.json() == {"error": "Not Found", "trace_id": response.headers["x-trace-id"]}


def test_a_method_that_is_not_allowed_has_the_standard_error_body_too(client):
    response = client.delete("/cohorts")
    assert response.status_code == 405
    assert response.json()["error"] == "Method Not Allowed"
    assert response.json()["trace_id"] == response.headers["x-trace-id"]
