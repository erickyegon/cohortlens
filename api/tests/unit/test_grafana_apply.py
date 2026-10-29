"""Issue #23: infra/grafana/apply.sh, which puts the dashboard and the two alert rules into a Grafana Cloud stack.

It asks for a Grafana service-account token at a hidden prompt (never an argument, never printed), looks up the stack's
real Prometheus, Loki and Tempo data sources, fills the placeholders of the exported files with them, and creates or
updates the folder, the dashboard and the two rules through Grafana's HTTP API. Running it twice changes nothing the
second time. It runs here against a FAKE curl, so no network is used; the token is generated per run.
"""
import json
import os
import pathlib
import secrets
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra" / "grafana" / "apply.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the script")

TOKEN = "glsa_" + secrets.token_hex(20)
URL = "https://eagertrout1651.grafana.net"
SEP = "\x1f"
DATASOURCES = [
    {"uid": "grafanacloud-usage-insights", "type": "prometheus", "name": "usage-insights"},
    {"uid": "grafanacloud-prom", "type": "prometheus", "name": "stack-prom", "isDefault": True},
    {"uid": "grafanacloud-alert-state-history", "type": "loki", "name": "alert-state-history"},
    {"uid": "grafanacloud-logs", "type": "loki", "name": "stack-logs"},
    {"uid": "grafanacloud-traces", "type": "tempo", "name": "stack-traces"},
]

FAKE_CURL = r"""#!/usr/bin/env bash
# a fake curl: records the call, the -K config file's content and the body, and answers like Grafana's API
n=$(( $(cat "$FAKE_DIR/count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$FAKE_DIR/count"
printf '%s\x1f' "$@" > "$FAKE_DIR/args_$n"
method=GET; url=""; out=""; previous=""; data=""
for argument in "$@"; do
  case "$previous" in
    -X) method="$argument" ;;
    -o) out="$argument" ;;
    -K) cat "$argument" > "$FAKE_DIR/config_$n" ;;
    --data-binary) data="${argument#@}"; cp "$data" "$FAKE_DIR/body_$n" ;;
  esac
  case "$argument" in https://*) url="$argument" ;; esac
  previous="$argument"
done
printf '%s %s\n' "$method" "$url" > "$FAKE_DIR/request_$n"
status=200; body='{}'
if [[ "$url" == */api/datasources ]]; then body="$FAKE_DATASOURCES"; fi
if [[ "$url" == */api/folders/cohortlens && "$method" == GET ]]; then status="${FAKE_FOLDER_STATUS:-404}"; fi
if [[ "$url" == */api/folders && "$method" == POST ]]; then status="${FAKE_FOLDER_POST_STATUS:-200}"; fi
if [[ "$url" == */provisioning/alert-rules/* && "$method" == GET ]]; then status="${FAKE_RULE_STATUS:-404}"; fi
if [[ "$url" == */provisioning/alert-rules && "$method" == POST ]]; then status="${FAKE_RULE_CREATE_STATUS:-200}"; fi
if [ -n "${FAKE_FAIL_ON:-}" ] && [[ "$method $url" == *"$FAKE_FAIL_ON"* ]]; then
  status=500; body='{"message":"boom"}'
fi
printf '%s' "$body" > "$out"
printf '%s' "$status"
"""


def _install(directory, name, body):
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run_apply(tmp_path, *args, token=TOKEN + "\n", env=None, shell=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    _install(bin_dir, "curl", FAKE_CURL)
    full_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_DIR": str(work),
                "FAKE_DATASOURCES": json.dumps(DATASOURCES), **(env or {})}
    if not args:
        args = ("--url", URL, "--stdin")
    command = [*(shell or [BASH]), str(SCRIPT), *args]
    result = subprocess.run(command, input=token, capture_output=True, text=True, env=full_env, timeout=90)
    return result, work


def requests(work):
    found = []
    for path in sorted(work.glob("request_*"), key=lambda item: int(item.name.rsplit("_", 1)[1])):
        found.append(path.read_text(encoding="utf-8").strip())
    return found


def argv_of(work, number):
    return (work / f"args_{number}").read_text(encoding="utf-8").split(SEP)[:-1]


def applied(tmp_path, *args, **kwargs):
    result, work = run_apply(tmp_path, *args, **kwargs)
    assert result.returncode == 0, result.stderr + result.stdout
    return result, work


def body_for(work, request_line):
    for number, line in enumerate(requests(work), start=1):
        if line == request_line:
            return json.loads((work / f"body_{number}").read_text(encoding="utf-8"))
    raise AssertionError(request_line)


def test_the_script_exists_and_is_valid_bash_with_strict_mode():
    assert SCRIPT.exists()
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


# ---- order and content of what it sends ----------------------------------------

def test_it_reads_the_data_sources_then_creates_the_folder_the_dashboard_and_each_rule(tmp_path):
    _, work = applied(tmp_path)
    assert requests(work) == [
        f"GET {URL}/api/datasources",
        f"GET {URL}/api/folders/cohortlens", f"POST {URL}/api/folders",
        f"POST {URL}/api/dashboards/db",
        f"GET {URL}/api/v1/provisioning/alert-rules/cohortlens-error-rate-high",
        f"POST {URL}/api/v1/provisioning/alert-rules",
        f"GET {URL}/api/v1/provisioning/alert-rules/cohortlens-latency-high",
        f"POST {URL}/api/v1/provisioning/alert-rules",
    ]


def test_existing_things_are_updated_not_recreated(tmp_path):
    _, work = applied(tmp_path, env={"FAKE_FOLDER_STATUS": "200", "FAKE_RULE_STATUS": "200"})
    assert f"POST {URL}/api/folders" not in requests(work)
    assert f"PUT {URL}/api/v1/provisioning/alert-rules/cohortlens-error-rate-high" in requests(work)
    assert f"PUT {URL}/api/v1/provisioning/alert-rules/cohortlens-latency-high" in requests(work)
    assert f"POST {URL}/api/v1/provisioning/alert-rules" not in requests(work)


def test_the_stacks_real_data_sources_are_chosen_not_the_look_alikes(tmp_path):
    _, work = applied(tmp_path)
    dashboard = body_for(work, f"POST {URL}/api/dashboards/db")["dashboard"]
    uids = {panel["datasource"]["uid"] for panel in dashboard["panels"]}
    assert uids == {"grafanacloud-prom", "grafanacloud-logs", "grafanacloud-traces"}  # not usage-insights / history
    rule = body_for(work, f"POST {URL}/api/v1/provisioning/alert-rules")
    queries = [item for item in rule["data"] if item["datasourceUid"] != "__expr__"]
    assert [item["datasourceUid"] for item in queries] == ["grafanacloud-prom"]


def test_the_dashboard_goes_into_the_cohortlens_folder_and_overwrites_its_previous_version(tmp_path):
    _, work = applied(tmp_path)
    payload = body_for(work, f"POST {URL}/api/dashboards/db")
    assert payload["folderUid"] == "cohortlens" and payload["overwrite"] is True
    assert payload["dashboard"]["uid"] == "cohortlens-core-journey" and payload["dashboard"]["id"] is None
    assert body_for(work, f"POST {URL}/api/folders") == {"uid": "cohortlens", "title": "CohortLens"}


def test_no_placeholder_is_left_in_anything_sent(tmp_path):
    _, work = applied(tmp_path)
    for path in work.glob("body_*"):
        text = path.read_text(encoding="utf-8")
        assert "__PROMETHEUS_UID__" not in text and "__LOKI_UID__" not in text and "__TEMPO_UID__" not in text
        assert "__FOLDER_UID__" not in text
    rule = body_for(work, f"POST {URL}/api/v1/provisioning/alert-rules")
    assert rule["folderUID"] == "cohortlens" and rule["for"] == "2m" and rule["title"] == "CohortRunErrorRateHigh"
    assert {"service", "environment", "agent", "runbook_url"} <= set(rule["labels"])


def test_the_rule_requests_ask_grafana_to_leave_them_editable(tmp_path):
    _, work = applied(tmp_path)
    for number, line in enumerate(requests(work), start=1):
        if "provisioning/alert-rules" in line and line.startswith(("POST", "PUT")):
            assert "X-Disable-Provenance: true" in argv_of(work, number)


def test_running_twice_sends_exactly_the_same_bodies(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    _, work_one = applied(one)
    _, work_two = applied(two)
    for name in ("body_4", "body_6", "body_8"):
        assert (work_one / name).read_text(encoding="utf-8") == (work_two / name).read_text(encoding="utf-8")


# ---- the token ----------------------------------------

def test_the_token_is_sent_only_in_a_config_file_never_on_a_command_line_or_in_any_output(tmp_path):
    result, work = applied(tmp_path)
    for number in range(1, len(requests(work)) + 1):
        assert TOKEN not in " ".join(argv_of(work, number))
        assert f"Authorization: Bearer {TOKEN}" in (work / f"config_{number}").read_text(encoding="utf-8")
    assert TOKEN not in result.stdout + result.stderr
    assert not [path for path in work.glob("body_*") if TOKEN in path.read_text(encoding="utf-8")]


def test_a_piped_token_is_refused_unless_stdin_is_asked_for_and_tracing_is_refused(tmp_path):
    result, work = run_apply(tmp_path, "--url", URL)
    assert result.returncode != 0 and requests(work) == [] and "--stdin" in result.stderr
    result, work = run_apply(tmp_path, shell=[BASH, "-x"])
    assert result.returncode != 0 and requests(work) == [] and TOKEN not in result.stdout + result.stderr
    result, work = run_apply(tmp_path, env={"SHELLOPTS": "xtrace"})
    assert result.returncode != 0 and requests(work) == []


@pytest.mark.parametrize("token", ["", "abc123", "has a space in it " + "x" * 30])
def test_an_empty_short_or_whitespace_token_is_refused_before_any_request(tmp_path, token):
    result, work = run_apply(tmp_path, token=token + "\n")
    assert result.returncode != 0 and requests(work) == []


def test_nothing_inherited_from_the_callers_environment_carries_the_token(tmp_path):
    result, work = run_apply(tmp_path, env={"TOKEN": "inherited-value"})
    assert result.returncode == 0, result.stderr


# ---- refusals and failures ----------------------------------------

@pytest.mark.parametrize("args", [
    [], ["--stdin"], ["--url", "http://x.grafana.net", "--stdin"], ["--url", "https://evil.example.com", "--stdin"],
    ["--url", "https://x.grafana.net.evil.io", "--stdin"], ["--url", "https://x.grafana.net/path", "--stdin"],
    ["--url", "https://user:pw@x.grafana.net", "--stdin"], ["--url", URL, "--stdin", "--token", "x" * 30],
    ["--url", URL, "--stdin", "extra"], ["--url"],
])
def test_bad_arguments_are_refused_before_any_request(tmp_path, args):
    result, work = run_apply(tmp_path, *args) if args else run_apply(tmp_path, "--bogus")
    assert result.returncode != 0 and requests(work) == []


def test_a_stack_without_the_three_data_sources_is_refused_before_anything_is_written(tmp_path):
    only_prom = [item for item in DATASOURCES if item["type"] == "prometheus"]
    result, work = run_apply(tmp_path, env={"FAKE_DATASOURCES": json.dumps(only_prom)})
    assert result.returncode != 0 and requests(work) == [f"GET {URL}/api/datasources"]
    assert "loki" in result.stderr.lower()


@pytest.mark.parametrize("fail_on", ["POST " + URL + "/api/dashboards/db", "POST " + URL + "/api/folders",
                                     "alert-rules"])
def test_a_failing_request_stops_the_script_with_a_message_and_never_prints_the_token(tmp_path, fail_on):
    result, work = run_apply(tmp_path, env={"FAKE_FAIL_ON": fail_on})
    assert result.returncode != 0 and "500" in result.stderr and TOKEN not in result.stdout + result.stderr


def test_dry_run_asks_for_no_token_makes_no_request_and_says_what_it_would_do(tmp_path):
    result, work = run_apply(tmp_path, "--url", URL, "--dry-run", token="")
    assert result.returncode == 0 and requests(work) == []
    for needle in ("dry run", "dashboard.json", "alert-rules.json", "CohortRunErrorRateHigh", "CohortRunLatencyHigh"):
        assert needle in result.stdout, needle


# ---- review findings of #73 ----------------------------------------------------------------------------------------

def test_help_prints_the_usage_comment_and_no_code(tmp_path):
    result, work = run_apply(tmp_path, "--help")
    assert result.returncode == 0 and requests(work) == []
    assert "apply.sh" in result.stdout and "set -euo" not in result.stdout and "LC_ALL" not in result.stdout


STORE_STUB = "#!/usr/bin/env bash\necho 'Python was not found' >&2\nexit 9\n"


def test_a_python_that_does_not_work_is_found_before_the_token_is_asked_for(tmp_path):
    """On Windows `python3` can be the Microsoft Store stub, which exits with an error instead of running."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install(bin_dir, "python3", STORE_STUB)
    _install(bin_dir, "python", STORE_STUB)
    result, work = run_apply(tmp_path)
    assert result.returncode != 0 and requests(work) == []
    assert "python" in result.stderr.lower() and TOKEN not in result.stdout + result.stderr


@pytest.mark.parametrize("status", ["409", "412"])
def test_a_folder_with_the_same_title_under_another_uid_gets_a_message_that_says_what_to_do(tmp_path, status):
    result, work = run_apply(tmp_path, env={"FAKE_FOLDER_POST_STATUS": status})
    assert result.returncode != 0
    assert "CohortLens" in result.stderr and "uid" in result.stderr and status in result.stderr
    assert not any("dashboards/db" in line for line in requests(work))  # nothing was written after the failure


def test_a_201_on_creating_a_rule_is_a_success(tmp_path):
    result, _ = run_apply(tmp_path, env={"FAKE_RULE_CREATE_STATUS": "201"})
    assert result.returncode == 0, result.stderr
