"""Issue #27: the small traffic generator used by the chaos drill (`loadgen/runs.py`).

It creates the reference cohort once, runs it `count` times at a fixed pace, counts the answers, and always deletes the
cohort again. Realistic definitions only (docs/observability.md): the reference cohort finishes in well under a second.
"""
import pytest

from loadgen.runs import MAX_COUNT, main


class FakeApi:
    def __init__(self, run_status=201):
        self.calls, self.run_status = [], run_status

    def __call__(self, method, url, body=None):
        self.calls.append((method, url))
        if method == "POST" and url.endswith("/cohorts"):
            return 201, {"id": "11111111-1111-1111-1111-111111111111"}
        if method == "POST" and url.endswith("/runs"):
            return self.run_status, {}
        if method == "DELETE":
            return 204, {}
        return 404, {}


def run(args, api=None, sleeps=None):
    api = api or FakeApi()
    slept = sleeps if sleeps is not None else []
    code = main(args, send=api, sleep=slept.append)
    return code, api, slept


URL = "https://cohortlens-api-dev-abc123-ul.a.run.app"


def test_it_creates_the_cohort_runs_it_count_times_and_deletes_it():
    code, api, sleeps = run(["--url", URL, "--count", "3", "--interval", "5"])
    methods = [(method, url.replace(URL, "")) for method, url in api.calls]
    cohort = "/cohorts/11111111-1111-1111-1111-111111111111"
    assert methods == [("POST", "/cohorts"), ("POST", cohort + "/runs"), ("POST", cohort + "/runs"),
                       ("POST", cohort + "/runs"), ("DELETE", cohort)]
    assert code == 0 and sleeps == [5, 5]


def test_failed_runs_are_counted_and_do_not_stop_the_traffic_or_the_cleanup():
    code, api, _ = run(["--url", URL, "--count", "4", "--interval", "1"], api=FakeApi(run_status=500))
    assert sum(1 for method, url in api.calls if url.endswith("/runs")) == 4 and api.calls[-1][0] == "DELETE"
    assert code == 0  # the generator's job is to send traffic; the drill reads the outcome from the alert


def test_the_api_prefix_is_for_the_web_service():
    _, api, _ = run(["--url", "https://cohortlens-web-dev-abc123-ul.a.run.app", "--api-prefix", "/api", "--count", "1"])
    assert api.calls[0][1].endswith("/api/cohorts")


@pytest.mark.parametrize("args", [
    [], ["--url", "http://x.run.app"], ["--url", "https://evil.example.com"], ["--url", URL + "/path"],
    ["--url", URL, "--count", "0"], ["--url", URL, "--count", str(MAX_COUNT + 1)], ["--url", URL, "--count", "x"],
    ["--url", URL, "--interval", "0"], ["--url", URL, "--interval", "9999"], ["--url", URL, "--api-prefix", "api"],
    ["--url", URL, "--bogus"],
])
def test_bad_arguments_are_refused_before_any_request(args):
    code, api, _ = run(args)
    assert code == 2 and api.calls == []


def test_the_cohort_is_deleted_even_when_the_traffic_loop_is_interrupted():
    api = FakeApi()

    def interrupt(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        main(["--url", URL, "--count", "3", "--interval", "5"], send=api, sleep=interrupt)
    assert api.calls[-1][0] == "DELETE"
