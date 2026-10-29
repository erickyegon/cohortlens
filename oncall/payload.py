"""The alert that starts the on-call agent (issue #24).

Grafana posts it to GitHub as a `repository_dispatch` event of type `grafana-alert`. The payload comes from outside the
repository's trust boundary, so every field is validated against a closed set or a strict pattern, and nothing else is
kept: the agent's context is built from these five values only.
"""
import re
from dataclasses import dataclass
from datetime import datetime

STATUSES = frozenset({"firing", "resolved"})
ALERTS = frozenset({"CohortRunErrorRateHigh", "CohortRunLatencyHigh"})
ENVIRONMENTS = frozenset({"dev", "prod"})
_FINGERPRINT = re.compile(r"[0-9a-f]{1,64}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})")


class PayloadError(ValueError):
    """The dispatch payload is not a well-formed alert."""


@dataclass(frozen=True)
class Alert:
    status: str
    alertname: str
    environment: str
    fingerprint: str
    starts_at: str


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise PayloadError(f"{key} is missing or not text")
    return value


def parse_alert(raw: object) -> Alert:
    if not isinstance(raw, dict):
        raise PayloadError("the payload is not an object")
    status = _text(raw, "status")
    alertname = _text(raw, "alertname")
    environment = _text(raw, "environment")
    fingerprint = _text(raw, "fingerprint")
    starts_at = _text(raw, "startsAt")
    if status not in STATUSES:
        raise PayloadError("status must be firing or resolved")
    if alertname not in ALERTS:
        raise PayloadError("unknown alert name")
    if environment not in ENVIRONMENTS:
        raise PayloadError("unknown environment")
    if not _FINGERPRINT.fullmatch(fingerprint):
        raise PayloadError("fingerprint must be lowercase hex")
    if not _TIMESTAMP.fullmatch(starts_at):
        raise PayloadError("startsAt must be an RFC 3339 timestamp")
    try:
        datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise PayloadError("startsAt is not a real date") from error
    return Alert(status, alertname, environment, fingerprint, starts_at)
