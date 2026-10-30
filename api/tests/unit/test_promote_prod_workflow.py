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


def _job(name):
    """The text of one job under `jobs:` (two-space indent), up to the next job."""
    body = WORKFLOW.split("\njobs:\n", 1)[1]
    match = re.search(rf"^  {name}:\n((?:    .*\n|\n)*)", body, re.MULTILINE)
    assert match, f"no job named {name}"
    return match.group(1)


def test_the_run_name_shows_the_commit_being_promoted():
    """A stale run must be recognisable in the list of runs and on the approval page."""
    run_name = re.search(r"^run-name: (.+)$", WORKFLOW, re.MULTILINE)
    assert run_name, "the workflow needs a run-name"
    assert "github.event.workflow_run.head_sha" in run_name.group(1) and "inputs.version" in run_name.group(1)
    assert "github.event.workflow_run.display_title" in run_name.group(1)  # what the dev deploy was for


def test_the_serialising_group_is_on_the_promote_job_only_so_a_new_run_can_clean_up_while_an_old_one_waits():
    """A run waiting for approval holds a workflow-level group, so a newer run would never start; the cleanup job of the
    newer run must be able to run, and only the job that deploys is serialised and never cancelled."""
    assert not re.search(r"^concurrency:", WORKFLOW, re.MULTILINE)
    promote = _job("promote")
    assert "concurrency:" in promote and "group: promote-prod" in promote and "cancel-in-progress: false" in promote


def test_a_cleanup_job_cancels_older_promotions_that_are_still_waiting_for_approval():
    supersede = _job("supersede")
    assert "bash infra/gcp/cancel_stale_promotions.sh" in supersede
    assert "environment:" not in supersede  # it needs no approval and no production credentials
    assert "google-github-actions" not in supersede and "id-token" not in supersede and "secrets." not in supersede
    assert re.search(r"^      actions: write\b", supersede, re.MULTILINE) and "contents: read" in supersede
    assert "GH_TOKEN: ${{ github.token }}" in supersede
    assert "github.event.workflow_run.conclusion == 'success'" in supersede
    assert "github.ref == 'refs/heads/main'" in supersede


def test_the_promotion_waits_for_the_cleanup_and_still_needs_the_approval():
    promote = _job("promote")
    assert re.search(r"^    needs: supersede$", promote, re.MULTILINE)
    assert re.search(r"^    environment: production$", promote, re.MULTILINE)
    assert "cancel_stale_promotions" not in promote


def test_it_authenticates_as_the_prod_deploy_account_with_workload_identity_and_no_key():
    assert "id-token: write" in WORKFLOW and "google-github-actions/auth@v3" in WORKFLOW
    assert "GCP_DEPLOY_SA_PROD" in WORKFLOW and "GCP_RUNTIME_SA_PROD" in WORKFLOW
    assert "GCP_DEPLOY_SA_DEV" not in WORKFLOW and "GCP_RUNTIME_SA_DEV" not in WORKFLOW
    assert "credentials_json" not in WORKFLOW and "secrets." not in WORKFLOW


def test_it_builds_nothing_and_promotes_what_dev_serves_through_the_tested_script():
    assert "docker/build-push-action" not in WORKFLOW and "docker build" not in WORKFLOW
    assert "bash infra/gcp/promote.sh" in WORKFLOW


def test_untrusted_text_never_reaches_the_shell_inline():
    # the manual version input and the triggering commit go through environment variables, validated by the script
    assert "${{ inputs.version }}" not in WORKFLOW.split("run:", 1)[1] if "run:" in WORKFLOW else True
    run_blocks = re.findall(r"run: [>|]?-?\s*\n?((?:.|\n)*?)(?=\n\s*\n|\n      - |\Z)", WORKFLOW)  # up to a blank line
    assert not any("${{ inputs." in block or "${{ github.event." in block for block in run_blocks)


def test_third_party_actions_are_the_ones_the_repository_already_uses_or_google_s_own():
    uses = set(re.findall(r"uses: ([\w./-]+)@", WORKFLOW))
    assert uses <= {"actions/checkout", "google-github-actions/auth", "google-github-actions/setup-gcloud"}


def test_the_otlp_endpoint_is_a_repository_variable_not_a_secret():
    assert "OTLP_ENDPOINT: ${{ vars.GCP_OTLP_ENDPOINT }}" in WORKFLOW
