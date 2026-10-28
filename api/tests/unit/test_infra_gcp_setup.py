"""Issue #17 groundwork (E4 delivery): infra/gcp/setup.sh, the one-time Google Cloud setup.

The script runs against a FAKE `gcloud` that records every command, so nothing here touches a real project. What it
must do: nothing at all without `--apply`; enable the APIs, create the Artifact Registry repository, one runtime and
one deploy service account per environment, Workload Identity Federation restricted to this GitHub repository, and
empty Secret Manager slots (values are loaded elsewhere, never by this script); be safe to run twice; never delete
anything and never grant broad roles.
"""
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "infra" / "gcp" / "setup.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the setup script")

PROJECT, REGION, REPO = "cohortlens", "us-east5", "erickyegon/cohortlens"
APIS = {
    "run.googleapis.com", "artifactregistry.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com",
    "sts.googleapis.com", "secretmanager.googleapis.com", "cloudresourcemanager.googleapis.com",
}
SECRETS = {
    "cohortlens-dev-database-url-app", "cohortlens-dev-database-url-reader",
    "cohortlens-prod-database-url-app", "cohortlens-prod-database-url-reader",
}
FAKE = """#!/usr/bin/env bash
# records every call; `describe`/`list` style existence checks succeed only when FAKE_EXISTS=1
echo "$@" >> "$FAKE_LOG"
case "$*" in
  *"projects describe"*) echo "24304250435"; exit 0 ;;
  *" describe "*) [ "${FAKE_EXISTS:-0}" = "1" ] && exit 0 || exit 1 ;;
esac
exit 0
"""


def run_script(tmp_path, *args, exists=False, env=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "gcloud"
    fake.write_text(FAKE, encoding="utf-8", newline="\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "gcloud.log"
    log.write_text("", encoding="utf-8")
    full_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(log),
                "FAKE_EXISTS": "1" if exists else "0", **(env or {})}
    result = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=full_env, timeout=120)
    calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return result, calls


def applied(tmp_path, **kwargs):
    result, calls = run_script(tmp_path, "--apply", **kwargs)
    assert result.returncode == 0, result.stderr
    return result, calls


def writes(calls):
    """The commands that change something (everything except describe/list/get-style reads)."""
    return [c for c in calls if not any(word in c.split() for word in ("describe", "list"))]


def test_the_script_exists_and_is_valid_bash():
    assert SCRIPT.exists()
    check = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


# ---- without --apply it only prints the plan --------------------------------------------------------------------

def test_without_apply_it_prints_a_plan_and_runs_no_gcloud_command_at_all(tmp_path):
    result, calls = run_script(tmp_path)
    assert result.returncode == 0, result.stderr
    assert calls == []
    for needle in ("run.googleapis.com", "artifact-registry", "workload-identity-pools", "secrets create"):
        assert needle in result.stdout, needle
    assert "--apply" in result.stdout  # says how to really run it


def test_the_plan_names_the_project_region_and_repository_it_will_use(tmp_path):
    result, _ = run_script(tmp_path)
    assert PROJECT in result.stdout and REGION in result.stdout and REPO in result.stdout


# ---- with --apply --------------------------------------------------------------------------------------------------

def test_it_enables_exactly_the_documented_apis(tmp_path):
    _, calls = applied(tmp_path)
    enabled = {part for c in calls if c.startswith("services enable")
               for part in c.split() if part.endswith(".googleapis.com")}
    assert enabled == APIS
    assert all(f"--project={PROJECT}" in c for c in calls if c.startswith("services enable"))


def test_it_creates_one_docker_artifact_registry_repository_in_the_region(tmp_path):
    _, calls = applied(tmp_path)
    created = [c for c in calls if c.startswith("artifacts repositories create")]
    assert len(created) == 1
    assert "cohortlens" in created[0] and "--repository-format=docker" in created[0]
    assert f"--location={REGION}" in created[0]


def test_it_creates_a_runtime_and_a_deploy_service_account_for_each_environment(tmp_path):
    _, calls = applied(tmp_path)
    accounts = {c.split()[3] for c in calls if c.startswith("iam service-accounts create")}
    assert accounts == {"cohortlens-run-dev", "cohortlens-run-prod", "cohortlens-deploy-dev", "cohortlens-deploy-prod"}


def test_workload_identity_federation_trusts_github_but_only_this_repository(tmp_path):
    _, calls = applied(tmp_path)
    pool = [c for c in calls if c.startswith("iam workload-identity-pools create ")]
    provider = [c for c in calls if c.startswith("iam workload-identity-pools providers create-oidc")]
    assert len(pool) == 1 and len(provider) == 1
    assert "https://token.actions.githubusercontent.com" in provider[0]
    # the provider itself refuses any other repository
    assert f"assertion.repository == '{REPO}'" in provider[0].replace('"', "")
    assert "attribute.repository=assertion.repository" in provider[0]
    assert "attribute.environment=assertion.environment" in provider[0]
    assert "attribute.ref=assertion.ref" in provider[0]


def test_dev_can_be_impersonated_only_from_main_and_prod_only_from_the_production_environment(tmp_path):
    _, calls = applied(tmp_path)
    bindings = [c for c in calls if c.startswith("iam service-accounts add-iam-policy-binding")
                and "workloadIdentityUser" in c]
    by_account = {c.split()[3].split("@")[0]: c for c in bindings}
    assert set(by_account) == {"cohortlens-deploy-dev", "cohortlens-deploy-prod"}
    assert "attribute.ref/refs/heads/main" in by_account["cohortlens-deploy-dev"]
    assert "attribute.environment/production" in by_account["cohortlens-deploy-prod"]
    assert "attribute.environment" not in by_account["cohortlens-deploy-dev"]
    assert "attribute.ref" not in by_account["cohortlens-deploy-prod"]
    for command in bindings:  # the project NUMBER from `projects describe`, never a wildcard principal
        prefix = "principalSet://iam.googleapis.com/projects/24304250435/locations/global/workloadIdentityPools/"
        assert prefix in command
        assert "allUsers" not in command and "allAuthenticatedUsers" not in command and "principal://" not in command


def test_each_deploy_account_may_act_as_only_its_own_runtime_account(tmp_path):
    _, calls = applied(tmp_path)
    acts_as = [c for c in calls if "roles/iam.serviceAccountUser" in c]
    assert len(acts_as) == 2
    dev = [c for c in acts_as if "cohortlens-run-dev@" in c.split()[3]]
    prod = [c for c in acts_as if "cohortlens-run-prod@" in c.split()[3]]
    assert len(dev) == 1 and "serviceAccount:cohortlens-deploy-dev@" in dev[0]
    assert len(prod) == 1 and "serviceAccount:cohortlens-deploy-prod@" in prod[0]


def test_deploy_accounts_get_only_cloud_run_admin_and_registry_write(tmp_path):
    _, calls = applied(tmp_path)
    granted = {part for c in calls for part in c.split() if part.startswith("--role=") or part.startswith("roles/")}
    granted = {g.removeprefix("--role=") for g in granted}
    assert granted == {"roles/run.admin", "roles/artifactregistry.writer", "roles/iam.serviceAccountUser",
                       "roles/iam.workloadIdentityUser", "roles/secretmanager.secretAccessor"}
    joined = "\n".join(calls)
    for broad in ("roles/owner", "roles/editor", "roles/viewer", "roles/iam.serviceAccountTokenCreator",
                  "roles/iam.serviceAccountAdmin", "roles/resourcemanager"):
        assert broad not in joined, broad


def test_it_creates_four_empty_secret_slots_and_never_stores_a_value(tmp_path):
    _, calls = applied(tmp_path)
    created = {c.split()[2] for c in calls if c.startswith("secrets create")}
    assert created == SECRETS
    assert all("--replication-policy=automatic" in c for c in calls if c.startswith("secrets create"))
    joined = "\n".join(calls)
    assert "versions add" not in joined and "--data-file" not in joined and "--data=" not in joined


def test_each_runtime_account_can_read_only_its_own_environments_secrets(tmp_path):
    _, calls = applied(tmp_path)
    reads = [c for c in calls if c.startswith("secrets add-iam-policy-binding")]
    assert len(reads) == 4
    for command in reads:
        secret, member = command.split()[2], next(p for p in command.split() if p.startswith("--member="))
        environment = "dev" if "-dev-" in secret else "prod"
        assert member == f"--member=serviceAccount:cohortlens-run-{environment}@{PROJECT}.iam.gserviceaccount.com"
        assert "secretmanager.secretAccessor" in command


def test_it_never_deletes_or_removes_anything(tmp_path):
    _, calls = applied(tmp_path)
    for command in calls:
        words = command.split()
        assert "delete" not in words and "remove-iam-policy-binding" not in words and "undelete" not in words, command
        assert "--quiet" not in words


def test_it_does_not_create_billing_budgets_or_projects(tmp_path):
    _, calls = applied(tmp_path)
    joined = "\n".join(calls)
    assert "billing" not in joined and "projects create" not in joined and "organizations" not in joined


# ---- safe to run twice ------------------------------------------------------------------------------------------

def test_a_second_run_creates_nothing_that_already_exists(tmp_path):
    _, calls = applied(tmp_path, exists=True)
    creating = [c for c in writes(calls) if " create" in f" {c}" and not c.startswith("services enable")]
    assert creating == [], creating


def test_enabling_an_api_and_adding_a_policy_binding_are_repeatable(tmp_path):
    _, calls = applied(tmp_path, exists=True)
    assert any(c.startswith("services enable") for c in calls)  # idempotent in gcloud
    assert any("add-iam-policy-binding" in c for c in calls)  # idempotent in gcloud


# ---- settings and refusals ---------------------------------------------------------------------------------------

def test_project_region_and_repository_can_be_overridden_by_environment_variables(tmp_path):
    result, calls = applied(tmp_path, env={"PROJECT_ID": "other-proj", "REGION": "europe-west1",
                                           "GITHUB_REPO": "someone/else"})
    assert all("--project=other-proj" in c for c in calls if c.startswith("services enable"))
    assert any("--location=europe-west1" in c for c in calls if c.startswith("artifacts repositories create"))
    provider = next(c for c in calls if "providers create-oidc" in c)
    assert "assertion.repository == 'someone/else'" in provider.replace('"', "")
    assert "erickyegon" not in provider


@pytest.mark.parametrize("name,value", [
    ("GITHUB_REPO", "not-a-repo"), ("GITHUB_REPO", "a/b/c"), ("GITHUB_REPO", "a/b; rm -rf /"), ("GITHUB_REPO", ""),
    ("PROJECT_ID", ""), ("PROJECT_ID", "Bad_ID"), ("PROJECT_ID", "x; echo hi"), ("REGION", "us east"), ("REGION", ""),
])
def test_it_refuses_malformed_settings_before_running_any_command(tmp_path, name, value):
    result, calls = run_script(tmp_path, "--apply", env={name: value})
    assert result.returncode != 0
    assert calls == []


def test_it_refuses_an_unknown_argument(tmp_path):
    result, calls = run_script(tmp_path, "--wipe")
    assert result.returncode != 0 and calls == []


def test_a_failing_gcloud_command_stops_the_script_instead_of_carrying_on(tmp_path):
    failing = tmp_path / "bin"
    failing.mkdir(exist_ok=True)
    (failing / "gcloud").write_text(
        "#!/usr/bin/env bash\necho \"$@\" >> \"$FAKE_LOG\"\nexit 1\n", encoding="utf-8", newline="\n")
    log = tmp_path / "gcloud.log"
    log.write_text("", encoding="utf-8")
    env = {**os.environ, "PATH": f"{failing}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(log)}
    (failing / "gcloud").chmod((failing / "gcloud").stat().st_mode | stat.S_IEXEC)
    result = subprocess.run([BASH, str(SCRIPT), "--apply"], capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode != 0
    assert len(log.read_text(encoding="utf-8").splitlines()) <= 3  # it did not push on through every step


# ---- what it tells the human at the end -------------------------------------------------------------------------

def test_it_prints_the_values_github_needs_and_no_secrets(tmp_path):
    result, _ = applied(tmp_path)
    out = result.stdout
    assert "projects/24304250435/locations/global/workloadIdentityPools/github/providers/github-actions" in out
    assert f"cohortlens-deploy-dev@{PROJECT}.iam.gserviceaccount.com" in out
    assert f"cohortlens-deploy-prod@{PROJECT}.iam.gserviceaccount.com" in out
    assert "password" not in out.lower() and "postgresql://" not in out
