"""Issue #17 groundwork (E4 delivery): infra/gcp/setup.sh, the one-time Google Cloud setup.

This script decides who may deploy to production, so its tests pin the security properties EXACTLY (full argument lists
and exact sets of bindings, not substrings). It runs against a FAKE `gcloud` and a FAKE `gh` that record every call, so
nothing here touches a real project or GitHub. What it must do: nothing at all without `--apply`; refuse to apply unless
the GitHub `production` environment is protected; enable the APIs, create the Artifact Registry repository, one runtime
and one deploy service account per environment, Workload Identity Federation pinned to this repository's NAME and
numeric IDs, and empty Secret Manager slots; be safe to run twice and never trust an existing resource blindly; never
delete anything and never grant broad roles.
"""
import os
import pathlib
import shlex
import shutil
import stat
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "infra" / "gcp" / "setup.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the setup script")

PROJECT, REGION, REPO = "cohortlens", "us-east5", "erickyegon/cohortlens"
NUMBER, REPO_ID, OWNER_ID = "24304250435", "1234567", "7654321"
APIS = {
    "run.googleapis.com", "artifactregistry.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com",
    "sts.googleapis.com", "secretmanager.googleapis.com", "cloudresourcemanager.googleapis.com",
}
SECRETS = {
    "cohortlens-dev-database-url-app", "cohortlens-dev-database-url-reader",
    "cohortlens-prod-database-url-app", "cohortlens-prod-database-url-reader",
}
SEP = "\x1f"
EXPECTED_CONDITION = (f"assertion.repository_id == '{REPO_ID}' && assertion.repository_owner_id == '{OWNER_ID}' "
                      f"&& assertion.repository == '{REPO}'")
EXPECTED_MAPPING = ("google.subject=assertion.sub,attribute.repository=assertion.repository,"
                    "attribute.ref=assertion.ref,attribute.environment=assertion.environment")
POOL_ROOT = f"principalSet://iam.googleapis.com/projects/{NUMBER}/locations/global/workloadIdentityPools/github"

FAKE_GCLOUD = r"""#!/usr/bin/env bash
# records every call (arguments separated by the unit separator); describe succeeds only when FAKE_EXISTS=1
printf '%s\x1f' "$@" >> "$FAKE_LOG"; printf '\n' >> "$FAKE_LOG"
args="$*"
if [[ "$args" == *"projects describe"* ]]; then echo "${FAKE_NUMBER-24304250435}"; exit 0; fi
if [[ "$args" == *" describe "* ]]; then
  if [ -n "${FAKE_DESCRIBE_ERROR:-}" ]; then echo "ERROR: (gcloud) $FAKE_DESCRIBE_ERROR" >&2; exit 1; fi
  if [ "${FAKE_EXISTS:-0}" != "1" ]; then echo "ERROR: (gcloud) NOT_FOUND: resource not found" >&2; exit 1; fi
  if [[ "$args" == *"attributeCondition"* ]]; then echo "${FAKE_CONDITION:-}"; fi
  if [[ "$args" == *"value(state)"* ]]; then echo "${FAKE_POOL_STATE:-ACTIVE}"; fi
  exit 0
fi
exit 0
"""

FAKE_GH = r"""#!/usr/bin/env bash
# a fake `gh api`: answers the few read-only questions the script asks about the repository and its environment
printf '%s\x1f' "$@" >> "$FAKE_GH_LOG"; printf '\n' >> "$FAKE_GH_LOG"
args="$*"
if [[ "$args" == *"deployment-branch-policies"* ]]; then echo "${FAKE_GH_BRANCHES-main}"; exit 0; fi
if [[ "$args" == *"environments/production"* ]]; then
  if [ "${FAKE_GH_ENV:-ok}" = "missing" ]; then echo "gh: Not Found (HTTP 404)" >&2; exit 1; fi
  if [[ "$args" == *"required_reviewers"* ]]; then echo "${FAKE_GH_REVIEWERS-1}"; exit 0; fi
  if [[ "$args" == *"custom_branch_policies"* ]]; then echo "${FAKE_GH_CUSTOM-true}"; exit 0; fi
fi
if [[ "$args" == *".owner.id"* ]]; then echo "${FAKE_OWNER_ID-7654321}"; exit 0; fi
if [[ "$args" == *".id"* ]]; then echo "${FAKE_REPO_ID-1234567}"; exit 0; fi
exit 0
"""


def _install(directory, name, body):
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run_script(tmp_path, *args, exists=False, env=None, gcloud=FAKE_GCLOUD):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _install(bin_dir, "gcloud", gcloud)
    _install(bin_dir, "gh", FAKE_GH)
    log, gh_log = tmp_path / "gcloud.log", tmp_path / "gh.log"
    log.write_text("", encoding="utf-8")
    gh_log.write_text("", encoding="utf-8")
    full_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(log),
                "FAKE_GH_LOG": str(gh_log), "FAKE_EXISTS": "1" if exists else "0",
                "FAKE_CONDITION": EXPECTED_CONDITION, **(env or {})}
    for name in ("PROJECT_ID", "REGION", "GITHUB_REPO"):
        if env is None or name not in env:
            full_env.pop(name, None)  # a developer's shell must not leak into the tests
    result = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=full_env, timeout=120)
    calls = [line.split(SEP)[:-1] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return result, calls


def applied(tmp_path, **kwargs):
    result, calls = run_script(tmp_path, "--apply", **kwargs)
    assert result.returncode == 0, result.stderr + result.stdout
    return result, calls


def joined(call):
    return " ".join(call)


def flag(call, name):
    """The value of --name=value in a call (None if absent)."""
    prefix = f"--{name}="
    values = [part[len(prefix):] for part in call if part.startswith(prefix)]
    assert len(values) <= 1, call
    return values[0] if values else None


def starting(calls, *words):
    return [c for c in calls if c[:len(words)] == list(words)]


def test_the_script_exists_and_is_valid_bash_with_strict_mode():
    assert SCRIPT.exists()
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


# ---- without --apply it only prints the plan --------------------------------------------------------------------

def test_without_apply_it_prints_a_plan_and_runs_no_command_at_all(tmp_path):
    result, calls = run_script(tmp_path)
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert (tmp_path / "gh.log").read_text(encoding="utf-8") == ""  # not even a read-only GitHub call
    for needle in ("run.googleapis.com", "artifact-registry", "workload-identity-pools", "secrets create"):
        assert needle in result.stdout, needle
    assert "--apply" in result.stdout
    assert PROJECT in result.stdout and REGION in result.stdout and REPO in result.stdout


def test_the_plan_is_copy_pasteable_so_the_condition_stays_one_argument(tmp_path):
    result, _ = run_script(tmp_path)
    line = next(item for item in result.stdout.splitlines() if "providers create-oidc" in item)
    parts = shlex.split(line)  # must parse back into separate arguments, the condition as one
    conditions = [part for part in parts if part.startswith("--attribute-condition=")]
    assert len(conditions) == 1 and "assertion.repository_id" in conditions[0]


def test_the_plan_states_the_prerequisites_and_what_apply_will_check(tmp_path):
    result, _ = run_script(tmp_path)
    text = result.stdout.lower()
    assert "production" in text and "required reviewer" in text and "main" in text


# ---- the GitHub production environment must be protected before applying ---------------------------------------

@pytest.mark.parametrize("env", [
    {"FAKE_GH_ENV": "missing"}, {"FAKE_GH_REVIEWERS": "0"}, {"FAKE_GH_REVIEWERS": ""}, {"FAKE_GH_CUSTOM": "false"},
    {"FAKE_GH_CUSTOM": ""}, {"FAKE_GH_BRANCHES": ""}, {"FAKE_GH_BRANCHES": "main,dev"}, {"FAKE_GH_BRANCHES": "*"},
    {"FAKE_GH_BRANCHES": "main,*"}, {"FAKE_GH_BRANCHES": "release"},
])
def test_it_refuses_to_apply_while_the_production_environment_is_unprotected(tmp_path, env):
    result, calls = run_script(tmp_path, "--apply", env=env)
    assert result.returncode != 0
    assert calls == []  # nothing in Google Cloud was touched, not even a read
    assert "production" in (result.stderr + result.stdout)


def test_it_applies_when_the_environment_has_a_reviewer_and_is_limited_to_main(tmp_path):
    applied(tmp_path, env={"FAKE_GH_REVIEWERS": "2"})


def test_the_explicit_skip_flag_bypasses_the_check_loudly(tmp_path):
    result, calls = run_script(tmp_path, "--apply", "--skip-github-check", env={"FAKE_GH_ENV": "missing"})
    assert result.returncode == 0 and calls
    assert "skip" in (result.stdout + result.stderr).lower()


# ---- with --apply: what it enables and creates --------------------------------------------------------------------

def test_it_enables_exactly_the_documented_apis_on_the_right_project(tmp_path):
    _, calls = applied(tmp_path)
    enabling = starting(calls, "services", "enable")
    assert len(enabling) == 1
    assert {part for part in enabling[0] if part.endswith(".googleapis.com")} == APIS
    assert flag(enabling[0], "project") == PROJECT


def test_it_creates_one_docker_artifact_registry_repository_in_the_region(tmp_path):
    _, calls = applied(tmp_path)
    created = starting(calls, "artifacts", "repositories", "create")
    assert len(created) == 1
    assert created[0][3] == "cohortlens" and flag(created[0], "repository-format") == "docker"
    assert flag(created[0], "location") == REGION and flag(created[0], "project") == PROJECT


def test_it_creates_a_runtime_and_a_deploy_service_account_for_each_environment(tmp_path):
    _, calls = applied(tmp_path)
    created = starting(calls, "iam", "service-accounts", "create")
    assert sorted(c[3] for c in created) == ["cohortlens-deploy-dev", "cohortlens-deploy-prod",
                                             "cohortlens-run-dev", "cohortlens-run-prod"]
    assert all(flag(c, "project") == PROJECT for c in created)


# ---- Workload Identity Federation: pinned to this repository, by name AND by numeric id -----------------------------

def test_the_pool_and_provider_are_created_once_with_the_exact_github_issuer_and_mapping(tmp_path):
    _, calls = applied(tmp_path)
    pools = starting(calls, "iam", "workload-identity-pools", "create")
    providers = starting(calls, "iam", "workload-identity-pools", "providers", "create-oidc")
    assert len(pools) == 1 and pools[0][3] == "github" and flag(pools[0], "location") == "global"
    assert len(providers) == 1
    provider = providers[0]
    assert provider[4] == "github-actions" and flag(provider, "workload-identity-pool") == "github"
    assert flag(provider, "issuer-uri") == "https://token.actions.githubusercontent.com"
    assert flag(provider, "attribute-mapping") == EXPECTED_MAPPING
    assert flag(provider, "project") == PROJECT and flag(provider, "location") == "global"


def test_the_provider_condition_is_exactly_name_and_numeric_ids_all_required(tmp_path):
    _, calls = applied(tmp_path)
    provider = starting(calls, "iam", "workload-identity-pools", "providers", "create-oidc")[0]
    condition = flag(provider, "attribute-condition")
    assert condition == EXPECTED_CONDITION  # exact: no '|| true', no extra clause, no missing id
    assert "||" not in condition


def test_the_numeric_ids_are_read_from_github_at_apply_time(tmp_path):
    _, calls = applied(tmp_path, env={"FAKE_REPO_ID": "42", "FAKE_OWNER_ID": "77"})
    provider = starting(calls, "iam", "workload-identity-pools", "providers", "create-oidc")[0]
    condition = flag(provider, "attribute-condition")
    assert "assertion.repository_id == '42'" in condition and "assertion.repository_owner_id == '77'" in condition


@pytest.mark.parametrize("env", [{"FAKE_REPO_ID": ""}, {"FAKE_OWNER_ID": ""}, {"FAKE_REPO_ID": "abc"},
                                 {"FAKE_OWNER_ID": "1 2"}])
def test_it_refuses_to_apply_with_a_missing_or_malformed_github_id(tmp_path, env):
    result, calls = run_script(tmp_path, "--apply", env=env)
    assert result.returncode != 0 and calls == []


# ---- least privilege: the complete, exact set of IAM grants -----------------------------------------------------

def all_bindings(calls):
    found = []
    for call in calls:
        if "add-iam-policy-binding" in call:
            at = call.index("add-iam-policy-binding")
            found.append((" ".join(call[:at + 1]), call[at + 1], flag(call, "role"), flag(call, "member"),
                          flag(call, "location"), flag(call, "project"), flag(call, "condition")))
    return found


def sa(kind, environment):
    return f"cohortlens-{kind}-{environment}@{PROJECT}.iam.gserviceaccount.com"


def member(kind, environment):
    return f"serviceAccount:{sa(kind, environment)}"


def test_the_complete_set_of_grants_is_exactly_this_and_nothing_else(tmp_path):
    _, calls = applied(tmp_path)
    sab = "iam service-accounts add-iam-policy-binding"
    expected = {
        # who may impersonate the deploy accounts
        (sab, sa("deploy", "dev"), "roles/iam.workloadIdentityUser", f"{POOL_ROOT}/attribute.ref/refs/heads/main",
         None, PROJECT, None),
        (sab, sa("deploy", "prod"), "roles/iam.workloadIdentityUser",
         f"{POOL_ROOT}/attribute.environment/production", None, PROJECT, None),
        # per deploy account: Cloud Run admin, write ITS registry repository, act as its OWN runtime account
        ("projects add-iam-policy-binding", PROJECT, "roles/run.admin", member("deploy", "dev"), None, None, "None"),
        ("projects add-iam-policy-binding", PROJECT, "roles/run.admin", member("deploy", "prod"), None, None, "None"),
        ("artifacts repositories add-iam-policy-binding", "cohortlens", "roles/artifactregistry.writer",
         member("deploy", "dev"), REGION, PROJECT, None),
        ("artifacts repositories add-iam-policy-binding", "cohortlens", "roles/artifactregistry.writer",
         member("deploy", "prod"), REGION, PROJECT, None),
        (sab, sa("run", "dev"), "roles/iam.serviceAccountUser", member("deploy", "dev"), None, PROJECT, None),
        (sab, sa("run", "prod"), "roles/iam.serviceAccountUser", member("deploy", "prod"), None, PROJECT, None),
    }
    for environment in ("dev", "prod"):  # each runtime account reads ONLY its own environment's two secrets
        for kind in ("app", "reader"):
            expected.add(("secrets add-iam-policy-binding", f"cohortlens-{environment}-database-url-{kind}",
                          "roles/secretmanager.secretAccessor", member("run", environment), None, PROJECT, None))
    found = all_bindings(calls)
    assert len(found) == len(set(found)), "a binding is granted twice"
    assert set(found) == expected


def test_nothing_is_granted_to_the_public_or_to_a_broad_principal(tmp_path):
    _, calls = applied(tmp_path)
    everything = "\n".join(joined(c) for c in calls)
    for broad in ("allUsers", "allAuthenticatedUsers", "domain:", "group:", "user:", "roles/owner", "roles/editor",
                  "roles/viewer", "roles/iam.serviceAccountTokenCreator", "roles/iam.serviceAccountAdmin",
                  "roles/resourcemanager", "roles/secretmanager.admin", "roles/iam.securityAdmin"):
        assert broad not in everything, broad


def test_every_command_names_the_project_explicitly(tmp_path):
    _, calls = applied(tmp_path)
    for call in calls:
        if call[:2] in (["projects", "describe"], ["projects", "add-iam-policy-binding"]):
            continue  # these take the project as an argument
        assert flag(call, "project") == PROJECT, call


def test_it_creates_four_empty_secret_slots_and_never_stores_a_value(tmp_path):
    _, calls = applied(tmp_path)
    created = starting(calls, "secrets", "create")
    assert {c[2] for c in created} == SECRETS and len(created) == 4
    assert all(flag(c, "replication-policy") == "automatic" and flag(c, "project") == PROJECT for c in created)
    everything = "\n".join(joined(c) for c in calls)
    for forbidden in ("versions", "--data-file", "--data=", "--set-secrets"):
        assert forbidden not in everything, forbidden


def test_it_never_deletes_removes_or_forces_anything(tmp_path):
    _, calls = applied(tmp_path)
    forbidden = {"delete", "remove-iam-policy-binding", "undelete", "set-iam-policy", "--quiet", "--force"}
    for call in calls:
        assert not forbidden & set(call), call


def test_it_does_not_create_billing_budgets_or_projects(tmp_path):
    _, calls = applied(tmp_path)
    everything = "\n".join(joined(c) for c in calls)
    assert "billing" not in everything and "projects create" not in everything and "organizations" not in everything


# ---- safe to run twice, and never trusting an existing resource blindly ----------------------------------------

def test_a_second_run_creates_nothing_that_already_exists(tmp_path):
    _, calls = applied(tmp_path, exists=True)
    creating = [c for c in calls if "create" in c or "create-oidc" in c]
    assert creating == [], creating


def test_a_second_run_still_applies_the_idempotent_enable_and_bindings(tmp_path):
    _, calls = applied(tmp_path, exists=True)
    assert starting(calls, "services", "enable")
    assert len(all_bindings(calls)) == 12  # 2 identity + 2 Cloud Run + 2 registry + 2 act-as + 4 secrets


def test_an_existing_provider_with_a_different_condition_is_refused_not_skipped(tmp_path):
    weaker = f"assertion.repository == '{REPO}'"
    result, calls = run_script(tmp_path, "--apply", exists=True, env={"FAKE_CONDITION": weaker})
    assert result.returncode != 0
    assert "condition" in (result.stderr + result.stdout).lower()
    assert not [c for c in calls if "add-iam-policy-binding" in c]  # it stopped before granting anything


def test_an_existing_provider_with_an_empty_condition_is_refused(tmp_path):
    result, calls = run_script(tmp_path, "--apply", exists=True, env={"FAKE_CONDITION": ""})
    assert result.returncode != 0 and not [c for c in calls if "add-iam-policy-binding" in c]


def test_a_deleted_pool_is_refused(tmp_path):
    result, calls = run_script(tmp_path, "--apply", exists=True, env={"FAKE_POOL_STATE": "DELETED"})
    assert result.returncode != 0 and not [c for c in calls if "add-iam-policy-binding" in c]


@pytest.mark.parametrize("error", ["PERMISSION_DENIED: caller does not have permission",
                                   "SERVICE_DISABLED: API has not been used", "UNAVAILABLE: try later"])
def test_a_describe_that_fails_for_another_reason_is_not_treated_as_not_found(tmp_path, error):
    result, calls = run_script(tmp_path, "--apply", env={"FAKE_DESCRIBE_ERROR": error})
    assert result.returncode != 0
    assert [c for c in calls if "create" in c or "create-oidc" in c] == []  # no confusing create after a failed check
    assert not [c for c in calls if "add-iam-policy-binding" in c]


# ---- settings and refusals ---------------------------------------------------------------------------------------

def test_project_region_and_repository_can_be_overridden_by_environment_variables(tmp_path):
    env = {"PROJECT_ID": "other-proj", "REGION": "europe-west1", "GITHUB_REPO": "someone/else"}
    _, calls = applied(tmp_path, env=env)
    assert flag(starting(calls, "services", "enable")[0], "project") == "other-proj"
    assert flag(starting(calls, "artifacts", "repositories", "create")[0], "location") == "europe-west1"
    provider = starting(calls, "iam", "workload-identity-pools", "providers", "create-oidc")[0]
    assert "assertion.repository == 'someone/else'" in flag(provider, "attribute-condition")
    assert "erickyegon" not in joined(provider)


@pytest.mark.parametrize("name,value", [
    ("GITHUB_REPO", "not-a-repo"), ("GITHUB_REPO", "a/b/c"), ("GITHUB_REPO", "a/b; rm -rf /"), ("GITHUB_REPO", ""),
    ("GITHUB_REPO", "a/b'c"), ("GITHUB_REPO", 'a/b"c'), ("GITHUB_REPO", "../.."), ("GITHUB_REPO", "a/.."),
    ("GITHUB_REPO", "./x"), ("GITHUB_REPO", "a/b\nc"), ("GITHUB_REPO", "-a/b"), ("GITHUB_REPO", "a/" + "x" * 101),
    ("GITHUB_REPO", "x" * 40 + "/b"), ("GITHUB_REPO", "owner/ünï"),
    ("PROJECT_ID", ""), ("PROJECT_ID", "Bad_ID"), ("PROJECT_ID", "x; echo hi"), ("PROJECT_ID", "abcd"),
    ("PROJECT_ID", "a" * 31), ("PROJECT_ID", "-abcde"), ("REGION", "us east"), ("REGION", ""), ("REGION", "global"),
    ("REGION", "us-east5\n"),
])
def test_it_refuses_malformed_settings_before_running_any_command(tmp_path, name, value):
    result, calls = run_script(tmp_path, "--apply", env={name: value})
    assert result.returncode != 0
    assert calls == [] and (tmp_path / "gh.log").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("argument", ["--wipe", "--apply=1", "--APPLY", "", "--", "apply"])
def test_it_refuses_an_unknown_argument(tmp_path, argument):
    result, calls = run_script(tmp_path, argument)
    assert result.returncode == 2 and calls == []


def test_help_prints_usage_and_runs_nothing(tmp_path):
    result, calls = run_script(tmp_path, "--help")
    assert result.returncode == 0 and calls == [] and "--apply" in result.stdout


def test_a_failing_gcloud_command_stops_the_script_instead_of_carrying_on(tmp_path):
    failing = "#!/usr/bin/env bash\nprintf '%s\\x1f' \"$@\" >> \"$FAKE_LOG\"; printf '\\n' >> \"$FAKE_LOG\"\nexit 1\n"
    result, calls = run_script(tmp_path, "--apply", gcloud=failing)
    assert result.returncode != 0
    assert len(calls) <= 1  # it stopped at the first failing command


def test_an_empty_project_number_is_refused(tmp_path):
    result, calls = run_script(tmp_path, "--apply", env={"FAKE_NUMBER": ""})
    assert result.returncode != 0
    assert not [c for c in calls if "add-iam-policy-binding" in c or "create" in c]


# ---- what it tells the human at the end -------------------------------------------------------------------------

def test_it_prints_the_values_github_needs_and_no_secrets(tmp_path):
    result, _ = applied(tmp_path)
    out = result.stdout
    assert f"projects/{NUMBER}/locations/global/workloadIdentityPools/github/providers/github-actions" in out
    for account in ("deploy-dev", "deploy-prod", "run-dev", "run-prod"):
        assert f"cohortlens-{account}@{PROJECT}.iam.gserviceaccount.com" in out
    assert f"{REGION}-docker.pkg.dev/{PROJECT}/cohortlens" in out
    assert "password" not in out.lower() and "postgresql://" not in out
