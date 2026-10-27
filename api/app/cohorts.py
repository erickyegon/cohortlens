"""Cohort CRUD (FR-2). Served at `/cohorts`; the nginx proxy strips `/api`, so the public path is `/api/cohorts`.

Definitions are validated by `parse_definition` before anything is stored; the stored JSON is the validated,
normalised definition. Only definitions are handled here: no patient-level data is read or returned.
"""
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import delete, func, insert, select, update
from starlette.concurrency import run_in_threadpool

from app.cohort_engine.models import DefinitionError, parse_definition
from app.db.session import app_engine
from app.db.tables import cohort_definition as cohorts
from app.db.tables import cohort_run as runs
from app.errors import error_response

router = APIRouter(prefix="/cohorts")

MAX_BODY_BYTES = 256 * 1024  # a definition is a few KB; anything near this is not a cohort


def _iso(value: datetime) -> str:
    return value.isoformat()


def _full(row) -> dict:
    return {"id": str(row.id), "name": row.name, "version": row.version, "definition": row.definition,
            "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}


def _summary(row) -> dict:
    return {"id": str(row.id), "name": row.name, "version": row.version,
            "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}


def _parse_id(cohort_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(cohort_id)
    except ValueError:
        return None


def _not_found(request: Request):
    return error_response(request, 404, "Cohort not found")


async def _read_definition(request: Request):
    """Read at most MAX_BODY_BYTES, parse the JSON and validate it. Returns (definition, None) or (None, response)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None, error_response(request, 413, "The request body is too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None, error_response(request, 413, "The request body is too large")
    try:
        raw = json.loads(bytes(body))
    except (ValueError, RecursionError):
        return None, error_response(request, 422, "Invalid cohort definition",
                                    [{"field": "", "message": "The request body must be a JSON object"}])
    try:
        return parse_definition(raw), None
    except DefinitionError as error:
        return None, error_response(request, 422, "Invalid cohort definition", error.errors)


def _insert(name: str, definition: dict):
    with app_engine().begin() as conn:
        return conn.execute(insert(cohorts).values(name=name, definition=definition).returning(*cohorts.c)).one()


def _update(cohort_id: uuid.UUID, name: str, definition: dict):
    statement = (update(cohorts).where(cohorts.c.id == cohort_id)
                 .values(name=name, definition=definition, version=cohorts.c.version + 1, updated_at=func.now())
                 .returning(*cohorts.c))
    with app_engine().begin() as conn:
        return conn.execute(statement).one_or_none()


def _get(cohort_id: uuid.UUID):
    with app_engine().connect() as conn:
        return conn.execute(select(cohorts).where(cohorts.c.id == cohort_id)).one_or_none()


def _list():
    """Cohort rows, most recently updated first, and the newest run of each cohort that has one."""
    statement = select(*cohorts.c).order_by(cohorts.c.updated_at.desc(), cohorts.c.created_at.desc(), cohorts.c.id)
    newest = (select(runs.c.cohort_id, runs.c.status, runs.c.step_counts, runs.c.created_at)
              .distinct(runs.c.cohort_id).order_by(runs.c.cohort_id, runs.c.created_at.desc(), runs.c.id.desc()))
    with app_engine().connect() as conn:
        return conn.execute(statement).all(), {row.cohort_id: row for row in conn.execute(newest)}


def _last_run(row) -> dict | None:
    """What the cohort list shows about the newest run: its status, the (suppressed) final count and when it ran."""
    if row is None:
        return None
    final = row.step_counts[-1]["persons"] if row.status == "success" and row.step_counts else None
    return {"status": row.status, "final_count": final, "created_at": _iso(row.created_at)}


def _delete(cohort_id: uuid.UUID) -> bool:
    statement = delete(cohorts).where(cohorts.c.id == cohort_id).returning(cohorts.c.id)
    with app_engine().begin() as conn:
        return conn.execute(statement).first() is not None


@router.post("", status_code=201)
async def create_cohort(request: Request, response: Response):
    definition, failure = await _read_definition(request)
    if failure:
        return failure
    row = await run_in_threadpool(_insert, definition.name, definition.model_dump(mode="json"))
    response.headers["Location"] = f"/api/cohorts/{row.id}"
    return _full(row)


@router.get("")
async def list_cohorts():
    rows, newest = await run_in_threadpool(_list)
    return [{**_summary(row), "last_run": _last_run(newest.get(row.id))} for row in rows]


@router.get("/{cohort_id}")
async def get_cohort(cohort_id: str, request: Request):
    parsed = _parse_id(cohort_id)
    row = await run_in_threadpool(_get, parsed) if parsed else None
    return _full(row) if row else _not_found(request)


@router.put("/{cohort_id}")
async def update_cohort(cohort_id: str, request: Request):
    parsed = _parse_id(cohort_id)
    if parsed is None:
        return _not_found(request)
    definition, failure = await _read_definition(request)
    if failure:
        return failure
    row = await run_in_threadpool(_update, parsed, definition.name, definition.model_dump(mode="json"))
    return _full(row) if row else _not_found(request)


@router.delete("/{cohort_id}", status_code=204)
async def delete_cohort(cohort_id: str, request: Request):
    parsed = _parse_id(cohort_id)
    if parsed is None or not await run_in_threadpool(_delete, parsed):
        return _not_found(request)
    return Response(status_code=204)
