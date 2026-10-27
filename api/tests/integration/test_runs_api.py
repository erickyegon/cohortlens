"""Issue #12: run endpoint and run history (docs/spec.md FR-6, FR-8, FR-12, FR-14; US-4, US-7, US-9).

Served at /cohorts/{id}/runs (public path /api/cohorts/{id}/runs). Expected counts come from the hand-derived truth
fixture: the reference cohort has 22, 21, 17, 15, 12 and 10 persons after its six steps (the final 10 is shown as
"<11"); the index-only cohort has 22, 22 and 21. Only suppressed values may be returned or stored.
"""
import copy
import json
import logging
import pathlib
import time
import uuid

from sqlalchemy import create_engine, func, select, text

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))
INDEX_ONLY = {"name": "Index only", "index_event": {"domain": "drug", "concept_ids": [1308216]}}
KEYS = ["index", "demographics", "observation", "rule_1", "rule_2", "rule_3"]
DEAD = "postgresql://nobody:nothing@127.0.0.1:1/none"


def save(client, raw=None):
    response = client.post("/cohorts", json=copy.deepcopy(raw or REFERENCE))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def run(client, cohort_id):
    return client.post(f"/cohorts/{cohort_id}/runs")


def rows(url, sql, **params):
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as conn:
        result = conn.execute(text(sql), params).mappings().all()
    engine.dispose()
    return result


# ---- Given a saved cohort, when I POST /cohorts/{id}/runs, then the final count and per-step counts come back ------

def test_running_the_reference_cohort_returns_every_step_count_in_order_with_small_counts_masked(client):
    started = time.perf_counter()
    response = run(client, save(client))
    assert time.perf_counter() - started < 3  # NFR-1 (the demo dataset is checked separately on the seed)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    assert [s["key"] for s in body["step_counts"]] == KEYS
    assert [s["persons"] for s in body["step_counts"]] == [22, 21, 17, 15, 12, "<11"]
    assert body["step_counts"][3]["label"] == "No ACE inhibitor in the prior 365 days"
    assert body["final_count"] == "<11"


def test_a_larger_cohort_shows_its_exact_final_count(client):
    body = run(client, save(client, INDEX_ONLY)).json()
    assert [s["persons"] for s in body["step_counts"]] == [22, 22, 21]
    assert body["final_count"] == 21


def test_the_response_includes_the_baseline_with_the_same_suppression(client):
    baseline = run(client, save(client)).json()["baseline"]
    assert len(baseline["age_sex"]) == 24
    assert {c["persons"] for c in baseline["age_sex"]} == {0, "<11"}
    assert baseline["comorbidities"] == []  # 9, 1 and 1 patients: all under 11 and left out
    assert baseline["comorbidity_note"] == "Conditions with fewer than 11 patients are not shown."
    index_only = run(client, save(client, INDEX_ONLY)).json()["baseline"]
    assert [(c["concept_id"], c["persons"]) for c in index_only["comorbidities"]] == [(320128, 17)]


def test_a_response_holds_aggregates_and_run_metadata_only(client):
    body = run(client, save(client, INDEX_ONLY)).json()
    assert set(body) == {"id", "cohort_id", "status", "step_counts", "final_count", "baseline", "duration_ms",
                         "trace_id", "service_version", "created_at"}
    assert all(set(s) == {"key", "label", "persons"} for s in body["step_counts"])


# ---- Given a successful or failed run, then it is stored with status, step counts, duration, trace id, version -----

def test_a_successful_run_is_stored_with_everything_the_history_needs(client, api_env, monkeypatch):
    import app.runs as runs

    monkeypatch.setattr(runs, "SERVICE_VERSION", "abc1234")
    cohort_id = save(client, INDEX_ONLY)
    response = run(client, cohort_id)
    stored = rows(api_env, "select * from app.cohort_run")
    assert len(stored) == 1
    row = stored[0]
    assert str(row["id"]) == response.json()["id"] and str(row["cohort_id"]) == cohort_id
    assert row["status"] == "success" and row["final_count"] == 21 and row["duration_ms"] >= 0
    assert row["trace_id"] == response.headers["x-trace-id"] == response.json()["trace_id"]
    assert row["service_version"] == "abc1234" == response.json()["service_version"]
    assert [s["persons"] for s in row["step_counts"]] == [22, 22, 21]


def test_only_suppressed_counts_reach_the_stored_run(client, api_env):
    run(client, save(client))
    row = rows(api_env, "select * from app.cohort_run")[0]
    assert [s["persons"] for s in row["step_counts"]] == [22, 21, 17, 15, 12, "<11"]
    assert row["final_count"] is None  # "<11" is not an integer, and the exact 10 must not be stored
    assert '"persons": 10' not in json.dumps(row["step_counts"])


def test_a_run_that_fails_for_an_unexpected_reason_is_recorded_and_answered_with_a_reference(client, api_env,
                                                                                              monkeypatch, caplog):
    import app.runs as runs

    def boom(*args, **kwargs):
        raise RuntimeError("SECRET-DETAIL patient 42 select * from person")

    monkeypatch.setattr(runs, "execute_run", boom)
    cohort_id = save(client)
    with caplog.at_level(logging.ERROR):
        response = run(client, cohort_id)
    trace_id = response.headers["x-trace-id"]
    assert response.status_code == 500
    assert response.json()["error"].startswith("Something went wrong") and response.json()["trace_id"] == trace_id
    assert "SECRET" not in response.text and "SECRET" not in caplog.text
    assert trace_id in caplog.text and "RuntimeError" in caplog.text
    row = rows(api_env, "select * from app.cohort_run")[0]
    assert row["status"] == "failed" and row["trace_id"] == trace_id and row["step_counts"] is None
    assert row["final_count"] is None


# ---- Given the database is unreachable, then the response carries X-Trace-Id and the run is recorded as failed -----

def test_when_the_cdm_database_is_unreachable_the_run_is_a_503_with_a_reference_and_is_recorded_as_failed(
        client, api_env, monkeypatch):
    from app.db import session

    cohort_id = save(client)
    monkeypatch.setenv("DATABASE_URL_READER", DEAD)
    session.reader_engine.cache_clear()
    response = run(client, cohort_id)
    assert response.status_code == 503
    trace_id = response.headers["x-trace-id"]
    assert len(trace_id) == 32 and response.json()["trace_id"] == trace_id
    assert "nothing" not in response.text and "127.0.0.1" not in response.text
    row = rows(api_env, "select * from app.cohort_run")[0]
    assert row["status"] == "failed" and row["trace_id"] == trace_id


def test_when_the_app_database_is_down_the_run_is_a_503_with_a_reference_and_nothing_is_recorded(
        client, api_env, monkeypatch):
    from app.db import session

    cohort_id = save(client)
    monkeypatch.setenv("DATABASE_URL_APP", DEAD)
    session.app_engine.cache_clear()
    response = run(client, cohort_id)
    assert response.status_code == 503 and response.json()["trace_id"] == response.headers["x-trace-id"]
    monkeypatch.setenv("DATABASE_URL_APP", api_env)
    session.app_engine.cache_clear()
    assert rows(api_env, "select count(*) as n from app.cohort_run")[0]["n"] == 0


# ---- timeouts: a clear message, and the run is recorded as a timeout -----------------------------------------------

def test_a_run_over_the_statement_timeout_is_cancelled_and_reported_with_a_clear_message(client, api_env, monkeypatch):
    import app.runs as runs

    import app.cohort_engine.attrition as attrition

    cohort_id = save(client)
    monkeypatch.setattr(attrition, "count_statement", lambda plan: select(func.pg_sleep(5)))  # a genuinely slow query
    monkeypatch.setattr(runs, "RUN_TIMEOUT_MS", 300)
    response = run(client, cohort_id)
    assert response.status_code == 504
    body = response.json()
    assert body["trace_id"] == response.headers["x-trace-id"]
    assert "longer than 0.3 seconds" in body["error"] and "cancelled" in body["error"]
    row = rows(api_env, "select * from app.cohort_run")[0]
    assert row["status"] == "timeout" and row["trace_id"] == body["trace_id"] and row["duration_ms"] >= 250
    assert body["run_id"] == str(row["id"]) and body["status"] == "timeout"


def test_the_timeout_message_names_the_ten_second_limit_and_says_what_to_try(client, monkeypatch):
    import app.runs as runs

    from app.cohort_engine.attrition import CohortTimeout

    def too_slow(*args, **kwargs):
        raise CohortTimeout("exceeded")

    monkeypatch.setattr(runs, "execute_run", too_slow)
    error = run(client, save(client)).json()["error"]
    assert "longer than 10 seconds" in error and "cancelled" in error
    assert "narrower index event" in error and "fewer criteria" in error
    assert "exceeded" not in error  # the internal message is not shown


def test_a_timeout_is_logged_with_the_trace_id_and_no_data(client, monkeypatch, caplog):
    import app.runs as runs

    from app.cohort_engine.attrition import CohortTimeout

    monkeypatch.setattr(runs, "execute_run", lambda *a, **k: (_ for _ in ()).throw(CohortTimeout("exceeded")))
    with caplog.at_level(logging.WARNING):
        response = run(client, save(client))
    assert response.headers["x-trace-id"] in caplog.text and "CohortTimeout" in caplog.text


# ---- other refusals ------------------------------------------------------------------------------------------------

def test_running_an_unknown_or_malformed_cohort_is_a_404_and_records_nothing(client, api_env):
    for cohort_id in (str(uuid.uuid4()), "not-a-uuid", "1"):
        response = run(client, cohort_id)
        assert response.status_code == 404 and response.json()["error"] == "Cohort not found"
    assert rows(api_env, "select count(*) as n from app.cohort_run")[0]["n"] == 0


def test_a_stored_definition_that_no_longer_validates_is_a_422_and_a_failed_run(client, api_env):
    cohort_id = save(client)
    engine = create_engine(api_env.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as conn:  # simulate a rule change after the cohort was saved
        conn.execute(text("update app.cohort_definition set definition = definition || '{\"rules\": 5}'"))
    engine.dispose()
    response = run(client, cohort_id)
    assert response.status_code == 422
    assert response.json()["errors"] and response.json()["trace_id"] == response.headers["x-trace-id"]
    assert rows(api_env, "select status from app.cohort_run")[0]["status"] == "failed"


# ---- Given three runs, then GET /cohorts/{id}/runs lists them newest first -----------------------------------------

def test_three_runs_are_listed_newest_first(client):
    cohort_id = save(client, INDEX_ONLY)
    created = [run(client, cohort_id).json()["id"] for _ in range(3)]
    listing = client.get(f"/cohorts/{cohort_id}/runs")
    assert listing.status_code == 200
    assert [r["id"] for r in listing.json()] == list(reversed(created))
    assert all("baseline" not in r for r in listing.json())  # history holds counts; the baseline comes with a run
    assert listing.json()[0]["step_counts"][-1]["persons"] == 21 and listing.json()[0]["final_count"] == 21


def test_the_history_includes_failed_and_timed_out_runs_with_their_status(client, monkeypatch):
    import app.runs as runs

    from app.cohort_engine.attrition import CohortTimeout

    cohort_id = save(client, INDEX_ONLY)
    run(client, cohort_id)
    monkeypatch.setattr(runs, "execute_run", lambda *a, **k: (_ for _ in ()).throw(CohortTimeout("x")))
    run(client, cohort_id)
    monkeypatch.setattr(runs, "execute_run", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    run(client, cohort_id)
    statuses = [r["status"] for r in client.get(f"/cohorts/{cohort_id}/runs").json()]
    assert statuses == ["failed", "timeout", "success"]
    failed = client.get(f"/cohorts/{cohort_id}/runs").json()[0]
    assert failed["step_counts"] is None and failed["final_count"] is None


def test_the_history_of_one_cohort_does_not_include_another_cohorts_runs(client):
    first, second = save(client, INDEX_ONLY), save(client)
    run(client, first)
    run(client, second)
    assert len(client.get(f"/cohorts/{first}/runs").json()) == 1
    assert client.get(f"/cohorts/{second}/runs").json()[0]["cohort_id"] == second


def test_a_cohort_without_runs_has_an_empty_history_and_an_unknown_cohort_is_a_404(client):
    assert client.get(f"/cohorts/{save(client)}/runs").json() == []
    assert client.get(f"/cohorts/{uuid.uuid4()}/runs").status_code == 404
    assert client.get("/cohorts/not-a-uuid/runs").status_code == 404


def test_the_history_limit_is_validated_and_applied(client):
    cohort_id = save(client, INDEX_ONLY)
    for _ in range(3):
        run(client, cohort_id)
    assert len(client.get(f"/cohorts/{cohort_id}/runs", params={"limit": 2}).json()) == 2
    for bad in ("0", "101", "x", "-1"):
        response = client.get(f"/cohorts/{cohort_id}/runs", params={"limit": bad})
        assert response.status_code == 422 and response.json()["errors"][0]["field"] == "limit", bad


def test_deleting_a_cohort_deletes_its_history(client):
    cohort_id = save(client, INDEX_ONLY)
    run(client, cohort_id)
    client.delete(f"/cohorts/{cohort_id}")
    assert client.get(f"/cohorts/{cohort_id}/runs").status_code == 404


# ---- the cohort list shows the last run ----------------------------------------------------------------------------

def test_the_cohort_list_shows_each_cohorts_last_run_or_none(client, monkeypatch):
    import app.runs as runs

    never_run, index_only, reference = save(client, {**INDEX_ONLY, "name": "A"}), save(client, INDEX_ONLY), save(client)
    run(client, index_only)
    run(client, reference)
    by_id = {c["id"]: c for c in client.get("/cohorts").json()}
    assert by_id[never_run]["last_run"] is None
    assert by_id[index_only]["last_run"]["status"] == "success" and by_id[index_only]["last_run"]["final_count"] == 21
    assert by_id[reference]["last_run"]["final_count"] == "<11"
    assert set(by_id[reference]["last_run"]) == {"status", "final_count", "created_at"}
    monkeypatch.setattr(runs, "execute_run", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    run(client, index_only)  # the newest run failed: that is what the list shows
    latest = {c["id"]: c for c in client.get("/cohorts").json()}[index_only]["last_run"]
    assert latest["status"] == "failed" and latest["final_count"] is None


def test_every_response_of_the_run_routes_carries_a_trace_id(client):
    cohort_id = save(client, INDEX_ONLY)
    for response in (run(client, cohort_id), client.get(f"/cohorts/{cohort_id}/runs"), run(client, str(uuid.uuid4()))):
        assert len(response.headers["x-trace-id"]) == 32
