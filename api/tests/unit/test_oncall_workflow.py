"""Issue #24: pins the safety properties of .github/workflows/oncall.yml as text (the logic lives in the tested `oncall`
package; the workflow authenticates and calls it)."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github" / "workflows" / "oncall.yml").read_text(encoding="utf-8")


def run_blocks():
    lines, blocks = WORKFLOW.splitlines(), []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s+)run: [|>]?", line)
        if not match:
            continue
        indent, body = len(match.group(1)), [line.split("run:", 1)[1]]
        for following in lines[index + 1:]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            body.append(following)
        blocks.append("\n".join(body))
    return blocks


def test_it_starts_from_the_grafana_dispatch_event_or_by_hand_only():
    assert "repository_dispatch:\n    types: [grafana-alert]" in WORKFLOW and "workflow_dispatch:" in WORKFLOW
    assert "pull_request" not in WORKFLOW and "push:" not in WORKFLOW


def test_it_runs_only_on_main_and_never_authenticates_as_the_prod_account():
    assert "github.ref == 'refs/heads/main'" in WORKFLOW
    assert "GCP_DEPLOY_SA_DEV" in WORKFLOW and "GCP_DEPLOY_SA_PROD" not in WORKFLOW
    assert "environment: production" not in WORKFLOW


def test_a_setting_that_names_prod_stops_the_job_before_anything_is_installed_or_authenticated():
    refusal = WORKFLOW.index("Refuse prod")
    assert refusal < WORKFLOW.index("actions/checkout") < WORKFLOW.index("google-github-actions/auth")
    assert "*,prod,*) echo" in WORKFLOW


def test_nothing_from_the_alert_or_the_inputs_is_written_into_a_shell_line():
    for block in run_blocks():
        for forbidden in ("github.event.client_payload", "inputs.alertname", "inputs.status", "secrets."):
            assert forbidden not in block, (forbidden, block)


def test_the_two_secrets_are_given_to_the_final_step_only():
    head, tail = WORKFLOW.split("      - name: Run the agent")
    assert "secrets." not in head
    assert "secrets.ANTHROPIC_API_KEY" in tail and "secrets.GRAFANA_READ_TOKEN" in tail


def test_dry_run_is_the_default_for_the_manual_start_and_for_a_missing_variable():
    assert "default: true" in WORKFLOW
    assert '${VAR_DRY_RUN:-true}' in WORKFLOW


def test_alerts_are_handled_one_at_a_time_and_never_cancelled_midway():
    assert "group: oncall\n  cancel-in-progress: false" in WORKFLOW


def test_the_job_has_the_minimum_permissions():
    after = WORKFLOW.split("permissions:", 1)[1]
    block = [line for line in after.splitlines()[1:] if line.startswith("  ")]
    block = block[:3]
    granted = {line.split(":")[0].strip(): line.split(":")[1].split("#")[0].strip() for line in block}
    assert granted == {"contents": "read", "id-token": "write", "issues": "write"}
