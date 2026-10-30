"""infra/gcp/cancel_stale_promotions.sh: cancels "Promote to prod" runs that are still WAITING for approval and are
older than the current run, so a stale promotion cannot sit approvable for hours (the near-miss of 2026-10-29, audit
section 13).

It runs against a FAKE gh, so nothing touches GitHub. A run that is already deploying (in progress) must never be cut
off, and a failure to list or cancel must never block the promotion that called it.
"""
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra" / "gcp" / "cancel_stale_promotions.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the script")

SEP = "\x1f"
REPO = "erickyegon/cohortlens"
CURRENT = "500"

# the fake answers the listing with these lines (id, status, head sha) and logs every call
FAKE_GH = r"""#!/usr/bin/env bash
printf '%s\x1f' "$@" >> "$FAKE_GH_LOG"; printf '\n' >> "$FAKE_GH_LOG"
case "$*" in
  *"-X POST"*"/cancel")
    id="$(printf '%s' "$*" | sed -E 's#.*/runs/([0-9]+)/cancel#\1#')"
    case " $FAKE_CANCEL_FAILS " in *" $id "*) echo "gh: HTTP 409" >&2; exit 1 ;; esac
    exit 0 ;;
  *"promote-prod.yml/runs"*)
    [ "${FAKE_LIST_FAIL:-0}" = "1" ] && { echo "gh: HTTP 502" >&2; exit 1; }
    printf '%s' "$FAKE_RUNS"
    exit 0 ;;
esac
exit 0
"""


def run_script(tmp_path, runs, *args, env=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "gh.log"
    log.write_text("", encoding="utf-8")
    listing = "".join(f"{run_id}\t{status}\t{sha}\n" for run_id, status, sha in runs)
    full_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_LOG": str(log),
                "FAKE_RUNS": listing, "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": CURRENT, "GH_TOKEN": "x",
                **(env or {})}
    result = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=full_env, timeout=60)
    calls = [line.split(SEP)[:-1] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    cancelled = [c[-1].split("/")[-2] for c in calls if "POST" in c]
    return result, calls, cancelled


SHA = "a" * 40


def test_it_cancels_older_waiting_runs_and_names_them(tmp_path):
    result, _, cancelled = run_script(tmp_path, [("400", "waiting", SHA), ("450", "waiting", SHA)])
    assert result.returncode == 0, result.stderr
    assert cancelled == ["400", "450"]
    assert "400" in result.stdout and "450" in result.stdout


def test_it_never_cancels_the_current_run_or_a_newer_one(tmp_path):
    runs = [("400", "waiting", SHA), (CURRENT, "waiting", SHA), ("600", "waiting", SHA)]
    _, _, cancelled = run_script(tmp_path, runs)
    assert cancelled == ["400"]


def test_it_never_cancels_a_run_that_is_already_deploying(tmp_path):
    runs = [("400", "in_progress", SHA), ("410", "queued", SHA), ("420", "waiting", SHA), ("430", "completed", SHA)]
    _, _, cancelled = run_script(tmp_path, runs)
    assert cancelled == ["420"]  # only a run waiting for approval; the listing is asked for waiting runs and rechecked


def test_it_asks_only_for_this_workflows_waiting_runs(tmp_path):
    _, calls, _ = run_script(tmp_path, [])
    listing = [c for c in calls if any("promote-prod.yml/runs" in part for part in c)]
    assert len(listing) == 1 and any("status=waiting" in part for part in listing[0])
    assert f"repos/{REPO}/actions/workflows/promote-prod.yml/runs" in " ".join(listing[0])


def test_it_says_so_when_there_is_nothing_to_cancel(tmp_path):
    result, _, cancelled = run_script(tmp_path, [])
    assert result.returncode == 0 and cancelled == [] and "no stale" in result.stdout.lower()


def test_a_dry_run_lists_what_it_would_cancel_and_cancels_nothing(tmp_path):
    result, calls, cancelled = run_script(tmp_path, [("400", "waiting", SHA)], "--dry-run")
    assert result.returncode == 0 and cancelled == [] and "400" in result.stdout and "dry run" in result.stdout.lower()
    assert not any("POST" in c for c in calls)


def test_a_failure_to_list_never_blocks_the_promotion(tmp_path):
    result, _, cancelled = run_script(tmp_path, [("400", "waiting", SHA)], env={"FAKE_LIST_FAIL": "1"})
    assert result.returncode == 0 and cancelled == [] and "warning" in result.stderr.lower()


def test_a_failure_to_cancel_one_run_does_not_stop_the_others_or_the_promotion(tmp_path):
    result, _, cancelled = run_script(tmp_path, [("400", "waiting", SHA), ("450", "waiting", SHA)],
                                      env={"FAKE_CANCEL_FAILS": "400"})
    assert result.returncode == 0 and cancelled == ["400", "450"]  # both were tried
    assert "warning" in result.stderr.lower() and "400" in result.stderr


@pytest.mark.parametrize("env", [
    {"GITHUB_REPOSITORY": "no-slash"}, {"GITHUB_REPOSITORY": "a/b/c"}, {"GITHUB_REPOSITORY": "a/b;rm -rf x"},
    {"GITHUB_REPOSITORY": ""}, {"GITHUB_RUN_ID": "abc"}, {"GITHUB_RUN_ID": "12; ls"}, {"GITHUB_RUN_ID": ""},
])
def test_bad_settings_are_refused_before_any_call(tmp_path, env):
    result, calls, _ = run_script(tmp_path, [("400", "waiting", SHA)], env=env)
    assert result.returncode == 2 and calls == []


def test_an_unknown_argument_is_refused(tmp_path):
    result, calls, _ = run_script(tmp_path, [], "--force")
    assert result.returncode == 2 and calls == []


def test_a_listed_id_that_is_not_a_number_is_ignored_never_passed_to_gh(tmp_path):
    _, calls, cancelled = run_script(tmp_path, [("400; echo hacked", "waiting", SHA), ("410", "waiting", SHA)])
    assert cancelled == ["410"]
    assert not any("hacked" in part for call in calls for part in call if "POST" in call)
