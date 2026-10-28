"""Issue #18 (E4 delivery): infra/gcp/promote.sh, what the approval-gated prod workflow runs once the reviewer
has said yes.

"Given approval, then the same image digest that passed dev is deployed with --no-traffic." The script does not build or
look anything up by tag (tags can be moved): it reads the images the DEV services are serving right now, which were
deployed by digest, checks they are digest-pinned and in this project's registry, checks that dev reports the version
the approver was shown, and hands exactly those digests to infra/gcp/deploy.sh for production. It runs against a FAKE
gcloud, a FAKE curl and a FAKE deploy script, so nothing touches a real project.
"""
import json
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra" / "gcp" / "promote.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the promote script")

PROJECT, REGION = "cohortlens", "us-east5"
REGISTRY = "us-east5-docker.pkg.dev/cohortlens/cohortlens"
PROD_SA = "cohortlens-run-prod@cohortlens.iam.gserviceaccount.com"
API_IMAGE = f"{REGISTRY}/api@sha256:{'a' * 64}"
WEB_IMAGE = f"{REGISTRY}/web@sha256:{'b' * 64}"
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
SEP = "\x1f"
API_SERVING, API_CANDIDATE = "cohortlens-api-dev-00002-aaa", "cohortlens-api-dev-00003-bbb"
WEB_SERVING = "cohortlens-web-dev-00002-ccc"
CANDIDATE_IMAGE = f"{REGISTRY}/api@sha256:{'c' * 64}"


def traffic(*entries):
    """The `status.traffic` of `gcloud run services describe --format=json` for a service."""
    return json.dumps({"status": {"traffic": list(entries)}})


SERVING_API = traffic({"percent": 100, "revisionName": API_SERVING})
SERVING_WEB = traffic({"percent": 100, "revisionName": WEB_SERVING})


FAKE_GCLOUD = r"""#!/usr/bin/env bash
printf '%s' "$@" >> "$FAKE_LOG"; printf '
' >> "$FAKE_LOG"
args="$*"
if [ -n "${FAKE_DESCRIBE_ERROR:-}" ]; then echo "ERROR: (gcloud) $FAKE_DESCRIBE_ERROR" >&2; exit 1; fi
if [[ "$args" == *"run services describe"* ]]; then
  if [[ "$args" == *"status.url"* ]]; then
    if [[ "$args" == *"cohortlens-api-dev"* ]]; then echo "https://cohortlens-api-dev-abc123-ul.a.run.app"; fi
    exit 0
  fi
  if [[ "$args" == *"--format=json"* ]]; then
    if [[ "$args" == *"cohortlens-api-dev"* ]]; then printf '%s' "$FAKE_API_TRAFFIC"; fi
    if [[ "$args" == *"cohortlens-web-dev"* ]]; then printf '%s' "$FAKE_WEB_TRAFFIC"; fi
    exit 0
  fi
fi
if [[ "$args" == *"run revisions describe"* ]]; then
  if [[ "$args" == *"cohortlens-api-dev-00002"* ]]; then echo "${FAKE_API_IMAGE-}"; fi
  if [[ "$args" == *"cohortlens-api-dev-00003"* ]]; then echo "${FAKE_CANDIDATE_IMAGE-}"; fi
  if [[ "$args" == *"cohortlens-web-dev-00002"* ]]; then echo "${FAKE_WEB_IMAGE-}"; fi
  exit 0
fi
exit 0
"""

FAKE_CURL = r"""#!/usr/bin/env bash
printf '%s\x1f' "$@" >> "$FAKE_CURL_LOG"; printf '\n' >> "$FAKE_CURL_LOG"
[ "${FAKE_CURL_FAIL:-0}" = "1" ] && exit 22
printf '%s' "${FAKE_HEALTHZ-}"
"""

FAKE_DEPLOY = r"""#!/usr/bin/env bash
printf '%s\x1f' "$@" >> "$FAKE_DEPLOY_LOG"; printf '\n' >> "$FAKE_DEPLOY_LOG"
env | grep -E '^(PROJECT_ID|REGION|RUNTIME_SA)=' >> "$FAKE_DEPLOY_LOG"
[ "${FAKE_DEPLOY_FAIL:-0}" = "1" ] && exit 1
exit 0
"""


def _install(directory, name, body, executable=True):
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC if executable else 0o644)


def run_promote(tmp_path, *args, env=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _install(bin_dir, "gcloud", FAKE_GCLOUD)
    _install(bin_dir, "curl", FAKE_CURL)
    _install(bin_dir, "deploy", FAKE_DEPLOY, executable=False)  # git stores our scripts as mode 100644
    logs = {name: tmp_path / f"{name}.log" for name in ("gcloud", "curl", "deploy")}
    for log in logs.values():
        log.write_text("", encoding="utf-8")
    full_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(logs["gcloud"]),
                "FAKE_CURL_LOG": str(logs["curl"]), "FAKE_DEPLOY_LOG": str(logs["deploy"]),
                "DEPLOY_SCRIPT": str(bin_dir / "deploy"), "PROJECT_ID": PROJECT, "REGION": REGION,
                "REGISTRY": REGISTRY, "RUNTIME_SA": PROD_SA, "FAKE_API_IMAGE": API_IMAGE, "FAKE_WEB_IMAGE": WEB_IMAGE,
                "FAKE_CANDIDATE_IMAGE": CANDIDATE_IMAGE, "FAKE_API_TRAFFIC": SERVING_API,
                "FAKE_WEB_TRAFFIC": SERVING_WEB,
                "FAKE_HEALTHZ": f'{{"status":"ok","version":"{SHA}"}}', **(env or {})}
    result = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=full_env, timeout=60)
    read = {}
    for name, log in logs.items():
        lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
        read[name] = [line.split(SEP)[:-1] if SEP in line else [line] for line in lines]
    return result, read


def is_arguments_line(call):
    return bool(call) and not call[0].startswith(("PROJECT_ID=", "REGION=", "RUNTIME_SA="))


def deploy_call(read):
    """The arguments the fake deploy script was called with (None if it was never called)."""
    return next((c for c in read["deploy"] if is_arguments_line(c)), None)


def promoted(tmp_path, *args, **kwargs):
    result, read = run_promote(tmp_path, *args, **kwargs)
    assert result.returncode == 0, result.stderr + result.stdout
    return read


def test_the_script_exists_and_is_valid_bash_with_strict_mode():
    assert SCRIPT.exists()
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


def test_it_deploys_to_prod_exactly_the_digests_dev_is_serving(tmp_path):
    read = promoted(tmp_path)
    call = deploy_call(read)
    assert call == ["--env", "prod", "--version", SHA, "--api-image", API_IMAGE, "--web-image", WEB_IMAGE]


def test_the_images_come_from_the_dev_services_not_from_a_tag_lookup(tmp_path):
    read = promoted(tmp_path)
    described = [" ".join(c) for c in read["gcloud"]]
    assert any("revisions describe cohortlens-api-dev-00002-aaa" in line and "containers" in line for line in described)
    assert any("revisions describe cohortlens-web-dev-00002-ccc" in line and "containers" in line for line in described)
    assert not any("artifacts" in line or "docker images" in line for line in described)  # tags are mutable
    assert all("-prod" not in line for line in described)  # reading dev touches nothing of prod


def test_the_version_is_what_the_dev_api_reports(tmp_path):
    read = promoted(tmp_path)
    assert read["curl"] and "https://cohortlens-api-dev-abc123-ul.a.run.app/healthz" in read["curl"][0]


def test_the_expected_version_the_approver_was_shown_must_match(tmp_path):
    promoted(tmp_path, "--expect-version", SHA)
    result, read = run_promote(tmp_path, "--expect-version", OTHER_SHA)
    assert result.returncode != 0 and deploy_call(read) is None
    assert OTHER_SHA in result.stderr and SHA in result.stderr  # says which two versions disagree


def test_the_settings_reach_the_deploy_script(tmp_path):
    read = promoted(tmp_path)
    env_lines = [c[0] for c in read["deploy"] if c and c[0].startswith(("PROJECT_ID=", "REGION=", "RUNTIME_SA="))]
    assert set(env_lines) == {f"PROJECT_ID={PROJECT}", f"REGION={REGION}", f"RUNTIME_SA={PROD_SA}"}


@pytest.mark.parametrize("env", [
    {"FAKE_API_IMAGE": f"{REGISTRY}/api:latest"},                              # a tag, not a digest
    {"FAKE_WEB_IMAGE": f"{REGISTRY}/web:{SHA}"},
    {"FAKE_API_IMAGE": f"evil.example.com/cohortlens/api@sha256:{'a' * 64}"},   # not this project's registry
    {"FAKE_WEB_IMAGE": f"us-east5-docker.pkg.dev/other/other/web@sha256:{'b' * 64}"},
    {"FAKE_API_IMAGE": ""}, {"FAKE_WEB_IMAGE": ""},
    {"FAKE_API_IMAGE": f"{REGISTRY}/web@sha256:{'a' * 64}"},                   # the web image in the api slot
    {"FAKE_WEB_IMAGE": f"{REGISTRY}/api@sha256:{'b' * 64}"},
])
def test_an_image_that_is_not_a_digest_pinned_image_of_this_registry_is_refused(tmp_path, env):
    result, read = run_promote(tmp_path, env=env)
    assert result.returncode != 0 and deploy_call(read) is None


@pytest.mark.parametrize("env", [
    {"FAKE_HEALTHZ": ""}, {"FAKE_HEALTHZ": "not json"}, {"FAKE_HEALTHZ": '{"status":"ok"}'},
    {"FAKE_HEALTHZ": '{"status":"ok","version":"dev"}'},                # a local build is not a promotable version
    {"FAKE_HEALTHZ": '{"status":"ok","version":"abc; rm -rf /"}'}, {"FAKE_CURL_FAIL": "1"},
])
def test_a_dev_that_does_not_report_a_commit_version_is_refused(tmp_path, env):
    result, read = run_promote(tmp_path, env=env)
    assert result.returncode != 0 and deploy_call(read) is None


def test_a_describe_that_fails_stops_everything(tmp_path):
    error = "PERMISSION_DENIED: caller lacks run.services.get"
    result, read = run_promote(tmp_path, env={"FAKE_DESCRIBE_ERROR": error})
    assert result.returncode != 0 and deploy_call(read) is None


def test_a_failing_prod_deploy_fails_the_promotion(tmp_path):
    result, _ = run_promote(tmp_path, env={"FAKE_DEPLOY_FAIL": "1"})
    assert result.returncode != 0


@pytest.mark.parametrize("env", [
    {"PROJECT_ID": ""}, {"REGION": "us east"}, {"REGISTRY": ""}, {"REGISTRY": "not a registry"}, {"RUNTIME_SA": ""},
    {"RUNTIME_SA": "cohortlens-run-dev@cohortlens.iam.gserviceaccount.com"},
])
def test_bad_settings_are_refused_before_anything_runs(tmp_path, env):
    result, read = run_promote(tmp_path, env=env)
    assert result.returncode != 0 and read["gcloud"] == [] and deploy_call(read) is None


@pytest.mark.parametrize("args", [
    ["--expect-version", "latest"], ["--expect-version"], ["--force"], ["extra"], ["--expect-version", "a; echo hi"],
    ["--expect-version", SHA[:12]],  # an abbreviated sha would never equal the full version dev reports
])
def test_bad_arguments_are_refused_before_anything_runs(tmp_path, args):
    result, read = run_promote(tmp_path, *args)
    assert result.returncode != 0 and read["gcloud"] == [] and deploy_call(read) is None


def test_it_never_changes_dev_deletes_or_touches_iam(tmp_path):
    read = promoted(tmp_path)
    text = " ".join(" ".join(c) for c in read["gcloud"])
    for word in ("delete", "update-traffic", "deploy", "add-iam-policy-binding", "set-iam-policy", "--quiet"):
        assert word not in text, word


# ---- finding A of the review: the image that SERVES traffic, not the newest template -----------------------------

def test_a_failed_candidate_in_dev_never_reaches_prod_the_serving_image_does(tmp_path):
    # a later dev deploy failed its smoke test: the newest revision (and its image) is a tagged candidate with no
    # traffic, while the old revision still serves 100% and /healthz still reports the old version
    pending = traffic({"percent": 100, "revisionName": API_SERVING},
                      {"revisionName": API_CANDIDATE, "tag": "candidate", "url": "https://candidate---x.a.run.app"})
    read = promoted(tmp_path, env={"FAKE_API_TRAFFIC": pending})
    assert deploy_call(read)[deploy_call(read).index("--api-image") + 1] == API_IMAGE
    described = " ".join(" ".join(c) for c in read["gcloud"])
    assert API_CANDIDATE not in described  # the candidate's revision is never even read


@pytest.mark.parametrize("api_traffic", [
    traffic({"percent": 50, "revisionName": API_SERVING}, {"percent": 50, "revisionName": API_CANDIDATE}),
    traffic({"percent": 0, "revisionName": API_SERVING}),
    traffic(),
    traffic({"percent": 100}),
    json.dumps({"status": {}}), "not json", "",
])
def test_a_dev_service_that_does_not_have_exactly_one_revision_serving_all_traffic_is_refused(tmp_path, api_traffic):
    result, read = run_promote(tmp_path, env={"FAKE_API_TRAFFIC": api_traffic})
    assert result.returncode != 0 and deploy_call(read) is None


def test_the_web_service_is_resolved_the_same_way(tmp_path):
    other = "cohortlens-web-dev-00003-d"
    split = traffic({"percent": 60, "revisionName": WEB_SERVING}, {"percent": 40, "revisionName": other})
    result, read = run_promote(tmp_path, env={"FAKE_WEB_TRAFFIC": split})
    assert result.returncode != 0 and deploy_call(read) is None


def test_the_deploy_script_is_run_through_bash_because_git_stores_it_as_mode_100644():
    """Same lesson as the dev deploy (`Permission denied` on the first real run): never execute a repository script
    directly. Windows hides this (every file runs), Linux does not."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'bash "$DEPLOY"' in text
    assert not [line for line in text.splitlines() if line.strip().startswith('"$DEPLOY"')]
