"""CohortLens API: health endpoints, the X-Trace-Id header, cohort CRUD, runs and concept search."""
import os
import secrets

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.cohorts import router as cohorts_router
from app.concepts import router as concepts_router
from app.db.session import app_engine, reader_engine
from app.errors import database_error_response, error_response, log_failure
from app.runs import router as runs_router

SERVICE_VERSION = os.environ.get("SERVICE_VERSION", "dev")

app = FastAPI(title="CohortLens API", version=SERVICE_VERSION)
app.include_router(cohorts_router)
app.include_router(concepts_router)
app.include_router(runs_router)


@app.middleware("http")
async def trace_header(request: Request, call_next):
    # Replaced by the OpenTelemetry trace id when telemetry lands (issue: OTel traces, metrics and logs).
    trace_id = secrets.token_hex(16)
    request.state.trace_id = trace_id
    try:
        response: Response = await call_next(request)
    except Exception as exc:  # unhandled errors still carry the reference the user quotes, and leave a log line
        log_failure(request, exc)
        response = JSONResponse({"error": "Internal Server Error", "trace_id": trace_id}, status_code=500)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
@app.exception_handler(PoolTimeout)
async def database_failure(request: Request, exc: Exception):
    """Connection-level failures are a 503 "unavailable", timeouts a 503 "took too long", anything else a 500; all
    with the trace reference and a log line, and never with connection details in the body."""
    return database_error_response(request, exc)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """Unknown routes and disallowed methods use the same body shape (and trace id) as every other error."""
    return error_response(request, exc.status_code, str(exc.detail))


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": SERVICE_VERSION}


@app.get("/version")
def version():
    """Which commit this revision runs. Cloud Run reserves /healthz (its front end answers 404 before the request
    reaches the container), so the smoke test and the prod promotion read the running version from here."""
    return {"version": SERVICE_VERSION}


@app.get("/readyz")
def readyz():
    try:
        with reader_engine().connect() as c:
            persons = c.execute(text("select count(*) from cdm.person")).scalar_one()
        with app_engine().connect() as c:
            c.execute(text("select 1 from app.cohort_definition limit 1"))
    except Exception as exc:  # readiness must never raise
        return JSONResponse({"status": "unavailable", "error": type(exc).__name__}, status_code=503)
    return {"status": "ready", "cdm_persons": persons}
