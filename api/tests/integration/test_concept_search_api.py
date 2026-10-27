"""Issue #13: concept search endpoint for the typeahead (docs/spec.md FR-1, US-1).

The API serves `/concepts`; the nginx proxy strips `/api`, so the public path is `/api/concepts?q=`. Only condition and
drug concepts can be used in a cohort definition, so only those are searchable; results are `{concept_id, name,
domain}` with the domain spelled as the definition model spells it. The truth fixture's vocabulary is small and known:
conditions "Essential hypertension" (320128, standard) and "Malignant essential hypertension" (317898), "Heart
failure" (316139), "Chronic congestive heart failure" (4229440); drugs "lisinopril" (1308216, ingredient), "lisinopril
10 MG Oral Tablet" (19080128), "lisinopril 20 MG Oral Tablet" (19080129), "hydrochlorothiazide 12.5 MG / lisinopril 20
MG Oral Tablet" (974200), "enalapril" (1341927), "enalapril maleate 10 MG Oral Tablet" (40165762) and the
classification concept "ACE inhibitors, plain" (21601784, standard_concept 'C'); also gender, visit and measurement
concepts that must NOT be returned.
"""
import pytest
from sqlalchemy import text


@pytest.fixture()
def client(truth_engine, monkeypatch):
    """The API wired to the truth-fixture database through the READER connection only (no app database at all)."""
    from fastapi.testclient import TestClient

    from app.db import session
    from app.main import app

    url = truth_engine.url.render_as_string(hide_password=False).replace("postgresql+psycopg://", "postgresql://")
    monkeypatch.setenv("DATABASE_URL_READER", url)
    monkeypatch.delenv("DATABASE_URL_APP", raising=False)  # the concept route must not need the app database
    session.reader_engine.cache_clear()
    session.app_engine.cache_clear()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    session.reader_engine().dispose()
    session.reader_engine.cache_clear()


def search(client, q, **params):
    return client.get("/concepts", params={"q": q, **params})


def names(response):
    return [c["name"] for c in response.json()]


# ---- Given the seeded vocabulary, when I search "hypertens", then matching concepts with id, name and domain --------

def test_searching_hypertens_returns_matching_concepts_with_id_name_and_domain(client):
    response = search(client, "hypertens")
    assert response.status_code == 200
    assert response.json() == [
        {"concept_id": 320128, "name": "Essential hypertension", "domain": "condition"},
        {"concept_id": 317898, "name": "Malignant essential hypertension", "domain": "condition"},
    ]


def test_the_search_is_case_insensitive(client):
    assert search(client, "HyPeRtEnS").json() == search(client, "hypertens").json()


def test_a_match_in_the_middle_of_a_name_is_found(client):
    assert 4229440 in [c["concept_id"] for c in search(client, "congestive").json()]


def test_prefix_matches_come_first_then_word_starts_then_anything_else_shorter_names_first(client):
    # prefix "lisinopril": the ingredient (shortest), then the two tablets by name; the combination product has the
    # word later in its name so it comes last
    assert names(search(client, "lisinopril")) == [
        "lisinopril", "lisinopril 10 MG Oral Tablet", "lisinopril 20 MG Oral Tablet",
        "hydrochlorothiazide 12.5 MG / lisinopril 20 MG Oral Tablet"]


def test_a_match_inside_a_word_is_found_and_equal_ranks_are_ordered_by_name_length(client):
    # "tension" occurs only inside "hypertension" in both names, so they tie on rank and the shorter name comes first
    assert names(search(client, "tension")) == ["Essential hypertension", "Malignant essential hypertension"]


def test_classification_concepts_are_searchable_because_cohorts_use_them_as_ancestors(client):
    assert search(client, "ace inh").json() == [
        {"concept_id": 21601784, "name": "ACE inhibitors, plain", "domain": "drug"}]


def test_the_domain_filter_narrows_the_results(client):
    assert {c["domain"] for c in search(client, "il").json()} == {"condition", "drug"}  # "failure" and "...pril"
    only_drugs = search(client, "enalapril", domain="drug").json()
    assert {c["domain"] for c in only_drugs} == {"drug"}
    assert search(client, "failure", domain="drug").json() == []
    assert [c["concept_id"] for c in search(client, "failure", domain="Condition").json()] == [316139, 4229440]


def test_concepts_that_cannot_be_used_in_a_definition_are_never_returned(client):
    # gender (MALE/FEMALE), visits and measurements exist in the vocabulary but are not condition or drug concepts
    for query in ("male", "visit", "pressure", "hemoglobin"):
        assert search(client, query).json() == [], query


def test_invalidated_and_non_standard_concepts_are_not_returned(client, truth_engine):
    with truth_engine.connect() as conn:
        conn.execute(text("insert into cdm.concept values (910001, 'Hypertension, retired', 'Condition', 'SNOMED',"
                          " 'Clinical Finding', 'S', 'x1', 'D')"))
        conn.execute(text("insert into cdm.concept values (910002, 'Hypertension, source code', 'Condition', 'ICD10',"
                          " 'Clinical Finding', NULL, 'x2', NULL)"))
        conn.commit()  # the API reads through its own connection
        try:
            ids = {c["concept_id"] for c in search(client, "hypertens").json()}
        finally:
            conn.execute(text("delete from cdm.concept where concept_id in (910001, 910002)"))
            conn.commit()
    assert ids == {320128, 317898}  # not the retired concept ('D') and not the non-standard source code (NULL)


# ---- Given a query with no matches, then an empty list is returned, not an error ------------------------------------

def test_a_query_with_no_matches_is_an_empty_list_not_an_error(client):
    response = search(client, "zzzzzz")
    assert response.status_code == 200 and response.json() == []


# ---- Given a very short query (<2 characters), then no search is run -----------------------------------------------

@pytest.mark.parametrize("q", ["", " ", "h", " h ", "%", "_", "\t\t "])
def test_a_query_shorter_than_two_characters_returns_nothing_and_does_not_touch_the_database(client, q, monkeypatch):
    from app.db import session

    def explode():
        raise AssertionError("the database was used for a query that is too short")

    monkeypatch.setattr(session, "reader_engine", explode)
    import app.concepts as concepts
    monkeypatch.setattr(concepts, "reader_engine", explode, raising=False)
    response = search(client, q)
    assert response.status_code == 200 and response.json() == []


def test_a_request_without_q_is_the_same_as_an_empty_query(client):
    response = client.get("/concepts")
    assert response.status_code == 200 and response.json() == []


# ---- input handling ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("q", ["%%", "__", "100%", "a_b", "\\\\", "hypert%ns"])
def test_like_wildcards_in_the_query_are_literal_characters(client, q):
    assert search(client, q).json() == []


def test_a_sql_injection_attempt_is_just_a_query_that_matches_nothing(client, truth_engine):
    assert search(client, "'; drop table cdm.concept; --").json() == []
    assert search(client, "x' or '1'='1").json() == []
    with truth_engine.connect() as conn:
        assert conn.execute(text("select count(*) from cdm.concept")).scalar_one() > 10


def test_a_query_with_a_nul_or_control_character_is_a_422_not_a_crash(client):
    for q in ("hyp\x00ertens", "hyp\x07ertens", "hyp\nertens"):
        response = search(client, q)
        assert response.status_code == 422, repr(q)
        assert response.json()["errors"][0]["field"] == "q"


def test_a_query_of_100_characters_is_fine_and_101_is_a_422(client):
    assert search(client, "a" * 100).status_code == 200
    response = search(client, "a" * 101)
    assert response.status_code == 422 and response.json()["errors"][0]["field"] == "q"


def test_non_ascii_queries_work(client):
    assert search(client, "hypertensión").json() == []
    assert search(client, "日本語のクエリ").json() == []


def test_the_limit_parameter_caps_the_results_and_is_validated(client):
    assert len(search(client, "lisinopril", limit=2).json()) == 2
    for bad in ("0", "51", "-1", "x", "1.5", ""):
        response = search(client, "lisinopril", limit=bad)
        assert response.status_code == 422 and response.json()["errors"][0]["field"] == "limit", bad


def test_an_unknown_domain_is_a_422_on_the_domain_field(client):
    response = search(client, "hypertens", domain="measurement")
    assert response.status_code == 422 and response.json()["errors"][0]["field"] == "domain"


def test_repeated_q_parameters_use_the_last_one(client):
    response = client.get("/concepts", params=[("q", "zzzz"), ("q", "hypertens")])
    assert [c["concept_id"] for c in response.json()] == [320128, 317898]


# ---- the response and its envelope ---------------------------------------------------------------------------------

def test_a_result_holds_exactly_an_id_a_name_and_a_domain(client):
    for concept in search(client, "il").json():
        assert set(concept) == {"concept_id", "name", "domain"}
        assert isinstance(concept["concept_id"], int)


def test_the_route_uses_only_the_reader_connection(client):
    # DATABASE_URL_APP is unset in this fixture: if the route touched the app engine it would raise a KeyError (500)
    assert search(client, "hypertens").status_code == 200


def test_every_response_carries_a_trace_id_and_errors_repeat_it_in_the_body(client):
    ok = search(client, "hypertens")
    bad = search(client, "a" * 101)
    assert len(ok.headers["x-trace-id"]) == 32
    assert bad.json()["trace_id"] == bad.headers["x-trace-id"]


def test_when_the_database_is_unreachable_the_response_is_a_503_with_a_trace_reference(client, monkeypatch):
    from app.db import session

    monkeypatch.setenv("DATABASE_URL_READER", "postgresql://nobody:nothing@127.0.0.1:1/none")
    session.reader_engine.cache_clear()
    response = search(client, "hypertens")
    assert response.status_code == 503
    assert response.json()["trace_id"] == response.headers["x-trace-id"]
    assert "nothing" not in response.text and "127.0.0.1" not in response.text
