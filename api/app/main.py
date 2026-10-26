"""CohortLens API. Phase 1 skeleton: health endpoints and the X-Trace-Id header."""
import os
import secrets

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import app_engine, reader_engine

SERVICE_VERSION = os.environ.get("SERVICE_VERSION", "dev")

app = FastAPI(title="CohortLens API", version=SERVICE_VERSION)


@app.middleware("http")
async def trace_header(request: Request, call_next):
    # Replaced by the OpenTelemetry trace id when telemetry lands (issue: OTel traces, metrics and logs).
    trace_id = secrets.token_hex(16)
    try:
        response: Response = await call_next(request)
    except Exception:  # unhandled errors still carry the reference the user quotes
        response = JSONResponse({"detail": "Internal Server Error", "trace_id": trace_id}, status_code=500)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": SERVICE_VERSION}


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
