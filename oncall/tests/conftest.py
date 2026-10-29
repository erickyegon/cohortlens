"""Shared fakes for the on-call agent tests. Nothing here touches the network, Google Cloud, GitHub or Anthropic."""
import json
import pathlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

RECORDINGS = pathlib.Path(__file__).parent / "recordings"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
BAD_SHA = "b" * 40
GOOD_SHA = "a" * 40
OTHER_SHA = "c" * 40
NEW = "cohortlens-api-dev-00007-bad"
OLD = "cohortlens-api-dev-00006-ok1"
ANCIENT = "cohortlens-api-dev-00002-old"


def default_revisions():
    return [
        {"name": NEW, "created": (NOW - timedelta(minutes=12)).isoformat(), "version": BAD_SHA, "traffic_percent": 100},
        {"name": OLD, "created": (NOW - timedelta(hours=5)).isoformat(), "version": GOOD_SHA, "traffic_percent": 0},
        {"name": ANCIENT, "created": (NOW - timedelta(days=2)).isoformat(), "version": OTHER_SHA, "traffic_percent": 0},
    ]


class FakeBackend:
    """Everything the tools can touch, recorded. `rollbacks` and `comments` are the only writes."""

    def __init__(self, *, revisions=None, success_runs=None, history=None):
        self.revisions = revisions if revisions is not None else default_revisions()
        default_runs = {GOOD_SHA: 120.0, BAD_SHA: 3.0, OTHER_SHA: 4.0}
        self.success_runs = success_runs if success_runs is not None else default_runs
        self.history = history if history is not None else []
        self.rollbacks = []
        self.comments = []
        self.queries = []

    def query_metric(self, name, window, environment):
        self.queries.append((name, window, environment))
        return [{"revision": BAD_SHA, "value": 0.62}, {"revision": GOOD_SHA, "value": 0.0}]

    def list_revisions(self, environment, service):
        return [dict(item) for item in self.revisions]

    def successful_runs(self, environment, version):
        return self.success_runs.get(version, 0.0)

    def rollback_history(self, environment, service):
        return list(self.history)

    def rollback(self, environment, service, revision, incident):
        self.rollbacks.append((environment, service, revision, incident))
        return {"ok": True, "detail": "traffic moved"}

    def comment_incident(self, incident, body, status):
        self.comments.append((incident, status, body))


class ReplayClient:
    """Plays back recorded model responses, in order. It has the one method the agent uses and fails if asked for
    more."""

    def __init__(self, recording):
        self._responses = list(recording)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=str)))
        if not self._responses:
            raise AssertionError("the agent asked for more model turns than were recorded")
        item = self._responses.pop(0)
        blocks = [SimpleNamespace(**block) for block in item["content"]]
        return SimpleNamespace(stop_reason=item["stop_reason"], content=blocks)

    @property
    def unused(self):
        return len(self._responses)


def load_recording(name):
    return json.loads((RECORDINGS / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def backend():
    return FakeBackend()


@pytest.fixture()
def now():
    return lambda: NOW
