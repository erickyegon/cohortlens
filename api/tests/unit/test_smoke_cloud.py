"""Issue #17 (E4 delivery): scripts/smoke_cloud.sh, the post-deploy smoke test of one URL.

"Given a deployed dev revision, then a smoke test hits /readyz and runs one real cohort." It runs against a FAKE `curl`
that answers from a directory of canned responses, so no network is used. The reference cohort must come back with its
documented final count (481) from the seeded data; a cohort it creates is always deleted again, even when a check fails.
"""
import json
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "smoke_cloud.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the smoke script")

SEP = "\x1f"
COHORT_ID = "6f1c1f0e-5b1c-4d55-9a0e-0a5f4c2b9a11"
BASE = "https://candidate---cohortlens-api-dev-abc123-ul.a.run.app"

FAKE_CURL = r"""#!/usr/bin/env bash
# a fake curl: finds the method and URL, answers from $FAKE_CURL_DIR/<METHOD>_<path with / as _> (first line =
# status code, the rest = body), and prints the body then the status code on its own last line, as curl does for
# -w '
%{http_code}'
printf '%s\x1f' "$@" >> "$FAKE_LOG"; printf '\n' >> "$FAKE_LOG"
method=GET; url=""; previous=""
for argument in "$@"; do
  if [ "$previous" = "-X" ]; then method="$argument"; fi
  case "$argument" in http*) url="$argument" ;; esac
  previous="$argument"
done
path="${url#*://}"; path="/${path#*/}"; path="${path%%\?*}"
key="${method}_$(echo "$path" | sed 's#[/-]#_#g')"
file="$FAKE_CURL_DIR/$key"
if [ ! -f "$file" ]; then printf '\n404\n'; exit 0; fi
tail -n +2 "$file"; printf '\n%s\n' "$(head -n 1 "$file")"
"""


def _install(directory, name, body):
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def scenario(tmp_path, overrides=None):
    """Canned responses of a healthy deployment; `overrides` maps a file name to (status, body) or None to remove."""
    directory = tmp_path / "responses"
    directory.mkdir(exist_ok=True)
    run = {"id": "r1", "cohort_id": COHORT_ID, "status": "success", "final_count": 481,
           "step_counts": [{"key": "index", "label": "Index", "persons": 920}]}
    files = {
        "GET__healthz": (200, {"status": "ok", "version": "abc"}),
        "GET__readyz": (200, {"status": "ready", "cdm_persons": 5724}),
        "POST__cohorts": (201, {"id": COHORT_ID, "name": "smoke"}),
        f"POST__cohorts_{COHORT_ID}_runs": (201, run),
        f"DELETE__cohorts_{COHORT_ID}": (204, ""),
        "GET__api_readyz": (200, {"status": "ready", "cdm_persons": 5724}),
        "POST__api_cohorts": (201, {"id": COHORT_ID, "name": "smoke"}),
        f"POST__api_cohorts_{COHORT_ID}_runs": (201, run),
        f"DELETE__api_cohorts_{COHORT_ID}": (204, ""),
        "GET__api_healthz": (200, {"status": "ok"}),
    }
    for name, value in (overrides or {}).items():
        if value is None:
            files.pop(name, None)
        else:
            files[name] = value
    for name, (status, body) in files.items():
        text = body if isinstance(body, str) else json.dumps(body)
        (directory / name.replace("-", "_")).write_text(f"{status}\n{text}", encoding="utf-8")
    return directory


def run_smoke(tmp_path, *args, overrides=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _install(bin_dir, "curl", FAKE_CURL)
    log = tmp_path / "curl.log"
    log.write_text("", encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(log),
           "FAKE_CURL_DIR": str(scenario(tmp_path, overrides))}
    result = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=120)
    calls = [line.split(SEP)[:-1] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return result, calls


def methods_and_paths(calls):
    found = []
    for call in calls:
        method = call[call.index("-X") + 1] if "-X" in call else "GET"
        url = next(part for part in call if part.startswith("http"))
        found.append((method, url[len(BASE):]))
    return found


def test_the_script_exists_and_is_valid_bash_with_strict_mode():
    assert SCRIPT.exists()
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


def test_a_healthy_deployment_passes_and_runs_one_real_cohort_then_cleans_up(tmp_path):
    result, calls = run_smoke(tmp_path, BASE)
    assert result.returncode == 0, result.stderr + result.stdout
    assert methods_and_paths(calls) == [("GET", "/healthz"), ("GET", "/readyz"), ("POST", "/cohorts"),
                                        ("POST", f"/cohorts/{COHORT_ID}/runs"), ("DELETE", f"/cohorts/{COHORT_ID}")]
    assert "481" in result.stdout


def test_through_the_web_proxy_every_path_is_under_the_api_prefix(tmp_path):
    result, calls = run_smoke(tmp_path, BASE, "--api-prefix", "/api")
    assert result.returncode == 0, result.stderr + result.stdout
    assert [path for _, path in methods_and_paths(calls)] == [
        "/api/healthz", "/api/readyz", "/api/cohorts", f"/api/cohorts/{COHORT_ID}/runs", f"/api/cohorts/{COHORT_ID}"]


@pytest.mark.parametrize("overrides", [
    {"GET__healthz": (503, {"status": "unavailable"})},
    {"GET__healthz": None},
    {"GET__readyz": (503, {"status": "unavailable", "error": "OperationalError"})},
    {"GET__readyz": (200, {"status": "ready", "cdm_persons": 0})},         # an empty database is not ready
    {"GET__readyz": (200, {"status": "starting", "cdm_persons": 5724})},
    {"POST__cohorts": (500, {"error": "Internal Server Error"})},
])
def test_an_unhealthy_deployment_fails_the_smoke_test(tmp_path, overrides):
    result, _ = run_smoke(tmp_path, BASE, overrides=overrides)
    assert result.returncode != 0


@pytest.mark.parametrize("run", [
    {"status": "success", "final_count": 480}, {"status": "success", "final_count": "<11"},
    {"status": "failed", "final_count": None}, {"status": "success"},
])
def test_a_wrong_or_missing_final_count_fails_and_the_cohort_is_still_deleted(tmp_path, run):
    body = {"id": "r1", "cohort_id": COHORT_ID, **run}
    result, calls = run_smoke(tmp_path, BASE, overrides={f"POST__cohorts_{COHORT_ID}_runs": (201, body)})
    assert result.returncode != 0
    assert ("DELETE", f"/cohorts/{COHORT_ID}") in methods_and_paths(calls)


def test_a_failing_run_request_fails_and_the_cohort_is_still_deleted(tmp_path):
    timeout = {f"POST__cohorts_{COHORT_ID}_runs": (504, {"error": "timeout"})}
    result, calls = run_smoke(tmp_path, BASE, overrides=timeout)
    assert result.returncode != 0
    assert ("DELETE", f"/cohorts/{COHORT_ID}") in methods_and_paths(calls)


def test_a_failed_cleanup_is_reported_but_does_not_hide_a_passing_run(tmp_path):
    result, _ = run_smoke(tmp_path, BASE, overrides={f"DELETE__cohorts_{COHORT_ID}": (500, {"error": "x"})})
    assert result.returncode == 0
    assert "could not delete" in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize("args", [[], ["not-a-url"], ["http://example.com"], [BASE, "--api-prefix", "api"],
                                  [BASE, "--unknown"], [BASE + "/path"]])
def test_bad_arguments_are_refused_before_any_request(tmp_path, args):
    result, calls = run_smoke(tmp_path, *args)
    assert result.returncode != 0 and calls == []


def test_the_script_sends_no_credentials_and_uses_timeouts(tmp_path):
    _, calls = run_smoke(tmp_path, BASE)
    text = " ".join(" ".join(c) for c in calls).lower()
    assert "authorization" not in text and "password" not in text
    assert all("--max-time" in call for call in calls)
