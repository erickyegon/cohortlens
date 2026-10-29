"""The on-call agent's guardrails (issues #25 and #26). They are code, checked before any tool runs, and the
model cannot change them; the SRE agent definition forbids editing this file.

- the tool allowlist: four tools, two of which write;
- the environments the agent may act in (`ONCALL_ENVIRONMENTS`, none by default);
- dry run unless `DRY_RUN=false` is set explicitly;
- a rollback only to an existing, not-serving revision of the service with at least 50 successful runs;
- at most one rollback per service per hour.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

TOOLS = frozenset({"query_metrics", "list_revisions", "rollback", "update_incident"})
WRITE_TOOLS = frozenset({"rollback", "update_incident"})
SERVICES = frozenset({"api", "web"})
ENVIRONMENTS = frozenset({"dev", "prod"})
MIN_SUCCESSFUL_RUNS = 50
ROLLBACK_COOLDOWN = timedelta(hours=1)


class Refused(Exception):
    """A guardrail refused the call; the message is shown to the model so it can choose another course."""


@dataclass(frozen=True)
class Policy:
    environments: frozenset
    dry_run: bool = True

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "Policy":
        names = [part.strip() for part in env.get("ONCALL_ENVIRONMENTS", "").split(",") if part.strip()]
        unknown = [name for name in names if name not in ENVIRONMENTS]
        if unknown:
            raise ValueError(f"ONCALL_ENVIRONMENTS may only contain dev and prod, not {unknown!r}")
        return cls(frozenset(names), env.get("DRY_RUN", "true").strip().lower() != "false")


def check_tool(name: str) -> None:
    if name not in TOOLS:
        raise Refused(f"{name!r} is not an allowed tool; the allowed tools are {sorted(TOOLS)}")


def check_environment(policy: Policy, environment: str) -> None:
    if environment not in policy.environments:
        enabled = sorted(policy.environments) or "none"
        raise Refused(f"the agent is not enabled for {environment!r} (enabled: {enabled})")


def check_rollback(*, service: str, revision: str, revisions: list, successful_runs: float, history: list,
                   now: datetime) -> None:
    if service not in SERVICES:
        raise Refused(f"service must be one of {sorted(SERVICES)}")
    target = next((item for item in revisions if item["name"] == revision), None)
    if target is None:
        raise Refused(f"revision {revision!r} does not exist for this service")
    if target["traffic_percent"] > 0:
        raise Refused(f"revision {revision!r} is already serving traffic")
    if successful_runs < MIN_SUCCESSFUL_RUNS:
        raise Refused(f"revision {revision!r} has {successful_runs:g} successful runs; a rollback target needs at "
                      f"least {MIN_SUCCESSFUL_RUNS}")
    # a timestamp in the future (clock skew or a forged entry) counts as recent: only entries older than an hour
    # are free
    if any(now - moment < ROLLBACK_COOLDOWN for moment in history):
        raise Refused(f"{service} was already rolled back within the last hour; only one automated rollback per hour")
