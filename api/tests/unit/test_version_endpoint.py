"""GET /version: which commit this revision runs.

Cloud Run reserves /healthz (its front end answers 404 before the request reaches the container), found on the first
real dev deploy. The smoke test and the prod promotion therefore read the running version from /version. It needs no
database and answers even when the database is down, like /healthz.
"""
import importlib

import pytest

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SERVICE_VERSION", SHA)
    import app.main

    importlib.reload(app.main)
    with TestClient(app.main.app, raise_server_exceptions=False) as test_client:
        yield test_client
    monkeypatch.delenv("SERVICE_VERSION")
    importlib.reload(app.main)


def test_version_reports_the_commit_the_revision_was_built_from(client):
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": SHA}


def test_version_carries_the_trace_header_like_every_other_route(client):
    assert client.get("/version").headers["X-Trace-Id"]


def test_version_needs_no_database(client, monkeypatch):
    for name in ("DATABASE_URL_APP", "DATABASE_URL_READER"):
        monkeypatch.delenv(name, raising=False)
    assert client.get("/version").status_code == 200


def test_healthz_still_exists_for_compose_and_the_docker_healthcheck(client):
    assert client.get("/healthz").json()["status"] == "ok"
