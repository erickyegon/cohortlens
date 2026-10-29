"""The real backend of the on-call agent's tools: Grafana Cloud for metrics, `gcloud` for revisions, the existing
`infra/gcp/rollback.sh` for the rollback, and `gh` for the incident issue.

Nothing here takes a command from the model: every value that reaches a command line or a query was validated by
`Tools` first and is validated again here (names against patterns, a version against hex). Commands are argument
lists, never shell strings. The Grafana token is sent in a header, never in a URL.
"""
import json
import math
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime

METRIC = "cohortlens_cohort_runs_total"
DURATION = "cohortlens_cohort_run_duration_seconds_bucket"
INCIDENT_LABEL = "incident"
ROLLBACK_MARKER = re.compile(r"<!-- oncall-rollback service=(api|web) env=(dev|prod) at=([0-9T:.+Z-]+) -->")
_ENV = re.compile(r"dev|prod")
_VERSION = re.compile(r"[0-9a-f]{7,40}")
_SERVICE = re.compile(r"api|web")
_REVISION = re.compile(r"cohortlens-(api|web)-(dev|prod)-\d{5}-[a-z0-9]{3}")
_GRAFANA = re.compile(r"https://[a-z0-9]([a-z0-9-]*[a-z0-9])?\.grafana\.net")
_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
_REGION = re.compile(r"[a-z]+-[a-z]+[0-9]+")
_REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_DATASOURCE = re.compile(r"[A-Za-z0-9_-]{1,40}")


def _run(command, input_text=None, timeout=900):
    return subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=timeout, check=False)


def _fetch(url, headers):
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - https, host validated
    with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310
        return json.loads(answer.read().decode("utf-8"))


def promql(metric: str, window: str, environment: str) -> str:
    if not _ENV.fullmatch(environment) or window not in ("5m", "10m", "30m", "1h", "6h"):
        raise ValueError("invalid environment or window")
    selector = f'environment="{environment}"'
    queries = {
        "error_ratio": (f'sum by (revision) (increase({METRIC}{{{selector},outcome=~"failed|timeout"}}[{window}])) '
                        f"/ sum by (revision) (increase({METRIC}{{{selector}}}[{window}]))"),
        "p95_seconds": (f"histogram_quantile(0.95, sum by (revision, le) (rate({DURATION}{{{selector}}}[{window}])))"),
        "run_rate": f"sum by (revision) (rate({METRIC}{{{selector}}}[{window}]))",
        "success_runs": f'sum by (revision) (increase({METRIC}{{{selector},outcome="success"}}[{window}]))',
    }
    if metric not in queries:
        raise ValueError("unknown metric")
    return queries[metric]


class LiveBackend:
    def __init__(self, env, run=_run, fetch=_fetch):
        self._run, self._fetch = run, fetch
        self.project = self._need(env, "PROJECT_ID", _PROJECT)
        self.region = self._need(env, "REGION", _REGION)
        self.repo = self._need(env, "GITHUB_REPOSITORY", _REPO)
        self.grafana = self._need(env, "GRAFANA_URL", _GRAFANA)
        self.datasource = env.get("GRAFANA_PROM_UID", "grafanacloud-prom")
        if not _DATASOURCE.fullmatch(self.datasource):
            raise ValueError("invalid GRAFANA_PROM_UID")
        self._token = env.get("GRAFANA_READ_TOKEN", "")
        self._run_url = env.get("GITHUB_RUN_URL", "")

    @staticmethod
    def _need(env, name, pattern):
        value = env.get(name, "")
        if not pattern.fullmatch(value):
            raise ValueError(f"{name} is missing or invalid")
        return value

    # ---- metrics ----------------------------------------------------------------------------------------------------

    def _instant(self, query):
        url = (f"{self.grafana}/api/datasources/proxy/uid/{self.datasource}/api/v1/query?"
               + urllib.parse.urlencode({"query": query}))
        answer = self._fetch(url, {"Authorization": f"Bearer {self._token}", "Accept": "application/json"})
        if answer.get("status") != "success":
            raise RuntimeError("the metrics query failed")
        return answer["data"]["result"]

    def query_metric(self, name, window, environment):
        series = []
        for item in self._instant(promql(name, window, environment)):
            value = float(item["value"][1])
            series.append({"revision": str(item["metric"].get("revision", ""))[:40],
                           "value": None if math.isnan(value) or math.isinf(value) else round(value, 4)})
        return series

    def successful_runs(self, environment, version):
        if not _ENV.fullmatch(environment) or not _VERSION.fullmatch(version):
            raise ValueError("invalid environment or version")
        query = (f'sum(increase({METRIC}{{environment="{environment}",outcome="success",revision="{version}"}}[7d]))')
        result = self._instant(query)
        return float(result[0]["value"][1]) if result else 0.0

    # ---- Cloud Run --------------------------------------------------------------------------------------------------

    def _gcloud_json(self, *args):
        done = self._run(["gcloud", "run", *args, "--region", self.region, "--project", self.project, "--format=json"])
        if done.returncode != 0:
            raise RuntimeError("gcloud failed")
        return json.loads(done.stdout)

    def list_revisions(self, environment, service):
        if not _ENV.fullmatch(environment) or not _SERVICE.fullmatch(service):
            raise ValueError("invalid environment or service")
        name = f"cohortlens-{service}-{environment}"
        traffic = {}
        for entry in self._gcloud_json("services", "describe", name)["status"].get("traffic", []):
            if entry.get("revisionName"):
                traffic[entry["revisionName"]] = traffic.get(entry["revisionName"], 0) + int(entry.get("percent", 0))
        found = []
        for item in self._gcloud_json("revisions", "list", "--service", name, "--limit", "15"):
            revision = item["metadata"]["name"]
            env = {entry["name"]: entry.get("value", "") for entry in item["spec"]["containers"][0].get("env", [])}
            found.append({"name": revision, "created": item["metadata"]["creationTimestamp"],
                          "version": env.get("SERVICE_VERSION", ""), "traffic_percent": traffic.get(revision, 0)})
        return sorted(found, key=lambda entry: entry["created"], reverse=True)

    def rollback(self, environment, service, revision, incident):
        if not (_ENV.fullmatch(environment) and _SERVICE.fullmatch(service) and _REVISION.fullmatch(revision)):
            raise ValueError("invalid rollback target")
        command = ["bash", "infra/gcp/rollback.sh", "--env", environment, "--service", service, "--revision", revision]
        if incident:
            command += ["--incident", str(int(incident))]
        done = self._run(command)
        tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
        ok = done.returncode == 0
        if ok and incident:
            moment = datetime_now_iso()
            self._comment(incident, f"<!-- oncall-rollback service={service} env={environment} at={moment} -->\n"
                                    f"On-call agent rolled {service} back to {revision} in {environment}.")
        return {"ok": ok, "detail": " | ".join(tail)[:500]}

    # ---- GitHub incident issue --------------------------------------------------------------------------------------

    def _gh(self, *args, input_text=None):
        done = self._run(["gh", *args, "--repo", self.repo], input_text=input_text)
        if done.returncode != 0:
            raise RuntimeError("gh failed")
        return done.stdout

    def _comment(self, incident, body):
        self._gh("issue", "comment", str(int(incident)), "--body-file", "-", input_text=body)

    def comment_incident(self, incident, body, status):
        text = f"**On-call agent** ({status})\n\n{body}\n"
        if self._run_url:
            text += f"\nRun: {self._run_url}\n"
        self._comment(incident, text)
        if status == "escalated":
            self._gh("issue", "edit", str(int(incident)), "--add-label", "needs-human")
        if status == "resolved":
            self._gh("issue", "edit", str(int(incident)), "--add-label", "resolved-by-agent")
            self._gh("issue", "close", str(int(incident)))

    def rollback_history(self, environment, service):
        listing = json.loads(self._gh("issue", "list", "--label", INCIDENT_LABEL, "--state", "all", "--limit", "30",
                                      "--json", "comments"))
        moments = []
        for issue in listing:
            for comment in issue.get("comments", []):
                for found_service, found_env, stamp in ROLLBACK_MARKER.findall(comment.get("body", "")):
                    if found_service == service and found_env == environment:
                        moments.append(datetime.fromisoformat(stamp.replace("Z", "+00:00")))
        return moments

    def find_incident(self, fingerprint):
        marker = f"<!-- oncall-fingerprint:{fingerprint} -->"
        listing = json.loads(self._gh("issue", "list", "--label", INCIDENT_LABEL, "--state", "open", "--limit", "30",
                                      "--json", "number,body"))
        return next((issue["number"] for issue in listing if marker in issue.get("body", "")), None)

    def open_incident(self, alert):
        marker = f"<!-- oncall-fingerprint:{alert.fingerprint} -->"
        body = (f"{marker}\nAlert **{alert.alertname}** in **{alert.environment}**, started {alert.starts_at}.\n"
                f"Opened by the on-call workflow; the agent's findings follow as comments.\n")
        out = self._gh("issue", "create", "--title", f"[incident] {alert.alertname} in {alert.environment}",
                       "--label", INCIDENT_LABEL, "--body-file", "-", input_text=body)
        return int(out.strip().rsplit("/", 1)[-1])


def datetime_now_iso() -> str:
    from datetime import UTC
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
