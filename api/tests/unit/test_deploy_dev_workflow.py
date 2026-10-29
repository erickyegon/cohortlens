"""Issue #17: pins the safety properties of .github/workflows/deploy-dev.yml as text (the logic lives in the tested
infra/gcp/deploy.sh; the workflow only authenticates, builds, pushes and calls it)."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text(encoding="utf-8")


def test_it_runs_on_pushes_to_main_only_and_never_on_pull_requests():
    assert re.search(r"on:\s*\n\s*push:\s*\n\s*branches: \[main\]", WORKFLOW)
    assert "pull_request" not in WORKFLOW


def test_it_authenticates_with_workload_identity_federation_and_no_key_or_secret():
    assert "id-token: write" in WORKFLOW and "google-github-actions/auth@v3" in WORKFLOW
    assert "workload_identity_provider" in WORKFLOW and "GCP_DEPLOY_SA_DEV" in WORKFLOW
    assert "credentials_json" not in WORKFLOW and "secrets." not in WORKFLOW


def test_it_does_not_use_the_production_deploy_account_or_environment():
    assert "PROD" not in WORKFLOW and "environment:" not in WORKFLOW


def test_the_job_is_limited_to_main_and_skipped_until_the_setup_variables_exist():
    assert "github.ref == 'refs/heads/main'" in WORKFLOW and "vars.GCP_PROJECT_ID != ''" in WORKFLOW


def test_deploys_never_overlap_or_get_cancelled_half_way():
    assert "group: deploy-dev" in WORKFLOW and "cancel-in-progress: false" in WORKFLOW


def test_images_are_tagged_with_the_git_sha_and_deployed_by_digest():
    assert WORKFLOW.count("${{ github.sha }}") >= 4
    assert "/api:${{ github.sha }}" in WORKFLOW and "/web:${{ github.sha }}" in WORKFLOW
    assert "@${{ steps.api.outputs.digest }}" in WORKFLOW and "@${{ steps.web.outputs.digest }}" in WORKFLOW


def test_the_deploy_step_calls_the_tested_script_for_the_dev_environment():
    assert "bash infra/gcp/deploy.sh --env dev" in WORKFLOW


def test_third_party_actions_are_the_ones_the_repository_already_uses_or_google_s_own():
    uses = set(re.findall(r"uses: ([\w./-]+)@", WORKFLOW))
    assert uses <= {"actions/checkout", "docker/setup-buildx-action", "docker/build-push-action",
                    "google-github-actions/auth", "google-github-actions/setup-gcloud"}


def test_the_otlp_endpoint_is_a_repository_variable_not_a_secret():
    assert "OTLP_ENDPOINT: ${{ vars.GCP_OTLP_ENDPOINT }}" in WORKFLOW
