"""Entry point of the on-call workflow: `python -m oncall`.

Inputs come from the environment (the workflow sets them; the alert payload goes in as data, never into a shell line):
ALERT_PAYLOAD (the repository_dispatch client_payload as JSON), ONCALL_ENVIRONMENTS, DRY_RUN, ANTHROPIC_API_KEY,
ONCALL_MODEL, and the backend settings (PROJECT_ID, REGION, GITHUB_REPOSITORY, GRAFANA_URL, GRAFANA_READ_TOKEN).
"""
import json
import os
import sys

from oncall.agent import run_agent
from oncall.backend import LiveBackend
from oncall.guardrails import Policy
from oncall.payload import PayloadError, parse_alert
from oncall.tools import Tools, now_utc

DEFAULT_MODEL = "claude-sonnet-5-5"
FALLBACK = ("The on-call agent finished without posting an update. Treating this as needing a person: see the workflow "
            "run for what it did.")


def _anthropic_client():
    import anthropic  # only here: the tests and the rest of the package never import the SDK

    return anthropic.Anthropic()


def main(env=None, backend_factory=LiveBackend, client_factory=_anthropic_client, now=now_utc) -> int:
    env = os.environ if env is None else env
    try:
        alert = parse_alert(json.loads(env.get("ALERT_PAYLOAD", "")))
        policy = Policy.from_environment(env)
    except (PayloadError, ValueError) as error:
        print(f"refused: {error}")
        return 2
    if alert.environment not in policy.environments:
        enabled = sorted(policy.environments) or "none"
        print(f"ignored: the agent is not enabled for {alert.environment} (enabled: {enabled})")
        return 0
    backend = backend_factory(env)
    mode = "DRY RUN" if policy.dry_run else "LIVE"
    print(f"on-call agent [{mode}]: {alert.alertname} {alert.status} in {alert.environment} ({alert.fingerprint})")

    if alert.status == "resolved":
        incident = backend.find_incident(alert.fingerprint)
        if incident is None:
            print("no open incident for this alert; nothing to close")
        elif policy.dry_run:
            print(f"would close incident #{incident}")
        else:
            backend.comment_incident(incident, "The alert has resolved. Closing this incident.", "resolved")
            print(f"closed incident #{incident}")
        return 0

    if policy.dry_run:
        incident = 0
    else:
        incident = backend.find_incident(alert.fingerprint) or backend.open_incident(alert)
    tools = Tools(policy, backend, alert, incident, now)
    result = run_agent(alert, tools, client_factory(), model=env.get("ONCALL_MODEL", DEFAULT_MODEL))
    for call in tools.calls:
        outcome = "ok"
        if call["refused"]:
            outcome = "REFUSED: " + call["refused"]
        elif call["error"]:
            outcome = "error: " + call["error"]
        arguments = json.dumps(call["args"], sort_keys=True)[:200]
        print(f"  tool {call['tool']} {arguments} dry_run={call['dry_run']} -> {outcome}")
    posted = any(call["tool"] == "update_incident" and not (call["refused"] or call["error"]) for call in tools.calls)
    if not policy.dry_run and not posted:
        backend.comment_incident(incident, FALLBACK, "escalated")
    print(f"finished={result.finished} turns={result.turns}")
    return 0 if result.finished else 1


if __name__ == "__main__":
    sys.exit(main())
