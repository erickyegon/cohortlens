"""Issue #27: the small traffic generator used by the chaos drill (`loadgen/runs.py`).

It runs a dedicated cohort, "Drill: reference cohort (synthetic load)", `count` times at a fixed pace and counts the
answers. The cohort is found by name or created if it is missing, and it is kept, so the drill's runs never land in the
history of a cohort a person uses, and an interrupted drill leaves nothing to clean up. Realistic definitions only
(docs/observability.md): the reference cohort finishes in well under a second.
"""
import pytest

from loadgen.runs import DRILL_COHORT_NAME, MAX_COUNT, main

DRILL_ID = "11111111-1111-1111-1111-111111111111"


class FakeApi:
    def __init__(self, run_status=201, existing=None):
        self.calls, self.run_status, self.existing, self.created_body = [], run_status, existing or [], None

    def __call__(self, method, url, body=None):
        self.calls.append((method, url))
        if method == "GET" and url.endswith("/cohorts"):
            return 200, self.existing
        if method == "POST" and url.endswith("/cohorts"):
            self.created_body = body
            return 201, {"id": DRILL_ID}
        if method == "POST" and url.endswith("/runs"):
            return self.run_status, {}
        return 404, {}


def run(args, api=None, sleeps=None):
    api = api or FakeApi()
    slept = sleeps if sleeps is not None else []
    code = main(args, send=api, sleep=slept.append)
    return code, api, slept


URL = "https://cohortlens-api-dev-abc123-ul.a.run.app"


def test_it_creates_the_dedicated_cohort_when_missing_and_runs_it_count_times():
    code, api, sleeps = run(["--url", URL, "--count", "3", "--interval", "5"])
    methods = [(method, url.replace(URL, "")) for method, url in api.calls]
    cohort = f"/cohorts/{DRILL_ID}"
    assert methods == [("GET", "/cohorts"), ("POST", "/cohorts")] + [("POST", cohort + "/runs")] * 3
    assert code == 0 and sleeps == [5, 5]


def test_the_dedicated_cohort_is_named_so_and_has_the_reference_definition():
    _, api, _ = run(["--url", URL, "--count", "1"])
    assert DRILL_COHORT_NAME == "Drill: reference cohort (synthetic load)"
    assert api.created_body["name"] == DRILL_COHORT_NAME
    assert api.created_body["index_event"]["concept_ids"] and len(api.created_body["rules"]) == 3


def test_an_existing_dedicated_cohort_is_reused_not_created_again():
    existing = [{"id": "22222222-2222-2222-2222-222222222222", "name": DRILL_COHORT_NAME},
                {"id": "33333333-3333-3333-3333-333333333333", "name": "A person's cohort"}]
    code, api, _ = run(["--url", URL, "--count", "2", "--interval", "1"], api=FakeApi(existing=existing))
    methods = [(method, url.replace(URL, "")) for method, url in api.calls]
    assert ("POST", "/cohorts") not in methods
    assert methods[1:] == [("POST", "/cohorts/22222222-2222-2222-2222-222222222222/runs")] * 2 and code == 0


def test_a_cohort_with_another_name_is_never_used_even_if_it_has_the_reference_definition():
    existing = [{"id": "33333333-3333-3333-3333-333333333333",
                 "name": "New users of lisinopril with hypertension, aged 50+, no prior heart failure"}]
    _, api, _ = run(["--url", URL, "--count", "1"], api=FakeApi(existing=existing))
    assert ("POST", "/cohorts") in [(method, url.replace(URL, "")) for method, url in api.calls]
    assert not any("33333333" in url for _, url in api.calls)


def test_it_never_deletes_anything():
    _, api, _ = run(["--url", URL, "--count", "2", "--interval", "1"])
    assert all(method != "DELETE" for method, _ in api.calls)


def test_failed_runs_are_counted_and_do_not_stop_the_traffic_or_the_cleanup():
    code, api, _ = run(["--url", URL, "--count", "4", "--interval", "1"], api=FakeApi(run_status=500))
    assert sum(1 for method, url in api.calls if url.endswith("/runs")) == 4
    assert code == 0  # the generator's job is to send traffic; the drill reads the outcome from the alert


def test_the_api_prefix_is_for_the_web_service():
    _, api, _ = run(["--url", "https://cohortlens-web-dev-abc123-ul.a.run.app", "--api-prefix", "/api", "--count", "1"])
    assert api.calls[0] == ("GET", "https://cohortlens-web-dev-abc123-ul.a.run.app/api/cohorts")
    assert api.calls[1][1].endswith("/api/cohorts")


@pytest.mark.parametrize("args", [
    [], ["--url", "http://x.run.app"], ["--url", "https://evil.example.com"], ["--url", URL + "/path"],
    ["--url", URL, "--count", "0"], ["--url", URL, "--count", str(MAX_COUNT + 1)], ["--url", URL, "--count", "x"],
    ["--url", URL, "--interval", "0"], ["--url", URL, "--interval", "9999"], ["--url", URL, "--api-prefix", "api"],
    ["--url", URL, "--bogus"],
])
def test_bad_arguments_are_refused_before_any_request(args):
    code, api, _ = run(args)
    assert code == 2 and api.calls == []


def test_an_interrupted_loop_leaves_nothing_to_clean_up():
    api = FakeApi()

    def interrupt(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        main(["--url", URL, "--count", "3", "--interval", "5"], send=api, sleep=interrupt)
    assert all(method != "DELETE" for method, _ in api.calls)


def test_when_the_cohort_list_cannot_be_read_it_stops_before_creating_anything():
    class Broken(FakeApi):
        def __call__(self, method, url, body=None):
            self.calls.append((method, url))
            return 503, {}

    code, api, _ = run(["--url", URL, "--count", "1"], api=Broken())
    assert code == 1 and api.calls == [("GET", URL + "/cohorts")]  # a second drill cohort must not be created blindly
