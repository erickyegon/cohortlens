"""Issue #18: pins the safety properties of .github/workflows/promote-prod.yml as text (the logic lives in the tested
infra/gcp/promote.sh and infra/gcp/deploy.sh; the workflow only waits for approval, authenticates and calls them)."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github" / "workflows" / "promote-prod.yml").read_text(encoding="utf-8")


def test_the_job_runs_in_the_protected_production_environment_so_it_waits_for_the_reviewer():
    assert re.search(r"^    environment: production$", WORKFLOW, re.MULTILINE)


def test_it_starts_after_a_successful_dev_deploy_or_by_hand_and_never_from_a_pull_request():
    assert "workflow_run:" in WORKFLOW and 'workflows: ["Deploy dev"]' in WORKFLOW and "types: [completed]" in WORKFLOW
    assert "workflow_dispatch:" in WORKFLOW and "pull_request" not in WORKFLOW and "push:" not in WORKFLOW
    assert "github.event.workflow_run.conclusion == 'success'" in WORKFLOW


def test_it_only_runs_on_main_and_not_until_the_google_cloud_variables_exist():
    assert "github.ref == 'refs/heads/main'" in WORKFLOW and "vars.GCP_PROJECT_ID != ''" in WORKFLOW


def test_promotions_never_overlap_or_get_cancelled_half_way():
    assert "group: promote-prod" in WORKFLOW and "cancel-in-progress: false" in WORKFLOW


def test_it_authenticates_as_the_prod_deploy_account_with_workload_identity_and_no_key():
    assert "id-token: write" in WORKFLOW and "google-github-actions/auth@v2" in WORKFLOW
    assert "GCP_DEPLOY_SA_PROD" in WORKFLOW and "GCP_RUNTIME_SA_PROD" in WORKFLOW
    assert "GCP_DEPLOY_SA_DEV" not in WORKFLOW and "GCP_RUNTIME_SA_DEV" not in WORKFLOW
    assert "credentials_json" not in WORKFLOW and "secrets." not in WORKFLOW


def test_it_builds_nothing_and_promotes_what_dev_serves_through_the_tested_script():
    assert "docker/build-push-action" not in WORKFLOW and "docker build" not in WORKFLOW
    assert "bash infra/gcp/promote.sh" in WORKFLOW


def test_untrusted_text_never_reaches_the_shell_inline():
    # the manual version input and the triggering commit go through environment variables, validated by the script
    assert "${{ inputs.version }}" not in WORKFLOW.split("run:", 1)[1] if "run:" in WORKFLOW else True
    run_blocks = re.findall(r"run: [>|]?-?\s*\n?((?:.|\n)*?)(?=\n      - |\Z)", WORKFLOW)
    assert not any("${{ inputs." in block or "${{ github.event." in block for block in run_blocks)


def test_third_party_actions_are_the_ones_the_repository_already_uses_or_google_s_own():
    uses = set(re.findall(r"uses: ([\w./-]+)@", WORKFLOW))
    assert uses <= {"actions/checkout", "google-github-actions/auth", "google-github-actions/setup-gcloud"}


def test_the_otlp_endpoint_is_a_repository_variable_not_a_secret():
    assert "OTLP_ENDPOINT: ${{ vars.GCP_OTLP_ENDPOINT }}" in WORKFLOW
