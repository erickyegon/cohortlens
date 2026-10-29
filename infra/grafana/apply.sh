#!/usr/bin/env bash
# Puts the CohortLens dashboard and the two alert rules into a Grafana Cloud stack (issue #23).
#
#   bash infra/grafana/apply.sh --url https://<your-stack>.grafana.net [--dry-run]
#
# It asks for a Grafana service-account token at a HIDDEN prompt. The token is never an argument, never printed and never
# written to a file: it reaches curl through a pipe (curl -K -), so it is not in the process list either. Run it in a
# terminal window (Git Bash). It refuses to run with tracing on, and refuses piped input unless --stdin is passed, which
# reads one line from a pipe and is for automation only: `echo <token> | script` puts the token in the shell history.
#
# What it does, in order (running it again changes nothing that is already right):
#   1. reads the stack's data sources and picks the Prometheus, Loki and Tempo ones (grafanacloud-prom, -logs, -traces)
#   2. creates the folder "CohortLens" if it is missing
#   3. creates or overwrites the dashboard "CohortLens core journey"
#   4. creates or updates each alert rule in alert-rules.json
# The files in this folder hold three placeholders for the data-source ids; this script fills them in at apply time.
set -euo pipefail
export LC_ALL=C

# tracing (bash -x, set -x, or SHELLOPTS=xtrace inherited from the caller) would print the token when it is expanded
case "$-" in
  *x*) echo "error: do not run this script with tracing (bash -x, set -x, SHELLOPTS=xtrace): it would print the token" >&2; exit 2 ;;
esac
# nothing inherited from the caller's environment may stand in for the token
unset TOKEN 2> /dev/null || true

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOLDER_UID="cohortlens"
FOLDER_TITLE="CohortLens"

refuse() { echo "error: $*" >&2; exit 2; }

usage() { awk 'NR > 1 { if ($0 ~ /^#/) { sub(/^# ?/, ""); print } else exit }' "${BASH_SOURCE[0]}"; }

URL="" DRY_RUN=0 STDIN_OK=0
while (( $# )); do
  case "$1" in
    --url) [ $# -ge 2 ] || refuse "--url needs a value"; URL="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --stdin) STDIN_OK=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) refuse "unknown argument: $1 (the token is never an argument; see --help)" ;;
  esac
done

[ -n "$URL" ] || refuse "--url is required, for example --url https://<your-stack>.grafana.net"
[[ "$URL" =~ ^https://[a-z0-9]([a-z0-9-]*[a-z0-9])?\.grafana\.net$ ]] \
  || refuse "--url must look like https://<stack>.grafana.net (https, no path, no credentials, no other host)"

for file in dashboard.json alert-rules.json; do
  [ -f "$DIR/$file" ] || refuse "$DIR/$file is missing"
done

if (( DRY_RUN )); then
  echo "dry run: no token is asked for and nothing is sent to $URL. It would:"
  echo "  1. read $URL/api/datasources and pick the Prometheus, Loki and Tempo data sources"
  echo "  2. create the folder '$FOLDER_TITLE' ($FOLDER_UID) if it does not exist"
  echo "  3. create or overwrite the dashboard from $DIR/dashboard.json"
  echo "  4. create or update the alert rules from $DIR/alert-rules.json: CohortRunErrorRateHigh, CohortRunLatencyHigh"
  exit 0
fi

PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON" ] && "$PYTHON" -c 'import sys' > /dev/null 2>&1   || refuse "a working python is required to read the JSON answers (on Windows, python3 can be the Microsoft Store stub: install Python or turn that alias off)"
command -v curl > /dev/null || refuse "curl is required"

# ---- the token: hidden prompt, or one piped line when --stdin is explicit ------------------------------------------
if [ -t 0 ]; then
  printf 'Grafana service-account token (hidden, nothing is shown): ' >&2
  read -rs TOKEN || true
  printf '\n' >&2
elif (( STDIN_OK )); then
  read -r TOKEN || true
else
  refuse "there is no terminal to ask for the token at a hidden prompt. Run this in a Git Bash window (not through a pipe: piping a token puts it in your shell history). --stdin reads one line from a pipe and is meant for automation only"
fi
TOKEN="${TOKEN%$'\r'}"
[[ "$TOKEN" =~ ^[A-Za-z0-9_.=-]{20,}$ ]] || { unset TOKEN; refuse "that does not look like a Grafana service-account token (20 or more letters, digits, _ . = -, no spaces)"; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# call METHOD PATH [BODY_FILE] [--no-provenance]: sets STATUS; the answer is in $WORK/answer
call() {
  local method="$1" path="$2" body="${3:-}" provenance="${4:-}"
  local -a args=(-sS --max-time 60 --proto '=https' -X "$method" -o "$WORK/answer" -w '%{http_code}'
                 -H 'Accept: application/json' -H 'Content-Type: application/json')
  [ -n "$provenance" ] && args+=(-H 'X-Disable-Provenance: true')
  [ -n "$body" ] && args+=(--data-binary "@$body")
  # the token reaches curl as a config file on a pipe: not in argv, not on disk
  STATUS="$(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | curl -K - "${args[@]}" "$URL$path")" \
    || { echo "error: could not reach $URL ($method $path)" >&2; exit 1; }
  return 0
}

fail() {
  echo "error: $1 $2 answered HTTP $STATUS: $(head -c 300 "$WORK/answer" 2> /dev/null | tr -d '\r\n')" >&2
  exit 1
}

PICK=$(cat <<'PY'
import json, re, sys
items = json.loads(sys.stdin.buffer.read().decode("utf-8"))
preferred = {"prometheus": "grafanacloud-prom", "loki": "grafanacloud-logs", "tempo": "grafanacloud-traces"}
helpers = {"grafanacloud-usage-insights", "grafanacloud-alert-state-history", "grafanacloud-ml-metrics"}
found = []
for kind, wanted in preferred.items():
    of_kind = [item for item in items if item.get("type") == kind]
    chosen = next((item for item in of_kind if item.get("uid") == wanted), None)
    if chosen is None:
        others = [item for item in of_kind if item.get("uid") not in helpers]
        chosen = others[0] if len(others) == 1 else None
    if chosen is None:
        sys.stderr.write("error: this stack has no usable %s data source (looked for %s)\n" % (kind, wanted))
        sys.exit(1)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", chosen["uid"]):
        sys.stderr.write("error: unexpected data source id\n")
        sys.exit(1)
    found.append(chosen["uid"])
print(" ".join(found))
PY
)

DASHBOARD=$(cat <<'PY'
import json, sys
prometheus, loki, tempo, folder = sys.argv[1:5]
text = sys.stdin.buffer.read().decode("utf-8")
text = text.replace("__PROMETHEUS_UID__", prometheus).replace("__LOKI_UID__", loki).replace("__TEMPO_UID__", tempo)
dashboard = json.loads(text)
dashboard["id"] = None
sys.stdout.reconfigure(newline="\n")
print(json.dumps({"dashboard": dashboard, "folderUid": folder, "overwrite": True,
                  "message": "applied by infra/grafana/apply.sh"}, separators=(",", ":")))
PY
)

RULES=$(cat <<'PY'
import json, sys
prometheus, loki, tempo, folder = sys.argv[1:5]
text = sys.stdin.buffer.read().decode("utf-8")
text = (text.replace("__PROMETHEUS_UID__", prometheus).replace("__LOKI_UID__", loki)
        .replace("__TEMPO_UID__", tempo).replace("__FOLDER_UID__", folder))
sys.stdout.reconfigure(newline="\n")
for rule in json.loads(text):
    print(rule["uid"] + "\t" + rule["title"] + "\t" + json.dumps(rule, separators=(",", ":")))
PY
)

# 1. data sources
call GET /api/datasources
[ "$STATUS" = 200 ] || fail GET /api/datasources
read -r PROM LOKI TEMPO < <("$PYTHON" -c "$PICK" < "$WORK/answer" | tr -d '\r') || exit 1
echo "data sources: prometheus=$PROM loki=$LOKI tempo=$TEMPO"

# 2. folder
call GET "/api/folders/$FOLDER_UID"
case "$STATUS" in
  200) echo "folder '$FOLDER_TITLE' exists" ;;
  404)
    printf '{"uid":"%s","title":"%s"}' "$FOLDER_UID" "$FOLDER_TITLE" > "$WORK/folder.json"
    call POST /api/folders "$WORK/folder.json"
    case "$STATUS" in
      200) ;;
      409 | 412) echo "error: Grafana answered HTTP $STATUS creating the folder '$FOLDER_TITLE' (uid $FOLDER_UID): a folder with that title probably exists under another uid. Rename or delete it in Grafana (Dashboards) and run this again" >&2; exit 1 ;;
      *) fail POST /api/folders ;;
    esac
    echo "folder '$FOLDER_TITLE' created" ;;
  *) fail GET "/api/folders/$FOLDER_UID" ;;
esac

# 3. dashboard
"$PYTHON" -c "$DASHBOARD" "$PROM" "$LOKI" "$TEMPO" "$FOLDER_UID" < "$DIR/dashboard.json" | tr -d '\r' > "$WORK/dashboard.json"
call POST /api/dashboards/db "$WORK/dashboard.json"
[ "$STATUS" = 200 ] || fail POST /api/dashboards/db
echo "dashboard applied: $URL/d/cohortlens-core-journey"

# 4. alert rules
"$PYTHON" -c "$RULES" "$PROM" "$LOKI" "$TEMPO" "$FOLDER_UID" < "$DIR/alert-rules.json" | tr -d '\r' > "$WORK/rules.tsv"
while IFS=$'\t' read -r uid title json; do
  [[ "$uid" =~ ^[a-z0-9-]{1,40}$ ]] || refuse "unexpected rule id in alert-rules.json"
  printf '%s' "$json" > "$WORK/rule.json"
  call GET "/api/v1/provisioning/alert-rules/$uid"
  case "$STATUS" in
    200)
      call PUT "/api/v1/provisioning/alert-rules/$uid" "$WORK/rule.json" provenance
      [ "$STATUS" = 200 ] || fail PUT "/api/v1/provisioning/alert-rules/$uid"
      echo "alert rule $title updated" ;;
    404)
      call POST /api/v1/provisioning/alert-rules "$WORK/rule.json" provenance
      [ "$STATUS" = 201 ] || [ "$STATUS" = 200 ] || fail POST /api/v1/provisioning/alert-rules
      echo "alert rule $title created" ;;
    *) fail GET "/api/v1/provisioning/alert-rules/$uid" ;;
  esac
done < "$WORK/rules.tsv"

unset TOKEN
echo "done: open $URL/dashboards and $URL/alerting/list"
