"""Issue #19 (E4 delivery): infra/gcp/rollback.sh, rollback by traffic shift.

"Given a service and a target revision, when rollback runs, then 100% of traffic moves to that revision without a
rebuild. Given no target, the previous revision is used. Given a completed rollback, a smoke test runs and the incident
issue is commented." It runs against a FAKE gcloud, a FAKE gh and a FAKE smoke script, so nothing touches a real
project.
"""
import json
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra" / "gcp" / "rollback.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the rollback script")

PROJECT, REGION = "cohortlens", "us-east5"
SEP = "\x1f"
API_URL = "https://cohortlens-api-dev-abc123-ul.a.run.app"
WEB_URL = "https://cohortlens-web-dev-abc123-ul.a.run.app"
# newest first, as `gcloud run revisions list --sort-by=~metadata.creationTimestamp` prints them: 00004 is a candidate
# that never served, 00003 serves now, 00002 is the previous one, 00001 the oldest
API_REVISIONS = ["cohortlens-api-dev-00004-ddd", "cohortlens-api-dev-00003-ccc", "cohortlens-api-dev-00002-bbb",
                 "cohortlens-api-dev-00001-aaa"]
SERVING = "cohortlens-api-dev-00003-ccc"
PREVIOUS = "cohortlens-api-dev-00002-bbb"
WEB_REVISIONS = ["cohortlens-web-dev-00002-bbb", "cohortlens-web-dev-00001-aaa"]
RUN_URL = "https://github.com/erickyegon/cohortlens/actions/runs/123"


def traffic(*entries):
    return json.dumps({"status": {"traffic": list(entries)}})


FAKE_GCLOUD = r"""#!/usr/bin/env bash
printf '%s\x1f' "$@" >> "$FAKE_LOG"; printf '\n' >> "$FAKE_LOG"
args="$*"
if [ -n "${FAKE_GCLOUD_ERROR:-}" ] && [[ "$args" == *"${FAKE_GCLOUD_ERROR_ON:-run services describe}"* ]]; then
  echo "ERROR: (gcloud) $FAKE_GCLOUD_ERROR" >&2; exit 1
fi
if [[ "$args" == *"update-traffic"* ]]; then
  if [ "${FAKE_UPDATE_FAIL:-0}" = "1" ]; then echo "ERROR: update failed" >&2; exit 1; fi
  printf 'move\n' >> "$FAKE_ORDER"
  exit 0
fi
if [[ "$args" == *"run services describe"* ]]; then
  if [[ "$args" == *"status.url"* ]]; then
    if [[ "$args" == *"cohortlens-api-"* ]]; then echo "$FAKE_API_URL"; else echo "$FAKE_WEB_URL"; fi
    exit 0
  fi
  if [[ "$args" == *"cohortlens-api-"* ]]; then printf '%s' "$FAKE_API_TRAFFIC"
  else printf '%s' "$FAKE_WEB_TRAFFIC"; fi
  exit 0
fi
if [[ "$args" == *"run revisions list"* ]]; then
  if [[ "$args" == *"--service=cohortlens-api-"* ]]; then printf '%s\n' $FAKE_API_REVISIONS; fi
  if [[ "$args" == *"--service=cohortlens-web-"* ]]; then printf '%s\n' $FAKE_WEB_REVISIONS; fi
  exit 0
fi
exit 0
"""

FAKE_SMOKE = r"""#!/usr/bin/env bash
printf '%s\x1f' "$@" >> "$FAKE_SMOKE_LOG"; printf '\n' >> "$FAKE_SMOKE_LOG"
printf 'smoke\n' >> "$FAKE_ORDER"
[ "${FAKE_SMOKE_FAIL:-0}" = "1" ] && { echo "smoke failed" >&2; exit 1; }
echo "SMOKE OK"
"""

FAKE_GH = r"""#!/usr/bin/env bash
printf '%s\x1f' "$@" >> "$FAKE_GH_LOG"; printf '\n' >> "$FAKE_GH_LOG"
cat > "$FAKE_GH_BODY"
printf 'comment\n' >> "$FAKE_ORDER"
[ "${FAKE_GH_FAIL:-0}" = "1" ] && { echo "gh: forbidden" >&2; exit 1; }
exit 0
"""


def _install(directory, name, body, executable=True):
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC if executable else 0o644)


def run_rollback(tmp_path, *args, env=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _install(bin_dir, "gcloud", FAKE_GCLOUD)
    _install(bin_dir, "gh", FAKE_GH)
    _install(bin_dir, "smoke", FAKE_SMOKE, executable=False)  # git stores our scripts as mode 100644
    logs = {name: tmp_path / f"{name}.log" for name in ("gcloud", "smoke", "gh", "order")}
    for log in logs.values():
        log.write_text("", encoding="utf-8")
    body = tmp_path / "comment_body.txt"
    body.write_text("", encoding="utf-8")
    full_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(logs["gcloud"]),
                "FAKE_SMOKE_LOG": str(logs["smoke"]), "FAKE_GH_LOG": str(logs["gh"]), "FAKE_GH_BODY": str(body),
                "FAKE_ORDER": str(logs["order"]), "SMOKE_SCRIPT": str(bin_dir / "smoke"), "PROJECT_ID": PROJECT,
                "REGION": REGION, "GITHUB_REPOSITORY": "erickyegon/cohortlens", "GITHUB_ACTOR": "erickyegon",
                "GITHUB_RUN_URL": RUN_URL, "FAKE_API_URL": API_URL, "FAKE_WEB_URL": WEB_URL,
                "FAKE_API_REVISIONS": " ".join(API_REVISIONS), "FAKE_WEB_REVISIONS": " ".join(WEB_REVISIONS),
                "FAKE_API_TRAFFIC": traffic({"percent": 100, "revisionName": SERVING},
                                            {"revisionName": API_REVISIONS[0], "tag": "candidate"}),
                "FAKE_WEB_TRAFFIC": traffic({"percent": 100, "revisionName": WEB_REVISIONS[0]}), **(env or {})}
    result = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=full_env, timeout=60)

    def read(name):
        lines = [line for line in logs[name].read_text(encoding="utf-8").splitlines() if line.strip()]
        return [line.split(SEP)[:-1] if SEP in line else [line] for line in lines]

    return result, {"gcloud": read("gcloud"), "smoke": read("smoke"), "gh": read("gh"), "order": read("order"),
                    "body": body.read_text(encoding="utf-8")}


def rolled_back(tmp_path, *args, **kwargs):
    result, seen = run_rollback(tmp_path, *args, **kwargs)
    assert result.returncode == 0, result.stderr + result.stdout
    return result, seen


def moves(seen):
    return [c for c in seen["gcloud"] if "update-traffic" in c]


API = ("--env", "dev", "--service", "api")


def test_the_script_exists_and_is_valid_bash_with_strict_mode():
    assert SCRIPT.exists()
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


def test_without_a_target_the_previous_revision_gets_all_the_traffic(tmp_path):
    _, seen = rolled_back(tmp_path, *API)
    assert len(moves(seen)) == 1
    move = moves(seen)[0]
    assert move[:4] == ["run", "services", "update-traffic", "cohortlens-api-dev"]
    assert f"--to-revisions={PREVIOUS}=100" in move
    assert f"--region={REGION}" in move and f"--project={PROJECT}" in move


def test_the_previous_revision_is_the_one_before_the_serving_one_not_a_newer_candidate(tmp_path):
    _, seen = rolled_back(tmp_path, *API)
    assert f"--to-revisions={PREVIOUS}=100" in moves(seen)[0]
    assert not any(API_REVISIONS[0] in part for part in moves(seen)[0])  # the never-served candidate is not the target


def test_an_explicit_target_revision_is_used(tmp_path):
    _, seen = rolled_back(tmp_path, *API, "--revision", API_REVISIONS[3])
    assert f"--to-revisions={API_REVISIONS[3]}=100" in moves(seen)[0]


def test_it_rolls_back_without_building_or_deploying_anything(tmp_path):
    _, seen = rolled_back(tmp_path, *API)
    text = " ".join(" ".join(c) for c in seen["gcloud"])
    forbidden = ("run deploy", "builds", "docker", "jobs execute", "delete", "add-iam-policy-binding", "set-iam-policy")
    for word in forbidden:
        assert word not in text, word


def test_the_web_service_and_prod_use_their_own_names(tmp_path):
    _, seen = rolled_back(tmp_path, "--env", "dev", "--service", "web")
    assert f"--to-revisions={WEB_REVISIONS[1]}=100" in moves(seen)[0] and "cohortlens-web-dev" in moves(seen)[0]
    prod_revisions = [name.replace("-dev-", "-prod-") for name in API_REVISIONS]
    prod_serving = SERVING.replace("-dev-", "-prod-")
    result, seen = run_rollback(tmp_path, "--env", "prod", "--service", "api", env={
        "FAKE_API_REVISIONS": " ".join(prod_revisions),
        "FAKE_API_TRAFFIC": traffic({"percent": 100, "revisionName": prod_serving})})
    assert result.returncode == 0, result.stderr
    assert "--to-revisions=" + PREVIOUS.replace("-dev-", "-prod-") + "=100" in moves(seen)[0]


# ---- the smoke test and the incident comment follow the move -----------------------------------------------------

def test_a_smoke_test_runs_after_the_move_on_the_service_url(tmp_path):
    _, seen = rolled_back(tmp_path, *API)
    assert seen["smoke"] == [[API_URL]]
    _, seen = rolled_back(tmp_path, "--env", "dev", "--service", "web")
    assert seen["smoke"] == [[WEB_URL, "--api-prefix", "/api"]]  # the web service is tested through its proxy


def test_the_order_is_move_then_smoke_then_comment(tmp_path):
    _, seen = rolled_back(tmp_path, *API, "--incident", "42")
    assert [line[0] for line in seen["order"]] == ["move", "smoke", "comment"]


def test_the_incident_is_commented_with_what_happened_and_the_body_stays_off_the_command_line(tmp_path):
    result, seen = rolled_back(tmp_path, *API, "--incident", "42")
    call = seen["gh"][0]
    assert call[:3] == ["issue", "comment", "42"] and "--repo=erickyegon/cohortlens" in call
    assert "--body-file=-" in call
    for needle in (SERVING, PREVIOUS, "cohortlens-api-dev", "erickyegon", RUN_URL, "smoke test passed"):
        assert needle in seen["body"], needle
    assert all(SERVING not in part for part in call)  # on stdin only, not in argv


def test_no_comment_is_posted_without_an_incident(tmp_path):
    _, seen = rolled_back(tmp_path, *API)
    assert seen["gh"] == []


def test_a_failed_smoke_test_fails_the_run_keeps_the_rollback_and_says_so_in_the_comment(tmp_path):
    result, seen = run_rollback(tmp_path, *API, "--incident", "42", env={"FAKE_SMOKE_FAIL": "1"})
    assert result.returncode != 0
    assert len(moves(seen)) == 1  # the traffic is NOT moved back automatically
    assert "smoke test FAILED" in seen["body"] and PREVIOUS in seen["body"]


def test_a_failed_traffic_move_runs_no_smoke_test_and_says_so_in_the_comment(tmp_path):
    result, seen = run_rollback(tmp_path, *API, "--incident", "42", env={"FAKE_UPDATE_FAIL": "1"})
    assert result.returncode != 0 and seen["smoke"] == []
    assert "FAILED" in seen["body"] and "traffic was not moved" in seen["body"].lower()


def test_a_failed_comment_is_a_warning_not_a_failed_rollback(tmp_path):
    result, seen = run_rollback(tmp_path, *API, "--incident", "42", env={"FAKE_GH_FAIL": "1"})
    assert result.returncode == 0 and len(moves(seen)) == 1
    assert "could not comment" in (result.stderr + result.stdout).lower()


def test_dry_run_prints_the_plan_and_changes_nothing(tmp_path):
    result, seen = rolled_back(tmp_path, *API, "--incident", "42", "--dry-run")
    assert moves(seen) == [] and seen["smoke"] == [] and seen["gh"] == []
    assert SERVING in result.stdout and PREVIOUS in result.stdout and "dry run" in result.stdout.lower()


# ---- refusals ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra, env", [
    (["--revision", "cohortlens-api-dev-00009-zzz"], {}),                    # not a revision of this service
    (["--revision", SERVING], {}),                                           # it already serves
    (["--revision", "cohortlens-api-prod-00002-bbb"], {}),                   # another environment's revision
    (["--revision", "cohortlens-web-dev-00002-bbb"], {}),                    # another service's revision
    (["--revision", "latest"], {}), (["--revision", "x; rm -rf /"], {}), (["--revision", ""], {}),
])
def test_a_bad_or_unusable_target_is_refused_and_no_traffic_moves(tmp_path, extra, env):
    result, seen = run_rollback(tmp_path, *API, *extra, env=env)
    assert result.returncode != 0 and moves(seen) == [] and seen["smoke"] == []


def test_the_oldest_serving_revision_has_no_previous_one(tmp_path):
    only = [API_REVISIONS[3]]
    result, seen = run_rollback(tmp_path, *API, env={
        "FAKE_API_REVISIONS": " ".join(only), "FAKE_API_TRAFFIC": traffic({"percent": 100, "revisionName": only[0]})})
    assert result.returncode != 0 and "no previous" in result.stderr.lower() and moves(seen) == []


@pytest.mark.parametrize("api_traffic", [
    traffic({"percent": 50, "revisionName": SERVING}, {"percent": 50, "revisionName": PREVIOUS}),
    traffic({"percent": 0, "revisionName": SERVING}), traffic(), "not json", "",
])
def test_a_service_without_exactly_one_serving_revision_is_refused(tmp_path, api_traffic):
    result, seen = run_rollback(tmp_path, *API, env={"FAKE_API_TRAFFIC": api_traffic})
    assert result.returncode != 0 and moves(seen) == []


def test_a_gcloud_error_stops_the_rollback_before_anything_moves(tmp_path):
    for on in ("run services describe", "run revisions list"):
        result, seen = run_rollback(tmp_path, *API, env={"FAKE_GCLOUD_ERROR": "PERMISSION_DENIED: no access",
                                                         "FAKE_GCLOUD_ERROR_ON": on})
        assert result.returncode != 0 and moves(seen) == [], on


@pytest.mark.parametrize("args", [
    [], ["--env", "dev"], ["--env", "staging", "--service", "api"], ["--env", "dev", "--service", "db"],
    [*API, "--incident", "abc"], [*API, "--incident", "0"], [*API, "--incident", "42; echo"], [*API, "--force"],
    [*API, "extra"], [*API, "--revision"], [*API, "--incident"], ["--env"], ["--service"],
])
def test_bad_arguments_are_refused_before_any_command_runs(tmp_path, args):
    result, seen = run_rollback(tmp_path, *args)
    assert result.returncode != 0 and seen["gcloud"] == [] and seen["gh"] == [] and seen["smoke"] == []


@pytest.mark.parametrize("env", [{"PROJECT_ID": ""}, {"PROJECT_ID": "Bad Project"}, {"REGION": "us east"}])
def test_bad_settings_are_refused_before_any_command_runs(tmp_path, env):
    result, seen = run_rollback(tmp_path, *API, env=env)
    assert result.returncode != 0 and seen["gcloud"] == []


def test_the_smoke_script_is_run_through_bash_because_git_stores_it_as_mode_100644():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'bash "$SMOKE"' in text
    assert not [line for line in text.splitlines() if line.strip().startswith('"$SMOKE"')]
