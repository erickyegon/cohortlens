"""Issue #17 (E4 delivery): infra/gcp/deploy.sh, the dev deploy that .github/workflows/deploy-dev.yml runs.

Like the setup script's tests, these run the script against a FAKE `gcloud` that records every call and a FAKE smoke
script, so nothing touches a real project. What it must do, in this order: run the database migrations as a Cloud Run
job BEFORE any new revision exists; deploy each service as a revision that gets NO traffic (a tagged candidate); smoke
test the candidate; only then move traffic to it. A failed migration or a failed smoke test must leave traffic where it
was. Images are pinned by digest, secrets are passed by reference, and nothing is ever deleted.
"""
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra" / "gcp" / "deploy.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the deploy script")

PROJECT, REGION = "cohortlens", "us-east5"
RUNTIME_SA = "cohortlens-run-dev@cohortlens.iam.gserviceaccount.com"
PROD_RUNTIME_SA = "cohortlens-run-prod@cohortlens.iam.gserviceaccount.com"
REGISTRY = "us-east5-docker.pkg.dev/cohortlens/cohortlens"
API_IMAGE = f"{REGISTRY}/api@sha256:{'a' * 64}"
WEB_IMAGE = f"{REGISTRY}/web@sha256:{'b' * 64}"
SHA = "0123456789abcdef0123456789abcdef01234567"
SEP = "\x1f"

FAKE_GCLOUD = r"""#!/usr/bin/env bash
# records every call (arguments separated by the unit separator)
printf '%s\x1f' "$@" >> "$FAKE_LOG"; printf '\n' >> "$FAKE_LOG"
args="$*"
if [[ "$args" == *"run services describe"* ]]; then
  if [ -n "${FAKE_DESCRIBE_ERROR:-}" ]; then echo "ERROR: (gcloud) $FAKE_DESCRIBE_ERROR" >&2; exit 1; fi
  # a service that did not exist does exist once it has been deployed
  deployed=0
  for svc in cohortlens-api-dev cohortlens-web-dev cohortlens-api-prod cohortlens-web-prod; do
    if [[ "$args" == *"$svc"* ]] && grep -qa "run"$'\x1f'"deploy"$'\x1f'"$svc" "$FAKE_LOG"; then deployed=1; fi
  done
  if [ "${FAKE_EXISTS:-1}" != "1" ] && [ "$deployed" = "0" ]; then
    echo "ERROR: (gcloud.run.services.describe) Cannot find service" >&2; exit 1
  fi
  for svc in cohortlens-api-dev cohortlens-web-dev cohortlens-api-prod cohortlens-web-prod; do
    if [[ "$args" == *"run services describe $svc "* ]]; then echo "https://$svc-abc123-ul.a.run.app"; fi
  done
  exit 0
fi
if [[ "$args" == *"run jobs execute"* ]] && [ "${FAKE_MIGRATE_FAIL:-0}" = "1" ]; then
  echo "ERROR: migration failed" >&2; exit 1
fi
exit 0
"""

FAKE_SMOKE = r"""#!/usr/bin/env bash
# the fake smoke test: records the URL it was asked to test, fails when told to
printf '%s\x1f' "$@" >> "$FAKE_SMOKE_LOG"; printf '\n' >> "$FAKE_SMOKE_LOG"
case "$*" in
  *"$FAKE_SMOKE_FAIL_ON"*) [ -n "$FAKE_SMOKE_FAIL_ON" ] && { echo "smoke failed" >&2; exit 1; } ;;
esac
exit 0
"""


def _install(directory, name, body):
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run_deploy(tmp_path, *, env=None, args=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _install(bin_dir, "gcloud", FAKE_GCLOUD)
    _install(bin_dir, "smoke", FAKE_SMOKE)
    log, smoke_log = tmp_path / "gcloud.log", tmp_path / "smoke.log"
    log.write_text("", encoding="utf-8")
    smoke_log.write_text("", encoding="utf-8")
    full_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(log),
                "FAKE_SMOKE_LOG": str(smoke_log), "FAKE_SMOKE_FAIL_ON": "", "SMOKE_SCRIPT": str(bin_dir / "smoke"),
                "PROJECT_ID": PROJECT, "REGION": REGION, "RUNTIME_SA": RUNTIME_SA, **(env or {})}
    if args is None:
        args = ["--env", "dev", "--version", SHA, "--api-image", API_IMAGE, "--web-image", WEB_IMAGE]
    result = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=full_env, timeout=120)
    calls = [line.split(SEP)[:-1] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    smokes = [line.split(SEP)[:-1] for line in smoke_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return result, calls, smokes


def deployed(tmp_path, **kwargs):
    result, calls, smokes = run_deploy(tmp_path, **kwargs)
    assert result.returncode == 0, result.stderr + result.stdout
    return calls, smokes


def starting(calls, *words):
    return [c for c in calls if c[:len(words)] == list(words)]


def index_of(calls, *words, nth=0):
    return [i for i, c in enumerate(calls) if c[:len(words)] == list(words)][nth]


def flag(call, name):
    prefix = f"--{name}="
    values = [part[len(prefix):] for part in call if part.startswith(prefix)]
    assert len(values) <= 1, call
    return values[0] if values else None


def test_the_script_exists_and_is_valid_bash_with_strict_mode():
    assert SCRIPT.exists()
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


# ---- the order is the safety property ----------------------------------------------------------------------------

def test_migrations_run_before_any_revision_is_deployed_and_traffic_moves_last(tmp_path):
    calls, _ = deployed(tmp_path)
    migrate_deploy = index_of(calls, "run", "jobs", "deploy")
    migrate_run = index_of(calls, "run", "jobs", "execute")
    api_deploy = index_of(calls, "run", "deploy", "cohortlens-api-dev")
    api_traffic = index_of(calls, "run", "services", "update-traffic", "cohortlens-api-dev")
    web_deploy = index_of(calls, "run", "deploy", "cohortlens-web-dev")
    web_traffic = index_of(calls, "run", "services", "update-traffic", "cohortlens-web-dev")
    assert migrate_deploy < migrate_run < api_deploy < api_traffic < web_deploy < web_traffic


def test_the_migration_job_waits_for_the_migration_to_finish(tmp_path):
    calls, _ = deployed(tmp_path)
    execute = starting(calls, "run", "jobs", "execute")[0]
    assert "--wait" in execute and execute[3] == "cohortlens-migrate-dev"
    assert flag(execute, "region") == REGION and flag(execute, "project") == PROJECT


def test_a_failed_migration_deploys_no_revision_and_moves_no_traffic(tmp_path):
    result, calls, smokes = run_deploy(tmp_path, env={"FAKE_MIGRATE_FAIL": "1"})
    assert result.returncode != 0
    assert starting(calls, "run", "deploy") == [] and starting(calls, "run", "services", "update-traffic") == []
    assert smokes == []


def test_the_migration_job_runs_alembic_with_the_app_secret_as_the_runtime_account(tmp_path):
    calls, _ = deployed(tmp_path)
    job = starting(calls, "run", "jobs", "deploy")[0]
    assert job[3] == "cohortlens-migrate-dev" and flag(job, "image") == API_IMAGE
    assert flag(job, "service-account") == RUNTIME_SA
    assert flag(job, "set-secrets") == "DATABASE_URL_APP=cohortlens-dev-database-url-app:latest"
    assert flag(job, "command") == "alembic" and flag(job, "args") == "upgrade,head"
    assert flag(job, "max-retries") == "0"


# ---- candidate revisions, smoke test, then traffic ---------------------------------------------------------------

def test_an_existing_service_gets_a_tagged_revision_with_no_traffic(tmp_path):
    calls, _ = deployed(tmp_path)
    for name, image in (("cohortlens-api-dev", API_IMAGE), ("cohortlens-web-dev", WEB_IMAGE)):
        deploy = starting(calls, "run", "deploy", name)[0]
        assert "--no-traffic" in deploy and flag(deploy, "tag") == "candidate"
        assert flag(deploy, "image") == image and flag(deploy, "service-account") == RUNTIME_SA
        assert flag(deploy, "region") == REGION and flag(deploy, "project") == PROJECT


def test_the_first_deploy_of_a_service_cannot_use_no_traffic(tmp_path):
    calls, _ = deployed(tmp_path, env={"FAKE_EXISTS": "0"})
    for name in ("cohortlens-api-dev", "cohortlens-web-dev"):
        deploy = starting(calls, "run", "deploy", name)[0]
        assert "--no-traffic" not in deploy and flag(deploy, "tag") is None  # nothing to protect yet


def test_each_candidate_is_smoke_tested_on_its_tagged_url_before_traffic_moves(tmp_path):
    calls, smokes = deployed(tmp_path)
    assert [s[0] for s in smokes] == ["https://candidate---cohortlens-api-dev-abc123-ul.a.run.app",
                                      "https://candidate---cohortlens-web-dev-abc123-ul.a.run.app"]
    assert smokes[1][1:] == ["--api-prefix", "/api"]  # the web smoke goes through the nginx proxy


def test_a_failed_smoke_test_of_the_api_candidate_moves_no_traffic_and_deploys_no_web(tmp_path):
    result, calls, _ = run_deploy(tmp_path, env={"FAKE_SMOKE_FAIL_ON": "cohortlens-api-dev"})
    assert result.returncode != 0
    assert starting(calls, "run", "services", "update-traffic") == []
    assert starting(calls, "run", "deploy", "cohortlens-web-dev") == []


def test_a_failed_smoke_test_of_the_web_candidate_leaves_web_traffic_alone(tmp_path):
    result, calls, _ = run_deploy(tmp_path, env={"FAKE_SMOKE_FAIL_ON": "cohortlens-web-dev"})
    assert result.returncode != 0
    assert starting(calls, "run", "services", "update-traffic", "cohortlens-web-dev") == []


def test_traffic_moves_to_the_latest_revision_and_the_candidate_tag_is_removed(tmp_path):
    calls, _ = deployed(tmp_path)
    for name in ("cohortlens-api-dev", "cohortlens-web-dev"):
        move = starting(calls, "run", "services", "update-traffic", name)[0]
        assert "--to-latest" in move and flag(move, "remove-tags") == "candidate"
        assert flag(move, "region") == REGION and flag(move, "project") == PROJECT


def test_the_web_service_is_pointed_at_the_api_url_and_the_api_gets_the_version(tmp_path):
    calls, _ = deployed(tmp_path)
    api = starting(calls, "run", "deploy", "cohortlens-api-dev")[0]
    web = starting(calls, "run", "deploy", "cohortlens-web-dev")[0]
    assert flag(api, "set-env-vars") == f"SERVICE_VERSION={SHA},DEPLOYMENT_ENVIRONMENT=dev"
    env = dict(item.split("=", 1) for item in flag(web, "set-env-vars").split(","))
    assert env["API_UPSTREAM"] == "https://cohortlens-api-dev-abc123-ul.a.run.app"
    assert env["API_HOST"] == "cohortlens-api-dev-abc123-ul.a.run.app"
    assert env["NGINX_ENTRYPOINT_LOCAL_RESOLVERS"] == "1"
    assert flag(api, "port") == "8000" and flag(web, "port") == "8080"


# ---- secrets and least surprise ----------------------------------------------------------------------------------

def test_secrets_are_passed_by_reference_to_the_api_only(tmp_path):
    calls, _ = deployed(tmp_path)
    api = starting(calls, "run", "deploy", "cohortlens-api-dev")[0]
    web = starting(calls, "run", "deploy", "cohortlens-web-dev")[0]
    assert flag(api, "set-secrets") == ("DATABASE_URL_APP=cohortlens-dev-database-url-app:latest,"
                                        "DATABASE_URL_READER=cohortlens-dev-database-url-reader:latest")
    assert flag(web, "set-secrets") is None
    text = " ".join(" ".join(c) for c in calls)
    assert "postgresql://" not in text and "password" not in text.lower()


def test_nothing_is_deleted_and_no_iam_policy_is_touched(tmp_path):
    calls, _ = deployed(tmp_path)
    text = " ".join(" ".join(c) for c in calls)
    for word in ("delete", "remove-iam", "add-iam-policy-binding", "set-iam-policy", "--quiet"):
        assert word not in text, word


def test_a_describe_that_fails_for_another_reason_is_not_treated_as_a_first_deploy(tmp_path):
    error = "PERMISSION_DENIED: caller lacks run.services.get"
    result, calls, _ = run_deploy(tmp_path, env={"FAKE_DESCRIBE_ERROR": error})
    assert result.returncode != 0
    assert starting(calls, "run", "deploy") == [] and starting(calls, "run", "services", "update-traffic") == []


# ---- arguments and settings are validated before any command runs -----------------------------------------------

@pytest.mark.parametrize("args", [
    ["--env", "staging", "--version", SHA, "--api-image", API_IMAGE, "--web-image", WEB_IMAGE],
    ["--env", "dev", "--version", "latest", "--api-image", API_IMAGE, "--web-image", WEB_IMAGE],
    ["--env", "dev", "--version", SHA, "--api-image", f"{REGISTRY}/api:latest", "--web-image", WEB_IMAGE],
    ["--env", "dev", "--version", SHA, "--api-image", API_IMAGE, "--web-image", f"{REGISTRY}/web:{SHA}"],
    ["--env", "dev", "--version", SHA, "--api-image", API_IMAGE],
    ["--env", "dev", "--version", SHA, "--api-image", API_IMAGE, "--web-image", WEB_IMAGE, "--force"],
    [],
])
def test_bad_arguments_are_refused_before_any_command_runs(tmp_path, args):
    result, calls, smokes = run_deploy(tmp_path, args=args)
    assert result.returncode != 0 and calls == [] and smokes == []


@pytest.mark.parametrize("env", [{"PROJECT_ID": ""}, {"PROJECT_ID": "Bad Project"}, {"REGION": "us east"},
                                 {"RUNTIME_SA": ""}, {"RUNTIME_SA": "not-an-email"}])
def test_bad_settings_are_refused_before_any_command_runs(tmp_path, env):
    result, calls, _ = run_deploy(tmp_path, env=env)
    assert result.returncode != 0 and calls == []


@pytest.mark.parametrize("flag_name", ["--env", "--version", "--api-image", "--web-image"])
def test_a_flag_without_its_value_is_refused_instead_of_looping_forever(tmp_path, flag_name):
    result = subprocess.run([BASH, str(SCRIPT), flag_name], capture_output=True, text=True, timeout=15)
    assert result.returncode == 2 and flag_name in result.stderr


# ---- production (issue #18): the same script, the prod names, secrets and runtime account ----------------------------

PROD_ARGS = ["--env", "prod", "--version", SHA, "--api-image", API_IMAGE, "--web-image", WEB_IMAGE]
PROD_ENV = {"RUNTIME_SA": PROD_RUNTIME_SA}


def test_prod_runs_the_same_order_with_prod_names(tmp_path):
    calls, smokes = deployed(tmp_path, args=PROD_ARGS, env=PROD_ENV)
    order = [index_of(calls, "run", "jobs", "deploy"), index_of(calls, "run", "jobs", "execute"),
             index_of(calls, "run", "deploy", "cohortlens-api-prod"),
             index_of(calls, "run", "services", "update-traffic", "cohortlens-api-prod"),
             index_of(calls, "run", "deploy", "cohortlens-web-prod"),
             index_of(calls, "run", "services", "update-traffic", "cohortlens-web-prod")]
    assert order == sorted(order)
    assert [s[0] for s in smokes] == ["https://candidate---cohortlens-api-prod-abc123-ul.a.run.app",
                                      "https://candidate---cohortlens-web-prod-abc123-ul.a.run.app"]


def test_prod_uses_the_prod_secrets_runtime_account_and_the_digests_it_was_given(tmp_path):
    calls, _ = deployed(tmp_path, args=PROD_ARGS, env=PROD_ENV)
    job = starting(calls, "run", "jobs", "deploy")[0]
    assert job[3] == "cohortlens-migrate-prod" and flag(job, "image") == API_IMAGE
    assert flag(job, "set-secrets") == "DATABASE_URL_APP=cohortlens-prod-database-url-app:latest"
    api = starting(calls, "run", "deploy", "cohortlens-api-prod")[0]
    web = starting(calls, "run", "deploy", "cohortlens-web-prod")[0]
    assert flag(api, "image") == API_IMAGE and flag(web, "image") == WEB_IMAGE
    assert flag(api, "service-account") == PROD_RUNTIME_SA and flag(web, "service-account") == PROD_RUNTIME_SA
    assert flag(api, "set-secrets") == ("DATABASE_URL_APP=cohortlens-prod-database-url-app:latest,"
                                        "DATABASE_URL_READER=cohortlens-prod-database-url-reader:latest")
    assert flag(api, "set-env-vars") == f"SERVICE_VERSION={SHA},DEPLOYMENT_ENVIRONMENT=prod"
    text = " ".join(" ".join(c) for c in calls)
    assert "-dev" not in text  # nothing of dev is touched or referenced by a prod deploy


def test_a_prod_candidate_is_tagged_with_no_traffic_and_a_failed_smoke_test_moves_nothing(tmp_path):
    calls, _ = deployed(tmp_path, args=PROD_ARGS, env=PROD_ENV)
    for name in ("cohortlens-api-prod", "cohortlens-web-prod"):
        deploy = starting(calls, "run", "deploy", name)[0]
        assert "--no-traffic" in deploy and flag(deploy, "tag") == "candidate"
    failing = {**PROD_ENV, "FAKE_SMOKE_FAIL_ON": "cohortlens-api-prod"}
    result, calls, _ = run_deploy(tmp_path, args=PROD_ARGS, env=failing)
    assert result.returncode != 0 and starting(calls, "run", "services", "update-traffic") == []


def test_prod_refuses_the_dev_runtime_account(tmp_path):
    result, calls, _ = run_deploy(tmp_path, args=PROD_ARGS)  # RUNTIME_SA defaults to the dev account in this harness
    assert result.returncode != 0 and calls == []


def test_dev_refuses_the_prod_runtime_account(tmp_path):
    result, calls, _ = run_deploy(tmp_path, env=PROD_ENV)  # --env dev with the prod account
    assert result.returncode != 0 and calls == []
