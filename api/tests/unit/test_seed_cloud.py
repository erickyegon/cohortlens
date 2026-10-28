"""Issue #17 (E4 delivery): scripts/seed_cloud.sh, `make seed-cloud`: Neon roles, schema and seed for an
environment, and the two database URLs into Secret Manager.

It runs against a FAKE `psql` and a FAKE `gcloud` that record every call and the SQL fed on stdin, so nothing touches
Neon or Google Cloud. What it must do: refuse unless the operator confirms the endpoint they are about to change;
never print or pass a password on a command line; create the two roles with generated passwords and write the matching
URLs to Secret Manager; load the seed in ONE transaction and only into an empty database; apply the grants; be safe
to run twice; never delete or drop anything.
"""
import os
import pathlib
import re
import secrets
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "seed_cloud.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the seed script")

ENDPOINT = "ep-cool-dev-123456"
HOST = f"{ENDPOINT}.us-east-2.aws.neon.tech"
OWNER_PASSWORD = "fake" + secrets.token_hex(8)  # generated per run: no credential-looking literal in the repository
ADMIN_URL = f"postgresql://neondb_owner:{OWNER_PASSWORD}@{HOST}/neondb?sslmode=require"
SEP = "\x1f"

FAKE_PSQL = r"""#!/usr/bin/env bash
# records the arguments and the SQL fed on stdin; answers the script's state questions from the environment
n=$(( $(cat "$FAKE_DIR/count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$FAKE_DIR/count"
printf '%s\x1f' "$@" > "$FAKE_DIR/psql_args_$n"
if [ ! -t 0 ]; then cat > "$FAKE_DIR/psql_stdin_$n"; fi
env | grep '^PG' | sed 's/^PGPASSWORD=.*/PGPASSWORD=<set>/' > "$FAKE_DIR/psql_env_$n"
printf 'psql %s\n' "$n" >> "$FAKE_DIR/order"
args="$*"
if [ "${FAKE_PSQL_FAIL_ON:-}" != "" ] && grep -qa "$FAKE_PSQL_FAIL_ON" "$FAKE_DIR/psql_stdin_$n" 2>/dev/null; then
  echo "ERROR: simulated failure" >&2; exit 1
fi
if [[ "$args" == *"from pg_roles"* ]]; then echo "${FAKE_ROLES_EXIST:-0}"; exit 0; fi
if [[ "$args" == *"to_regclass"* ]]; then
  if [ "${FAKE_PERSON_BEFORE:-0}" = "0" ]; then echo f; else echo t; fi; exit 0
fi
if [[ "$args" == *"count(*) from cdm.person"* ]]; then
  # the count is the "before" value until the seed has been fed to psql, the "after" value from then on
  if grep -qa "CREATE TABLE cdm.person" "$FAKE_DIR"/psql_stdin_* 2>/dev/null; then echo "${FAKE_PERSON_AFTER:-5724}"
  else echo "${FAKE_PERSON_BEFORE:-0}"; fi
  exit 0
fi
exit 0
"""

FAKE_GCLOUD = r"""#!/usr/bin/env bash
n=$(( $(cat "$FAKE_DIR/gcount" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$FAKE_DIR/gcount"
printf '%s\x1f' "$@" > "$FAKE_DIR/gcloud_args_$n"
if [[ "$*" == *"versions list"* ]] && [ "${FAKE_LIST_FAIL:-0}" = "1" ]; then echo "ERROR: list failed" >&2; exit 1; fi
if [[ "$*" == *"versions add"* ]]; then
  cat > "$FAKE_DIR/gcloud_stdin_$n"
  if [ "${FAKE_SECRET_WRITE_FAIL:-0}" = "1" ]; then echo "ERROR: write failed" >&2; exit 1; fi
fi
if [[ "$*" == *"versions access"* ]]; then
  if [ -z "${FAKE_OTHER_URL-}" ] || [ "${FAKE_ACCESS_FAIL:-0}" = "1" ]; then echo "ERROR: access failed" >&2; exit 1; fi
  printf '%s' "$FAKE_OTHER_URL"; exit 0
fi
printf 'gcloud %s\n' "$n" >> "$FAKE_DIR/order"
if [[ "$*" == *"versions list"* ]]; then echo "${FAKE_SECRET_VERSION-projects/1/secrets/s/versions/1}"; fi
exit 0
"""


def _install(directory, name, body):
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


OTHER_ENDPOINT = "ep-other-env-999999"
OTHER_PASSWORD = "other" + secrets.token_hex(8)
OTHER_URL = f"postgresql://cohortlens_app:{OTHER_PASSWORD}@{OTHER_ENDPOINT}.us-east-2.aws.neon.tech/neondb?sslmode=require"
PROD_ENDPOINT = "ep-warm-prod-654321"
PROD_HOST = f"{PROD_ENDPOINT}.us-east-2.aws.neon.tech"
PROD_URL = f"postgresql://neondb_owner:{OWNER_PASSWORD}@{PROD_HOST}/neondb?sslmode=require"
PROD_ARGS = ("--env", "prod", "--confirm-endpoint", PROD_ENDPOINT, "--yes-this-is-production")


def run_seed(tmp_path, *args, env=None, admin_url=ADMIN_URL, confirm=True, url_var="NEON_DEV_URL"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    _install(bin_dir, "psql", FAKE_PSQL)
    _install(bin_dir, "gcloud", FAKE_GCLOUD)
    full_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_DIR": str(work),
                "PROJECT_ID": "cohortlens", "FAKE_OTHER_URL": OTHER_URL, **(env or {})}
    for name in ("NEON_DEV_URL", "NEON_PROD_URL"):
        full_env.pop(name, None)
    if admin_url is not None:
        full_env[url_var] = admin_url
    for name, value in (env or {}).items():
        if name in ("NEON_DEV_URL", "NEON_PROD_URL"):
            full_env[name] = value
    if not args:
        args = ("--env", "dev") + (("--confirm-endpoint", ENDPOINT) if confirm else ())
    result = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=full_env, timeout=120,
                            cwd=str(ROOT))
    return result, work


def files(work, prefix):
    found = sorted((p for p in work.glob(f"{prefix}_*") if p.is_file()), key=lambda p: int(p.name.rsplit("_", 1)[1]))
    return [p.read_text(encoding="utf-8", errors="replace") for p in found]


def psql_calls(work):
    return list(zip(files(work, "psql_args"), strict=False))


def stdins(work):
    return files(work, "psql_stdin")


def all_text(result, work):
    parts = [result.stdout, result.stderr, *files(work, "psql_args"), *files(work, "gcloud_args"),
             *files(work, "psql_env")]
    return "\n".join(parts)


def secret_writes(work):
    """The `secrets versions add` calls: (arguments, the value that was fed on stdin), matched by call number."""
    out = []
    for path in sorted(work.glob("gcloud_stdin_*"), key=lambda item: int(item.name.rsplit("_", 1)[1])):
        number = path.name.rsplit("_", 1)[1]
        args = (work / f"gcloud_args_{number}").read_text(encoding="utf-8", errors="replace")
        out.append((args.split(SEP)[:-1], path.read_text(encoding="utf-8", errors="replace")))
    return out


def written_urls(work):
    urls = {}
    for args, stdin in secret_writes(work):
        urls[args[3]] = stdin
    return urls


def seeded(tmp_path, **kwargs):
    result, work = run_seed(tmp_path, **kwargs)
    assert result.returncode == 0, result.stderr + result.stdout
    return result, work


def test_the_script_exists_and_is_valid_bash_with_strict_mode():
    assert SCRIPT.exists()
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text(encoding="utf-8")


# ---- refusals happen before anything is touched -----------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ["--env", "prod", "--confirm-endpoint", ENDPOINT],                    # prod is a separate, deliberate step
    ["--env", "dev"],                                                      # no endpoint confirmation
    ["--env", "dev", "--confirm-endpoint", "ep-some-other-branch-999"],   # the wrong branch's endpoint
    ["--env", "dev", "--confirm-endpoint", ENDPOINT, "--force"],
    ["--confirm-endpoint", ENDPOINT],
    ["--env", "dev", "--confirm-endpoint"],                                # a flag without its value must not hang
    ["unexpected-positional"],
])
def test_bad_arguments_are_refused_before_anything_is_touched(tmp_path, args):
    result, work = run_seed(tmp_path, *args)
    assert result.returncode != 0
    assert files(work, "psql_args") == [] and files(work, "gcloud_args") == []


@pytest.mark.parametrize("url", [
    None, "", "not a url", "mysql://u:p@host/db?sslmode=require",
    f"postgresql://neondb_owner:{OWNER_PASSWORD}@{HOST}/neondb",                                   # no sslmode
    f"postgresql://neondb_owner:{OWNER_PASSWORD}@{HOST}/neondb?sslmode=disable",
    f"postgresql://neondb_owner:{OWNER_PASSWORD}@{ENDPOINT}-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require",
    f"postgresql://neondb_owner@{HOST}/neondb?sslmode=require",                                    # no password
])
def test_a_missing_or_unsuitable_connection_string_is_refused(tmp_path, url):
    result, work = run_seed(tmp_path, admin_url=url)
    assert result.returncode != 0
    assert files(work, "psql_args") == [] and files(work, "gcloud_args") == []
    assert OWNER_PASSWORD not in result.stdout + result.stderr


def test_the_error_for_a_bad_url_does_not_echo_it(tmp_path):
    result, _ = run_seed(tmp_path, admin_url=f"postgresql://neondb_owner:{OWNER_PASSWORD}@{HOST}/neondb")
    assert OWNER_PASSWORD not in result.stdout + result.stderr


# ---- a fresh database -------------------------------------------------------------------------------------------

def test_a_fresh_run_creates_the_roles_then_writes_the_secrets_then_loads_the_seed_then_grants(tmp_path):
    result, work = seeded(tmp_path)
    inputs = stdins(work)
    roles_at = next(i for i, text in enumerate(inputs) if "CREATE ROLE cohortlens_app" in text)
    seed_at = next(i for i, text in enumerate(inputs) if "CREATE TABLE cdm.person" in text)
    grants_at = next(i for i, text in enumerate(inputs) if "GRANT SELECT ON ALL TABLES IN SCHEMA cdm" in text)
    assert roles_at < seed_at < grants_at
    order = (work / "order").read_text(encoding="utf-8").splitlines()
    first_secret = next(i for i, line in enumerate(order) if line.startswith("gcloud") and
                        "add" in files(work, "gcloud_args")[int(line.split()[1]) - 1].split(SEP))
    roles_call = next(i for i, line in enumerate(order) if line.startswith("psql") and
                      "CREATE ROLE" in inputs_for(work, line))
    seed_call = next(i for i, line in enumerate(order) if line.startswith("psql") and
                     "CREATE TABLE cdm.person" in inputs_for(work, line))
    assert roles_call < first_secret < seed_call  # roles and their secrets agree before the long load starts
    assert "5724" in result.stdout


def inputs_for(work, line):
    number = line.split()[1]
    path = work / f"psql_stdin_{number}"
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def test_the_seed_is_loaded_in_one_transaction(tmp_path):
    _, work = seeded(tmp_path)
    loads = [args for args, text in zip(files(work, "psql_args"), files(work, "psql_stdin"), strict=False)
             if "CREATE TABLE cdm.person" in text]
    assert len(loads) == 1 and "--single-transaction" in loads[0].split(SEP) and "ON_ERROR_STOP=1" in loads[0]


def test_two_secrets_are_written_with_urls_for_the_two_roles_at_the_same_host_and_database(tmp_path):
    _, work = seeded(tmp_path)
    urls = written_urls(work)
    assert set(urls) == {"cohortlens-dev-database-url-app", "cohortlens-dev-database-url-reader"}
    for kind, secret in (("app", "cohortlens-dev-database-url-app"), ("reader", "cohortlens-dev-database-url-reader")):
        match = re.fullmatch(rf"postgresql://cohortlens_{kind}:([0-9a-f]{{32,}})@{re.escape(HOST)}/neondb\?sslmode=require",
                             urls[secret])
        assert match, urls[secret]
    assert re.search(r":([0-9a-f]+)@", urls["cohortlens-dev-database-url-app"]).group(1) != \
        re.search(r":([0-9a-f]+)@", urls["cohortlens-dev-database-url-reader"]).group(1)


def test_the_password_in_each_secret_is_the_password_given_to_the_role(tmp_path):
    _, work = seeded(tmp_path)
    urls = written_urls(work)
    sql = next(text for text in stdins(work) if "CREATE ROLE cohortlens_app" in text)
    for kind in ("app", "reader"):
        password = re.search(r":([0-9a-f]+)@", urls[f"cohortlens-dev-database-url-{kind}"]).group(1)
        assert f"\\set {kind}_pw '{password}'" in sql


def test_secrets_are_written_with_gcloud_from_stdin_to_the_right_project(tmp_path):
    _, work = seeded(tmp_path)
    for args, _ in secret_writes(work):
        assert args[:3] == ["secrets", "versions", "add"] and "--data-file=-" in args
        assert "--project=cohortlens" in args


def test_no_password_or_url_ever_appears_in_output_or_on_a_command_line(tmp_path):
    result, work = seeded(tmp_path)
    text = all_text(result, work)
    assert OWNER_PASSWORD not in text
    for url in written_urls(work).values():
        assert url not in text and re.search(r":([0-9a-f]{32,})@", url).group(1) not in text
    assert "postgresql://" not in text


def test_the_owner_connection_reaches_psql_through_libpq_environment_variables(tmp_path):
    _, work = seeded(tmp_path)
    environment = files(work, "psql_env")[0]
    for line in (f"PGHOST={HOST}", "PGUSER=neondb_owner", "PGDATABASE=neondb", "PGSSLMODE=require",
                 "PGPASSWORD=<set>"):
        assert line in environment, environment


def test_the_cloud_role_sql_has_no_literal_password_and_no_schema_authorization(tmp_path):
    sql = (ROOT / "db" / "cloud" / "00-roles.sql").read_text(encoding="utf-8")
    assert ":'app_pw'" in sql and ":'reader_pw'" in sql
    assert "AUTHORIZATION" not in sql  # the Neon owner is not a superuser and cannot create a schema for another role
    assert "GRANT USAGE, CREATE ON SCHEMA app TO cohortlens_app" in sql


def test_the_grants_are_the_same_file_the_local_stack_uses(tmp_path):
    _, work = seeded(tmp_path)
    grants = (ROOT / "db" / "init" / "20-grants.sql").read_text(encoding="utf-8")
    assert any(grants.strip() in text for text in stdins(work))


# ---- safe to run twice ------------------------------------------------------------------------------------------

def test_a_second_run_creates_no_role_loads_no_seed_and_writes_no_secret(tmp_path):
    result, work = seeded(tmp_path, env={"FAKE_ROLES_EXIST": "2", "FAKE_PERSON_BEFORE": "5724"})
    text = "\n".join(stdins(work))
    assert "CREATE ROLE" not in text and "CREATE TABLE cdm.person" not in text
    assert secret_writes(work) == []
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA cdm" in text  # the grants are idempotent and re-applied
    assert "already" in result.stdout.lower()


def test_existing_roles_without_a_stored_secret_are_refused_with_the_way_out(tmp_path):
    result, work = run_seed(tmp_path, env={"FAKE_ROLES_EXIST": "2", "FAKE_SECRET_VERSION": ""})
    assert result.returncode != 0 and "--rotate-passwords" in result.stderr
    assert secret_writes(work) == [] and "CREATE ROLE" not in "\n".join(stdins(work))


def test_only_one_of_the_two_roles_existing_is_refused(tmp_path):
    result, work = run_seed(tmp_path, env={"FAKE_ROLES_EXIST": "1"})
    assert result.returncode != 0 and secret_writes(work) == []


def test_rotating_passwords_alters_the_roles_and_writes_new_secret_versions_without_reloading_the_seed(tmp_path):
    _, work = seeded(tmp_path, env={"FAKE_ROLES_EXIST": "2", "FAKE_PERSON_BEFORE": "5724"}) \
        if False else (None, None)
    result, work = run_seed(tmp_path, "--env", "dev", "--confirm-endpoint", ENDPOINT, "--rotate-passwords",
                            env={"FAKE_ROLES_EXIST": "2", "FAKE_PERSON_BEFORE": "5724"})
    assert result.returncode == 0, result.stderr
    text = "\n".join(stdins(work))
    assert "ALTER ROLE cohortlens_app" in text and "ALTER ROLE cohortlens_reader" in text
    assert "CREATE ROLE" not in text and "CREATE TABLE cdm.person" not in text
    assert len(secret_writes(work)) == 2


def test_a_seed_is_never_loaded_into_a_database_that_already_has_data(tmp_path):
    result, work = run_seed(tmp_path, env={"FAKE_PERSON_BEFORE": "17", "FAKE_PERSON_AFTER": "17"})
    assert result.returncode != 0 or "CREATE TABLE cdm.person" not in "\n".join(stdins(work))
    assert "CREATE TABLE cdm.person" not in "\n".join(stdins(work))


# ---- failures stop the script before anything worse happens -----------------------------------------------------

def test_a_failed_role_creation_writes_no_secret_and_loads_no_seed(tmp_path):
    result, work = run_seed(tmp_path, env={"FAKE_PSQL_FAIL_ON": "CREATE ROLE"})
    assert result.returncode != 0
    assert secret_writes(work) == [] and "CREATE TABLE cdm.person" not in "\n".join(stdins(work))


def test_a_failed_secret_write_stops_before_the_seed_is_loaded(tmp_path):
    result, work = run_seed(tmp_path, env={"FAKE_SECRET_WRITE_FAIL": "1"})
    assert result.returncode != 0 and "CREATE TABLE cdm.person" not in "\n".join(stdins(work))


def test_a_wrong_final_person_count_fails_the_run(tmp_path):
    result, _ = run_seed(tmp_path, env={"FAKE_PERSON_AFTER": "100"})
    assert result.returncode != 0 and "5724" in result.stderr


def test_nothing_is_dropped_deleted_or_truncated(tmp_path):
    _, work = seeded(tmp_path)
    text = "\n".join(stdins(work) + files(work, "gcloud_args")).upper()
    seed_free = text.split("CREATE SCHEMA IF NOT EXISTS CDM;")[0]
    for word in ("DROP ", "TRUNCATE", "DELETE FROM", "DESTROY", " DELETE"):
        assert word not in seed_free, word
    assert "versions destroy" not in text.lower() and "secrets delete" not in text.lower()


def test_a_failed_secret_write_says_how_to_recover_because_the_roles_have_already_changed(tmp_path):
    result, work = run_seed(tmp_path, env={"FAKE_SECRET_WRITE_FAIL": "1"})
    assert result.returncode != 0
    assert "--rotate-passwords" in result.stderr and "secret" in result.stderr.lower()


def test_a_failed_secret_write_during_rotation_says_how_to_recover(tmp_path):
    result, _ = run_seed(tmp_path, "--env", "dev", "--confirm-endpoint", ENDPOINT, "--rotate-passwords",
                         env={"FAKE_ROLES_EXIST": "2", "FAKE_PERSON_BEFORE": "5724", "FAKE_SECRET_WRITE_FAIL": "1"})
    assert result.returncode != 0 and "--rotate-passwords" in result.stderr


def test_only_enabled_secret_versions_count_as_a_stored_value(tmp_path):
    _, work = seeded(tmp_path, env={"FAKE_ROLES_EXIST": "2", "FAKE_PERSON_BEFORE": "5724"})
    listing = [args for args in files(work, "gcloud_args") if "list" in args.split(SEP)]
    own_secrets = ("cohortlens-dev-database-url-app", "cohortlens-dev-database-url-reader")
    own = [args for args in listing if any(name in args for name in own_secrets)]
    assert len(own) == 2 and all("--filter=state=enabled" in args.split(SEP) for args in listing)


@pytest.mark.parametrize("host", [f"{ENDPOINT}.example.com", ENDPOINT, f"{ENDPOINT}.neon.tech.evil.io"])
def test_a_host_that_is_not_a_neon_host_is_refused(tmp_path, host):
    url = f"postgresql://neondb_owner:{OWNER_PASSWORD}@{host}/neondb?sslmode=require"
    result, work = run_seed(tmp_path, admin_url=url)
    assert result.returncode != 0 and files(work, "psql_args") == []


# ---- production (the extra guards) ------------------------------------------------------------------------------

def test_prod_needs_its_own_url_variable_the_endpoint_and_the_explicit_production_flag(tmp_path):
    cases = (
        (("--env", "prod", "--confirm-endpoint", PROD_ENDPOINT), {"admin_url": PROD_URL}),            # no flag
        (PROD_ARGS, {"admin_url": None}),                                                             # no URL
        (PROD_ARGS, {"admin_url": None, "env": {"NEON_DEV_URL": PROD_URL}}),                         # DEV variable only
        (("--env", "prod", "--confirm-endpoint", ENDPOINT, "--yes-this-is-production"), {"admin_url": PROD_URL}),
    )
    for args, kwargs in cases:
        result, work = run_seed(tmp_path, *args, url_var="NEON_PROD_URL", **kwargs)
        assert result.returncode != 0, args
        assert files(work, "psql_args") == [] and files(work, "gcloud_args") == []


def test_the_production_flag_is_refused_for_dev(tmp_path):
    result, work = run_seed(tmp_path, "--env", "dev", "--confirm-endpoint", ENDPOINT, "--yes-this-is-production")
    assert result.returncode != 0 and files(work, "psql_args") == []


def test_prod_seeds_the_prod_branch_and_stores_the_prod_secrets(tmp_path):
    result, work = run_seed(tmp_path, *PROD_ARGS, admin_url=PROD_URL, url_var="NEON_PROD_URL")
    assert result.returncode == 0, result.stderr + result.stdout
    assert set(written_urls(work)) == {"cohortlens-prod-database-url-app", "cohortlens-prod-database-url-reader"}
    for url in written_urls(work).values():
        assert f"@{PROD_HOST}/neondb?sslmode=require" in url
    assert f"PGHOST={PROD_HOST}" in files(work, "psql_env")[0]
    assert "GCP_PROJECT_ID" not in result.stdout  # the dev-deploy hint is for dev only
    assert "prod" in result.stdout.lower()


def test_prod_uses_the_prod_variable_even_when_the_dev_variable_is_also_set(tmp_path):
    result, work = run_seed(tmp_path, *PROD_ARGS, admin_url=PROD_URL, url_var="NEON_PROD_URL",
                            env={"NEON_DEV_URL": ADMIN_URL})
    assert result.returncode == 0 and f"PGHOST={PROD_HOST}" in files(work, "psql_env")[0]


def test_prod_refuses_an_endpoint_that_dev_already_uses_before_touching_the_database(tmp_path):
    dev_url = f"postgresql://cohortlens_app:{OTHER_PASSWORD}@{PROD_HOST}/neondb?sslmode=require"
    result, work = run_seed(tmp_path, *PROD_ARGS, admin_url=PROD_URL, url_var="NEON_PROD_URL",
                            env={"FAKE_OTHER_URL": dev_url})
    assert result.returncode != 0 and "is the one dev already uses" in result.stderr
    assert files(work, "psql_args") == [] and secret_writes(work) == []
    assert OTHER_PASSWORD not in all_text(result, work) and OWNER_PASSWORD not in all_text(result, work)


def test_dev_refuses_an_endpoint_that_prod_already_uses(tmp_path):
    prod_url = f"postgresql://cohortlens_app:{OTHER_PASSWORD}@{HOST}/neondb?sslmode=require"
    result, work = run_seed(tmp_path, env={"FAKE_OTHER_URL": prod_url})
    assert result.returncode != 0 and "is the one prod already uses" in result.stderr
    assert files(work, "psql_args") == [] and secret_writes(work) == []


def test_prod_is_refused_until_dev_has_been_prepared(tmp_path):
    result, work = run_seed(tmp_path, *PROD_ARGS, admin_url=PROD_URL, url_var="NEON_PROD_URL",
                            env={"FAKE_SECRET_VERSION": ""})
    assert result.returncode != 0 and "dev has not been prepared yet" in result.stderr
    assert files(work, "psql_args") == []


def test_dev_does_not_need_prod_to_exist_yet(tmp_path):
    result, _ = run_seed(tmp_path, env={"FAKE_SECRET_VERSION": ""})
    assert result.returncode == 0, result.stderr + result.stdout


def test_a_secret_that_cannot_be_read_stops_the_run_before_the_database(tmp_path):
    result, work = run_seed(tmp_path, env={"FAKE_ACCESS_FAIL": "1"})
    assert result.returncode != 0 and files(work, "psql_args") == []


def test_the_other_environments_secret_is_read_not_printed_and_only_by_reference(tmp_path):
    result, work = seeded(tmp_path)
    reads = [args.split(SEP)[:-1] for args in files(work, "gcloud_args") if "access" in args.split(SEP)]
    assert len(reads) == 1
    assert "--secret=cohortlens-prod-database-url-app" in reads[0] and "--project=cohortlens" in reads[0]
    assert OTHER_PASSWORD not in all_text(result, work) and OTHER_ENDPOINT not in result.stdout


def test_prod_can_be_run_twice_and_rotated_like_dev(tmp_path):
    existing = {"FAKE_ROLES_EXIST": "2", "FAKE_PERSON_BEFORE": "5724"}
    second, work = run_seed(tmp_path, *PROD_ARGS, admin_url=PROD_URL, url_var="NEON_PROD_URL", env=existing)
    assert second.returncode == 0 and secret_writes(work) == []
    rotated, work = run_seed(tmp_path, *PROD_ARGS, "--rotate-passwords", admin_url=PROD_URL, url_var="NEON_PROD_URL",
                             env=existing)
    assert rotated.returncode == 0 and len(secret_writes(work)) == 2


def test_an_other_environment_secret_that_holds_no_readable_host_stops_the_run_before_the_database(tmp_path):
    for value in ("not a url", "postgresql://user:pw@/neondb"):
        result, work = run_seed(tmp_path, env={"FAKE_OTHER_URL": value})
        assert result.returncode != 0 and "does not hold a database URL" in result.stderr
        assert files(work, "psql_args") == [] and secret_writes(work) == []
        assert value not in result.stderr and "pw" not in result.stderr.replace("password", "")


def test_a_failing_check_of_the_other_environments_secret_stops_the_run_before_the_database(tmp_path):
    result, work = run_seed(tmp_path, env={"FAKE_LIST_FAIL": "1"})
    assert result.returncode != 0 and "could not check whether prod has been prepared" in result.stderr
    assert files(work, "psql_args") == [] and secret_writes(work) == []
