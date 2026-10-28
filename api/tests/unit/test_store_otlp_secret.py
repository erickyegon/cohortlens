"""Issue #22: scripts/store_otlp_secret.sh, which puts the Grafana Cloud OTLP credential into Secret Manager without
it ever appearing in chat, on a command line, in the shell history or on the screen.

The owner runs it by hand: it asks for the token with hidden input (here: on stdin, as a pipe), builds the OTLP header
the exporter reads from `OTEL_EXPORTER_OTLP_HEADERS` (`Authorization=Basic%20<base64 of instance:token>`, URL-encoded
as the OpenTelemetry specification requires) and stores it as a new version of `cohortlens-<env>-otlp-headers`,
creating the secret and letting that environment's runtime account read it. It runs against a FAKE `gcloud`, so
nothing real is touched. The token below is generated per run: no credential-looking literal lives in the repository.
"""
import base64
import os
import pathlib
import secrets
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "store_otlp_secret.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the script")

INSTANCE = "1896701"
TOKEN = "glc_" + secrets.token_hex(24)
SEP = "\x1f"

FAKE_GCLOUD = r"""#!/usr/bin/env bash
n=$(( $(cat "$FAKE_DIR/count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$FAKE_DIR/count"
printf '%s\x1f' "$@" > "$FAKE_DIR/args_$n"
args="$*"
if [[ "$args" == *"versions add"* ]]; then cat > "$FAKE_DIR/stdin_$n"; fi
if [[ "$args" == *"secrets describe"* ]]; then
  if [ -n "${FAKE_DESCRIBE_ERROR:-}" ]; then echo "ERROR: (gcloud) $FAKE_DESCRIBE_ERROR" >&2; exit 1; fi
  if [ "${FAKE_SECRET_EXISTS:-0}" != "1" ] && [ ! -f "$FAKE_DIR/created" ]; then
    echo "ERROR: (gcloud.secrets.describe) NOT_FOUND: Secret [projects/1/secrets/x] not found" >&2; exit 1
  fi
fi
if [[ "$args" == *"secrets create"* ]]; then touch "$FAKE_DIR/created"; fi
if [ "${FAKE_FAIL_ON:-}" != "" ] && [[ "$args" == *"$FAKE_FAIL_ON"* ]]; then echo "ERROR: failed" >&2; exit 1; fi
exit 0
"""


def _install(directory, name, body):
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run_store(tmp_path, *args, token=TOKEN + "\n", env=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    _install(bin_dir, "gcloud", FAKE_GCLOUD)
    full_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_DIR": str(work),
                "PROJECT_ID": "cohortlens", **(env or {})}
    if not args:
        args = ("--env", "dev", "--instance-id", INSTANCE)
    result = subprocess.run([BASH, str(SCRIPT), *args], input=token, capture_output=True, text=True, env=full_env,
                            timeout=60)
    return result, work


def calls(work):
    found = []
    for path in sorted(work.glob("args_*"), key=lambda item: int(item.name.rsplit("_", 1)[1])):
        found.append(path.read_text(encoding="utf-8").split(SEP)[:-1])
    return found


def stored_header(work):
    stdins = sorted(work.glob("stdin_*"), key=lambda item: int(item.name.rsplit("_", 1)[1]))
    assert len(stdins) == 1
    return stdins[0].read_text(encoding="utf-8")


def stored(tmp_path, *args, **kwargs):
    result, work = run_store(tmp_path, *args, **kwargs)
    assert result.returncode == 0, result.stderr + result.stdout
    return result, work


def test_the_script_exists_and_is_valid_bash_with_strict_mode():
    assert SCRIPT.exists()
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


def test_the_stored_value_is_the_url_encoded_basic_auth_header_the_exporter_reads(tmp_path):
    from opentelemetry.util.re import parse_env_headers

    _, work = stored(tmp_path)
    header = stored_header(work)
    assert header.startswith("Authorization=Basic%20") and "\n" not in header and " " not in header
    parsed = parse_env_headers(header)  # the exporter's own parser must accept it
    scheme, _, encoded = parsed["authorization"].partition(" ")
    assert scheme == "Basic" and base64.b64decode(encoded).decode() == f"{INSTANCE}:{TOKEN}"


def test_the_token_never_appears_on_a_command_line_or_in_any_output(tmp_path):
    result, work = stored(tmp_path)
    everything = result.stdout + result.stderr + "\n".join(" ".join(call) for call in calls(work))
    assert TOKEN not in everything
    assert base64.b64encode(f"{INSTANCE}:{TOKEN}".encode()).decode() not in everything
    assert "glc_" not in everything


def test_a_new_secret_is_created_readable_by_that_environments_runtime_account_then_a_version_is_added(tmp_path):
    _, work = stored(tmp_path)
    steps = [" ".join(call[:3]) for call in calls(work)]
    assert steps == ["secrets describe cohortlens-dev-otlp-headers", "secrets create cohortlens-dev-otlp-headers",
                     "secrets add-iam-policy-binding cohortlens-dev-otlp-headers",
                     "secrets versions add cohortlens-dev-otlp-headers"]
    binding = calls(work)[2]
    assert ("--member=serviceAccount:cohortlens-run-dev@cohortlens.iam.gserviceaccount.com" in binding
            and "--role=roles/secretmanager.secretAccessor" in binding and "--project=cohortlens" in binding)
    add = calls(work)[3]
    assert "--data-file=-" in add and "--project=cohortlens" in add


def test_prod_uses_its_own_secret_and_runtime_account(tmp_path):
    _, work = stored(tmp_path, "--env", "prod", "--instance-id", INSTANCE)
    text = "\n".join(" ".join(call) for call in calls(work))
    assert "cohortlens-prod-otlp-headers" in text and "cohortlens-run-prod@" in text
    assert "-dev-" not in text and "run-dev" not in text


def test_running_again_adds_a_new_version_without_recreating_the_secret(tmp_path):
    _, work = stored(tmp_path, env={"FAKE_SECRET_EXISTS": "1"})
    steps = [" ".join(call[:3]) for call in calls(work)]
    assert "secrets create cohortlens-dev-otlp-headers" not in steps
    assert steps[-1] == "secrets versions add cohortlens-dev-otlp-headers"


def test_a_failed_check_that_is_not_a_plain_not_found_stops_before_anything_is_created(tmp_path):
    error = "PERMISSION_DENIED: no access (or it may not exist)"
    result, work = run_store(tmp_path, env={"FAKE_DESCRIBE_ERROR": error})
    assert result.returncode != 0
    assert not [call for call in calls(work) if "create" in call or "add" in call]


@pytest.mark.parametrize("fail_on", ["secrets create", "add-iam-policy-binding", "versions add"])
def test_a_failing_step_fails_the_script_and_still_never_prints_the_token(tmp_path, fail_on):
    result, work = run_store(tmp_path, env={"FAKE_FAIL_ON": fail_on})
    assert result.returncode != 0 and TOKEN not in result.stdout + result.stderr


@pytest.mark.parametrize("token", ["", "\n", "short", "has a space in it " + "x" * 30, "tab\tin\tit" + "x" * 30])
def test_an_empty_short_or_whitespace_token_is_refused_before_any_command_runs(tmp_path, token):
    result, work = run_store(tmp_path, token=token + "\n" if not token.endswith("\n") else token)
    assert result.returncode != 0 and calls(work) == []
    assert token.strip() == "" or token.strip() not in result.stdout + result.stderr


@pytest.mark.parametrize("args", [
    [], ["--env", "dev"], ["--instance-id", INSTANCE], ["--env", "staging", "--instance-id", INSTANCE],
    ["--env", "dev", "--instance-id", "abc"], ["--env", "dev", "--instance-id", "1; echo hi"],
    ["--env", "dev", "--instance-id", INSTANCE, "--token", "x" * 30],
    ["--env", "dev", "--instance-id", INSTANCE, "extra"],
    ["--env"], ["--instance-id"],
])
def test_bad_arguments_are_refused_before_any_command_runs(tmp_path, args):
    result, work = run_store(tmp_path, *args) if args else run_store(tmp_path, "--bogus")
    assert result.returncode != 0 and calls(work) == []


def test_the_token_cannot_be_given_as_an_argument(tmp_path):
    """It is read from the prompt only, so it cannot end up in the shell history or the process list."""
    result, _ = run_store(tmp_path, "--env", "dev", "--instance-id", INSTANCE, "--token", TOKEN)
    assert result.returncode != 0 and TOKEN not in result.stdout + result.stderr


@pytest.mark.parametrize("env", [{"PROJECT_ID": ""}, {"PROJECT_ID": "Bad Project"}])
def test_a_bad_project_is_refused_before_any_command_runs(tmp_path, env):
    result, work = run_store(tmp_path, env=env)
    assert result.returncode != 0 and calls(work) == []


def test_the_script_asks_for_hidden_input_when_run_in_a_terminal():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "read -rs" in text and "[ -t 0 ]" in text
