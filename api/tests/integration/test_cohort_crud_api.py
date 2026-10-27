"""Issue #11: cohort CRUD endpoints (docs/spec.md FR-2, US-2, US-6, US-3).

The API serves `/cohorts`; the nginx proxy strips `/api`, so the public path is `/api/cohorts`. Definitions are
validated by `parse_definition` before anything is stored. Error responses carry field-level errors and the trace id
that is also in the `X-Trace-Id` header.
"""
import copy
import json
import pathlib
import uuid

import pytest
from sqlalchemy import create_engine, text

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))


def definition(**overrides):
    raw = copy.deepcopy(REFERENCE)
    raw.update(overrides)
    return raw


def stored_count(url):
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as conn:
        count = conn.execute(text("select count(*) from app.cohort_definition")).scalar_one()
    engine.dispose()
    return count


def create(client, **overrides):
    response = client.post("/cohorts", json=definition(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


# ---- Given a valid definition, when I POST /api/cohorts, then it is stored and returned with an id and version 1 ----

def test_a_valid_definition_is_stored_and_returned_with_an_id_and_version_one(client, api_env):
    response = client.post("/cohorts", json=definition())
    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"]) and body["version"] == 1
    assert body["name"] == REFERENCE["name"]
    assert body["definition"]["index_event"]["concept_ids"] == [1308216]
    assert stored_count(api_env) == 1


def test_the_response_location_points_at_the_new_cohort(client):
    response = client.post("/cohorts", json=definition())
    assert response.headers["location"] == f"/api/cohorts/{response.json()['id']}"


def test_the_stored_definition_is_the_validated_normalised_one(client):
    ids_with_duplicates = definition()
    ids_with_duplicates["index_event"]["concept_ids"] = [1308216, 1308216]
    assert create_from(client, ids_with_duplicates)["definition"]["index_event"]["concept_ids"] == [1308216]


def create_from(client, raw):
    response = client.post("/cohorts", json=raw)
    assert response.status_code == 201, response.text
    return response.json()


def test_a_name_that_looks_like_sql_is_stored_verbatim(client, api_env):
    nasty = "x'); drop table app.cohort_definition; --"
    created = create(client, name=nasty)
    assert client.get(f"/cohorts/{created['id']}").json()["name"] == nasty
    assert stored_count(api_env) == 1


# ---- Given a saved cohort, when I PUT it, then version increases by 1 ---------------------------------------------

def test_updating_a_cohort_increases_its_version_by_one_each_time(client):
    created = create(client)
    first = client.put(f"/cohorts/{created['id']}", json=definition(name="Edited once"))
    second = client.put(f"/cohorts/{created['id']}", json=definition(name="Edited twice"))
    assert (first.status_code, first.json()["version"]) == (200, 2)
    assert (second.status_code, second.json()["version"]) == (200, 3)
    assert second.json()["id"] == created["id"] and second.json()["name"] == "Edited twice"
    assert client.get(f"/cohorts/{created['id']}").json()["version"] == 3


def test_an_update_replaces_the_definition_and_moves_updated_at_forward(client):
    created = create(client)
    changed = definition()
    changed["demographics"] = {"age_min": 65, "sex": ["F"]}
    updated = client.put(f"/cohorts/{created['id']}", json=changed).json()
    assert updated["definition"]["demographics"] == {"age_min": 65, "age_max": None, "sex": ["F"]}
    assert updated["created_at"] == created["created_at"] and updated["updated_at"] > created["updated_at"]


def test_updating_an_unknown_cohort_is_a_404_and_creates_nothing(client, api_env):
    response = client.put(f"/cohorts/{uuid.uuid4()}", json=definition())
    assert response.status_code == 404 and stored_count(api_env) == 0


# ---- Given an invalid definition, then a 422 with field-level errors is returned and nothing is stored ------------

def test_an_invalid_new_definition_gets_a_422_with_field_level_errors_and_is_not_stored(client, api_env):
    bad = definition(demographics={"age_min": 80, "age_max": 40})
    response = client.post("/cohorts", json=bad)
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert errors and all(set(e) == {"field", "message"} for e in errors)
    assert any(e["field"].startswith("demographics") for e in errors)
    assert stored_count(api_env) == 0


def test_an_invalid_update_gets_a_422_and_leaves_the_stored_cohort_unchanged(client):
    created = create(client)
    response = client.put(f"/cohorts/{created['id']}", json=definition(rules=[{"label": "x"}], name="Changed"))
    assert response.status_code == 422
    after = client.get(f"/cohorts/{created['id']}").json()
    assert after["version"] == 1 and after["name"] == REFERENCE["name"]


def test_every_problem_is_reported_in_one_422(client):
    bad = definition(demographics={"age_min": 80, "age_max": 40}, observation={"prior_days": -1})
    fields = {e["field"] for e in client.post("/cohorts", json=bad).json()["errors"]}
    assert len(fields) >= 2


def test_the_cost_cap_of_issue_37_is_enforced_with_a_field_level_error(client):
    raw = definition()
    raw["index_event"]["first_occurrence_only"] = False
    raw["rules"] = raw["rules"] + [dict(raw["rules"][0], label=f"extra {n}") for n in range(2)]  # 5 rules
    errors = {e["field"] for e in client.post("/cohorts", json=raw).json()["errors"]}
    assert "rules" in errors


@pytest.mark.parametrize("body", [b"not json", b"", b"[1, 2]", b'"text"', b"null"])
def test_a_body_that_is_not_a_json_object_gets_a_422(client, body):
    response = client.post("/cohorts", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == ""


def test_error_messages_do_not_echo_what_was_submitted(client):
    response = client.post("/cohorts", json=definition(name="SECRET-NAME-123", surprise="SECRET-VALUE-456"))
    assert response.status_code == 422
    assert "SECRET" not in response.text


def test_an_oversized_body_is_rejected_with_a_413_before_it_is_parsed(client, api_env):
    huge = b'{"name": "' + b"a" * 400_000 + b'"}'
    response = client.post("/cohorts", content=huge, headers={"content-type": "application/json"})
    assert response.status_code == 413 and stored_count(api_env) == 0


# ---- list, get, delete ---------------------------------------------------------------------------------------------

def test_listing_shows_summaries_most_recently_updated_first(client):
    first = create(client, name="First")
    second = create(client, name="Second")
    client.put(f"/cohorts/{first['id']}", json=definition(name="First, edited"))
    listing = client.get("/cohorts").json()
    assert [c["name"] for c in listing] == ["First, edited", "Second"]
    assert set(listing[0]) == {"id", "name", "version", "created_at", "updated_at"}  # no definition in the list
    assert second["id"] == listing[1]["id"]


def test_an_empty_list_is_an_empty_array(client):
    assert client.get("/cohorts").json() == []


def test_getting_a_cohort_returns_its_definition(client):
    created = create(client)
    got = client.get(f"/cohorts/{created['id']}").json()
    assert got == created


def test_deleting_a_cohort_removes_it_and_its_runs(client, api_env):
    created = create(client)
    engine = create_engine(api_env.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as conn:
        conn.execute(text("insert into app.cohort_run (cohort_id, status) values (:c, 'success')"),
                     {"c": created["id"]})
    assert client.delete(f"/cohorts/{created['id']}").status_code == 204
    assert client.get(f"/cohorts/{created['id']}").status_code == 404
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from app.cohort_run")).scalar_one() == 0
    engine.dispose()
    assert client.get("/cohorts").json() == []


@pytest.mark.parametrize("method", ["get", "put", "delete"])
@pytest.mark.parametrize("cohort_id", [str(uuid.uuid4()), "not-a-uuid", "1", "%27%3B--"])
def test_an_unknown_or_malformed_id_is_a_404_not_a_422_or_a_crash(client, method, cohort_id):
    kwargs = {"json": definition()} if method == "put" else {}
    response = getattr(client, method)(f"/cohorts/{cohort_id}", **kwargs)
    assert response.status_code == 404
    assert response.json()["error"] == "Cohort not found"


# ---- Given a restart of the stack, then saved cohorts are still listed ----------------------------------------------

def test_saved_cohorts_survive_a_restart_of_the_api(client, api_env):
    created = create(client)
    from fastapi.testclient import TestClient

    from app.db import session
    from app.main import app

    session.app_engine().dispose()          # what a restart does: all connections and caches are gone
    session.app_engine.cache_clear()
    session.reader_engine.cache_clear()
    with TestClient(app, raise_server_exceptions=False) as restarted:
        assert [c["id"] for c in restarted.get("/cohorts").json()] == [created["id"]]
        assert restarted.get(f"/cohorts/{created['id']}").json()["definition"] == created["definition"]


# ---- trace id and failure behaviour --------------------------------------------------------------------------------

def test_every_response_carries_a_trace_id_and_errors_repeat_it_in_the_body(client):
    ok = client.get("/cohorts")
    missing = client.get(f"/cohorts/{uuid.uuid4()}")
    invalid = client.post("/cohorts", json={"name": "x"})
    for response in (ok, missing, invalid):
        assert len(response.headers["x-trace-id"]) == 32
    assert missing.json()["trace_id"] == missing.headers["x-trace-id"]
    assert invalid.json()["trace_id"] == invalid.headers["x-trace-id"]


def test_when_the_database_is_unreachable_the_response_is_a_503_with_a_trace_reference(client, monkeypatch):
    from app.db import session

    monkeypatch.setenv("DATABASE_URL_APP", "postgresql://nobody:nothing@127.0.0.1:1/none")
    session.app_engine.cache_clear()
    response = client.get("/cohorts")
    assert response.status_code == 503
    assert response.json()["trace_id"] == response.headers["x-trace-id"]
    assert "nothing" not in response.text and "127.0.0.1" not in response.text  # no connection details leak


def test_the_cohort_routes_are_documented_as_served_without_the_api_prefix():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert {"/cohorts", "/cohorts/{cohort_id}"} <= paths
