"""Issue #19: pins the safety properties of .github/workflows/rollback.yml as text (the logic lives in the tested
infra/gcp/rollback.sh; the workflow only authenticates and calls it)."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github" / "workflows" / "rollback.yml").read_text(encoding="utf-8")


def run_blocks():
    """The text of every `run: |` block, found by indentation (no YAML library needed)."""
    lines, blocks = WORKFLOW.splitlines(), []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s+)run: [|>]", line)
        if not match:
            continue
        indent, body = len(match.group(1)), []
        for following in lines[index + 1:]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            body.append(following)
        blocks.append("\n".join(body))
    return blocks


def job(name):
    match = re.search(rf"^  {name}:\n((?:    .*\n|\n)*)", WORKFLOW, re.MULTILINE)
    assert match, name
    return match.group(1)


def test_it_is_started_by_hand_only():
    assert re.search(r"on:\s*\n\s*workflow_dispatch:", WORKFLOW)
    for trigger in ("push:", "pull_request", "workflow_run", "schedule"):
        assert trigger not in WORKFLOW


def test_the_inputs_are_choices_so_free_text_is_limited_to_the_revision_and_the_issue_number():
    assert "type: choice" in WORKFLOW and "options: [dev, prod]" in WORKFLOW and "options: [api, web]" in WORKFLOW
    for name in ("revision", "incident", "dry_run"):
        assert f"{name}:" in WORKFLOW


def test_there_is_one_job_per_environment_and_only_the_prod_one_is_in_the_protected_environment():
    dev, prod = job("rollback-dev"), job("rollback-prod")
    assert "environment:" not in dev
    assert re.search(r"^    environment: production$", prod, re.MULTILINE)
    assert "inputs.environment == 'dev'" in dev and "inputs.environment == 'prod'" in prod


def test_each_job_authenticates_as_its_own_environments_deploy_account_with_no_key():
    dev, prod = job("rollback-dev"), job("rollback-prod")
    assert "GCP_DEPLOY_SA_DEV" in dev and "GCP_DEPLOY_SA_PROD" not in dev
    assert "GCP_DEPLOY_SA_PROD" in prod and "GCP_DEPLOY_SA_DEV" not in prod
    assert "id-token: write" in WORKFLOW and "credentials_json" not in WORKFLOW and "secrets." not in WORKFLOW


def test_it_runs_only_on_main_and_not_until_the_google_cloud_variables_exist():
    for body in (job("rollback-dev"), job("rollback-prod")):
        assert "github.ref == 'refs/heads/main'" in body and "vars.GCP_PROJECT_ID != ''" in body


def test_it_may_comment_on_issues_and_nothing_else_beyond_reading_the_repository():
    assert re.search(r"permissions:\s*\n\s*contents: read\s*\n\s*id-token: write[^\n]*\n\s*issues: write", WORKFLOW)
    assert "pull-requests: write" not in WORKFLOW and "contents: write" not in WORKFLOW


def test_it_waits_for_a_running_deploy_instead_of_racing_it():
    assert "group: deploy-dev" in job("rollback-dev") and "group: promote-prod" in job("rollback-prod")
    assert WORKFLOW.count("cancel-in-progress: false") == 2


def test_inputs_reach_the_script_through_environment_variables_never_inline_in_the_shell():
    blocks = run_blocks()
    assert len(blocks) == 2 and not any("${{" in block for block in blocks)
    assert "bash infra/gcp/rollback.sh" in WORKFLOW


def test_third_party_actions_are_the_ones_the_repository_already_uses_or_google_s_own():
    uses = set(re.findall(r"uses: ([\w./-]+)@", WORKFLOW))
    assert uses <= {"actions/checkout", "google-github-actions/auth", "google-github-actions/setup-gcloud"}
