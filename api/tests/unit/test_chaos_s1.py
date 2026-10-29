"""Issue #27: chaos/s1.sh. Only its refusals and its dry run can be tested without a cloud; the first real run is the
drill itself on dev (docs/oncall.md). The dry run must never call gcloud, gh or python."""
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "chaos" / "s1.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")
GOOD_ENV = {"PROJECT_ID": "cohortlens", "REGION": "us-east5"}


def run(tmp_path, *args, env=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("gcloud", "gh", "python3", "python"):
        stub = bin_dir / tool
        stub.write_text("#!/usr/bin/env bash\necho \"$0 was called\" >> \"$STUB_LOG\"\nexit 9\n", newline="\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    full = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "STUB_LOG": str(log), **(env or {})}
    result = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=full, timeout=60)
    return result, (log.read_text() if log.exists() else "")


def test_the_script_is_valid_bash_with_strict_mode():
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


def test_the_dry_run_lists_the_plan_and_calls_nothing(tmp_path):
    result, calls = run(tmp_path, "--dry-run", env=GOOD_ENV)
    assert result.returncode == 0 and calls == ""
    for needle in ("cohortlens-api-dev", "CHAOS_FAULT=fail_runs", "warm-up", "restore the healthy revision"):
        assert needle in result.stdout, needle


@pytest.mark.parametrize("args,env", [
    (["--bogus"], GOOD_ENV), (["--minutes"], GOOD_ENV), (["--minutes", "2"], GOOD_ENV),
    (["--minutes", "999"], GOOD_ENV), (["--minutes", "x"], GOOD_ENV),
    ([], {"PROJECT_ID": "cohortlens"}), ([], {"REGION": "us-east5"}),
    ([], {**GOOD_ENV, "PROJECT_ID": "bad id; ls"}), ([], {**GOOD_ENV, "REGION": "../x"}),
])
def test_bad_arguments_or_settings_are_refused_before_any_cloud_call(tmp_path, args, env):
    result, calls = run(tmp_path, *args, env=env)
    assert result.returncode != 0 and calls == ""


def test_it_touches_only_the_dev_api_service():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'SERVICE="cohortlens-api-dev"' in text and "cohortlens-api-prod" not in text and "-prod" not in text


def test_the_healthy_revision_is_restored_on_any_exit_after_the_fault_is_injected():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "trap cleanup EXIT" in text and "trap 'exit 130' INT TERM" in text
    assert text.index("ARMED=1") < text.index("--update-env-vars") and "ARMED=0" in text
    assert 'NOW_SERVING="$(serving || true)"' in text


def test_a_second_interrupt_cannot_abort_the_restore_and_a_stale_poll_cannot_override_the_agent():
    text = SCRIPT.read_text(encoding="utf-8")
    cleanup = text[text.index("cleanup() {"):text.index("trap cleanup EXIT")]
    assert "trap '' INT TERM" in cleanup
    assert text.count('NOW_SERVING="$(serving || true)"') >= 2  # re-read once more after the loop
    assert "grep '^run '" in text


def test_the_bad_revision_is_routed_explicitly_because_a_restore_pins_the_traffic():
    """After a restore the service sends 100% to a named revision, so a new revision gets 0% unless it is routed to.

    The second run of the drill deployed the faulty revision, saw it serving 0% and stopped.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    deploy = text.index("--update-env-vars")
    route = text.index('--to-revisions="${SERVICE}-${SUFFIX}=100"')
    assert deploy < route < text.index('BAD="$(serving)"')
