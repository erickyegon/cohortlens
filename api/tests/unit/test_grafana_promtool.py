"""Issue #23: the two alert rules are run against a real Prometheus rule engine with `promtool test rules`.

`infra/grafana/alert-tests.yml` holds synthetic series and says which environments must alert at 14 minutes. The rules
file is generated here from `alert-rules.json`, so the committed expressions are the ones that run. Uses `promtool`
if it is installed, else Docker (image `prom/prometheus`); skipped when neither is available (the text checks in
test_grafana_artifacts.py still run).
"""
import json
import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
GRAFANA = ROOT / "infra" / "grafana"
IMAGE = "prom/prometheus:v2.55.1"


def _docker_works():
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        return subprocess.run([docker, "info"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


PROMTOOL = shutil.which("promtool")
pytestmark = pytest.mark.skipif(not (PROMTOOL or _docker_works()), reason="needs promtool or Docker")


def rules_yaml(replace=None):
    rules = json.loads((GRAFANA / "alert-rules.json").read_text(encoding="utf-8"))
    text = "groups:\n- name: cohortlens\n  rules:\n"
    for rule in rules:
        expression = rule["data"][0]["model"]["expr"]
        for old, new in (replace or {}).items():
            expression = expression.replace(old, new)
        text += f"  - alert: {rule['title']}\n    expr: {expression}\n"
    return text


def run_promtool(directory):
    if PROMTOOL:
        command = [PROMTOOL, "test", "rules", "alert-tests.yml"]
        return subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=300)
    command = ["docker", "run", "--rm", "-v", f"{directory}:/t", "-w", "/t", "--entrypoint", "promtool", IMAGE,
               "test", "rules", "alert-tests.yml"]
    return subprocess.run(command, capture_output=True, text=True, timeout=600,
                          env={**os.environ, "MSYS_NO_PATHCONV": "1"})


def _image_available():
    """Docker may exist without being able to pull (no network, a restricted runner): then these tests skip."""
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0:
        return True
    try:
        return subprocess.run(["docker", "pull", "-q", IMAGE], capture_output=True, timeout=300).returncode == 0
    except subprocess.TimeoutExpired:
        return False


@pytest.fixture(autouse=True)
def _need_the_engine():
    if not PROMTOOL and not _image_available():
        pytest.skip(f"promtool is not installed and the image {IMAGE} cannot be pulled")


def prepare(tmp_path, replace=None):
    (tmp_path / "rules.yml").write_text(rules_yaml(replace), encoding="utf-8", newline="\n")
    shutil.copy(GRAFANA / "alert-tests.yml", tmp_path / "alert-tests.yml")
    # the container's user is not the runner's: it must be able to read the mounted directory (pytest makes it 0700)
    tmp_path.chmod(0o755)
    for name in ("rules.yml", "alert-tests.yml"):
        (tmp_path / name).chmod(0o644)
    return tmp_path


def test_the_rules_fire_and_stay_quiet_exactly_as_documented(tmp_path):
    result = run_promtool(prepare(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("replace", [
    {">= 10": ">= 3"},            # the 5-minute minimum-runs guard relaxed: 5 failing runs would page
    {"> 0.05": "> 0.5"},          # error-rate threshold raised: a 20% error rate would not page
    {'outcome=~"failed|timeout"': 'outcome="failed"'},  # timeouts no longer count as errors
    {">= 20": ">= 5"},            # the latency minimum-runs guard relaxed
    {"> 3)": "> 30)"},            # latency threshold raised
])
def test_a_changed_rule_makes_the_scenarios_fail(tmp_path, replace):
    """Proves the scenarios have teeth: each realistic edit to an expression is caught."""
    result = run_promtool(prepare(tmp_path, replace))
    assert result.returncode != 0, f"{replace} was not caught"
