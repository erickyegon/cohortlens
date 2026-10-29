"""The four tools the on-call agent may call (issue #25).

Every call goes through `Tools.call`, which checks the allowlist and the enabled environments first, validates the input
against a closed schema (named metrics and fixed windows, never free text), applies the guardrails, and honours dry run
for the two write tools. It never raises: a refusal or a failure becomes a structured answer for the model, and a
failure shows the model only the exception class (the message could carry a secret). `calls` records every call
for the tests and for the workflow log.
"""
import re
from datetime import datetime

from oncall.guardrails import (
    SERVICES,
    WRITE_TOOLS,
    Policy,
    Refused,
    check_environment,
    check_rollback,
    check_tool,
)
from oncall.payload import Alert

METRICS = ("error_ratio", "p95_seconds", "run_rate", "success_runs")
WINDOWS = ("5m", "10m", "30m", "1h", "6h")
INCIDENT_STATUSES = ("investigating", "rolled_back", "escalated", "resolved")
SUMMARY_LIMIT = 1500
_REVISION = re.compile(r"cohortlens-(api|web)-(dev|prod)-\d{5}-[a-z0-9]{3}")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

TOOL_SPECS = [
    {"name": "query_metrics",
     "description": "Cohort-run metrics of the alerting environment, one value per revision, over a recent window. "
                    "error_ratio: share of runs that failed or timed out; p95_seconds: 95th percentile run duration; "
                    "run_rate: runs per second; success_runs: number of successful runs.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["metric", "window"],
                      "properties": {"metric": {"type": "string", "enum": list(METRICS)},
                                     "window": {"type": "string", "enum": list(WINDOWS)}}}},
    {"name": "list_revisions",
     "description": "The recent Cloud Run revisions of a service: name, creation time, git version and the share of "
                    "traffic each serves. Use it to see whether a regression began right after a deploy.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["service"],
                      "properties": {"service": {"type": "string", "enum": sorted(SERVICES)}}}},
    {"name": "rollback",
     "description": "Move all traffic of a service to an earlier revision. Refused unless the revision exists, is not "
                    "serving, has at least 50 successful runs, and the service was not rolled back in the last hour. "
                    "Do not call it when every revision fails or when no deploy preceded the regression.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["service", "revision"],
                      "properties": {"service": {"type": "string", "enum": sorted(SERVICES)},
                                     "revision": {"type": "string",
                                                  "description": "e.g. cohortlens-api-dev-00006-abc"}}}},
    {"name": "update_incident",
     "description": "Post a status comment on the incident issue. Use status escalated when a person must act.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["status", "summary"],
                      "properties": {"status": {"type": "string", "enum": list(INCIDENT_STATUSES)},
                                     "summary": {"type": "string",
                                                 "description": "What you saw and what you did, plain text"}}}},
]


def _exact(args, required):
    if not isinstance(args, dict) or set(args) != set(required):
        raise Refused(f"input must be an object with exactly these fields: {sorted(required)}")
    return args


def _choice(value, allowed, field):
    if not isinstance(value, str) or value not in allowed:
        raise Refused(f"{field} must be one of {sorted(allowed)}")
    return value


def clean_text(text: str, limit: int = SUMMARY_LIMIT) -> str:
    """Plain text for the incident issue: no control characters, no @mentions that would notify strangers."""
    text = _CONTROL.sub('', text).replace('@', chr(0xff20))
    # the rollback history and the incident lookup are read back from HTML-comment markers: the model's text
    # must not be able to contain one
    return text.replace('<!--', '< !--').replace('-->', '-- >')[:limit]


class Tools:
    def __init__(self, policy: Policy, backend, alert: Alert, incident: int, now):
        self.policy, self.backend, self.alert, self.incident, self._now = policy, backend, alert, incident, now
        self.calls: list = []

    def call(self, name, args):
        record = {"tool": name, "args": args, "refused": None, "dry_run": False, "error": None}
        self.calls.append(record)
        try:
            check_tool(name)
            check_environment(self.policy, self.alert.environment)
            if name in WRITE_TOOLS and self.policy.dry_run:
                record["dry_run"] = True
            return getattr(self, "_" + name)(args, record)
        except Refused as refusal:
            record["refused"] = str(refusal)
            return {"refused": str(refusal)}, True
        except Exception as failure:  # noqa: BLE001 - the model must get an answer; only the class is shown
            record["error"] = type(failure).__name__
            return {"error": f"{type(failure).__name__} while running {name}; details are in the workflow log"}, True

    # ---- read tools -------------------------------------------------------------------------------------------------

    def _query_metrics(self, args, record):
        _exact(args, ("metric", "window"))
        metric = _choice(args["metric"], METRICS, "metric")
        window = _choice(args["window"], WINDOWS, "window")
        return {"environment": self.alert.environment, "metric": metric, "window": window,
                "series": self.backend.query_metric(metric, window, self.alert.environment)}, False

    def _list_revisions(self, args, record):
        _exact(args, ("service",))
        service = _choice(args["service"], SERVICES, "service")
        return {"service": service, "revisions": self.backend.list_revisions(self.alert.environment, service)}, False

    # ---- write tools ------------------------------------------------------------------------------------------------

    def _rollback(self, args, record):
        _exact(args, ("service", "revision"))
        service = _choice(args["service"], SERVICES, "service")
        revision = args["revision"]
        environment = self.alert.environment
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision) \
                or not revision.startswith(f"cohortlens-{service}-{environment}-"):
            example = f"cohortlens-{service}-{environment}-00006-abc"
            raise Refused(f"revision must be a {service} revision of {environment}, like {example}")
        revisions = self.backend.list_revisions(environment, service)
        target = next((item for item in revisions if item["name"] == revision), None)
        runs = self.backend.successful_runs(environment, target["version"]) if target else 0.0
        history = self.backend.rollback_history(environment, service)
        check_rollback(service=service, revision=revision, revisions=revisions, successful_runs=runs, history=history,
                       now=self._now())
        if self.policy.dry_run:
            return {"dry_run": True, "would_roll_back": {"service": service, "to": revision}}, False
        outcome = self.backend.rollback(environment, service, revision, self.incident)
        ok = bool(outcome.get("ok"))
        return {"ok": ok, "detail": clean_text(str(outcome.get("detail", "")), 500)}, not ok

    def _update_incident(self, args, record):
        _exact(args, ("status", "summary"))
        status = _choice(args["status"], INCIDENT_STATUSES, "status")
        summary = args["summary"]
        if not isinstance(summary, str) or not summary.strip():
            raise Refused("summary must be non-empty text")
        body = clean_text(summary)
        if self.policy.dry_run:
            return {"dry_run": True, "would_comment": body[:200]}, False
        self.backend.comment_incident(self.incident, body, status)
        return {"ok": True, "needs_human": status == "escalated"}, False


def now_utc() -> datetime:
    from datetime import UTC
    return datetime.now(UTC)
