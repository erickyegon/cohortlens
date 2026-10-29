"""Every workflow file must be valid YAML with triggers and jobs.

GitHub does not run a workflow whose file does not parse: it records an instant failed run ("This run likely failed
because of a workflow file issue") and no job starts. `oncall.yml` merged with exactly that defect, so a Grafana alert
could never have started the agent, and nothing in the test suite noticed because the other workflow tests read the
files as text.
"""
import pathlib

import pytest
import yaml

WORKFLOWS = sorted((pathlib.Path(__file__).resolve().parents[3] / ".github" / "workflows").glob("*.yml"))


def test_there_are_workflows_to_check():
    assert len(WORKFLOWS) >= 5


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_the_workflow_is_valid_yaml_with_triggers_and_jobs(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), path.name
    assert document.get("on") or document.get(True), f"{path.name} has no triggers"  # YAML 1.1 reads `on` as True
    assert document.get("jobs"), f"{path.name} has no jobs"
    for name, job in document["jobs"].items():
        assert isinstance(job, dict) and ("steps" in job or "uses" in job), f"{path.name}: job {name} has no steps"


def test_the_oncall_dry_run_step_is_a_single_line_that_appends_to_the_environment_file():
    """A shell escape written with a backslash once became a literal line break in this step and broke the file."""
    text = (WORKFLOWS[0].parent / "oncall.yml").read_text(encoding="utf-8")
    assert 'echo "DRY_RUN=$dry" >> "$GITHUB_ENV"' in text
