"""Run feasibility and run history (FR-6, FR-8, FR-12, FR-14). Served at `/cohorts/{id}/runs`; the public path is
`/api/cohorts/{id}/runs`.

The flow follows docs/architecture.md and is never one transaction across the two database roles: load the saved
definition (app connection), validate, compile, count every step and the baseline (reader connection, under the
10 s statement timeout), suppress, then record the run (app connection). Only SUPPRESSED values are returned or
stored. A run that fails or times out is recorded too, with the trace id the user is given.
"""
import os
import time
import uuid
from dataclasses import asdict, dataclass

from fastapi import APIRouter, Request
from sqlalchemy import insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.concurrency import run_in_threadpool

from app.cohort_engine.attrition import CohortTimeout, run_attrition
from app.cohort_engine.baseline import PublicBaseline, run_baseline, suppress_baseline
from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import CohortDefinition, DefinitionError, parse_definition
from app.cohort_engine.suppression import PublicStepCount, suppress_steps
from app.db.session import STATEMENT_TIMEOUT_MS, app_engine, reader_engine
from app.db.tables import cohort_definition as cohorts
from app.db.tables import cohort_run as runs_table
from app.errors import database_error_response, error_response, log_failure, logger, trace_id_of

router = APIRouter(prefix="/cohorts/{cohort_id}/runs")

SERVICE_VERSION = os.environ.get("SERVICE_VERSION", "dev")
RUN_TIMEOUT_MS = STATEMENT_TIMEOUT_MS
DEFAULT_HISTORY = 50
MAX_HISTORY = 100


@dataclass(frozen=True)
class RunOutcome:
    steps: list[PublicStepCount]
    baseline: PublicBaseline

    @property
    def final_count(self) -> int | str:
        return self.steps[-1].persons


def execute_run(definition: CohortDefinition, timeout_ms: int) -> RunOutcome:
    """Compile, count every step and the baseline on the READER connection, and suppress. Raises `CohortTimeout` when
    the database cancels a statement; connection failures propagate as SQLAlchemy errors."""
    plan = build_plan(definition)
    with reader_engine().connect() as conn:
        exact_steps = run_attrition(conn, plan, timeout_ms)
        exact_baseline = run_baseline(conn, plan, timeout_ms)
    return RunOutcome(steps=suppress_steps(exact_steps), baseline=suppress_baseline(exact_baseline))


def timeout_message(timeout_ms: int) -> str:
    seconds = f"{timeout_ms / 1000:g}"
    return (f"This cohort took longer than {seconds} seconds to count and was cancelled. "
            "Try a narrower index event, a shorter date range or fewer criteria, then run it again.")


def _iso(value) -> str:
    return value.isoformat()


def _final_count(status: str, step_counts) -> int | str | None:
    return step_counts[-1]["persons"] if status == "success" and step_counts else None


def _run_json(row, baseline: PublicBaseline | None = None) -> dict:
    body = {"id": str(row.id), "cohort_id": str(row.cohort_id), "status": row.status, "step_counts": row.step_counts,
            "final_count": _final_count(row.status, row.step_counts), "duration_ms": row.duration_ms,
            "trace_id": row.trace_id, "service_version": row.service_version, "created_at": _iso(row.created_at)}
    if baseline is not None:
        body["baseline"] = asdict(baseline)
    return body


def _load_definition(cohort_id: uuid.UUID):
    with app_engine().connect() as conn:
        return conn.execute(select(cohorts.c.definition).where(cohorts.c.id == cohort_id)).one_or_none()


def _record(cohort_id: uuid.UUID, status: str, trace_id: str, duration_ms: int, steps: list[PublicStepCount] | None):
    """Store the run. `step_counts` holds the suppressed values; `final_count` the exact number only when it is a real
    number (a suppressed `"<11"` cannot be an integer and the exact small count must not be stored)."""
    step_counts = [asdict(step) for step in steps] if steps is not None else None
    final = steps[-1].persons if steps else None
    statement = insert(runs_table).values(
        cohort_id=cohort_id, status=status, step_counts=step_counts,
        final_count=final if isinstance(final, int) else None, duration_ms=duration_ms, trace_id=trace_id,
        service_version=SERVICE_VERSION).returning(*runs_table.c)
    with app_engine().begin() as conn:
        return conn.execute(statement).one()


def _history(cohort_id: uuid.UUID, limit: int):
    exists = select(cohorts.c.id).where(cohorts.c.id == cohort_id)
    statement = (select(*runs_table.c).where(runs_table.c.cohort_id == cohort_id)
                 .order_by(runs_table.c.created_at.desc(), runs_table.c.id.desc()).limit(limit))
    with app_engine().connect() as conn:
        if conn.execute(exists).first() is None:
            return None
        return conn.execute(statement).all()


def _parse_id(cohort_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(cohort_id)
    except ValueError:
        return None


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


@router.post("", status_code=201)
async def run_cohort(cohort_id: str, request: Request):
    parsed = _parse_id(cohort_id)
    row = await run_in_threadpool(_load_definition, parsed) if parsed else None
    if row is None:
        return error_response(request, 404, "Cohort not found")
    trace_id = trace_id_of(request)
    started = time.perf_counter()

    try:
        definition = parse_definition(row.definition)
    except DefinitionError as error:
        await run_in_threadpool(_record, parsed, "failed", trace_id, _elapsed_ms(started), None)
        return error_response(request, 422, "The saved cohort no longer passes validation. Edit it and save it again.",
                              error.errors)

    try:
        outcome = await run_in_threadpool(execute_run, definition, RUN_TIMEOUT_MS)
    except CohortTimeout as exc:
        logger.warning("run timed out exception=%s trace_id=%s", type(exc).__name__, trace_id)
        recorded = await run_in_threadpool(_record, parsed, "timeout", trace_id, _elapsed_ms(started), None)
        return error_response(request, 504, timeout_message(RUN_TIMEOUT_MS), extra=_run_ref(recorded))
    except Exception as exc:  # noqa: BLE001 - every failure is recorded and answered with the trace reference
        recorded = await run_in_threadpool(_record, parsed, "failed", trace_id, _elapsed_ms(started), None)
        if isinstance(exc, (DBAPIError, PoolTimeout)):  # connection-level: 503; anything else a plain 500
            return database_error_response(request, exc, extra=_run_ref(recorded),
                                           fallback_message="Something went wrong running this cohort.")
        log_failure(request, exc)
        return error_response(request, 500, "Something went wrong running this cohort.", extra=_run_ref(recorded))

    recorded = await run_in_threadpool(_record, parsed, "success", trace_id, _elapsed_ms(started), outcome.steps)
    return _run_json(recorded, outcome.baseline)


def _run_ref(recorded) -> dict:
    """The run id and status added to an error body so the UI can link the failed run in the history."""
    return {"run_id": str(recorded.id), "status": recorded.status}


@router.get("")
async def run_history(cohort_id: str, request: Request):
    parsed = _parse_id(cohort_id)
    if parsed is None:
        return error_response(request, 404, "Cohort not found")
    raw_limit = request.query_params.get("limit", str(DEFAULT_HISTORY))
    if not (raw_limit.isascii() and raw_limit.isdigit() and 1 <= int(raw_limit) <= MAX_HISTORY):
        return error_response(request, 422, "Invalid history request", [
            {"field": "limit", "message": f"The limit must be a whole number from 1 to {MAX_HISTORY}"}])
    rows = await run_in_threadpool(_history, parsed, int(raw_limit))
    if rows is None:
        return error_response(request, 404, "Cohort not found")
    return [_run_json(row) for row in rows]
